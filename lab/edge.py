"""The edge study: every chart-setup signal on history, the option trade it implies, and what happened.

For each day and each closed 5-minute bar, the live setup rules (live/setups.py) are evaluated exactly as the live
scanner does. Every signal becomes a paper option buy (at-the-money call for longs, put for shorts) entered one
minute after the bar closes, exited at the setup's target or stop on the index, a time stop, or 15:15. Costs and
slippage are included. Alongside, ~25 facts known at signal time are recorded, so a model can learn which signals
to take (lab/meta.py) and the live app can say how similar signals did.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from live import setups as S, ta
from live.detectors import COOLDOWN

from .data import UNDERLYINGS, hist_dir, iter_days
from .engine import charges, slip

M_EXIT = 360            # 15:15
MAX_HOLD = 90           # minutes: option buyers can't wait all day for a move
SKIP_AFTER = 345        # no new signals after 15:00


def _daily(data_dir, sym) -> pd.DataFrame:
    f = hist_dir(data_dir) / sym / "daily.parquet"
    if not f.exists():
        return pd.DataFrame()
    d = pd.read_parquet(f).sort_values("date").reset_index(drop=True)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    r = np.log(d["close"] / d["close"].shift(1))
    # Parkinson range volatility: a better daily estimate than close-to-close
    pk = np.log(d["high"] / d["low"]) ** 2 / (4 * math.log(2))
    d["rv20"] = np.sqrt(pk.rolling(20).mean() * 252) * 100
    d["rv5"] = np.sqrt(pk.rolling(5).mean() * 252) * 100
    d["rv1"] = np.sqrt(pk * 252) * 100
    d["ret5"] = d["close"].pct_change(5) * 100
    d["cc20"] = r.rolling(20).std() * math.sqrt(252) * 100
    return d


def har_forecast(rv1, rv5, rv20):
    """Next-day volatility forecast (HAR: yesterday, last week, last month). Fixed weights from the literature,
    so nothing is fitted on the data it is tested on."""
    return 0.1 + 0.37 * rv1 + 0.36 * rv5 + 0.25 * rv20


def _expiries(data_dir, sym):
    out = []
    for f in sorted((hist_dir(data_dir) / sym).glob("raw_*.parquet")):
        tag = f.stem[4:]
        if len(tag) == 10:
            out.append(dt.date.fromisoformat(tag))
    return out


def _bars(spot):
    """1-minute closes -> 5-minute bars (o/h/l/c from the closes)."""
    n = int(np.sum(~np.isnan(spot)) // 5)
    seg = spot[:5 * n].reshape(n, 5)
    return {"t": np.arange(n) * 300.0, "o": seg[:, 0], "h": seg.max(1), "l": seg.min(1), "c": seg[:, -1], "v": np.zeros(n)}


def _atm_iv(day, m):
    k = day.atm(m)
    v = [day.price(k, s, m, "iv") for s in ("CE", "PE")]
    v = [x for x in v if not math.isnan(x)]
    return float(np.mean(v)) if v else math.nan


def _straddle(day, m):
    k = day.atm(m)
    c, p = day.price(k, "CE", m), day.price(k, "PE", m)
    return c + p if not (math.isnan(c) or math.isnan(p)) else math.nan


def _oi_view(day, m, spot, step):
    """OI walls and positioning near the money at minute m (from the strikes we hold)."""
    ce, pe = {}, {}
    for (k, side), a in day.px.items():
        oi = a.get("oi")
        if oi is None:
            continue
        x = oi[m]
        if math.isnan(x):
            continue
        (ce if side == "CE" else pe)[k] = x
    above = {k: v for k, v in ce.items() if k >= spot}
    below = {k: v for k, v in pe.items() if k <= spot}
    res = max(above, key=above.get) if above else None
    sup = max(below, key=below.get) if below else None
    near = [k for k in set(ce) | set(pe) if abs(k - spot) <= 5 * step]
    tce = sum(ce.get(k, 0) for k in near)
    tpe = sum(pe.get(k, 0) for k in near)
    return {"resistance": res, "support": sup, "pcr_near": tpe / tce if tce else math.nan, "ce_oi": tce, "pe_oi": tpe}


def _trade(day, sym, m_sig, d, stop, target, lot):
    """Buy ATM CE (long) / PE (short) at the next minute; exit on index target/stop, time stop or 15:15."""
    m0 = m_sig + 1
    if m0 >= M_EXIT:
        return None
    k = day.atm(m0)
    side = "CE" if d > 0 else "PE"
    px = day.px.get((float(k), side))
    if px is None:
        return None
    e = px["c"][m0]
    if math.isnan(e) or e <= 0.5:
        return None
    entry = e + slip(sym, e)
    end = min(M_EXIT, m0 + MAX_HOLD)
    reason, mx = "time", end
    sp = day.spot
    for m in range(m0 + 1, end + 1):
        s = sp[m]
        if (d > 0 and s <= stop) or (d < 0 and s >= stop):
            reason, mx = "stop", m
            break
        if (d > 0 and s >= target) or (d < 0 and s <= target):
            reason, mx = "target", m
            break
    x = px["c"][mx]
    if math.isnan(x):
        return None
    exit_ = max(0.05, x - slip(sym, x))
    gross = (exit_ - entry) * lot
    cost = charges([(1, entry), (-1, exit_)], lot)
    alt = {f"pnl_{name}": v for name, v in _exit_variants(day, sym, px, m0, entry, d, stop, lot).items()}
    # best price seen while holding (how much was there to take)
    hold = px["c"][m0:mx + 1]
    mfe = (np.nanmax(hold) - e) / e * 100 if len(hold) else 0.0
    return {"strike": k, "side": side, "entry_px": round(entry, 2), "exit_px": round(exit_, 2), "exit_m": mx,
            "exit_reason": reason, "hold_min": mx - m0, "pnl": round(gross - cost, 2), "ret_pct": round((exit_ / entry - 1) * 100, 2),
            "mfe_pct": round(float(mfe), 2), "premium_lot": round(entry * lot, 0), **alt}


# exit rules tested side by side: (option target %, option stop %, max minutes, trail after +x% keeping y of the gain)
EXITS = {"p20_15": (20, 15, 60, None), "p30_20": (30, 20, 90, None), "p50_25": (50, 25, 120, None),
         "trail": (None, 20, 150, (20, 0.5))}


def _exit_variants(day, sym, px, m0, entry, d, idx_stop, lot) -> dict:
    out = {}
    c = px["c"]
    sp = day.spot
    for name, (tgt, stp, hold, trail) in EXITS.items():
        end = min(M_EXIT, m0 + hold)
        mx, peak = end, 0.0
        for m in range(m0 + 1, end + 1):
            x = c[m]
            if math.isnan(x):
                continue
            g = (x / entry - 1) * 100
            peak = max(peak, g)
            if (d > 0 and sp[m] <= idx_stop) or (d < 0 and sp[m] >= idx_stop) or g <= -stp:
                mx = m
                break
            if tgt is not None and g >= tgt:
                mx = m
                break
            if trail and peak >= trail[0] and g <= peak * trail[1]:
                mx = m
                break
        x = c[mx]
        if math.isnan(x):
            out[name] = math.nan
            continue
        ex = max(0.05, x - slip(sym, x))
        out[name] = round((ex - entry) * lot - charges([(1, entry), (-1, ex)], lot), 2)
    return out


def study(data_dir, syms=("NIFTY", "BANKNIFTY"), log=print, should_stop=lambda: False) -> pd.DataFrame:
    rows = []
    for sym in syms:
        daily = _daily(data_dir, sym)
        vix = _daily(data_dir, "INDIAVIX")
        if daily.empty:
            log(f"{sym}: no daily prices, skipped")
            continue
        dmap = {r.date: r for r in daily.itertuples()}
        dates = list(daily["date"])
        vmap = {r.date: r for r in vix.itertuples()} if not vix.empty else {}
        exps = _expiries(data_dir, sym)
        step, lot = UNDERLYINGS[sym]["step"], UNDERLYINGS[sym]["lot"]
        iv_hist = []                      # 09:20 ATM IV by day, for IV rank
        n_days = 0
        for day in iter_days(data_dir, sym):
            if should_stop():
                break
            if day.date not in dmap:
                continue
            i = dates.index(day.date)
            if i < 21:
                continue
            prev = dmap[dates[i - 1]]
            today = dmap[day.date]
            nxt = [e for e in exps if e >= day.date]
            dte = (nxt[0] - day.date).days if nxt else math.nan
            iv920 = _atm_iv(day, 5)
            iv_rank = (sum(x <= iv920 for x in iv_hist[-60:]) / len(iv_hist[-60:]) * 100) if len(iv_hist) >= 20 and not math.isnan(iv920) else math.nan
            if not math.isnan(iv920):
                iv_hist.append(iv920)
            fc = har_forecast(prev.rv1, prev.rv5, prev.rv20)
            str920 = _straddle(day, 5)
            exp_move = str920 / day.spot[5] * 100 if str920 == str920 else math.nan
            vp = vmap.get(dates[i - 1])
            vt = vmap.get(day.date)
            vix_prev = getattr(vp, "close", math.nan) if vp is not None else math.nan
            vix_open = getattr(vt, "open", math.nan) if vt is not None else math.nan
            b = _bars(day.spot)
            if len(b["c"]) < 6:
                continue
            ind = S.indicators(b)
            cpr = ta.cpr(prev.high, prev.low, prev.close)
            n_days += 1
            last = {}                     # (setup, dir) -> minute of the last signal: same cooldown as the live alerts
            for j in range(3, len(b["c"])):
                m_sig = 5 * j + 4
                if m_sig > SKIP_AFTER:
                    break
                bi = {k: v[:j + 1] for k, v in b.items()}
                ii = {k: v[:j + 1] for k, v in ind.items()}
                spot = float(b["c"][j])
                oi = _oi_view(day, m_sig, spot, step)
                ctx = {"is_index": True, "pdh": prev.high, "pdl": prev.low, "pdc": prev.close, "cpr": cpr,
                       "or_high": float(max(b["h"][:3])), "or_low": float(min(b["l"][:3])),
                       "resistance": oi["resistance"], "support": oi["support"], "gamma": None}
                for s in S.evaluate(bi, ii, ctx):
                    d = 1 if s["direction"] == "up" else -1
                    key = (s["setup"], d)
                    if key in last and m_sig - last[key] < COOLDOWN.get("setup_" + s["setup"], 30):
                        continue
                    last[key] = m_sig
                    t = _trade(day, sym, m_sig, d, s["stop"], s["target"], lot)
                    if not t:
                        continue
                    o0 = day.spot[0]
                    hi, lo = np.nanmax(day.spot[:m_sig + 1]), np.nanmin(day.spot[:m_sig + 1])
                    atr = ii["atr"][-1]
                    iv_now = _atm_iv(day, m_sig)
                    oi0 = _oi_view(day, 15, day.spot[15], step) if m_sig > 15 else oi
                    rows.append({
                        "sym": sym, "date": day.date, "minute": m_sig, "setup": s["setup"], "dir": d,
                        "entry_spot": round(spot, 2), "stop": round(s["stop"], 2), "target": round(s["target"], 2), "rr": s["rr"],
                        # facts known at the signal
                        "dte": dte, "iv": iv_now, "iv920": iv920, "iv_rank": iv_rank, "rv20": prev.rv20,
                        "iv_vs_forecast": iv920 / fc if fc and fc == fc and iv920 == iv920 else math.nan,
                        "exp_move_pct": exp_move, "vix": vix_open if vix_open == vix_open else vix_prev,
                        "vix_chg": (vix_open / vix_prev - 1) * 100 if vix_prev and vix_open == vix_open and vix_prev == vix_prev else math.nan,
                        "gap_pct": (o0 / prev.close - 1) * 100, "ret5d": prev.ret5,
                        "move_pct": d * (spot / o0 - 1) * 100,
                        "range_vs_exp": ((hi - lo) / o0 * 100) / exp_move if exp_move and exp_move == exp_move else math.nan,
                        "dist_vwap_pct": d * (spot / ii["vw"][-1] - 1) * 100, "rsi": ii["rsi"][-1] if d > 0 else 100 - ii["rsi"][-1],
                        "ema_spread_pct": d * (ii["e9"][-1] / ii["e21"][-1] - 1) * 100, "st_with": int(ii["sd"][-1] == d),
                        "cpr_width": cpr.get("width_pct"), "risk_atr": abs(spot - s["stop"]) / atr if atr and atr == atr else math.nan,
                        "pcr_near": oi["pcr_near"],
                        "oi_flow": d * ((oi["pe_oi"] - oi0["pe_oi"]) - (oi["ce_oi"] - oi0["ce_oi"])) / max(1.0, oi0["pe_oi"] + oi0["ce_oi"]),
                        "wall_room": ((oi["resistance"] - spot) if d > 0 else (spot - oi["support"])) / spot * 100
                        if (oi["resistance"] if d > 0 else oi["support"]) else math.nan,
                        **t, "win": int(t["pnl"] > 0)})
        log(f"{sym}: {n_days} days, {sum(1 for r in rows if r['sym'] == sym)} signals")
    return pd.DataFrame(rows)


def setup_table(sig: pd.DataFrame) -> pd.DataFrame:
    """Per setup and direction: how often, win rate, average rupees per lot after costs, profit factor, t-stat."""
    if sig.empty:
        return pd.DataFrame()
    def agg(g):
        p = g["pnl"]
        win, loss = p[p > 0].sum(), -p[p < 0].sum()
        return pd.Series({"n": len(p), "win_pct": (p > 0).mean() * 100, "avg_rs": p.mean(), "median_rs": p.median(),
                          "pf": win / loss if loss else np.inf, "t": p.mean() / (p.std(ddof=1) / math.sqrt(len(p))) if len(p) > 2 and p.std() > 0 else 0.0,
                          "target_pct": (g["exit_reason"] == "target").mean() * 100, "avg_hold": g["hold_min"].mean()})
    out = sig.groupby(["sym", "setup", "dir"]).apply(agg, include_groups=False).reset_index()
    return out.sort_values("avg_rs", ascending=False)
