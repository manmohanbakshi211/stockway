"""Per-underlying metrics computed from live state. Pure functions; no I/O."""
from __future__ import annotations

import datetime as dt
import math

from .clock import OPEN, SESSION_MIN, minutes_since_open
from .universe import atm_window, strike_step

BUILDUPS = {
    ("up", "up"): ("long_buildup", "Long buildup", 1),
    ("down", "up"): ("short_buildup", "Short buildup", -1),
    ("up", "down"): ("short_covering", "Short covering", 1),
    ("down", "down"): ("long_unwinding", "Long unwinding", -1),
}


def norm_iv(v):
    if v is None or v <= 0 or v != v:
        return None
    return v * 100 if v < 3 else v


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def vol_profile(minutes: float) -> float:
    """Share of a normal day's volume done after `minutes` (front-loaded)."""
    m = max(5.0, min(SESSION_MIN, minutes))
    return (m / SESSION_MIN) ** 0.8


def max_pain(chain: dict) -> float | None:
    """chain: strike -> {"CE": oi, "PE": oi}. Settlement price with the least total payout to buyers."""
    strikes = sorted(chain)
    if len(strikes) < 3:
        return None
    best, best_pain = None, None
    for p in strikes:
        pain = 0.0
        for k in strikes:
            ce = chain[k].get("CE") or 0
            pe = chain[k].get("PE") or 0
            if p > k:
                pain += ce * (p - k)
            elif p < k:
                pain += pe * (k - p)
        if best_pain is None or pain < best_pain:
            best, best_pain = p, pain
    return best


def opening_range(bars, day: dt.date, tz) -> tuple:
    start = dt.datetime.combine(day, OPEN, tz).timestamp()
    end = start + 15 * 60
    rng = [b for b in bars.bars if start <= b[0] < end] if bars else []
    if not rng:
        return None, None
    return max(b[2] for b in rng), min(b[3] for b in rng)


def _opt_view(o, inst, lot):
    if inst is None or inst.ltp is None:
        return None
    base = inst.prev_oi or inst.base_oi
    return {"key": o.key, "strike": o.strike, "kind": o.kind, "ltp": inst.ltp, "cp": inst.cp,
            "chg_pct": pct(inst.ltp, inst.cp), "oi": inst.oi, "oi_chg": (inst.oi - base) if inst.oi and base else None,
            "oi_chg_pct": pct(inst.oi, base), "vol": inst.vol, "iv": norm_iv(inst.iv), "delta": inst.delta,
            "gamma": inst.gamma, "theta": inst.theta, "vega": inst.vega, "bid": inst.bid, "ask": inst.ask,
            "lot": lot}


def compute(u, state, now: dt.datetime, window: list, full_chain: dict | None, avg_vol: float | None,
            open_marks: dict, vix: float | None = None, daily: dict | None = None) -> dict | None:
    """Metrics for one underlying. `window`: subscribed strikes of the near expiry.
    `open_marks`: mutable per-symbol dict for values first seen after the open (pcr_open, iv_open)."""
    und = state.get(u.key)
    fut = state.get(u.fut_key) if u.fut_key else None
    spot = und.ltp if und and und.ltp else (fut.ltp if fut else None)
    if not spot:
        return None
    mins = minutes_since_open(now)
    m = {"sym": u.symbol, "name": u.name, "is_index": u.is_index, "spot": spot, "lot": u.lot,
         "cp": und.cp if und else None, "t": und.t if und else None}
    m["chg_pct"] = pct(spot, m["cp"])
    m["day_o"], m["day_h"], m["day_l"] = (und.day_o, und.day_h, und.day_l) if und else (None, None, None)
    vwap = (und.atp if und and und.atp else None) or (fut.atp if fut and fut.atp else None)
    if vwap and fut and not (und and und.atp) and fut.ltp:
        vwap = vwap * spot / fut.ltp      # index: futures VWAP, shifted by the basis
    m["vwap"] = vwap
    m["vs_vwap_pct"] = pct(spot, vwap)
    vol = und.vol if und and und.vol else None
    m["vol"] = vol
    m["rel_vol"] = (vol / (avg_vol * vol_profile(mins))) if vol and avg_vol else None

    # momentum from 1-minute bars
    bars = state.bars.get(u.symbol)
    closes = bars.closes(61) if bars else []
    def ret(n):
        return pct(closes[-1], closes[-1 - n]) if len(closes) > n else None
    m["ret_1m"], m["ret_5m"], m["ret_15m"] = ret(1), ret(5), ret(15)
    or_h, or_l = opening_range(bars, now.date(), now.tzinfo) if mins >= 15 else (None, None)
    m["or_high"], m["or_low"] = or_h, or_l

    # futures and OI buildup
    if fut and fut.ltp:
        base = fut.prev_oi or fut.base_oi
        f = {"ltp": fut.ltp, "chg_pct": pct(fut.ltp, fut.cp), "oi": fut.oi, "oi_chg_pct": pct(fut.oi, base),
             "basis_pct": pct(fut.ltp, spot), "oi_since": "prev day" if fut.prev_oi else "app start"}
        p, o = f["chg_pct"], f["oi_chg_pct"]
        f["buildup"] = f["buildup_label"] = None
        f["buildup_sign"] = 0
        if p is not None and o is not None and abs(p) >= 0.2 and abs(o) >= 1.0:
            code, label, sign = BUILDUPS[("up" if p > 0 else "down", "up" if o > 0 else "down")]
            f.update(buildup=code, buildup_label=label, buildup_sign=sign)
        m["fut"] = f
    else:
        m["fut"] = None

    # options on the near expiry
    exp = u.near_expiry(now.date())
    opt = {"expiry": exp.isoformat() if exp else None}
    sigma_iv = None
    if exp:
        chain = u.chains.get(exp, {})
        strikes = sorted(chain)
        step = strike_step(strikes, spot)
        atm = min(strikes, key=lambda k: abs(k - spot)) if strikes else None
        rows = []
        for k in window:
            pair = chain.get(k, {})
            ce = _opt_view(pair["CE"], state.get(pair["CE"].key), pair["CE"].lot) if "CE" in pair else None
            pe = _opt_view(pair["PE"], state.get(pair["PE"].key), pair["PE"].lot) if "PE" in pair else None
            if ce or pe:
                rows.append({"strike": k, "CE": ce, "PE": pe})
        by = {r["strike"]: r for r in rows}
        a = by.get(atm, {})
        ce_atm, pe_atm = a.get("CE"), a.get("PE")
        ivs = [x["iv"] for x in (ce_atm, pe_atm) if x and x.get("iv")]
        atm_iv = sum(ivs) / len(ivs) if ivs else None
        straddle = (ce_atm["ltp"] + pe_atm["ltp"]) if ce_atm and pe_atm else None
        opt.update(step=step, atm=atm, atm_iv=atm_iv, straddle=straddle,
                   exp_move_pct=(straddle / spot * 100) if straddle else None,
                   daily_move_pct=(atm_iv / math.sqrt(252)) if atm_iv else None,
                   days_to_expiry=(exp - now.date()).days)
        # IV change vs ~15 min ago (average of ATM CE/PE)
        prev = []
        for x in (ce_atm, pe_atm):
            if x:
                i = state.get(x["key"])
                h = i.ago(15) if i else None
                if h and h[4]:
                    prev.append(norm_iv(h[4]))
        prev = [p for p in prev if p]
        opt["iv_chg_15m"] = (atm_iv - sum(prev) / len(prev)) if atm_iv and prev else None
        # OI picture: prefer the full REST chain (all strikes), else the subscribed window
        if full_chain and full_chain.get("expiry") == opt["expiry"]:
            oi_map = full_chain["oi"]
            opt["pcr"] = full_chain.get("pcr")
            opt["max_pain"] = max_pain(oi_map)
            opt["oi_scope"] = "full chain"
        else:
            oi_map = {r["strike"]: {"CE": (r["CE"] or {}).get("oi") or 0, "PE": (r["PE"] or {}).get("oi") or 0}
                      for r in rows}
            ce_t = sum(v["CE"] for v in oi_map.values())
            pe_t = sum(v["PE"] for v in oi_map.values())
            opt["pcr"] = (pe_t / ce_t) if ce_t else None
            opt["max_pain"] = max_pain(oi_map) if len(oi_map) >= 7 else None
            opt["oi_scope"] = f"{len(rows)} strikes near the money"
        above = {k: v for k, v in oi_map.items() if k >= spot}
        below = {k: v for k, v in oi_map.items() if k <= spot}
        opt["resistance"], opt["resistance_lead"] = _wall(above, "CE")
        opt["support"], opt["support_lead"] = _wall(below, "PE")
        adds = [(r["strike"], s, (r[s] or {}).get("oi_chg") or 0) for r in rows for s in ("CE", "PE") if r[s]]
        ce_add = max((x for x in adds if x[1] == "CE"), key=lambda x: x[2], default=None)
        pe_add = max((x for x in adds if x[1] == "PE"), key=lambda x: x[2], default=None)
        opt["ce_writing"] = ce_add[0] if ce_add and ce_add[2] > 0 else None
        opt["pe_writing"] = pe_add[0] if pe_add and pe_add[2] > 0 else None
        if opt["pcr"] is not None and mins >= 1:
            open_marks.setdefault("pcr_open", opt["pcr"])
        if atm_iv:
            open_marks.setdefault("iv_open", atm_iv)
        opt["pcr_open"] = open_marks.get("pcr_open")
        opt["iv_open"] = open_marks.get("iv_open")
        opt["rows"] = rows
        opt["unusual"] = unusual_activity(rows, state, mins)
        sigma_iv = atm_iv
    m["opt"] = opt

    # z-scores of recent moves vs expected per-minute volatility
    sig = None
    if sigma_iv:
        sig = sigma_iv / 100 / math.sqrt(252 * SESSION_MIN) * 100
    elif len(closes) > 15:
        rs = [pct(b, a) for a, b in zip(closes[:-1], closes[1:])]
        mu = sum(rs) / len(rs)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rs) / max(1, len(rs) - 1))
        sig = sd or None
    m["sigma_1m"] = sig
    m["z_1m"] = (m["ret_1m"] / sig) if sig and m["ret_1m"] is not None else None
    m["z_5m"] = (m["ret_5m"] / (sig * math.sqrt(5))) if sig and m["ret_5m"] is not None else None
    if m["day_o"] and opt.get("daily_move_pct"):
        m["move_vs_priced"] = abs(pct(spot, m["day_o"])) / opt["daily_move_pct"]
    else:
        m["move_vs_priced"] = None
    m["flow"] = order_flow(und if und and und.vol else fut, now, mins)
    m["vol"] = vol_context(m, daily or {})
    m["score"], m["reasons"] = bias_score(m)
    m["heat"] = heat(m)
    return m


def _wall(side_map: dict, side: str):
    """Strike with the most OI on one side, and how far it leads the runner-up (1.25 = 25% bigger)."""
    xs = sorted(((v[side] or 0, k) for k, v in side_map.items()), reverse=True)
    if not xs or not xs[0][0]:
        return None, None
    lead = (xs[0][0] / xs[1][0]) if len(xs) > 1 and xs[1][0] else None
    return xs[0][1], (round(lead, 3) if lead else None)


def order_flow(inst, now, mins) -> dict | None:
    """Buyer- vs seller-initiated volume over the last 5 minutes, and order-book pressure."""
    if inst is None or not inst.flow:
        return None
    minute = int(now.timestamp() // 60) * 60
    b5, s5, n5 = inst.flow_window(5, minute)
    v5 = b5 + s5
    out = {"delta_5m": b5 - s5, "delta_pct_5m": round((b5 - s5) / v5 * 100, 1) if v5 and n5 >= 3 else None,
           "vol_5m": v5, "trades_5m": n5, "cvd": inst.cvd}
    if inst.vol and mins > 6:
        pace = (inst.vol - v5) / max(1.0, (mins - 5) / 5)
        out["rel_5m"] = round(v5 / pace, 2) if pace > 0 else None
    if inst.tbq and inst.tsq:
        out["book_imb"] = round((inst.tbq - inst.tsq) / (inst.tbq + inst.tsq) * 100, 1)
    return out


def vol_context(m, daily) -> dict:
    o = m.get("opt") or {}
    iv = o.get("atm_iv")
    hv20 = daily.get("hv20")
    out = {"hv20": hv20, "hv_pct": daily.get("hv_pct"), "iv": iv,
           "iv_hv": round(iv / hv20, 2) if iv and hv20 else None, "skew": None, "iv_pct": None}
    hist = daily.get("iv_hist") or []
    if iv and len(hist) >= 15:
        out["iv_pct"] = round(sum(1 for h in hist if h <= iv) / len(hist) * 100)
    try:
        from .vol import skew
        out["skew"] = skew(o.get("rows") or [])
    except Exception:
        pass
    return out


def unusual_activity(rows, state, mins) -> list:
    """Options whose last-5-minute volume is far above their own pace today."""
    out = []
    if mins < 20:
        return out
    for r in rows:
        for side in ("CE", "PE"):
            x = r[side]
            if not x or not x.get("vol"):
                continue
            i = state.get(x["key"])
            h = i.ago(5) if i else None
            if not h or h[2] is None or h[1] in (None, 0):
                continue
            v5 = x["vol"] - h[2]
            pace = h[2] / max(1.0, (mins - 5) / 5)
            lots = v5 / max(1, x["lot"])
            prem = pct(x["ltp"], h[1])
            oi5 = pct(x["oi"], h[3]) if h[3] else None
            if lots >= 20 and pace > 0 and v5 / pace >= 5 and ((prem is not None and abs(prem) >= 20) or (oi5 is not None and oi5 >= 10)):
                if prem is not None and prem > 0:
                    kind, direction = "buying", ("up" if side == "CE" else "down")
                else:
                    kind, direction = "writing", ("down" if side == "CE" else "up")
                out.append({"strike": r["strike"], "side": side, "key": x["key"], "lots_5m": round(lots),
                            "value_5m": round(v5 * x["ltp"]),
                            "x_pace": round(v5 / pace, 1), "prem_chg_5m": prem, "oi_chg_5m": oi5,
                            "ltp": x["ltp"], "kind": kind, "direction": direction})
    return sorted(out, key=lambda d: -d["x_pace"])[:5]


def bias_score(m) -> tuple:
    pts, why = 0, []
    def add(cond, p, text):
        nonlocal pts
        if cond:
            pts += p
            why.append(text)
    c = m.get("chg_pct")
    add(c is not None and c >= 0.5, 1, "up on the day")
    add(c is not None and c <= -0.5, -1, "down on the day")
    v = m.get("vs_vwap_pct")
    add(v is not None and v >= 0.15, 1, "above VWAP")
    add(v is not None and v <= -0.15, -1, "below VWAP")
    f = m.get("fut") or {}
    s = f.get("buildup_sign") or 0
    add(s > 0, 2, (f.get("buildup_label") or "").lower())
    add(s < 0, -2, (f.get("buildup_label") or "").lower())
    r = m.get("ret_15m")
    add(r is not None and r >= 0.3, 1, "rising last 15 min")
    add(r is not None and r <= -0.3, -1, "falling last 15 min")
    if m.get("or_high") and m.get("spot"):
        add(m["spot"] > m["or_high"], 1, "above opening range")
        add(m["spot"] < m["or_low"], -1, "below opening range")
    o = m.get("opt") or {}
    if m.get("is_index") and o.get("pcr") is not None:
        add(o["pcr"] >= 1.2, 1, "put writers in control (PCR high)")
        add(o["pcr"] <= 0.7, -1, "call writers in control (PCR low)")
    return round(max(-7, min(7, pts)) / 7 * 100), why


def heat(m) -> float:
    """How unusual the activity is right now (0-100), for ranking the scanner."""
    h = 0.0
    if m.get("rel_vol"):
        h += min(40, max(0, (m["rel_vol"] - 1) * 15))
    if m.get("z_5m") is not None:
        h += min(30, abs(m["z_5m"]) * 6)
    f = m.get("fut") or {}
    if f.get("oi_chg_pct") is not None:
        h += min(15, abs(f["oi_chg_pct"]) * 1.5)
    if (m.get("opt") or {}).get("unusual"):
        h += 15
    return round(min(100, h))
