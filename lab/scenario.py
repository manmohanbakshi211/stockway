"""Days like today: how each end-of-day option strategy did when the market looked like it does now.

Uses the trades from the 10-year end-of-day test (lab/eod_study.py, lab/eod_trades.parquet). Each past entry day is
described by five numbers, and today (the latest close in the bhavcopy) is described the same way:
  vix       India VIX close
  vix_chg5  VIX change over 5 sessions (falling fear or rising fear)
  trend     index vs its 20-day average, in %
  ret1      that day's index move, in %
  rich      at-the-money straddle to expiry / (0.8 x 20-day realised vol x sqrt(time)): above 1 = options priced rich
The 20% of past entry days closest to today on these numbers (each scaled by its spread) are "days like today".

For every index, strategy and entry timing, the result is one number per past entry day (the average over that
entry's strike/wing variants), so a busy grid cannot inflate the sample. Shown side by side: all days, days like
today, and the variant the strict walk-forward actually traded. Nothing here is a forecast or an order: it says
what happened before in similar conditions, and how much that evidence is worth.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .bhav import Bhavcopy, eod_dir
from .eod_study import NICE, STEP, _stats, build_books, implied_move, spot_daily
from .expiry_desk import weekdays_before

COLS = ["vix", "vix_chg5", "trend", "ret1", "rich"]
NEAR_FRAC = 0.2
ENTRY_NAME = {"1": "1 day before expiry", "2": "2 days before expiry", "after_expiry": "day after the previous expiry"}
FAM_SHORT = {"iron_condor": "Iron condor", "iron_fly": "Iron fly", "long_straddle": "Long straddle (buy)"}


def vix_daily(data_dir, log=print) -> pd.DataFrame:
    """India VIX closes: the app's own history plus Yahoo for the years before it (cached for 20 hours)."""
    parts = []
    own = Path(data_dir) / "history" / "INDIAVIX" / "daily.parquet"
    if own.exists():
        d = pd.read_parquet(own)[["date", "close"]]
        parts.append(d)
    cache = eod_dir(data_dir) / "vix_yahoo.parquet"
    y = None
    if cache.exists() and time.time() - cache.stat().st_mtime < 20 * 3600:
        y = pd.read_parquet(cache)
    else:
        try:
            import yfinance as yf
            d = yf.download("^INDIAVIX", start="2014-01-01", interval="1d", progress=False, auto_adjust=False, threads=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            y = d.reset_index().rename(columns={"Date": "date", "Close": "close"})[["date", "close"]].dropna()
            y.to_parquet(cache, index=False)
        except Exception as ex:
            log(f"India VIX history from Yahoo not available ({ex}); using the app's own VIX history")
            if cache.exists():
                y = pd.read_parquet(cache)
    if y is not None:
        parts.insert(0, y)
    if not parts:
        raise RuntimeError("No India VIX history. Download history on the Strategy desk first.")
    v = pd.concat(parts)
    v["date"] = pd.to_datetime(v["date"]).dt.date
    v = v.drop_duplicates("date", keep="last").sort_values("date").rename(columns={"close": "vix"}).reset_index(drop=True)
    v["vix_chg5"] = v["vix"] / v["vix"].shift(5) - 1
    return v


def features(sym, data_dir, vix: pd.DataFrame, fo: pd.DataFrame, log=print) -> pd.DataFrame:
    s = spot_daily(sym, data_dir, log)[["date", "close"]].copy()
    s["date"] = pd.to_datetime(s["date"]).dt.date
    tail = fo[(fo["sym"] == sym) & (fo["date"] > s["date"].max())].groupby("date")["underlying"].first().dropna()
    if len(tail):
        s = pd.concat([s, tail.rename("close").reset_index()])
    s = s.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    r = np.log(s["close"]).diff()
    s["ret1"] = s["close"].pct_change() * 100
    s["trend"] = (s["close"] / s["close"].rolling(20).mean() - 1) * 100
    s["rv20"] = r.rolling(20).std() * math.sqrt(252) * 100
    s = pd.merge_asof(s.sort_values("date").assign(_d=pd.to_datetime(s["date"])),
                      vix.assign(_d=pd.to_datetime(vix["date"]))[["_d", "vix", "vix_chg5"]], on="_d").drop(columns="_d")
    return s


def today_state(sym, fo: pd.DataFrame, f: pd.DataFrame) -> dict | None:
    g = fo[(fo["sym"] == sym) & (fo["kind"] == "OPT")]
    if g.empty:
        return None
    d = g["date"].max()
    g = g[g["date"] == d]
    exps = sorted(e for e in g["expiry"].unique() if e > d)
    if not exps:
        return None
    e = exps[0]
    S = float(g["underlying"].dropna().iloc[0]) if g["underlying"].notna().any() else float(f.iloc[-1]["close"])
    book = build_books(g[g["expiry"] == e]).get((d, e), {})
    im = implied_move(book, S, STEP[sym])
    row = f[f["date"] <= d].iloc[-1]
    days = (pd.Timestamp(e) - pd.Timestamp(d)).days
    em = 0.8 * float(row["rv20"]) * math.sqrt(max(days, 1) / 365)
    prev = [x for x in sorted(fo[(fo["sym"] == sym)]["expiry"].unique()) if x <= d]
    return {"date": str(d), "spot": round(S, 2), "next_expiry": str(e), "prev_expiry": str(prev[-1]) if prev else None,
            "days_to_expiry": days, "implied_move": round(im, 2) if im == im else None,
            "vix": round(float(row["vix"]), 2), "vix_chg5": round(float(row["vix_chg5"]) * 100, 1),
            "trend": round(float(row["trend"]), 2), "ret1": round(float(row["ret1"]), 2), "rv20": round(float(row["rv20"]), 1),
            "rich": round(im / em, 2) if im == im and em > 0 else None}


def describe(t: dict) -> list:
    out = [f"India VIX {t['vix']:.1f}, " + ("falling" if t["vix_chg5"] < 0 else "rising") + f" ({t['vix_chg5']:+.1f}% in 5 days)"]
    out.append(f"{'Up' if t['ret1'] >= 0 else 'Down'} {abs(t['ret1']):.2f}% on the day, "
               + ("above" if t["trend"] > 0.25 else "below" if t["trend"] < -0.25 else "at") + f" its 20-day average ({t['trend']:+.1f}%)")
    if t.get("rich"):
        out.append(f"Options to {t['next_expiry']} price a {t['implied_move']}% move: "
                   + ("rich" if t["rich"] > 1.15 else "cheap" if t["rich"] < 0.9 else "fair") + f" vs recent moves ({t['rich']}x)")
    return out


def next_entry(key: str, st: dict) -> str | None:
    exp = dt.date.fromisoformat(st["next_expiry"])
    today = dt.date.fromisoformat(st["date"])
    if key in ("1", "2"):
        d = weekdays_before(exp, int(key))
    else:
        d = None
        if st.get("prev_expiry"):
            d = dt.date.fromisoformat(st["prev_expiry"]) + dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
        if d is None or d <= today:
            return f"after the {exp:%d %b} expiry"
    return f"{d:%a %d %b} close" if d > today else ("today's close" if d == today else "passed")


def verdict(near: dict, allv: dict) -> tuple[str, str]:
    n, avg, t = near.get("n", 0), near.get("avg", 0), near.get("t", 0)
    if n < 25:
        return "few", "Too few similar days"
    if avg > 0 and t >= 1.5 and allv.get("avg", 0) > 0:
        return "good", "Worked in days like today"
    if avg > 0:
        return "maybe", "Slightly positive, could be luck"
    if t <= -1.5:
        return "bad", "Clearly lost in days like today"
    return "weak", "Lost a little in days like today"


def run(data_dir, log=print) -> dict:
    data_dir = Path(data_dir)
    lab = data_dir / "lab"
    tf = lab / "eod_trades.parquet"
    if not tf.exists():
        raise RuntimeError("Run the 10-year test on the Strategy desk first.")
    trades = pd.read_parquet(tf)
    trades["date"] = pd.to_datetime(trades["date"]).dt.date
    trades["expiry"] = pd.to_datetime(trades["expiry"]).dt.date
    wf = json.loads((lab / "eod_results.json").read_text()).get("results", {}) if (lab / "eod_results.json").exists() else {}
    vix = vix_daily(data_dir, log)
    bc = Bhavcopy(data_dir, log)
    yr = sorted(int(f.stem[3:]) for f in bc.dir.glob("fo_*.parquet"))
    fo = pd.concat([pd.read_parquet(bc.dir / f"fo_{y}.parquet") for y in yr[-1:]]) if yr else pd.DataFrame()
    out = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "today": {}, "strategies": [], "best": None,
           "notes": ["Days like today = the 20% of past entry days closest to today on VIX level, VIX direction, trend, "
                     "the day's move and how rich options are.",
                     "One result per past entry (averaged over strike variants), per lot, after costs and slippage, held to expiry.",
                     "'Strict test' = only the variant the walk-forward picked each year from the two years before it.",
                     "History, not a forecast. Paper trades only: Stockway never places real orders."]}
    for sym in sorted(trades["sym"].unique()):
        f = features(sym, data_dir, vix, fo, log)
        st = today_state(sym, fo, f)
        if not st or st.get("rich") is None:
            log(f"{sym}: no option prices for the latest day; skipped")
            continue
        st["conditions"] = describe(st)
        out["today"][sym] = st
        g = trades[trades["sym"] == sym].merge(f[["date", "ret1", "trend", "rv20", "vix", "vix_chg5"]], on="date", how="left")
        days = (pd.to_datetime(g["expiry"]) - pd.to_datetime(g["date"])).dt.days.clip(lower=1)
        g["rich"] = g["implied_move"] / (0.8 * g["rv20"] * np.sqrt(days / 365))
        g["entry"] = g["pid"].str.extract(r"entry=([^,]+)")[0]
        e = g.drop_duplicates(["date", "expiry"]).dropna(subset=COLS)
        mu, sd = e[COLS].mean(), e[COLS].std().replace(0, 1)
        tz = (pd.Series({c: st[c] / (100 if c == "vix_chg5" else 1) for c in COLS}) - mu) / sd
        dist = np.sqrt((((e[COLS] - mu) / sd - tz) ** 2).sum(axis=1))
        near = set(zip(e.loc[dist <= dist.quantile(NEAR_FRAC), "date"], e.loc[dist <= dist.quantile(NEAR_FRAC), "expiry"]))
        g["near"] = [k in near for k in zip(g["date"], g["expiry"])]
        st["similar_days"] = len(near)
        picks = {}
        r = wf.get(f"{sym}|iron_condor"), wf.get(f"{sym}|iron_fly"), wf.get(f"{sym}|long_straddle")
        for res in r:
            if res:
                picks[res["family"]] = {(y["year"], y["pid"]) for y in res["years"] if y.get("pid")}
        for (fam, key), x in g.groupby(["family", "entry"]):
            per = x.groupby(["date", "expiry"]).agg(pnl=("pnl", "mean"), near=("near", "first")).reset_index()
            allv, nearv = _stats(per["pnl"]), _stats(per[per["near"]]["pnl"])
            strict = None
            if picks.get(fam):
                sx = x[[(yy, p) in picks[fam] for yy, p in zip(x["year"], x["pid"])]]
                if len(sx):
                    strict = {"all": _stats(sx["pnl"]), "near": _stats(sx[sx["near"]]["pnl"])}
            code, label = verdict(nearv, allv)
            out["strategies"].append({"sym": sym, "family": fam, "name": FAM_SHORT.get(fam, fam), "long_name": NICE.get(fam, fam),
                                "entry": key, "entry_name": ENTRY_NAME.get(key, key), "next_entry": next_entry(key, st),
                                "all": allv, "near": nearv, "strict": strict, "verdict": code, "verdict_text": label})
    if not out["strategies"]:
        raise RuntimeError("No end-of-day data for the latest day.")
    ok = [r for r in out["strategies"] if r["verdict"] == "good"]
    # prefer what also survived the strict walk-forward, then the stronger t on days like today
    ok.sort(key=lambda r: (bool(r["strict"] and (r["strict"]["near"].get("avg") or 0) > 0), r["near"].get("t", 0)), reverse=True)
    if ok:
        b = ok[0]
        out["best"] = {k: b[k] for k in ("sym", "family", "name", "entry", "entry_name", "next_entry", "near", "strict")}
    out["avoid"] = [{k: r[k] for k in ("sym", "name", "entry_name", "near")} for r in out["strategies"] if r["verdict"] == "bad"]
    lab.mkdir(parents=True, exist_ok=True)
    (lab / "scenario.json").write_text(json.dumps(out, default=str))
    for sym, st in out["today"].items():
        log(f"{sym} {st['date']}: " + "; ".join(st["conditions"]) + f" — {st['similar_days']} similar past days")
    if out["best"]:
        b = out["best"]
        log(f"Best in days like today: {b['sym']} {b['name']}, {b['entry_name']}: avg ₹{b['near']['avg']}/lot over "
            f"{b['near']['n']} similar days, won {b['near']['win_pct']}%")
    return out


def stale(data_dir) -> bool:
    """True when scenario.json is older than the latest bhavcopy day or the 10-year test."""
    lab = Path(data_dir) / "lab"
    f = lab / "scenario.json"
    if not f.exists():
        return (lab / "eod_trades.parquet").exists()
    m = f.stat().st_mtime
    src = [lab / "eod_trades.parquet", eod_dir(data_dir) / "_done.json"]
    return any(s.exists() and s.stat().st_mtime > m for s in src)
