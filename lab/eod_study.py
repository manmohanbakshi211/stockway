"""Ten-plus years of index option strategies on NSE end-of-day data (lab/bhav.py).

Every trade is entered at a day's closing prices and held to expiry, where options settle at their intrinsic value
(from the index close on expiry day). That makes daily data enough for the strategies that matter most for an
options desk: selling weekly premium (hedged), expiry-eve trades, and buying straddles for comparison. Costs:
brokerage, STT, exchange, SEBI, GST, stamp, plus slippage of 1% of each option price (at least half a point).
Results are per lot at today's lot size.

Selection is walk-forward by calendar year: the variant used in year Y is chosen only from years Y-2 and Y-1
(enough trades, profit factor >= 1.15, profit about 2 standard errors above zero), otherwise that family sits out.
Also measured: the volatility risk premium (implied move from the at-the-money straddle vs the move that followed),
year by year.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .bhav import Bhavcopy, eod_dir
from .data import UNDERLYINGS
from .engine import charges

YAHOO = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
SLIP_PCT, SLIP_MIN = 0.01, 0.5
SELL = {"iron_condor", "iron_fly"}

GRID = {
    "iron_condor": {"entry": ["after_expiry", 2, 1], "short": [0.5, 1.0, 1.5, 2.0], "wing": [1.0, 2.0], "flt": ["none", "vrp"]},
    "iron_fly": {"entry": ["after_expiry", 2, 1], "wing": [1.0, 2.0, 3.0], "flt": ["none", "vrp"]},
    "long_straddle": {"entry": [2, 1], "flt": ["none"]},
}
NICE = {"iron_condor": "Iron condor to expiry (sell OTM call + put, buy wings)",
        "iron_fly": "Iron fly to expiry (sell ATM straddle, buy wings)",
        "long_straddle": "Long ATM straddle to expiry (buyer's view)"}


def spot_daily(sym, data_dir, log=print) -> pd.DataFrame:
    f = eod_dir(data_dir) / f"spot_{sym}.parquet"
    if f.exists() and time.time() - f.stat().st_mtime < 20 * 3600:
        return pd.read_parquet(f)
    import yfinance as yf
    d = yf.download(YAHOO[sym], start="2012-01-01", interval="1d", progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.reset_index().rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close"})
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d = d[["date", "open", "high", "low", "close"]].dropna()
    d.to_parquet(f, index=False)
    return d


def _px(row_close, row_settle, vol):
    if vol and vol > 0 and row_close and row_close > 0:
        return float(row_close)
    return float(row_settle) if row_settle and row_settle > 0 else float("nan")


def build_books(opt: pd.DataFrame) -> dict:
    """{(date, expiry): {(strike, side): price}} for quick lookups."""
    opt = opt[opt["kind"] == "OPT"]
    px = np.where((opt["volume"] > 0) & (opt["close"] > 0), opt["close"], opt["settle"])
    opt = opt.assign(px=px)
    books = {}
    for (d, e), g in opt.groupby(["date", "expiry"]):
        books[(d, e)] = dict(zip(zip(g["strike"].astype(float), g["side"]), g["px"].astype(float)))
    return books


def _rnd(x, step):
    return round(x / step) * step


def legs_for(family, p, S, step):
    k = _rnd(S, step)
    if family == "iron_condor":
        d = max(step, _rnd(S * p["short"] / 100, step))
        w = max(step, _rnd(S * p["wing"] / 100, step))
        return [(k + d, "CE", -1), (k - d, "PE", -1), (k + d + w, "CE", 1), (k - d - w, "PE", 1)]
    if family == "iron_fly":
        w = max(2 * step, _rnd(S * p["wing"] / 100, step))
        return [(k, "CE", -1), (k, "PE", -1), (k + w, "CE", 1), (k - w, "PE", 1)]
    if family == "long_straddle":
        return [(k, "CE", 1), (k, "PE", 1)]
    raise ValueError(family)


def simulate(legs, book, S_exp, lot):
    fills, pts = [], 0.0
    for k, side, q in legs:
        p = book.get((float(k), side))
        if p is None or not (p == p) or p <= 0:
            return None
        sl = max(SLIP_MIN, SLIP_PCT * p)
        entry = p + sl if q > 0 else max(0.05, p - sl)
        intrinsic = max(0.0, S_exp - k) if side == "CE" else max(0.0, k - S_exp)
        pts += q * (intrinsic - entry)
        fills.append((q, entry))
        if intrinsic > 0:
            fills.append((-q, intrinsic))
    cost = charges(fills, lot)
    return pts * lot - cost


def entries(sym, books, spot: pd.DataFrame):
    """For each expiry: candidate entry dates (the day after the previous expiry, and 1-2 trading days before)."""
    days = sorted(spot["date"])
    sidx = {d: i for i, d in enumerate(days)}
    close = dict(zip(spot["date"], spot["close"]))
    exps = sorted({e for (_, e) in books})
    out = []
    prev = None
    for e in exps:
        tdays = [d for d in days if (prev is None or d > prev) and d < e]
        # the expiry's settlement index level: close on expiry day (or the last trading day before it)
        j = np.searchsorted(np.array(days), e, side="right") - 1
        if j < 0 or not tdays:
            prev = e
            continue
        S_exp = close[days[j]]
        cand = {}
        if prev is not None:
            cand["after_expiry"] = tdays[0]
        for k in (1, 2):
            if len(tdays) >= k:
                cand[k] = tdays[-k]
        for key, d in cand.items():
            if (d, e) in books:
                out.append({"expiry": e, "entry_key": key, "date": d, "S": close[d], "S_exp": S_exp,
                            "days": sidx[days[j]] - sidx[d]})
        prev = e
    return out


def implied_move(book, S, step):
    k = _rnd(S, step)
    c, p = book.get((float(k), "CE")), book.get((float(k), "PE"))
    return (c + p) / S * 100 if c and p else float("nan")


def run_grid(sym, books, spot, log=print) -> pd.DataFrame:
    lot = UNDERLYINGS[sym]["lot"]
    step = STEP[sym]
    ents = entries(sym, books, spot)
    rows = []
    # realised vs implied, kept in time order for the VRP filter (only past expiries are known)
    hist_ratio = []
    by_key = {}
    for en in ents:
        by_key.setdefault(en["entry_key"], []).append(en)
    for key, lst in by_key.items():
        past = []
        for en in sorted(lst, key=lambda x: x["date"]):
            book = books[(en["date"], en["expiry"])]
            im = implied_move(book, en["S"], step)
            rm = abs(en["S_exp"] / en["S"] - 1) * 100
            known = [r for d, r in past if d < en["date"]][-20:]
            vrp_ok = bool(known) and im == im and im > np.mean(known) * 1.0
            for fam, space in GRID.items():
                if key not in space["entry"]:
                    continue
                keys = [k for k in space if k != "entry"]
                for vals in itertools.product(*[space[k] for k in keys]):
                    p = dict(zip(keys, vals))
                    if p.get("flt") == "vrp" and not vrp_ok:
                        continue
                    pnl = simulate(legs_for(fam, p, en["S"], step), book, en["S_exp"], lot)
                    if pnl is None:
                        continue
                    pid = fam + "|" + ",".join(f"{k}={v}" for k, v in sorted({**p, "entry": key}.items(), key=lambda x: x[0]))
                    rows.append({"sym": sym, "family": fam, "pid": pid, "date": en["date"], "expiry": en["expiry"],
                                 "year": en["date"].year, "pnl": round(pnl, 1), "implied_move": im, "real_move": rm})
            past.append((en["expiry"], rm))
            hist_ratio.append((en["date"], im, rm))
    return pd.DataFrame(rows)


def _stats(x: pd.Series) -> dict:
    n = len(x)
    if not n:
        return {"n": 0}
    win, loss = x[x > 0].sum(), -x[x < 0].sum()
    t = x.mean() / (x.std(ddof=1) / math.sqrt(n)) if n > 2 and x.std() > 0 else 0.0
    return {"n": int(n), "avg": round(float(x.mean()), 1), "total": round(float(x.sum()), 0), "win_pct": round(float((x > 0).mean() * 100), 1),
            "pf": round(float(win / loss), 2) if loss else None, "t": round(float(t), 2), "worst": round(float(x.min()), 0)}


def walk_forward(trades: pd.DataFrame, train_years=2, min_n=30, min_pf=1.15, min_t=2.0) -> dict:
    out = {}
    years = sorted(trades["year"].unique())
    for (sym, fam), g in trades.groupby(["sym", "family"]):
        yearly = []
        for y in years[train_years:]:
            tr = g[g["year"].between(y - train_years, y - 1)]
            te = g[g["year"] == y]
            best, best_s = None, None
            for pid, x in tr.groupby("pid"):
                s = _stats(x["pnl"])
                if s["n"] >= min_n and (s["pf"] or 0) >= min_pf and s["t"] >= min_t and (best_s is None or s["avg"] > best_s["avg"]):
                    best, best_s = pid, s
            if best is None:
                yearly.append({"year": int(y), "pid": None, "trades": 0, "pnl": 0.0, "note": "nothing passed: cash"})
                continue
            x = te[te["pid"] == best]["pnl"]
            yearly.append({"year": int(y), "pid": best, "trades": int(len(x)), "pnl": round(float(x.sum()), 0),
                           "win_pct": round(float((x > 0).mean() * 100), 1) if len(x) else None, "worst": round(float(x.min()), 0) if len(x) else None,
                           "train": best_s})
        all_oos = pd.concat([g[(g["year"] == r["year"]) & (g["pid"] == r["pid"])] for r in yearly if r["pid"]]) if any(r["pid"] for r in yearly) else pd.DataFrame(columns=["pnl"])
        hind = g.groupby("pid")["pnl"].agg(["count", "mean", "sum"]).sort_values("mean", ascending=False).head(3)
        out[f"{sym}|{fam}"] = {"sym": sym, "family": fam, "name": NICE[fam], "years": yearly, "oos": _stats(all_oos["pnl"]),
                               "hindsight_top": [{"pid": i, "n": int(r["count"]), "avg": round(float(r["mean"]), 1), "total": round(float(r["sum"]), 0)}
                                                 for i, r in hind.iterrows()]}
    return out


def vrp_table(trades: pd.DataFrame) -> list:
    t = trades.drop_duplicates(["sym", "date", "expiry"])
    out = []
    for (sym, y), g in t.groupby(["sym", "year"]):
        g = g.dropna(subset=["implied_move"])
        if not len(g):
            continue
        out.append({"sym": sym, "year": int(y), "n": int(len(g)), "implied_pct": round(float(g["implied_move"].mean()), 2),
                    "realised_pct": round(float(g["real_move"].mean()), 2),
                    "implied_over_pct": round(float((g["implied_move"] > g["real_move"]).mean() * 100), 1)})
    return out


def run(data_dir, syms=("NIFTY", "BANKNIFTY"), log=print) -> dict:
    bc = Bhavcopy(data_dir, log)
    allt = []
    for sym in syms:
        opt = bc.load(sym)
        if opt.empty:
            log(f"{sym}: no end-of-day data yet")
            continue
        spot = spot_daily(sym, data_dir, log)
        t0 = time.time()
        books = build_books(opt)
        tr = run_grid(sym, books, spot, log)
        log(f"{sym}: {len(books):,} day-expiry books, {len(tr):,} simulated trades ({time.time() - t0:.0f}s)")
        allt.append(tr)
    if not allt:
        raise RuntimeError("No end-of-day data. Download it first.")
    trades = pd.concat(allt, ignore_index=True)
    rep = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
           "period": {"from": str(trades["date"].min()), "to": str(trades["date"].max())},
           "results": walk_forward(trades), "vrp": vrp_table(trades),
           "notes": ["Entered at the day's close, held to expiry, settled at intrinsic value from the index close.",
                     "Costs: brokerage, STT, exchange, SEBI, GST, stamp, plus 1% slippage per option (min 0.5 pt).",
                     "Per lot at today's lot size. Weekly expiries where they existed, else monthly.",
                     "Each year uses only the variant chosen on the two years before it."]}
    d = Path(data_dir) / "lab"
    d.mkdir(parents=True, exist_ok=True)
    (d / "eod_results.json").write_text(json.dumps(rep, default=str))
    trades.to_parquet(d / "eod_trades.parquet", index=False)
    for k, v in rep["results"].items():
        o = v["oos"]
        log(f"{k}: out-of-sample {o.get('n', 0)} trades, avg ₹{o.get('avg', 0)}/lot, total ₹{o.get('total', 0)}")
    return rep
