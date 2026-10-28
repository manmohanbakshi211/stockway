"""Alert detectors. Each compares the latest metrics with the previous ones and returns alerts.

Severity: 1 = worth a look, 2 = important, 3 = act-now level."""
from __future__ import annotations

DEFAULTS = {
    "sharp_move_z": 4.0,          # 5-min move vs expected (IV-based) sigma
    "sharp_move_z_top": 6.5,      # act-now level
    "sharp_move_min_pct": 0.6,    # and at least this % in 5 min (indices: a third of it)
    "unusual_min_value": 5e5,     # option premium traded in 5 min, rupees (important)
    "unusual_top_value": 5e6,     # act-now level
    "wall_lead": 1.1,             # new OI wall must be 10% bigger than the next strike
    "volume_surge_x": 3.0,        # relative volume vs normal pace
    "volume_surge_move_pct": 1.5,
    "oi_buildup_oi_pct": 5.0,     # futures OI change since previous day
    "oi_buildup_px_pct": 1.0,
    "pcr_shift": 0.25,            # change since open (indices)
    "iv_spike_pts": 2.0,          # ATM IV up this many vol points in 15 min
    "vix_jump_pct": 4.0,
    "after_open_min": 15,         # ignore breakouts in the first minutes
    "orb_margin_pct": 0.15,       # breakout must clear the opening range by this much (indices: a third of it)
    "orb_min_rel_vol": 1.2,       # stocks: volume pace needed for a breakout alert
}

# how long the same alert (symbol + kind + direction) stays quiet, in minutes
COOLDOWN = {"orb": 999, "day_high": 30, "day_low": 30, "vwap_cross": 30, "oi_wall": 20, "pcr_shift": 60,
            "iv_spike": 30, "beyond_priced": 999, "oi_buildup": 60, "news": 60, "unusual_options": 10}
# for these, the cooldown ignores direction (they tend to flip back and forth)
NO_DIRECTION_COOLDOWN = {"oi_wall", "vwap_cross"}


def _a(m, kind, direction, sev, title, detail, /, **data):
    return {"sym": m["sym"], "kind": kind, "direction": direction, "severity": sev, "title": title,
            "detail": detail, "price": m.get("spot"), "data": data}


def _fmt(x, d=2):
    return "–" if x is None else f"{x:,.{d}f}"


def run(m: dict, prev: dict | None, cfg: dict, mins: float) -> list:
    c = {**DEFAULTS, **(cfg or {})}
    out = []
    if not m or not prev:
        return out
    spot, pspot = m.get("spot"), prev.get("spot")
    idx = m.get("is_index")
    name = m["sym"]

    # 1. sharp move
    z, r5 = m.get("z_5m"), m.get("ret_5m")
    min_pct = c["sharp_move_min_pct"] * (1 / 3 if idx else 1)
    if z is not None and r5 is not None and abs(z) >= c["sharp_move_z"] and abs(r5) >= min_pct:
        sev = 3 if abs(z) >= c["sharp_move_z_top"] else 2
        d = "up" if r5 > 0 else "down"
        out.append(_a(m, "sharp_move", d, sev, f"{name} {'spiking' if d == 'up' else 'dropping'} {r5:+.2f}% in 5 min",
                      f"{abs(z):.1f}× the move options priced for 5 minutes. Now {_fmt(spot)}.", z=z, ret_5m=r5))

    if mins >= c["after_open_min"] and spot and pspot:
        # 2. opening-range breakout
        orh, orl = m.get("or_high"), m.get("or_low")
        mg = c["orb_margin_pct"] / 100 * (1 / 3 if idx else 1)
        rv_ok = idx or (m.get("rel_vol") or 0) >= c["orb_min_rel_vol"]
        if orh and rv_ok and pspot <= orh * (1 + mg) < spot:
            out.append(_a(m, "orb", "up", 2 if (m.get("rel_vol") or 0) >= 1.5 or idx else 1,
                          f"{name} broke above opening range {_fmt(orh)}",
                          f"First-15-minute high taken out. Volume pace {_fmt(m.get('rel_vol'), 1)}× normal.", level=orh))
        if orl and rv_ok and pspot >= orl * (1 - mg) > spot:
            out.append(_a(m, "orb", "down", 2 if (m.get("rel_vol") or 0) >= 1.5 or idx else 1,
                          f"{name} broke below opening range {_fmt(orl)}",
                          f"First-15-minute low taken out. Volume pace {_fmt(m.get('rel_vol'), 1)}× normal.", level=orl))
        # 3. new day high / low (after the opening range)
        ph, pl = prev.get("day_h"), prev.get("day_l")
        hl_ok = idx or (m.get("rel_vol") or 0) >= 1.5
        if ph and hl_ok and spot > ph and mins >= 30:
            out.append(_a(m, "day_high", "up", 2 if idx else 1, f"{name} at a new day high {_fmt(spot)}",
                          f"Up {_fmt(m.get('chg_pct'))}% on the day.", level=ph))
        if pl and hl_ok and spot < pl and mins >= 30:
            out.append(_a(m, "day_low", "down", 2 if idx else 1, f"{name} at a new day low {_fmt(spot)}",
                          f"{_fmt(m.get('chg_pct'))}% on the day.", level=pl))
        # 4. VWAP cross (indices, or stocks with real volume)
        vw, pvw = m.get("vwap"), prev.get("vwap")
        if vw and pvw and (idx or (m.get("rel_vol") or 0) >= 2):
            if pspot <= pvw and spot > vw:
                out.append(_a(m, "vwap_cross", "up", 1, f"{name} crossed above VWAP {_fmt(vw)}",
                              "Buyers took back the average price of the day.", level=vw))
            if pspot >= pvw and spot < vw:
                out.append(_a(m, "vwap_cross", "down", 1, f"{name} slipped below VWAP {_fmt(vw)}",
                              "Sellers pushed below the average price of the day.", level=vw))

    # 5. volume surge with a real move
    rv, ch = m.get("rel_vol"), m.get("chg_pct")
    if rv and ch is not None and rv >= c["volume_surge_x"] and abs(ch) >= c["volume_surge_move_pct"] and mins >= 10:
        d = "up" if ch > 0 else "down"
        out.append(_a(m, "volume_surge", d, 3 if rv >= c["volume_surge_x"] * 2 else 2,
                      f"{name} on {rv:.1f}× normal volume, {ch:+.2f}%", "Heavy participation behind the move.",
                      rel_vol=rv, chg_pct=ch))

    # 6. futures OI buildup (fires when it first becomes strong)
    f, pf = m.get("fut") or {}, prev.get("fut") or {}
    o, p = f.get("oi_chg_pct"), f.get("chg_pct")
    strong = o is not None and p is not None and abs(o) >= c["oi_buildup_oi_pct"] and abs(p) >= c["oi_buildup_px_pct"]
    pstrong = (pf.get("oi_chg_pct") is not None and pf.get("chg_pct") is not None
               and abs(pf["oi_chg_pct"]) >= c["oi_buildup_oi_pct"] and abs(pf["chg_pct"]) >= c["oi_buildup_px_pct"]
               and pf.get("buildup") == f.get("buildup"))
    if strong and not pstrong and f.get("buildup"):
        d = "up" if f["buildup_sign"] > 0 else "down"
        out.append(_a(m, "oi_buildup", d, 2, f"{name}: {f['buildup_label']} in futures",
                      f"Price {p:+.2f}%, open interest {o:+.1f}% since {f.get('oi_since')}.", oi_chg=o, px_chg=p,
                      buildup=f["buildup"]))

    opt, popt = m.get("opt") or {}, prev.get("opt") or {}
    # 7. unusual option activity (new strikes only)
    seen = {(u["strike"], u["side"]) for u in popt.get("unusual") or []}
    for u in opt.get("unusual") or []:
        if (u["strike"], u["side"]) in seen:
            continue
        val = u.get("value_5m") or 0
        if val < c["unusual_min_value"]:
            continue
        sev = 3 if val >= c["unusual_top_value"] and u["x_pace"] >= 8 else 2
        what = "buying" if u["kind"] == "buying" else "writing"
        out.append(_a(m, "unusual_options", u["direction"], sev,
                      f"Heavy {what} in {name} {u['strike']:g} {u['side']}",
                      f"{u['lots_5m']:,} lots (₹{val / 1e5:,.1f} lakh) in 5 min, {u['x_pace']}× its pace. Premium {_fmt(u.get('prem_chg_5m'), 1)}%, "
                      f"OI {_fmt(u.get('oi_chg_5m'), 1)}%.", **u))
    # 8. OI wall shifted
    for side, label, d in (("resistance", "Resistance (biggest call OI)", "down"), ("support", "Support (biggest put OI)", "up")):
        a, b = opt.get(side), popt.get(side)
        lead = opt.get(side + "_lead") or 0
        if idx and a and b and a != b and mins >= 20 and lead >= c["wall_lead"]:
            moved = "up" if a > b else "down"
            out.append(_a(m, "oi_wall", moved, 2, f"{name} {label.split(' (')[0].lower()} moved {moved} to {a:g}",
                          f"{label} shifted from {b:g} to {a:g}.", old=b, new=a, side=side, lead=lead))
    # 9. PCR shift (indices)
    if idx and opt.get("pcr") is not None and opt.get("pcr_open") is not None and popt.get("pcr") is not None:
        dlt, pdlt = opt["pcr"] - opt["pcr_open"], popt["pcr"] - opt["pcr_open"]
        if abs(dlt) >= c["pcr_shift"] and abs(pdlt) < c["pcr_shift"]:
            d = "up" if dlt > 0 else "down"
            out.append(_a(m, "pcr_shift", d, 2, f"{name} PCR {'up' if dlt > 0 else 'down'} to {opt['pcr']:.2f}",
                          f"From {opt['pcr_open']:.2f} at the open: {'put writers adding (supportive)' if dlt > 0 else 'call writers adding (capping)'}.",
                          pcr=opt["pcr"], pcr_open=opt["pcr_open"]))
    # 10. IV spike
    iv15, piv15 = opt.get("iv_chg_15m"), popt.get("iv_chg_15m")
    if iv15 is not None and iv15 >= c["iv_spike_pts"] and (piv15 is None or piv15 < c["iv_spike_pts"]):
        out.append(_a(m, "iv_spike", "neutral", 2, f"{name} option IV jumped +{iv15:.1f} pts in 15 min",
                      f"ATM IV now {_fmt(opt.get('atm_iv'), 1)}%. Options got expensive: a move is expected, or fear is rising.",
                      iv=opt.get("atm_iv"), iv_chg=iv15))
    # 11. day's move beyond what options priced
    mv, pmv = m.get("move_vs_priced"), prev.get("move_vs_priced")
    if mv is not None and mv >= 1.0 and (pmv is None or pmv < 1.0) and mins >= 20:
        d = "up" if (m.get("spot") or 0) > (m.get("day_o") or 0) else "down"
        out.append(_a(m, "beyond_priced", d, 3 if idx else 2, f"{name} moved more than options priced for today",
                      f"Move from open {_fmt(abs((spot / m['day_o'] - 1) * 100))}% vs one-day priced move {_fmt(opt.get('daily_move_pct'))}%.",
                      ratio=mv))
    return out


def vix_alerts(vix, pvix, cp, cfg) -> list:
    c = {**DEFAULTS, **(cfg or {})}
    out = []
    if not (vix and pvix and cp):
        return out
    ch, pch = (vix / cp - 1) * 100, (pvix / cp - 1) * 100
    for lvl in (c["vix_jump_pct"], c["vix_jump_pct"] * 2):
        if ch >= lvl > pch:
            out.append({"sym": "INDIAVIX", "kind": "vix_jump", "direction": "neutral", "severity": 3 if lvl > c["vix_jump_pct"] else 2,
                        "title": f"India VIX up {ch:+.1f}% to {vix:.2f}", "detail": "Fear rising across the market: option premiums are getting expensive.",
                        "price": vix, "data": {"chg_pct": ch}})
        if ch <= -lvl < pch:
            out.append({"sym": "INDIAVIX", "kind": "vix_drop", "direction": "neutral", "severity": 1,
                        "title": f"India VIX down {ch:.1f}% to {vix:.2f}", "detail": "Volatility cooling: option premiums are getting cheaper.",
                        "price": vix, "data": {"chg_pct": ch}})
    return out
