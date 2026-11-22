"""Trend Rider in the Strategy Lab (2.87): the live setup (lab/rider.py) and its exit (lab/rider_exit.py) replayed on
every day of the Lab's option history, graded by days, with walk-forward and holdout checks.

Mandeep, 22 Nov 2026: "Karo bro or baaki jo pending the ... ek ke baad ek khud karna". The 19 Nov replay was one day;
this measures the same setup on a year (and, for Nifty / Bank Nifty, five years) before any number changes.

How (a proxy, said plainly on the page):
  - The underlying's 1-minute closes become 5-minute bars; the setup is read at each closed bar exactly as live
    (first look at 09:30, the same windows, 2 signals a symbol a day, 15 minutes apart). The Lab has no volume, bid /
    ask or order flow and no live greeks: VWAP is the time-weighted average, flow and option thrust are unknown (so at
    most 4 of the 6 families can agree), delta 0.5, the theta stand-in, the Lab's slippage for the spread.
  - Entry: the at-the-money option at the bar's last minute close plus slippage. Exit: the Trend Rider exit fed with
    every minute (5-minute closes for the stop, lock and trail; the option's minute close for the emergency stop),
    out at the minute's close minus slippage, real charges (rules/costs.json) on 1 lot (rules/lots.json).
  - The same entries are also exited the bench way (+30% / -20% / 60 minutes) as the yardstick.
  - Variants: the first window from 09:30 (as live), 09:45 or 10:00; no-move after 3 (as now), 4 or 6 bars or never;
    emergency stop -35% (as now) or -25%.
  - Grading by days (a day's trades are not independent): ₹ a day for 1 lot of each signal, its t-value and its lower
    95% bound; green-day share. Walk-forward: each month is judged by the variant that was best on the months before
    it. Holdout: the last fifth of the days is never used to choose.

Gun: knows no symbol or date; every number of the setup is rules/buy_setup.json. Reports only: nothing here changes
a rule, the live setup or a paper account (Mandeep decides). Paper research: nothing here can place an order.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import json
import math
import time
from pathlib import Path

import numpy as np

from . import rider as R
from . import rider_exit as X

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
VERSION = 1
N_MIN = 375
WIN_STARTS = (15, 30, 45)                      # minutes after 09:15 of the first look (live's first look is 09:30)
NOMOVE = (3, 4, 6, 99)                         # 99 = never
EMERG = (35.0, 25.0)
V1 = "w15_nm3_em35"                           # the rules as they are (live)
FIXED = (30.0, 20.0, 60)                       # the bench's buy: +30% / -20% / 60 minutes
MIN_DAYS_CHOOSE = 20


# ------------------------------------------------------------------ small helpers
def _bars(spot: np.ndarray, upto_bar: int) -> dict:
    """5-minute bars 0..upto_bar (inclusive) from minute closes."""
    n = (upto_bar + 1) * 5
    s = np.asarray(spot[:n], float).reshape(-1, 5)
    return {"o": s[:, 0], "h": s.max(1), "l": s.min(1), "c": s[:, -1], "v": np.zeros(len(s))}


def _lot(sym: str, day) -> int:
    from .data import UNDERLYINGS
    from .rules import lot
    try:
        return int(lot(sym, day) or UNDERLYINGS.get(sym, {}).get("lot") or 1)
    except Exception:
        return int(UNDERLYINGS.get(sym, {}).get("lot") or 1)


def _opt(d, sym, side: str, m: int, lot: int, dte) -> dict | None:
    from .engine import slip
    k = d.atm(m)
    px = d.price(k, side, m)
    if not px or not math.isfinite(px) or px <= 0:
        return None
    s = slip(sym, px)
    return {"ltp": float(px), "bid": float(px - s), "ask": float(px + s), "delta": 0.5, "theta": None, "lot": lot,
            "dte": (float(dte) + 1.0) if dte is not None and math.isfinite(float(dte)) else None, "strike": float(k)}


def _sig(c_: dict) -> str:
    entry = {k: v for k, v in c_.items() if k not in ("exit", "exit_about", "about")}
    src = inspect.getsource(R) + inspect.getsource(_signals_day) + json.dumps(entry, sort_keys=True, default=str)
    return hashlib.sha1(f"{VERSION}|{src}".encode()).hexdigest()[:16]


def _ctx_rows(table) -> dict:
    """date -> what was known before the day: yesterday's high / low, daily ATR and average range (14 days)."""
    out = {}
    if table is None or len(table) == 0:
        return out
    h, l, c = table["high"].to_numpy(float), table["low"].to_numpy(float), table["close"].to_numpy(float)
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    rng = h - l
    for i, d in enumerate(table["date"].tolist()):
        lo = max(0, i - 14)
        out[d] = {"pdh": float(h[i - 1]) if i else None, "pdl": float(l[i - 1]) if i else None,
                  "datr": float(np.nanmean(tr[lo:i])) if i >= 3 else None, "adr": float(np.nanmean(rng[lo:i])) if i >= 3 else None,
                  "dte": table["dte"].iloc[i] if "dte" in table else None}
    return out


# ------------------------------------------------------------------ the setup on one day
def _signals_day(d, sym: str, ctx: dict, c_: dict, lot: int, capital: float, nifty_spot=None) -> list:
    """Every closed bar inside the windows where the setup passes: [bar, dir, stop, atr, s0]. No cooldown here (each
    variant applies its own first window, cooldown and 2-a-day)."""
    spot = np.asarray(d.spot, float)
    if not np.isfinite(spot).all():
        return []
    wins = [[min(float(w[0]), float(WIN_STARTS[0])), float(w[1])] for w in (c_.get("windows") or [])]
    cw = {**c_, "windows": wins}
    out = []
    for j in range(2, N_MIN // 5):                 # bar j closes at slot j+1; live looks from slot 3 (09:30)
        tod = 5.0 * (j + 1)
        if not R.in_window(tod, cw):
            continue
        b = _bars(spot, j)
        m = 5 * j + 4
        up, dn = _opt(d, sym, "CE", m, lot, ctx.get("dte")), _opt(d, sym, "PE", m, lot, ctx.get("dte"))
        nb = _bars(nifty_spot, j) if nifty_spot is not None and sym != "NIFTY" else None
        r = R.readings(b, ctx, c_, nb, up, dn, capital, None, None)
        bo = r.get("breakout")
        dec = R.decide(b, r, tod, cw, (up if bo and bo["dir"] > 0 else dn) if bo else None)
        if dec.get("go") and dec.get("stop") is not None and r.get("atr"):
            out.append([j, int(dec["dir"]), float(dec["stop"]), float(r["atr"]), float(spot[m])])
    return out


def pick(sigs: list, start: float, cooldown_min: float, per_day: int) -> list:
    """The signals a live day would have fired with this first window: from `start`, `cooldown_min` apart, at most
    `per_day`."""
    out, last = [], None
    for s in sigs:
        tod = 5.0 * (s[0] + 1)
        if tod < start:
            continue
        if last is not None and tod - last < cooldown_min:
            continue
        out.append(s)
        last = tod
        if len(out) >= per_day:
            break
    return out


# ------------------------------------------------------------------ one trade, minute by minute
def simulate(d, sym: str, s: list, lot: int, ec: dict | None = None, fixed: tuple | None = None) -> dict | None:
    """One signal [bar, dir, stop, atr, s0] traded: the rider exit (ec) or the fixed exit (target %, stop %, minutes)."""
    from .engine import charge_amount, slip
    j, dr, stop, atr, s0 = s
    m0 = 5 * j + 4
    side = "CE" if dr > 0 else "PE"
    k = d.atm(m0)
    px0 = d.price(k, side, m0)
    if not px0 or not math.isfinite(px0) or px0 <= 0:
        return None
    entry = px0 + slip(sym, px0)
    st0 = dt.datetime.combine(d.date, dt.time(9, 15), IST).timestamp()
    stop_m = (dt.datetime.combine(d.date, dt.time(15, 10), IST).timestamp() - st0) // 60
    plan = X.RiderPlan(s0, dr, stop, atr, entry, st0 + 60 * (m0 + 1), ec) if fixed is None else None
    why, mx, last = None, None, None
    for m in range(m0 + 1, N_MIN):
        o = d.price(k, side, m)
        o = float(o) if o and math.isfinite(o) else None
        if o is not None:
            last = (m, o)
        if plan is not None:
            why = plan.check(float(d.spot[m]), o, st0 + 60 * m + 30)
        else:
            tp, sl, hold = fixed
            if o is not None and o >= entry * (1 + tp / 100):
                why = "target"
            elif o is not None and o <= entry * (1 - sl / 100):
                why = "stop loss"
            elif m - m0 >= hold or m >= stop_m:
                why = "time"
        if why:
            mx = m
            break
    if last is None:
        return None
    if mx is None:                                # the data ended first
        mx, why = last[0], "end of day"
    xo = last[1]                                  # the option's last known close at the exit minute
    exitp = max(0.05, xo - slip(sym, xo))
    gross = (exitp - entry) * lot
    net = gross - charge_amount(entry * lot, exitp * lot, 2, d.date)
    return {"net": round(net, 2), "pct": round(net / (entry * lot) * 100, 3), "why": why, "mins": int(mx - m0),
            "entry": round(entry, 2), "exit": round(exitp, 2)}


# ------------------------------------------------------------------ grading by days
def day_stats(days: dict) -> dict:
    """days: date -> ₹ that day (1 lot of each trade). Mean, t-value, one-sided 95% lower bound, green share."""
    v = np.array(list(days.values()), float)
    n = len(v)
    if n == 0:
        return {"days": 0, "per_day": None, "total": 0.0, "t": None, "lcb": None, "green": None}
    mu = float(v.mean())
    sd = float(v.std(ddof=1)) if n > 1 else float("nan")
    se = sd / math.sqrt(n) if n > 1 and sd > 0 else float("nan")
    try:
        from scipy.stats import t as _t
        q = float(_t.ppf(0.95, n - 1)) if n > 1 else float("nan")
    except Exception:
        q = 1.645
    return {"days": n, "per_day": round(mu, 1), "total": round(float(v.sum()), 0),
            "t": round(mu / se, 2) if math.isfinite(se) and se > 0 else None,
            "lcb": round(mu - q * se, 1) if math.isfinite(se) else None, "green": round(float((v > 0).mean()), 3)}


def _by_day(trades: list, keep=lambda t: True) -> dict:
    out: dict = {}
    for t in trades:
        if keep(t):
            out[t["date"]] = out.get(t["date"], 0.0) + t["net"]
    return out


def _variants() -> list:
    return [(f"w{w}_nm{n}_em{int(e)}", w, n, e) for w in WIN_STARTS for n in NOMOVE for e in EMERG]


# ------------------------------------------------------------------ the run
def run(data_dir, log=print, should_stop=lambda: False, syms=None, out_dir=None, capital: float = 500000.0) -> dict:
    """Read every Lab day, find the setup's signals (cached per symbol and day), trade them every way, grade by days.
    Writes <out_dir or data_dir/lab>/rider_lab.json and returns it."""
    from .data import UNDERLYINGS, day_table, iter_days, register_stocks
    t_start = time.time()
    data_dir = Path(data_dir)
    out_dir = Path(out_dir) if out_dir else data_dir / "lab"
    out_dir.mkdir(parents=True, exist_ok=True)
    c_ = R.cfg()
    base_exit = X.exit_cfg()
    cool, per_day = float(c_.get("cooldown_min", 15)), int(c_.get("max_signals_per_symbol_day", 2))
    sig = _sig(c_)
    cache_f = out_dir / "rider_signals.json"
    try:
        cache = json.loads(cache_f.read_text())
        if cache.get("sig") != sig:
            cache = {"sig": sig, "rows": {}}
    except Exception:
        cache = {"sig": sig, "rows": {}}
    register_stocks(data_dir)
    universe = list(syms or UNDERLYINGS)
    if "NIFTY" in universe:                       # NIFTY first: the others' strength vs Nifty needs its day
        universe.remove("NIFTY")
        universe.insert(0, "NIFTY")
    nifty: dict = {}
    vs = _variants()
    trades = {v[0]: [] for v in vs}
    trades["fixed"] = []
    reasons: dict = {}
    new_days = n_days = 0
    for sym in universe:
        if should_stop():
            break
        try:
            days = list(iter_days(data_dir, sym))
        except Exception as e:
            log(f"Trend Rider test: {sym} skipped ({e})")
            continue
        if not days:
            continue
        ctxs = _ctx_rows(day_table(data_dir, sym, days))
        for d in days:
            if should_stop():
                break
            if sym == "NIFTY":
                nifty[d.date] = np.asarray(d.spot, float)
            ctx = dict(ctxs.get(d.date) or {})
            lot = _lot(sym, d.date)
            key = f"{sym}|{d.date.isoformat()}"
            try:
                sigs = cache["rows"].get(key)
                if sigs is None:
                    sigs = _signals_day(d, sym, ctx, c_, lot, capital, nifty.get(d.date))
                    cache["rows"][key] = sigs
                    new_days += 1
                n_days += 1
                if not sigs:
                    continue
                fx_done: dict = {}
                sim_done: dict = {}
                for name, w, nm, em in vs:
                    for s in pick(sigs, w, cool, per_day):
                        sk = (s[0], nm, em)
                        if sk not in sim_done:
                            sim_done[sk] = simulate(d, sym, s, lot, {**base_exit, "nomove_bars": nm, "emergency_premium_pct": em})
                        r = sim_done[sk]
                        if r:
                            trades[name].append({"date": d.date.isoformat(), "sym": sym, "bar": s[0], "dir": s[1], **r})
                        if name == V1 and r:
                            reasons[r["why"]] = reasons.get(r["why"], 0) + 1
                        if w == WIN_STARTS[0] and nm == NOMOVE[0] and em == EMERG[0] and s[0] not in fx_done:
                            fx_done[s[0]] = simulate(d, sym, s, lot, fixed=FIXED)
                            if fx_done[s[0]]:
                                trades["fixed"].append({"date": d.date.isoformat(), "sym": sym, "bar": s[0], "dir": s[1],
                                                        **fx_done[s[0]]})
            except Exception as e:
                log(f"Trend Rider test: {sym} {d.date} skipped ({e})")
        log(f"Trend Rider test: {sym} done ({len(days)} days)")
    try:
        cache_f.write_text(json.dumps(cache))
    except Exception as e:
        log(f"Trend Rider test: signal cache not saved ({e})")
    res = grade(trades)
    res.update(ran_at=dt.datetime.now(IST).isoformat(timespec="seconds"), secs=round(time.time() - t_start, 1),
               syms=len(universe), sym_days=n_days, new_days=new_days, reasons_v1=reasons, sig=sig,
               rules={"windows": c_.get("windows"), "cooldown_min": cool, "per_day": per_day, "exit": base_exit},
               note=("Proxy: the Lab has no volume, bid/ask or order flow, so VWAP is time-weighted, flow and option "
                     "thrust are unknown (at most 4 of 6 families), delta 0.5, the Lab's slippage for the spread; 1 lot "
                     "of every signal, real charges. Reports only: nothing changes until Mandeep says so."))
    (out_dir / "rider_lab.json").write_text(json.dumps(res, default=str))
    v1 = (res.get("variants") or {}).get(V1) or {}
    fx = (res.get("variants") or {}).get("fixed") or {}
    log(f"Trend Rider test: {v1.get('trades', 0)} trades on {v1.get('days', 0)} days; as live ₹{v1.get('per_day')} a day "
        f"(lower bound {v1.get('lcb')}), bench exit ₹{fx.get('per_day')} a day; {res.get('verdict')}")
    return res


def grade(trades: dict) -> dict:
    """Every variant graded by days on all days, walk-forward by month and on the holdout (the last fifth of days)."""
    all_days = sorted({t["date"] for ts in trades.values() for t in ts})
    out: dict = {"variants": {}, "days_total": len(all_days)}
    if not all_days:
        out["verdict"] = "no Trend Rider signal in the Lab's days yet"
        return out
    cut = all_days[int(len(all_days) * 0.8)] if len(all_days) >= 10 else all_days[-1]
    out["holdout_from"] = cut
    for name, ts in trades.items():
        st = day_stats(_by_day(ts))
        n = len(ts)
        st.update(trades=n, won=round(sum(1 for t in ts if t["net"] > 0) / n, 3) if n else None,
                  avg_pct=round(float(np.mean([t["pct"] for t in ts])), 2) if n else None,
                  train=day_stats(_by_day(ts, lambda t: t["date"] < cut)),
                  holdout=day_stats(_by_day(ts, lambda t: t["date"] >= cut)))
        out["variants"][name] = st
    # choose on the training days only (lower bound, enough days), then look at the holdout
    cands = [(k, v["train"]) for k, v in out["variants"].items() if k != "fixed" and (v["train"]["days"] or 0) >= MIN_DAYS_CHOOSE
             and v["train"]["lcb"] is not None]
    best = max(cands, key=lambda kv: kv[1]["lcb"])[0] if cands else None
    out["best_on_train"] = best
    # walk-forward: each month by the variant best on the months before it (at least 3 months, 20 days)
    months = sorted({d[:7] for d in all_days})
    walk_days: dict = {}
    v1_days: dict = {}
    fx_days: dict = {}
    chosen = []
    for i, mo in enumerate(months):
        if i < 3:
            continue
        past = [(k, day_stats(_by_day(ts, lambda t, mo=mo: t["date"][:7] < mo))) for k, ts in trades.items() if k != "fixed"]
        past = [(k, s) for k, s in past if (s["days"] or 0) >= MIN_DAYS_CHOOSE and s["lcb"] is not None]
        if not past:
            continue
        k = max(past, key=lambda kv: kv[1]["lcb"])[0]
        chosen.append([mo, k])
        walk_days.update(_by_day(trades[k], lambda t, mo=mo: t["date"][:7] == mo))
        v1_days.update(_by_day(trades.get(V1, []), lambda t, mo=mo: t["date"][:7] == mo))
        fx_days.update(_by_day(trades.get("fixed", []), lambda t, mo=mo: t["date"][:7] == mo))
    out["walk"] = {"months": chosen, "walk": day_stats(walk_days), "as_live": day_stats(v1_days), "bench_exit": day_stats(fx_days)}
    # by symbol and by entry hour (as live)
    bys: dict = {}
    byh: dict = {}
    for t in trades.get(V1, []):
        s = bys.setdefault(t["sym"], [0, 0.0, 0])
        s[0] += 1
        s[1] += t["net"]
        s[2] += t["net"] > 0
        hh = f"{(9 * 60 + 15 + 5 * (t['bar'] + 1)) // 60:02d}"
        h = byh.setdefault(hh, [0, 0.0, 0])
        h[0] += 1
        h[1] += t["net"]
        h[2] += t["net"] > 0
    out["by_sym"] = sorted(([k, v[0], round(v[1]), round(v[2] / v[0], 2)] for k, v in bys.items()), key=lambda r: r[2])
    out["by_hour"] = [[k, v[0], round(v[1]), round(v[2] / v[0], 2)] for k, v in sorted(byh.items())]
    out["verdict"] = verdict(out)
    return out


def verdict(out: dict) -> str:
    v = out["variants"]
    v1, fx = v.get(V1) or {}, v.get("fixed") or {}
    b = out.get("best_on_train")
    h1 = (v1.get("holdout") or {})
    hb = ((v.get(b) or {}).get("holdout") or {}) if b else {}
    hf = (fx.get("holdout") or {})
    bits = []
    if (v1.get("lcb") or -1) > 0:
        bits.append("as live it makes money by days (lower bound above zero)")
    else:
        bits.append("as live it is not proven (lower bound not above zero)")
    if hf.get("per_day") is not None and h1.get("per_day") is not None:
        bits.append("its exit beats the bench exit on the holdout" if h1["per_day"] > hf["per_day"]
                    else "the bench exit does better on the holdout")
    if b and b != V1 and hb.get("per_day") is not None and h1.get("per_day") is not None:
        bits.append(f"the best tuned variant ({b}) {'also wins' if hb['per_day'] > h1['per_day'] else 'does not hold up'} on the holdout")
    s = "; ".join(bits)
    return s[:1].upper() + s[1:]
