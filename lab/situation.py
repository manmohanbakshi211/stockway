"""Market situation picker (gun, 2.73): which live Strategy Lab rule fits today's market.

Mandeep, 13 Nov 2026: "why we have to select the rules ... the system should understand the market movement and choose
what is the best for making profit as per the market situation". Until now every promoted rule traded whenever its own
conditions matched, on every kind of day (a straddle sell on a trend day too).

Gun (here): the market situation of any 5-minute bar, from facts known at its close (no later bar): the day so far
(quiet / range / trend, against this symbol's typical day by that time of day; the same definitions as the day-type
forecaster), the trade with or against the day's move, VIX (low / mid / high: terciles of the learning days), expiry
(expiry day / 1-2 days / further), the opening gap (up / flat / down), the daily trend (up / down / none).
For each live rule: its Lab trades labelled with the situation at entry; on the train + validation days, its average
result in each situation (the difference from the rule's own average shrunk toward zero by shrink_k trades); the
expected result of a new signal = the rule's average + the shrunk differences of today's situation. Take it when that
is above zero. The holdout days (never used to learn) check it: every trade vs only the picked ones. Then the same is
learned again on all the days for the live use.
Bullets (rules/situation.json): the thresholds, the dimensions, the shrinkage, the trust bar.
Information and its own paper account only ("Auto pick"); the main account is unchanged. Market data only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / "rules" / "situation.json"
OUT = "situation.json"
BARS = 76                                    # 5-minute bars a session (09:15-15:30)
WORDS = {"day": {"quiet": "quiet day so far", "range": "range day so far", "trend": "trend day so far"},
         "move": {"with": "with the day's move", "against": "against the day's move", "flat": "day flat so far"},
         "vix": {"low": "VIX low", "mid": "VIX normal", "high": "VIX high"},
         "expiry": {"expiry": "expiry day", "near": "1-2 days to expiry", "far": "3+ days to expiry"},
         "gap": {"up": "gap up", "flat": "flat open", "down": "gap down"},
         "trend": {"up": "daily uptrend", "down": "daily downtrend", "none": "no daily trend"}}
DIM_NAMES = {"day": "Day so far", "move": "Trade vs the day's move", "vix": "VIX", "expiry": "Expiry", "gap": "Opening gap",
             "trend": "Daily trend"}


def cfg() -> dict:
    return json.loads(CFG_FILE.read_text())


def _f(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ one bar's situation
def classify(day_range, day_ret, gap, vix, dte, d_trend, tod, d: int, med20, frac, vix_cuts, c: dict) -> dict:
    """The situation of one bar (all % numbers as in the Lab's bar facts). frac: the share of a typical day's range
    reached by this time of day; med20: the typical whole-day range (median of the 20 days before)."""
    out = {}
    rng, ret, g = _f(day_range), _f(day_ret), _f(gap)
    med20, frac = _f(med20), _f(frac)                    # a missing (NaN) typical day: the day can't be judged
    if rng is not None and med20 and frac:
        rel = rng / (med20 * max(frac, 0.05))
        mv = (ret or 0.0) - (g or 0.0)
        eff = abs(mv) / rng if rng > 0 else 0.0
        out["day"] = "quiet" if rel < c["quiet_rel"] else "trend" if (eff >= c["trend_eff"] and rel >= c["trend_rel"]) else "range"
        if d:
            out["move"] = "flat" if abs(mv) < c["move_flat_rel"] * med20 * max(frac, 0.05) else \
                ("with" if mv * d > 0 else "against")
    v = _f(vix)
    if v is not None and vix_cuts:
        out["vix"] = "low" if v < vix_cuts[0] else "high" if v > vix_cuts[1] else "mid"
    t = _f(dte)
    if t is not None:
        out["expiry"] = "expiry" if t <= 0 else "near" if t <= c["near_expiry_days"] else "far"
    if g is not None and med20:
        out["gap"] = "up" if g >= c["gap_rel"] * med20 else "down" if g <= -c["gap_rel"] * med20 else "flat"
    tr = _f(d_trend)
    if tr is not None:
        out["trend"] = "up" if tr > 0 else "down" if tr < 0 else "none"
    dims = set(c.get("dims") or WORDS)
    return {k: v for k, v in out.items() if k in dims}


def words(sit: dict) -> str:
    return ", ".join(WORDS[k][v] for k, v in sit.items() if k in WORDS and v in WORDS[k])


# ------------------------------------------------------------------ learning from the Lab
def _bar_table(book, c: dict):
    """One row per (day, symbol, bar): the facts the situation needs, the typical day range before it (med20) and the
    share of a typical day's range reached by that time (per index; the stocks pooled), learned on train + validation."""
    import pandas as pd
    cols = book.cols
    df = pd.DataFrame({"day": book.dix, "sym": book.symcode, "tod": cols["tod"], "part": book.part,
                       "day_range": cols["day_range"], "day_ret": cols["day_ret"], "gap": cols["gap"], "vix": cols["vix"],
                       "dte": cols["dte"], "d_trend": cols["d_trend"]})
    df = df.drop_duplicates(["day", "sym", "tod"]).sort_values(["sym", "day", "tod"]).reset_index(drop=True)
    end = df.groupby(["sym", "day"]).tail(1)[["sym", "day", "day_range"]].rename(columns={"day_range": "rng_end"})
    end["med20"] = end.groupby("sym")["rng_end"].transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())
    df = df.merge(end, on=["sym", "day"], how="left")
    df["bar"] = np.clip(np.round(df["tod"].to_numpy(float) / 5).astype(int), 0, BARS - 1)
    df["grp"] = np.where(np.isin(df["sym"], INDEX), df["sym"], "STOCKS")
    learn = df[(df["part"] < 2) & (df["rng_end"] > 0)]
    share = (learn["day_range"] / learn["rng_end"]).clip(0, 1)
    frac = share.groupby([learn["grp"], learn["bar"]]).median()
    curves = {}
    for g in frac.index.get_level_values(0).unique():
        s = frac.loc[g].reindex(range(BARS)).ffill().bfill()
        curves[g] = [round(float(x), 4) for x in s.to_numpy()]
    df["frac"] = [curves.get(g, [None] * BARS)[b] for g, b in zip(df["grp"], df["bar"])]
    v = learn["vix"].to_numpy(float)
    v = v[np.isfinite(v)]
    vix_cuts = [round(float(np.quantile(v, 1 / 3)), 2), round(float(np.quantile(v, 2 / 3)), 2)] if len(v) > 30 else None
    med_now = end.dropna(subset=["rng_end"]).groupby("sym")["rng_end"].apply(lambda s: float(s.tail(20).median())).to_dict()
    return df, curves, vix_cuts, med_now


def _codes(df, d: int, vix_cuts, c: dict) -> list:
    return [classify(r.day_range, r.day_ret, r.gap, r.vix, r.dte, r.d_trend, r.tod, d, r.med20, r.frac, vix_cuts, c)
            for r in df.itertuples(index=False)]


def fit(pnl, sits: list, c: dict) -> dict:
    """The rule's average and, per situation, [trades, average ₹]."""
    pnl = np.asarray(pnl, float)
    m0 = float(pnl.mean()) if len(pnl) else 0.0
    cells: dict = {}
    for x, s in zip(pnl, sits):
        for k, v in s.items():
            cell = cells.setdefault(k, {}).setdefault(v, [0, 0.0])
            cell[0] += 1
            cell[1] += float(x)
    for k in cells:
        for v, (n, tot) in cells[k].items():
            cells[k][v] = [n, round(tot / n, 1)]
    return {"m0": round(m0, 1), "n": int(len(pnl)), "cells": cells}


def expected(model: dict, sit: dict, c: dict) -> tuple[float, list]:
    """The rule's average + each situation's shrunk difference: (expected ₹ a trade, [(dim, value, n, avg, shift)])."""
    m0, k = float(model.get("m0") or 0), float(c["shrink_k"])
    exp, parts = m0, []
    for dim, val in sit.items():
        cell = ((model.get("cells") or {}).get(dim) or {}).get(val)
        if not cell:
            continue
        n, avg = cell
        sh = (avg - m0) * n / (n + k)
        exp += sh
        parts.append((dim, val, n, avg, round(sh, 1)))
    return round(exp, 1), parts


def _stats(x) -> dict:
    x = np.asarray(x, float)
    if not len(x):
        return {"n": 0, "total": 0.0, "avg": 0.0, "win_pct": None}
    return {"n": int(len(x)), "total": round(float(x.sum()), 0), "avg": round(float(x.mean()), 1),
            "win_pct": round(float((x > 0).mean() * 100), 1)}


def _days(t, mask) -> dict:
    """Green days and the worst day of a set of trades."""
    if not mask.any():
        return {"days": 0, "green_pct": None, "worst_day": None}
    g = t[mask].groupby("day")["pnl"].sum()
    return {"days": int(len(g)), "green_pct": round(float((g > 0).mean() * 100), 1), "worst_day": round(float(g.min()), 0)}


def lean_book(f, rules: list):
    """Only the columns these rules and the situation need (the full table is ~1 million rows x 224 columns)."""
    import pandas as pd
    import pyarrow.parquet as pq
    from . import factory
    have = set(pq.ParquetFile(f).schema.names)
    feats = {"tod", "day_range", "day_ret", "gap", "vix", "dte", "d_trend"}
    for r in rules:
        for c_ in list(r.get("conds") or []) + list((r.get("then") or {}).get("conds") or []):
            feats.add(c_[0])
    pnl = {"pnl_" + factory.pnl_key(r) for r in rules if r.get("exit")}
    cols = [c_ for c_ in dict.fromkeys(["sym", "date", "dir", "premium"] + sorted(feats) + sorted(pnl)) if c_ in have]
    return factory.Book(pd.read_parquet(f, columns=cols), feats=sorted(feats & set(factory.FEATURES)))


def learn(data_dir, log=print, book=None, rules: list | None = None, should_stop=lambda: False) -> dict:
    """Every live Strategy Lab rule: its results per situation (learned on train + validation), the holdout check
    (every trade vs the picked ones), then learned again on every day for live use. Saved to data/situation.json."""
    import pandas as pd
    from . import factory
    t0 = time.time()
    data_dir = Path(data_dir)
    c = cfg()
    if rules is None:
        rules = factory.promoted(data_dir)
    if not rules:
        return _save(data_dir, {"made": time.time(), "note": "No live Strategy Lab rules yet.", "rules": {}})
    if book is None:                                     # the Lab's saved bar table, read only (the nightly Lab builds it)
        f = factory.cache_path(data_dir)
        if not f.exists():
            return _save(data_dir, {"made": time.time(), "note": "No Lab bar table here yet (the nightly Lab builds it).", "rules": {}})
        book = lean_book(f, rules)
    bars, curves, vix_cuts, med_now = _bar_table(book, c)
    key = ["day", "sym", "tod"]
    out_rules, all_hold, pick_hold, hold_tr = {}, [], [], []
    for r in rules:
        if should_stop():
            break
        try:
            tr = book.trades(r)
            if not len(tr):
                continue
            tr = tr.merge(bars[key + ["day_range", "day_ret", "gap", "vix", "dte", "d_trend", "med20", "frac"]], on=key, how="left")
            sits = _codes(tr, int(r.get("dir") or 0), vix_cuts, c)
            learnm = (tr["part"] < 2).to_numpy()
            model = fit(tr["pnl"].to_numpy()[learnm], [s for s, m in zip(sits, learnm) if m], c)
            hold = ~learnm
            take = np.array([expected(model, s, c)[0] > 0 if h else False for s, h in zip(sits, hold)])
            hx = tr["pnl"].to_numpy()[hold]
            px = tr["pnl"].to_numpy()[take]
            final = fit(tr["pnl"].to_numpy(), sits, c)                        # for live: every day
            cells = []
            for dim, vals in final["cells"].items():
                for v, (n, avg) in vals.items():
                    if n >= c["min_cell_n"]:
                        cells.append([dim, v, n, avg])
            cells.sort(key=lambda z: -z[3])
            out_rules[r["id"]] = {"dir": int(r.get("dir") or 0), "syms": r.get("syms"), "model": final,
                                  "hold": {"all": {**_stats(hx), **_days(tr, hold)}, "picked": {**_stats(px), **_days(tr, take)},
                                           "skipped": _stats(tr["pnl"].to_numpy()[hold & ~take])},
                                  "best": cells[:3], "worst": cells[::-1][:3]}
            all_hold.append(hx)
            pick_hold.append(px)
            t_ = tr[hold | take][["day", "pnl"]].assign(picked=take[hold | take])
            hold_tr.append(t_)
        except Exception as e:                                                # one broken rule is skipped, the rest run
            out_rules[r["id"]] = {"error": str(e)[:160]}
    a = np.concatenate(all_hold) if all_hold else np.array([])
    p = np.concatenate(pick_hold) if pick_hold else np.array([])
    ht = pd.concat(hold_tr) if hold_tr else pd.DataFrame(columns=["day", "pnl", "picked"])
    over = {"all": {**_stats(a), **_days(ht, np.ones(len(ht), bool))}, "picked": {**_stats(p), **_days(ht, ht["picked"].to_numpy(bool))}}
    tw = c["trust"]
    beats = (over["picked"]["n"] >= tw["min_hold_trades"] and (not tw.get("beat_total") or over["picked"]["total"] > over["all"]["total"])
             and (not tw.get("beat_avg") or over["picked"]["avg"] > over["all"]["avg"]))
    days = book.dates
    res = {"made": time.time(), "secs": round(time.time() - t0, 1), "rules": out_rules, "overall": over,
           "beats_holdout": bool(beats), "frac": curves, "vix_cuts": vix_cuts, "med20": {k: round(v, 4) for k, v in med_now.items()},
           "hold_from": str(book.split_dates["hold"][0]), "hold_to": str(book.split_dates["hold"][1]),
           "first": str(days[0]) if len(days) else None, "last": str(days[-1]) if len(days) else None}
    log(f"Situation picker: {len(out_rules)} live rules; holdout {over['all']['n']} trades ₹{over['all']['total']:,.0f} "
        f"-> picked {over['picked']['n']} ₹{over['picked']['total']:,.0f} ({'beats' if beats else 'does not beat'} taking every signal)")
    return _save(data_dir, res)


def _save(data_dir, res: dict) -> dict:
    (Path(data_dir) / OUT).write_text(json.dumps(res, default=str))
    return {k: v for k, v in res.items() if k not in ("rules", "frac", "med20")}


_CACHE: dict = {}


def load(data_dir) -> dict:
    f = Path(data_dir) / OUT
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {}
    if _CACHE.get(str(f), (None,))[0] != mt:
        try:
            _CACHE[str(f)] = (mt, json.loads(f.read_text()))
        except Exception:
            return {}
    return _CACHE[str(f)][1]


# ------------------------------------------------------------------ live
INDEX = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX")


def live_situation(info: dict, f: dict, sym: str, d: int, i: int | None = None) -> dict:
    """Today's situation for `sym` on the bar just closed, from the same facts the live rules read (bar_features)."""
    c = cfg()
    if not info:
        return {}
    i = len(f["tod"]) - 1 if i is None else i
    grp = sym if sym in INDEX else "STOCKS"
    curve = (info.get("frac") or {}).get(grp)
    tod = f["tod"][i]
    bar = int(min(BARS - 1, max(0, round(float(tod) / 5)))) if _f(tod) is not None else None
    g = lambda k: (f.get(k)[i] if f.get(k) is not None and len(f.get(k)) > i else None)
    return classify(g("day_range"), g("day_ret"), g("gap"), g("vix"), g("dte"), g("d_trend"), tod, d,
                    (info.get("med20") or {}).get(sym), curve[bar] if curve and bar is not None else None, info.get("vix_cuts"), c)


def verdict(info: dict, rule_id: str, sit: dict) -> dict:
    """Take this rule's signal in this situation? {"take": True / False / None (not learned yet), "exp", "why"}."""
    r = (info.get("rules") or {}).get(rule_id)
    if not r or not r.get("model"):
        return {"take": None, "why": "not learned yet (the picker learns every night)"}
    exp, parts = expected(r["model"], sit, cfg())
    big = sorted(parts, key=lambda z: -abs(z[4]))[:2]
    why = "; ".join(f"{WORDS[d_][v]}: ₹{avg:,.0f} a trade over {n}" for d_, v, n, avg, _ in big)
    return {"take": exp > 0, "exp": exp, "avg": r["model"].get("m0"), "situation": sit,
            "why": (f"expected ₹{exp:,.0f} a trade in this situation (₹{r['model'].get('m0', 0):,.0f} on average)"
                    + (f"; {why}" if why else ""))}


def view(data_dir, now_sits: dict | None = None, rules_meta: dict | None = None) -> dict:
    """The Strategy Lab page: the holdout proof, each rule's best / worst situations, and today's picks."""
    info = load(data_dir)
    if not info:
        return {"ready": False, "note": "The picker learns from the Lab's bar table: it runs every night after the Lab (or press Learn now)."}
    c = cfg()
    rows = []
    for rid, r in (info.get("rules") or {}).items():
        if r.get("error"):
            rows.append({"id": rid, "error": r["error"]})
            continue
        meta = (rules_meta or {}).get(rid) or {}
        now = {}
        for sym, sit in (now_sits or {}).items():
            from .factory import sym_ok
            if r.get("syms") and sym_ok(r["syms"], sym):
                full = dict(sit)
                if r["dir"] and sit.get("_mv") is not None:
                    mv = sit["_mv"]
                    full["move"] = "flat" if mv == 0 else ("with" if mv * r["dir"] > 0 else "against")
                full.pop("_mv", None)
                now[sym] = verdict(info, rid, full)
        rows.append({"id": rid, "rule": meta.get("rule"), "dir": r["dir"], "hold": r["hold"],
                     "best": [[WORDS[d][v], n, avg] for d, v, n, avg in r.get("best") or []],
                     "worst": [[WORDS[d][v], n, avg] for d, v, n, avg in r.get("worst") or []], "now": now})
    rows.sort(key=lambda x: -((x.get("hold") or {}).get("picked") or {}).get("total", 0))
    return {"ready": True, "made": info.get("made"), "overall": info.get("overall"), "beats_holdout": info.get("beats_holdout"),
            "hold_from": info.get("hold_from"), "hold_to": info.get("hold_to"), "rules": rows,
            "now": {s: {k: v for k, v in x.items() if k != "_mv"} for s, x in (now_sits or {}).items()},
            "words": {s: words({k: v for k, v in x.items() if k != "_mv"}) for s, x in (now_sits or {}).items()},
            "trust": c["trust"], "dims": [DIM_NAMES[d] for d in c["dims"]]}
