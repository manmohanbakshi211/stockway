"""Credibility check of the alerts, the score / conviction model and every parameter the app reads (gun, 2.50).

Mandeep, 10 Nov 2026 (#11): "check the credibility of the conviction model and the alerts with all the parameters";
(#6) candle patterns are chart reading, not strategies of their own: does a candle that agrees make other signals better?

Two honest tests, both on results that already happened (nothing re-run, nothing tuned here):
  1. Alerts: every graded live alert (worked = its option rose 20% before falling 20% within an hour). Does a higher
     score / conviction / model chance, Focus, strength, PASS from the risk check really mean "worked" more often?
     For the model: how well its chance sorts winners from losers (AUC: 0.5 = no better than a coin, 0.6+ = useful)
     and whether "70%" really comes true about 70% of the time (calibration).
  2. Parameters: every test-bench signal records ~50 facts at entry (Nifty with / against, VWAP, bigger timeframes,
     candles on 15m / 30m / day / week, news, VIX, IV, time of day ...). Its "Buy ATM" trade (1 lot, every charge) is
     split by each fact: with it vs against it (or low / middle / high). A fact is only called useful when the gap
     has the same sign in the older and the newer half of the days (one lucky week can't make it look good).

Read-only. Information for deciding what to trust; it changes nothing by itself.
"""
from __future__ import annotations

import datetime as dt
import json
import math

import numpy as np
import pandas as pd

from .clock import IST

BUY_STYLE = "buy"                        # one comparable trade per signal: 1 lot of the ATM option, bought
MIN_N = 30                               # fewer trades than this in a group: "not enough data"
MIN_DAYS = 10                            # fewer market days than this: a fact can only "lean", never "help" or "hurt"
NUMERIC = {"vix": "India VIX", "iv_pct": "Option IV rank", "iv_hv": "IV vs realised volatility", "rel_vol": "Relative volume",
           "mins": "Minutes since the open", "dte": "Days to expiry", "z5": "5-min move size (z)", "pcr": "PCR",
           "atr_pct": "Daily ATR %", "htf_rsi": "Daily RSI", "htf_pos20": "Place in the 20-day range", "news_n": "News count",
           "cost_lot": "Lot cost ₹", "unusual": "Unusual option activity", "iv_chg15": "IV change 15 min",
           "vix_chg": "VIX change", "htf_room_atr": "Room to the next daily level (ATR)"}
NAMES = {"nifty_al": "Nifty moving the same way", "nifty15_al": "Nifty's last 15 min the same way",
         "nifty_vwap_al": "Nifty on the same side of its VWAP", "vwap_al": "Stock on the right side of VWAP",
         "sector_al": "Sector the same way", "breadth_al": "Market breadth the same way", "global_al": "World markets the same way",
         "news_al": "News the same way", "buildup_al": "Futures OI buildup the same way", "chg_al": "Day move the same way",
         "ret5_al": "Last 5 min the same way", "ret15_al": "Last 15 min the same way", "htf_d_al": "Daily trend the same way",
         "htf_w_al": "Weekly trend the same way", "htf_ema20_al": "Above / below the daily EMA 20 the right way",
         "htf_st_al": "Daily Supertrend the same way", "depth_al": "Order book leaning the same way",
         "depth_all_al": "Whole order book leaning the same way", "mover_al": "Among today's movers the same way",
         "cdl_5m_al": "Candle pattern on 5 min agrees", "cdl_15m_al": "Candle pattern on 15 min agrees",
         "cdl_30m_al": "Candle pattern on 30 min agrees", "cdl_1h_al": "Candle pattern on 1 hour agrees",
         "cdl_75m_al": "Candle pattern on 75 min agrees", "cdl_4h_al": "Candle pattern on 4 hours agrees", "cdl_1d_al": "Candle pattern on the day chart agrees",
         "cdl_1w_al": "Candle pattern on the week chart agrees", "cdl_al_sum": "Candle patterns overall (all timeframes)",
         "conf_al": "Other named strategies agree", "conf_against": "Other named strategies against",
         "agree": "Evidence points agreeing", "against": "Evidence points against", "score_bias": "Score bias",
         "in_play": "Stock in play today", "is_index": "Index (not a stock)"}
CANDLE = {k for k in NAMES if k.startswith("cdl_")}


def auc(score: np.ndarray, y: np.ndarray) -> float | None:
    """Area under the ROC curve via ranks (ties averaged): chance that a random winner scored above a random loser."""
    ok = ~(np.isnan(score) | np.isnan(y))
    s, t = score[ok], y[ok].astype(int)
    n1, n0 = int(t.sum()), int(len(t) - t.sum())
    if n1 < 5 or n0 < 5:
        return None
    r = pd.Series(s).rank(method="average").to_numpy()
    return round(float((r[t == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 3)


def _grp(df: pd.DataFrame, by, name: str, order=None) -> list:
    out = []
    for k, g in df.groupby(by, dropna=True, observed=True):
        k = k[0] if isinstance(k, tuple) else k
        dec = g["worked"].notna().sum()
        hr = float(g["worked"].mean() * 100) if dec else None
        out.append({"group": name, "value": str(k), "alerts": int(len(g)), "decided": int(dec),
                    "worked_pct": round(hr, 1) if hr is not None else None,
                    "o60": round(float(g["o60"].mean()), 2) if g["o60"].notna().any() else None,
                    "enough": bool(dec >= MIN_N)})
    if order:
        out.sort(key=lambda x: order.index(x["value"]) if x["value"] in order else 99)
    return out


def _trend(rows: list) -> str:
    """Do the groups (in their natural order, low -> high) rise in 'worked %'? Only groups with enough data count."""
    v = [r["worked_pct"] for r in rows if r["enough"] and r["worked_pct"] is not None]
    if len(v) < 3:
        return "not enough data"
    rho = pd.Series(v).corr(pd.Series(range(len(v))), method="spearman")
    return "higher is better (credible)" if rho >= 0.6 else "higher is worse (not credible)" if rho <= -0.6 else "no clear order (not credible yet)"


def alerts_check(store, source: str, days: int = 60) -> dict:
    """Test 1: graded live alerts by what the app said about them."""
    cut = (dt.datetime.now(IST).date() - dt.timedelta(days=days)).isoformat()
    with store.lock:
        try:
            rows = store.db.execute("SELECT o.alert_id, o.day, o.ts, o.kind, o.direction, o.severity, o.is_index, o.result, "
                                    "o.o60, a.data FROM outcomes o JOIN alerts a ON a.id = o.alert_id "
                                    "WHERE o.source=? AND o.day >= ? AND o.result IS NOT NULL", (source, cut)).fetchall()
        except Exception:
            rows = []
    recs = []
    for r in rows:
        try:
            d = json.loads(r[9] or "{}")
        except Exception:
            d = {}
        res = r[7]
        recs.append({"day": r[1], "ts": r[2], "kind": r[3], "direction": r[4], "severity": r[5], "is_index": r[6],
                     "worked": 1.0 if res == "hit" else 0.0 if res == "miss" else np.nan, "o60": r[8],
                     "score": d.get("score"), "model_p": d.get("model_p"), "conviction": d.get("conviction"),
                     "focus": bool(d.get("focus")), "muted": bool(d.get("muted")), "regime": d.get("regime"),
                     "risk": ((d.get("risk") or {}).get("verdict")) or "–"})
    df = pd.DataFrame(recs)
    if df.empty:
        return {"n": 0, "groups": [], "verdicts": [], "note": "No graded alerts yet."}
    df["o60"] = pd.to_numeric(df["o60"], errors="coerce")
    tm = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    hm = tm.dt.hour * 60 + tm.dt.minute
    df["time"] = pd.cut(hm, [0, 600, 720, 840, 1440], labels=["09:15-10:00", "10:00-12:00", "12:00-14:00", "14:00-15:30"])
    df["score_band"] = pd.cut(pd.to_numeric(df["score"], errors="coerce"), [0, 40, 55, 70, 85, 100],
                              labels=["under 40", "40-55", "55-70", "70-85", "85+"])
    groups, verdicts = [], []
    sc = _grp(df, ["score_band"], "Signal score", ["under 40", "40-55", "55-70", "70-85", "85+"])
    groups += sc
    verdicts.append({"what": "Signal score", "verdict": _trend(sc)})
    mp = pd.to_numeric(df["model_p"], errors="coerce")
    model = {"n": int(mp.notna().sum())}
    if mp.notna().sum() >= 50:
        y = df["worked"].to_numpy(dtype=float)
        model["auc"] = auc(mp.to_numpy(dtype=float), y)
        df["model_band"] = pd.cut(mp, [0, 40, 50, 60, 70, 101], labels=["under 40%", "40-50%", "50-60%", "60-70%", "70%+"])
        cal = _grp(df, ["model_band"], "Model chance (conviction model)", ["under 40%", "40-50%", "50-60%", "60-70%", "70%+"])
        groups += cal
        a = model.get("auc")
        verdicts.append({"what": "Conviction model", "verdict": "not enough graded alerts" if a is None else
                         f"AUC {a}: " + ("useful (sorts winners from losers)" if a >= 0.6 else "weak" if a >= 0.55 else
                                         "no better than a coin (keep it hidden)")})
    else:
        verdicts.append({"what": "Conviction model", "verdict": f"only {model['n']} graded alerts carry the model's chance: not enough to judge"})
    fo = _grp(df, ["focus"], "Focus alert", ["False", "True"])
    groups += fo
    f_t = next((r for r in fo if r["value"] == "True"), None)
    f_f = next((r for r in fo if r["value"] == "False"), None)
    if f_t and f_f and f_t["enough"] and f_f["enough"]:
        verdicts.append({"what": "Focus", "verdict": f"Focus alerts worked {f_t['worked_pct']}% vs {f_f['worked_pct']}% for the rest: "
                         + ("Focus picks better alerts" if f_t["worked_pct"] - f_f["worked_pct"] >= 3 else
                            "Focus does not pick better alerts yet")})
    sv = _grp(df, ["severity"], "Strength (1 watch, 2 important, 3 act now)", ["1", "2", "3"])
    groups += sv
    verdicts.append({"what": "Strength", "verdict": _trend(sv)})
    groups += _grp(df, ["risk"], "Risk check", ["PASS", "BLOCK", "–"])
    groups += _grp(df, ["time"], "Time of day", ["09:15-10:00", "10:00-12:00", "12:00-14:00", "14:00-15:30"])
    groups += _grp(df, ["regime"], "Day type")
    groups += _grp(df.assign(where=np.where(df["is_index"] == 1, "Index", "Stock")), ["where"], "Index or stock")
    groups += _grp(df, ["direction"], "Direction")
    kinds = _grp(df, ["kind"], "Alert type")
    kinds.sort(key=lambda r: (not r["enough"], -(r["worked_pct"] or 0)))
    groups += kinds
    dec = df["worked"].notna().sum()
    return {"n": int(len(df)), "decided": int(dec), "worked_pct": round(float(df["worked"].mean() * 100), 1) if dec else None,
            "days": int(df["day"].nunique()), "first": df["day"].min(), "last": df["day"].max(), "model": model,
            "groups": groups, "verdicts": verdicts}


def params_check(store, source: str, days: int = 60) -> dict:
    """Test 2: test-bench 'Buy ATM' trades split by every fact recorded at the signal."""
    from . import bench as BN
    cut = (dt.datetime.now(IST).date() - dt.timedelta(days=days)).isoformat()
    con = BN.db_for(store).reader()
    try:                                   # two plain reads joined here (no index on bench_trades.sig: a SQL join is slow)
        sg = pd.read_sql("SELECT sig, day, kind, ctx FROM bench_signals WHERE source=? AND day >= ?", con, params=(source, cut))
        tr = pd.read_sql("SELECT sig, net, cost_lot FROM bench_trades WHERE source=? AND day >= ? AND style=? AND net IS NOT NULL "
                         "AND sig IS NOT NULL", con, params=(source, cut, BUY_STYLE))
        df = sg.merge(tr, on="sig", how="inner").drop(columns=["sig"])
    except Exception:
        df = pd.DataFrame()
    finally:
        con.close()
    if df.empty:
        return {"n": 0, "rows": [], "note": "No test-bench trades with recorded facts yet."}
    facts = pd.DataFrame([json.loads(x or "{}") for x in df["ctx"]])
    facts = facts.drop(columns=[c for c in facts.columns if c in ("day", "kind", "net", "cost_lot", "ctx")])
    facts["cost_lot"] = pd.to_numeric(df["cost_lot"], errors="coerce").to_numpy()   # the trade's own lot cost
    df = df.drop(columns=["cost_lot"])
    df = pd.concat([df.drop(columns=["ctx"]).reset_index(drop=True), facts.reset_index(drop=True)], axis=1)
    df["ret"] = df["net"] / df["cost_lot"].where(df["cost_lot"] > 0) * 100
    days_sorted = sorted(df["day"].unique())
    half = set(days_sorted[: len(days_sorted) // 2])
    df["half"] = np.where(df["day"].isin(half), "older", "newer")
    base = {"n": int(len(df)), "avg_net": round(float(df["net"].mean())), "win_pct": round(float((df["net"] > 0).mean() * 100), 1),
            "avg_ret": round(float(df["ret"].mean()), 2) if df["ret"].notna().any() else None}

    def stat(g):
        return {"n": int(len(g)), "avg_net": round(float(g["net"].mean())) if len(g) else None,
                "win_pct": round(float((g["net"] > 0).mean() * 100), 1) if len(g) else None,
                "avg_ret": round(float(g["ret"].mean()), 2) if len(g) and g["ret"].notna().any() else None}

    rows = []
    for col in [c for c in facts.columns if c in NAMES or c in NUMERIC]:
        x = pd.to_numeric(df[col], errors="coerce")
        if x.notna().sum() < MIN_N * 2:
            continue
        if col in NAMES and col not in ("agree", "against", "news_n", "score_bias", "conf_against"):      # signed: with / against
            if col in ("in_play", "is_index"):
                hi, lo = df[x > 0], df[x <= 0]
                lab = ("yes", "no")
            else:
                hi, lo = df[x > 0], df[x < 0]
                lab = ("with", "against")
            mid = df[x == 0] if col not in ("in_play", "is_index") else df.iloc[0:0]
        else:                                                           # numeric: high third vs low third
            q1, q2 = x.quantile([1 / 3, 2 / 3])
            if not (np.isfinite(q1) and np.isfinite(q2)) or q1 == q2:
                continue
            hi, lo, mid = df[x >= q2], df[x <= q1], df[(x > q1) & (x < q2)]
            lab = (f"high (≥ {q2:.3g})", f"low (≤ {q1:.3g})")
        if len(hi) < MIN_N or len(lo) < MIN_N:
            continue
        a, b = stat(hi), stat(lo)
        gap = a["avg_net"] - b["avg_net"]
        sd = math.sqrt(hi["net"].var(ddof=1) / len(hi) + lo["net"].var(ddof=1) / len(lo)) if len(hi) > 1 and len(lo) > 1 else 0
        t = gap / sd if sd > 0 else 0.0
        halves = []
        for h in ("older", "newer"):
            ah, bh = hi[hi["half"] == h], lo[lo["half"] == h]
            halves.append(float(ah["net"].mean() - bh["net"].mean()) if len(ah) >= 10 and len(bh) >= 10 else None)
        same = all(v is not None for v in halves) and np.sign(halves[0]) == np.sign(halves[1]) == np.sign(gap)
        # trades on one day move together, so a few days can look sure: "helps / hurts" needs MIN_DAYS days of data
        if abs(t) >= 2 and same and len(days_sorted) >= MIN_DAYS:
            verdict = "helps" if gap > 0 else "hurts"
        elif abs(t) >= 1 and same:
            verdict = ("leans helpful" if gap > 0 else "leans harmful") + ("" if len(days_sorted) >= MIN_DAYS else
                                                                          f" (only {len(days_sorted)} days)")
        else:
            verdict = "no real difference"
        rows.append({"fact": col, "name": NAMES.get(col) or NUMERIC.get(col, col), "candle": col in CANDLE,
                     "kind": "with / against" if lab[0] == "with" else "yes / no" if lab[0] == "yes" else "high / low",
                     "a_label": lab[0], "a": a, "b_label": lab[1], "b": b, "mid": stat(mid) if len(mid) else None,
                     "gap": round(gap), "t": round(t, 2), "halves": [round(v) if v is not None else None for v in halves],
                     "same_both_halves": bool(same), "verdict": verdict})
    rows.sort(key=lambda r: -abs(r["t"]))
    cand = [r for r in rows if r["candle"]]
    cv = "no candle facts recorded yet"
    if cand:
        best = max(cand, key=lambda r: r["t"])
        cv = ("candle patterns that agree make other signals better" if any(r["verdict"] == "helps" for r in cand) else
              "candle patterns that agree lean towards better results (not proven yet)" if any(
                  r["verdict"].startswith("leans helpful") for r in cand) else
              "candle patterns that agree do not make other signals better (so far)") + \
            f": best {best['name']} {best['a_label']} ₹{best['a']['avg_net']:+,} vs {best['b_label']} ₹{best['b']['avg_net']:+,} a trade"
    return {"n": base["n"], "base": base, "days": len(days_sorted), "first": days_sorted[0], "last": days_sorted[-1],
            "rows": rows, "candles": cv, "style": BUY_STYLE}


def report(engine, source: str | None = None, days: int = 60) -> dict:
    e = engine
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    return {"source": src, "days": days, "made": dt.datetime.now(IST).isoformat(timespec="minutes"),
            "alerts": alerts_check(e.store, src, days), "params": params_check(e.store, src, days),
            "note": "Read-only: built from results that already happened. 'Helps' needs a clear gap (t ≥ 2) with the same sign "
                    "in the older and the newer half of the days; anything less is a lead to watch, not a rule."}
