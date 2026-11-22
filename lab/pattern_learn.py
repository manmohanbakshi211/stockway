"""2.93 Pattern learning (gun): the system learns which candle patterns, in which situation, are worth catching.

Mandeep, 23 Nov 2026: "patterens to observe karte raho or ye dekhna hai humein ki system patterens achhe se pakdna
seekhe toh do the best." The candle-pattern test bench keeps running as it is; this learns from it.

Two sources, one method:
  - "lab": years of history in the Strategy Lab's 5-minute bar table (data/lab/factory_bars.parquet, and the 5-year
    one when there). Every 5-minute candle pattern, the at-the-money option bought its way at the real historical
    price, every cost (the Lab's own pnl_<exit>, as a share of the premium paid), and whether price moved its way in
    the next 30 minutes ("caught");
  - "bench": the live test bench (data/bench.db). Every candle pattern on every timeframe (15m ... 1W), its honest
    1-lot trade (ask in, bid out, every charge) in R, with ~50 facts recorded at entry.
Each occurrence is described by its situation (bullets in rules/pattern_learn.json: daily trend with / against, time of
day, key levels, volume, VWAP side, the market with / against, other timeframes' candles ...). The method is the
situation picker's (lab/situation.py): the pattern's own average plus each situation's difference from it, shrunk
toward zero by shrink_k occurrences, so a few lucky trades cannot decide. Days are split train / validation / holdout:
learned on train, the pick (the top x% by quality) chosen on validation, and every number shown is from the holdout
days nothing was learned on. One line per run (pattern_learn_runs.jsonl) shows whether the learning improves as days
pile up.

Live (shadow): the model learned on every bench day so far scores each candle pattern the test bench takes (quality
0-100 = where its expected result sits among the same pattern's learning occurrences; tag "pq" in its bench record).
forward() compares high-quality patterns with all of them on the days after the model was built: the one test it could
not have seen. Nothing here sends an alert, trades or changes a paper account. Market data only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "pattern_learn.json"
VERSION = 1
OUT = "pattern_learn.json"
RUNS = "pattern_learn_runs.jsonl"

DEFAULTS = {"split": [0.6, 0.2, 0.2], "pick_top_pct": [50, 30, 20, 10],
            "lab": {"exit": "std", "shrink_k": 200, "min_pattern_n": 2000, "min_pick_n": 300, "min_lift": 0.005,
                    "fwd_bars": 6, "indices": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"], "patterns": {},
                    "dims": []},
            "bench": {"style": "buy", "shrink_k": 50, "min_days": 8, "min_pattern_n": 300, "min_pick_n": 100,
                      "min_lift": 0.02, "dims": []}}


def cfg() -> dict:
    c = json.loads(json.dumps(DEFAULTS))
    try:
        u = json.loads(RULES.read_text())
    except Exception:
        return c
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c


# ---------------------------------------------------------------- situation labels (same code for learning and live)
def _num(raw) -> np.ndarray:
    return pd.to_numeric(pd.Series(list(raw) if not isinstance(raw, (pd.Series, np.ndarray)) else raw),
                         errors="coerce").to_numpy(float)


def _align(x: np.ndarray, d: np.ndarray, mode) -> np.ndarray:
    if mode == "mul":
        return x * d
    if mode == "flip01":
        return np.where(d > 0, x, 1 - x)
    if mode == "flip100":
        return np.where(d > 0, x, 100 - x)
    return x


def _one(v, d: float, sp: dict) -> str:
    """One occurrence's label (the live path: plain Python, same rules as the vector path below)."""
    kind = sp.get("type", "bins")
    if kind == "cat":
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return "na"
        if isinstance(v, (bool, np.bool_)):
            return "1" if v else "0"
        if isinstance(v, (int, float, np.integer, np.floating)) and float(v) == int(v):
            return str(int(v))
        return str(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "na"
    if not math.isfinite(x):
        return "na"
    mode = sp.get("align")
    x = x * d if mode == "mul" else (x if d > 0 else 1 - x) if mode == "flip01" else (x if d > 0 else 100 - x) if mode == "flip100" else x
    if kind == "sign":
        return str(int(min(1, max(-1, np.round(x)))))
    return f"b{int(np.digitize([x], list(sp.get('cuts') or []))[0])}"


def labels(cols: dict, d, dims: list) -> dict:
    """{dim name: array of labels} for n occurrences. cols: {fact: values}; d: +1 / -1 per occurrence (the pattern's
    direction). A missing or unknown fact is "na" (it then shifts nothing)."""
    d = np.asarray(d, float)
    n = len(d)
    if n == 1:                                       # live: one pattern at a time, no pandas
        return {sp["name"]: np.array([_one((list(cols[sp["fact"]]) or [None])[0] if sp.get("fact") in cols else None,
                                           float(d[0]), sp) if sp.get("fact") in cols else "na"], dtype=object)
                for sp in dims}
    out = {}
    for sp in dims:
        name, fact, kind = sp["name"], sp.get("fact"), sp.get("type", "bins")
        raw = cols.get(fact)
        if raw is None:
            out[name] = np.full(n, "na", dtype=object)
            continue
        if kind == "cat":
            vals = []
            for v in (raw.tolist() if hasattr(raw, "tolist") else list(raw)):
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    vals.append("na")
                elif isinstance(v, (bool, np.bool_)):
                    vals.append("1" if v else "0")
                elif isinstance(v, (int, float, np.integer, np.floating)) and float(v) == int(v):
                    vals.append(str(int(v)))
                else:
                    vals.append(str(v))
            out[name] = np.array(vals, dtype=object)
            continue
        x = _align(_num(raw), d, sp.get("align"))
        ok = np.isfinite(x)
        if kind == "sign":
            lab = np.array([str(int(v)) for v in np.clip(np.round(np.where(ok, x, 0)), -1, 1)], dtype=object)
        else:
            cuts = list(sp.get("cuts") or [])
            lab = np.array([f"b{i}" for i in np.digitize(np.where(ok, x, 0), cuts)], dtype=object)
        lab[~ok] = "na"
        out[name] = lab
    return out


def words(sp: dict, label: str) -> str:
    """The plain words of one situation label."""
    w = sp.get("words")
    if label == "na" or w is None:
        return f"{sp['name']} unknown" if label == "na" else f"{sp['name']} {label}"
    if isinstance(w, dict):
        return str(w.get(label) or f"{sp['name']} {label}")
    try:
        return str(w[int(label[1:])])
    except Exception:
        return f"{sp['name']} {label}"


# ---------------------------------------------------------------- the model (situation picker's method, any units)
def fit(y, labs: dict) -> dict:
    """A pattern's average and, per situation, [occurrences, average]."""
    y = np.asarray(y, float)
    m0 = float(y.mean()) if len(y) else 0.0
    cells, dm = {}, {}
    for k, lab in labs.items():
        g = pd.DataFrame({"l": lab, "y": y}).groupby("l")["y"].agg(["count", "mean"])
        cells[k] = {str(i): [int(r["count"]), round(float(r["mean"]), 5)] for i, r in g.iterrows() if str(i) != "na"}
        known = np.asarray(lab) != "na"            # each situation is measured against the occurrences where it is known
        dm[k] = round(float(y[known].mean()), 5) if known.any() else round(m0, 5)
    return {"m0": round(m0, 5), "n": int(len(y)), "cells": cells, "dm": dm}


def expected(model: dict, labs: dict, k: float) -> np.ndarray:
    """The pattern's average + each situation's difference shrunk toward zero by k occurrences."""
    n = len(next(iter(labs.values()))) if labs else 0
    m0 = float(model.get("m0") or 0.0)
    dm = model.get("dm") or {}
    if n == 1:                                       # live: plain dictionary lookups
        e1 = m0
        for dim, lab in labs.items():
            c = ((model.get("cells") or {}).get(dim) or {}).get(lab[0])
            if c:
                e1 += (c[1] - float(dm.get(dim, m0))) * c[0] / (c[0] + k)
        return np.array([e1])
    e = np.full(n, m0)
    for dim, lab in labs.items():
        cell = (model.get("cells") or {}).get(dim) or {}
        if not cell:
            continue
        b = float(dm.get(dim, m0))
        shift = {v: (c[1] - b) * c[0] / (c[0] + k) for v, c in cell.items()}
        e += pd.Series(lab).map(shift).fillna(0.0).to_numpy(float)
    return e


def explain(model: dict, lab_row: dict, dims: list, k: float, top: int = 3) -> dict:
    """What lifts and what hurts one occurrence: {"up": [(words, shift)], "down": [...]}."""
    m0 = float(model.get("m0") or 0.0)
    dm = model.get("dm") or {}
    spec = {sp["name"]: sp for sp in dims}
    parts = []
    for dim, v in lab_row.items():
        c = ((model.get("cells") or {}).get(dim) or {}).get(v)
        if not c or dim not in spec:
            continue
        parts.append((words(spec[dim], v), round((c[1] - float(dm.get(dim, m0))) * c[0] / (c[0] + k), 4)))
    parts.sort(key=lambda p: -p[1])
    return {"up": [p for p in parts if p[1] > 0][:top], "down": [p for p in parts[::-1] if p[1] < 0][:top]}


def best_worst(model: dict, dims: list, k: float, min_n: int, top: int = 4) -> dict:
    """The situations that most lift / hurt a pattern (shrunk, with at least min_n occurrences)."""
    m0 = float(model.get("m0") or 0.0)
    dm = model.get("dm") or {}
    spec = {sp["name"]: sp for sp in dims}
    rows = []
    for dim, cell in (model.get("cells") or {}).items():
        if dim not in spec or dim == "dir":
            continue
        b = float(dm.get(dim, m0))
        for v, (n, avg) in cell.items():
            if n >= min_n:
                rows.append({"dim": dim, "what": words(spec[dim], v), "n": n, "avg": round(avg, 4),
                             "shift": round((avg - b) * n / (n + k), 4)})
    rows.sort(key=lambda r: -r["shift"])
    return {"best": [r for r in rows if r["shift"] > 0][:top], "worst": [r for r in rows[::-1] if r["shift"] < 0][:top]}


def _qcuts(e) -> list:
    e = np.asarray(e, float)
    e = e[np.isfinite(e)]
    if not len(e):
        return []
    return [round(float(v), 6) for v in np.quantile(e, np.linspace(0, 1, 101)[1:-1])]


def quality(e, cuts: list) -> np.ndarray:
    """0-100: where an expected result sits among the learning occurrences of the same pattern."""
    e = np.atleast_1d(np.asarray(e, float))
    if not cuts:
        return np.full(len(e), 50.0)
    return np.round(np.searchsorted(np.asarray(cuts), e, side="right") / len(cuts) * 100, 0)


def _stats(y, extra=None) -> dict:
    y = np.asarray(y, float)
    if not len(y):
        return {"n": 0, "avg": None, "win_pct": None}
    out = {"n": int(len(y)), "avg": round(float(y.mean()), 4), "win_pct": round(float((y > 0).mean() * 100), 1)}
    if extra is not None and len(extra):
        ex = np.asarray(extra, float)
        ok = np.isfinite(ex)
        if ok.any():
            out["caught_pct"] = round(float((ex[ok] > 0).mean() * 100), 1)
            out["move_pct"] = round(float(ex[ok].mean()), 3)
    return out


# ---------------------------------------------------------------- learning (any source)
def learn_rows(df: pd.DataFrame, y, key, dims: list, c_src: dict, c: dict, names: dict, fwd=None) -> dict:
    """df: one row per occurrence with 'day' and the facts; y: its result; key: the pattern of each row.
    Learn on train days, pick the quality bar on validation, report holdout."""
    y = np.asarray(y, float)
    key = np.asarray(key, dtype=object)
    fwd = np.asarray(fwd, float) if fwd is not None else None
    days = np.array(sorted(df["day"].astype(str).unique()))
    nd = len(days)
    a = c.get("split") or [0.6, 0.2, 0.2]
    t_end = days[min(nd - 1, max(1, int(round(nd * a[0]))))] if nd > 2 else None
    v_end = days[min(nd - 1, max(2, int(round(nd * (a[0] + a[1])))))] if nd > 2 else None
    if t_end is None or v_end is None or t_end >= v_end:
        return {"error": f"not enough days ({nd})", "days": nd}
    day = df["day"].astype(str).to_numpy()
    part = np.where(day < t_end, 0, np.where(day < v_end, 1, 2))
    d = df["_d"].to_numpy(float)
    cols = {sp["fact"]: df[sp["fact"]].to_numpy() for sp in dims if sp.get("fact") in df.columns}
    labs = labels(cols, d, dims)
    k, min_n = float(c_src["shrink_k"]), int(c_src["min_pattern_n"])
    tr = part == 0
    models = {"*": fit(y[tr], {n_: l[tr] for n_, l in labs.items()})}
    for p in sorted(set(key.tolist())):
        m = tr & (key == p)
        if m.sum() >= min_n:
            models[p] = fit(y[m], {n_: l[m] for n_, l in labs.items()})
    e = np.zeros(len(y))
    for p in sorted(set(key.tolist())):
        rows = key == p
        e[rows] = expected(models.get(p, models["*"]), {n_: l[rows] for n_, l in labs.items()}, k)
    q = np.zeros(len(y))
    cuts = {}
    for p in sorted(set(key.tolist())):
        rows = key == p
        cuts[p] = _qcuts(e[rows & tr]) or _qcuts(e[tr])
        q[rows] = quality(e[rows], cuts[p])
    va, ho = part == 1, part == 2
    best, choose = None, []
    for top in c.get("pick_top_pct") or [30]:
        m = va & (q >= 100 - float(top))
        if m.sum() >= int(c_src["min_pick_n"]):
            avg = float(y[m].mean())
            choose.append({"top_pct": top, "n": int(m.sum()), "avg": round(avg, 4)})
            if best is None or avg > best[1]:
                best = (top, avg)
    top = best[0] if best else (c.get("pick_top_pct") or [30])[0]
    pick_q = 100 - float(top)
    pk = ho & (q >= pick_q)
    ex = fwd if fwd is not None else None
    hold_all = _stats(y[ho], ex[ho] if ex is not None else None)
    hold_pick = _stats(y[pk], ex[pk] if ex is not None else None)
    quint = []
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 101)):
        m = ho & (q >= lo) & (q < hi)
        quint.append({"q": f"{lo}-{min(hi, 100)}", **_stats(y[m], ex[m] if ex is not None else None)})
    lift = (hold_pick["avg"] - hold_all["avg"]) if hold_pick["n"] and hold_all["n"] else None
    pats = []
    for p in sorted(set(key.tolist())):
        rows = key == p
        a_ = _stats(y[ho & rows], ex[ho & rows] if ex is not None else None)
        p_ = _stats(y[pk & rows], ex[pk & rows] if ex is not None else None)
        lf = (p_["avg"] - a_["avg"]) if p_["n"] and a_["n"] else None
        own = p in models
        bw = best_worst(models.get(p, models["*"]), dims, k, max(30, int((tr & rows).sum() * 0.05) if own else int(min_n * 0.05)))
        pats.append({"key": p, "name": names.get(p, p), "train_n": int((tr & rows).sum()), "own_model": own,
                     "all": a_, "picked": p_, "lift": round(lf, 4) if lf is not None else None,
                     "verdict": _verdict(a_, p_, lf, c_src), **bw})
    pats.sort(key=lambda r: -(r["all"]["n"] or 0))
    ok_rank = len(quint) == 5 and quint[0]["n"] and quint[-1]["n"] and quint[-1]["avg"] > quint[0]["avg"]
    v = _verdict(hold_all, hold_pick, lift, c_src)
    return {"days": nd, "first": days[0], "last": days[-1], "train_to": t_end, "valid_to": v_end,
            "n": int(len(y)), "train_n": int(tr.sum()), "valid_n": int(va.sum()), "hold_n": int(ho.sum()),
            "pick_top_pct": top, "pick_q": pick_q, "choose": choose, "hold_all": hold_all, "hold_pick": hold_pick,
            "lift": round(lift, 4) if lift is not None else None, "quintiles": quint, "ranks": bool(ok_rank),
            "verdict": v, "patterns": pats, "k": k}


def _verdict(all_: dict, pick: dict, lift, c_src: dict) -> dict:
    if not all_.get("n") or not pick.get("n"):
        return {"code": "wait", "text": "Not enough holdout occurrences yet."}
    if lift is not None and lift >= float(c_src["min_lift"]) and pick["avg"] > 0:
        return {"code": "pays", "text": "The picked patterns beat all of them on unseen days and made money after costs."}
    if lift is not None and lift >= float(c_src["min_lift"]):
        return {"code": "learns", "text": "The picked patterns lost less than all of them on unseen days, "
                                          "but still lost after costs: learning to sort, not yet a trade."}
    return {"code": "no", "text": "On unseen days the picked patterns did no better than all of them."}


def _catch_verdict(all_: dict, pick: dict, c_l: dict) -> dict:
    """Does the learning pick patterns whose price really moves their way (the underlying, before any option cost)?"""
    a, p = all_.get("caught_pct"), pick.get("caught_pct")
    if a is None or p is None or not pick.get("n"):
        return {"code": "wait", "text": "Not enough holdout occurrences yet."}
    bar = float(c_l.get("catch_bar_pct") or 55)
    if p >= bar and p - a >= float(c_l.get("catch_lift_pct") or 3):
        return {"code": "catches", "text": f"Picked patterns moved their way {p:.0f}% of the time vs {a:.0f}% for all: "
                                           "the system catches the move."}
    return {"code": "no", "text": f"Picked patterns moved their way {p:.0f}% of the time vs {a:.0f}% for all: "
                                  "no better than a coin yet."}


def final_model(df, y, key, dims, c_src) -> dict:
    """The same learning on every day (for live scoring; the forward check judges it on later days)."""
    y = np.asarray(y, float)
    key = np.asarray(key, dtype=object)
    cols = {sp["fact"]: df[sp["fact"]].to_numpy() for sp in dims if sp.get("fact") in df.columns}
    labs = labels(cols, df["_d"].to_numpy(float), dims)
    k, min_n = float(c_src["shrink_k"]), int(c_src["min_pattern_n"])
    models = {"*": fit(y, labs)}
    for p in sorted(set(key.tolist())):
        m = key == p
        if m.sum() >= min_n:
            models[p] = fit(y[m], {n_: l[m] for n_, l in labs.items()})
    cuts = {"*": _qcuts(expected(models["*"], labs, k))}
    for p in sorted(set(key.tolist())):
        m = key == p
        cuts[p] = _qcuts(expected(models.get(p, models["*"]), {n_: l[m] for n_, l in labs.items()}, k))
    return {"models": models, "cuts": cuts, "k": k, "dims": dims}


# ---------------------------------------------------------------- source: the live test bench
def _bench_frame(db_path: Path, c_b: dict) -> pd.DataFrame | None:
    from live.practice import PRACTICE
    if not Path(db_path).exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        s = pd.read_sql("select sig, source, day, sym, kind, variant, direction, ctx, tag from bench_signals "
                        "where kind like 'candle_%'", con)
        t = pd.read_sql("select sig, r_mult, is_index from bench_trades where kind like 'candle_%' and style = ? "
                        "and exit_ts is not null", con, params=(c_b["style"],))
    finally:
        con.close()
    s = s[~s["source"].astype(str).str.contains("#") & ~s["source"].astype(str).str.split("#").str[0].isin(PRACTICE)]
    if s.empty or t.empty:
        return None
    t = t.drop_duplicates("sig")
    df = s.merge(t, on="sig", how="inner")
    df = df[np.isfinite(pd.to_numeric(df["r_mult"], errors="coerce"))]
    if df.empty:
        return None
    ctx = pd.json_normalize(df["ctx"].map(lambda x: json.loads(x) if x else {}).tolist())
    ctx.index = df.index
    ctx = ctx.drop(columns=[c for c in ctx.columns if c in df.columns], errors="ignore")   # the bench's own columns win
    df = pd.concat([df, ctx], axis=1)
    return add_bench_facts(df)


def add_bench_facts(df: pd.DataFrame) -> pd.DataFrame:
    var = df["variant"].fillna("5m").astype(str)
    df = df.assign(tf=var.str.split("@").str[0], early=var.str.contains("@").map(lambda b: "1" if b else "0"),
                   _d=df["direction"].map({"up": 1, "down": -1}).astype(float))
    return df.assign(dir=df["_d"])


def learn_bench(data_dir, c: dict, log=print) -> dict:
    from live import registry
    c_b = c["bench"]
    df = _bench_frame(Path(data_dir) / "bench.db", c_b)
    if df is None or df.empty:
        return {"error": "no candle patterns on the test bench yet"}
    nd = df["day"].nunique()
    if nd < int(c_b["min_days"]):
        return {"error": f"{nd} bench days (needs {c_b['min_days']})", "days": int(nd)}
    names = {"candle_" + k: v for k, v in registry.names("candle").items()}
    y = df["r_mult"].astype(float).to_numpy()
    res = learn_rows(df, y, df["kind"].to_numpy(), c_b["dims"], c_b, c, names)
    res.update(source="bench", unit="R", style=c_b["style"])
    fm = final_model(df, y, df["kind"].to_numpy(), c_b["dims"], c_b)
    res["live"] = {**fm, "through": str(df["day"].max()), "pick_q": res.get("pick_q")}
    log(f"Pattern learning (test bench): {len(df)} candle patterns on {nd} days; "
        f"holdout all {res['hold_all'].get('avg')} R, picked {res['hold_pick'].get('avg')} R")
    return res


# ---------------------------------------------------------------- source: years of history (Lab bar table)
def _lab_frame(data_dir, c_l: dict, should_stop=lambda: False) -> pd.DataFrame | None:
    f = Path(data_dir) / "lab" / "factory_bars.parquet"
    if not f.exists():
        return None
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(f)
    have = set(pf.schema_arrow.names)
    pats = [p for p in c_l["patterns"] if p in have]
    ex = "pnl_" + c_l["exit"]
    if not pats or ex not in have:
        return None
    facts = [sp["fact"] for sp in c_l["dims"] if sp.get("fact") in have and sp["fact"] not in ("dir",)]
    cols = list(dict.fromkeys(["sym", "date", "bar", "dir", "premium", ex, "ret_b6"] + pats + facts))
    cols = [x for x in cols if x in have]
    parts, moves = [], []
    for batch in pf.iter_batches(batch_size=200_000, columns=cols):
        if should_stop():
            return None
        t = batch.to_pandas()
        if "ret_b6" in t.columns:
            moves.append(t.loc[t["dir"] == 1, ["sym", "date", "bar", "ret_b6"]])
        t = t[t["dir"] != 0]
        dd = t["dir"].to_numpy(float)
        for p in pats:
            m = (t[p].fillna(0).to_numpy(float) * dd) > 0
            if m.any():
                parts.append(t.loc[m].drop(columns=pats).assign(pat=p))
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    fb = int(c_l.get("fwd_bars") or 6)
    if moves:                      # the move over the next fwd_bars 5-minute bars = ret_b6 of the bar fwd_bars later
        mv = pd.concat(moves, ignore_index=True).drop_duplicates(["sym", "date", "bar"])
        mv = mv.assign(bar=mv["bar"] - fb).rename(columns={"ret_b6": "_fwd"})
        df = df.merge(mv, on=["sym", "date", "bar"], how="left")
    else:
        df["_fwd"] = np.nan
    df["_y"] = df[ex] / df["premium"]
    df = df[np.isfinite(df["_y"].to_numpy(float))]
    df["_d"] = df["dir"].astype(float)
    df["_fwd"] = df["_fwd"].astype(float) * df["_d"]
    df["is_index"] = df["sym"].isin(set(c_l.get("indices") or [])).astype(int)
    df["day"] = df["date"].astype(str)
    return df


def learn_lab(data_dir, c: dict, log=print, should_stop=lambda: False) -> dict:
    c_l = c["lab"]
    df = _lab_frame(data_dir, c_l, should_stop)
    if df is None or df.empty:
        return {"error": "no Strategy Lab bar table with candle patterns here"}
    res = learn_rows(df, df["_y"].to_numpy(float), df["pat"].to_numpy(), c_l["dims"], c_l, c,
                     dict(c_l.get("patterns") or {}), fwd=df["_fwd"].to_numpy(float))
    res.update(source="lab", unit="share of premium", exit=c_l["exit"], syms=int(df["sym"].nunique()),
               fwd_min=int(c_l.get("fwd_bars") or 6) * 5)
    mv = df[np.isfinite(df["_fwd"].to_numpy(float))]       # the same learning on the underlying's next 30 minutes
    if len(mv) and not should_stop():
        m = learn_rows(mv, mv["_fwd"].to_numpy(float), mv["pat"].to_numpy(), c_l["dims"], {**c_l, "min_lift": 0.0}, c,
                       dict(c_l.get("patterns") or {}), fwd=mv["_fwd"].to_numpy(float))
        if not m.get("error"):
            res["move"] = {k_: m.get(k_) for k_ in ("hold_all", "hold_pick", "quintiles", "pick_top_pct", "ranks")}
            res["move"]["verdict"] = _catch_verdict(m.get("hold_all") or {}, m.get("hold_pick") or {}, c_l)
            res["move"]["patterns"] = [{k_: p.get(k_) for k_ in ("key", "name", "all", "picked", "best", "worst")}
                                       for p in m.get("patterns") or []]
    log(f"Pattern learning (years of history): {len(df)} 5-minute patterns, {res.get('syms')} symbols, "
        f"{res.get('days')} days; holdout all {res.get('hold_all', {}).get('avg')}, picked {res.get('hold_pick', {}).get('avg')}")
    return res


# ---------------------------------------------------------------- run, save, view
def run(data_dir, log=print, should_stop=lambda: False, out_dir=None) -> dict:
    t0 = time.time()
    c = cfg()
    data_dir = Path(data_dir)
    out = {"version": VERSION, "built": dt.datetime.now().isoformat(timespec="seconds"), "valid_from": c.get("valid_from")}
    try:
        out["lab"] = learn_lab(data_dir, c, log, should_stop)
    except Exception as e:
        out["lab"] = {"error": f"failed: {e}"}
    if (data_dir / "bench.db").exists():
        try:
            out["bench"] = learn_bench(data_dir, c, log)
        except Exception as e:
            out["bench"] = {"error": f"failed: {e}"}
    out["secs"] = round(time.time() - t0, 1)
    d = Path(out_dir) if out_dir else data_dir / "lab"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (OUT + ".tmp")
    tmp.write_text(json.dumps(out, default=str))
    tmp.replace(d / OUT)
    with (d / RUNS).open("a") as fh:
        for src in ("lab", "bench"):
            r = out.get(src) or {}
            if r.get("hold_all"):
                q = r.get("quintiles") or [{}]
                fh.write(json.dumps({"at": out["built"], "src": src, "days": r.get("days"), "last": r.get("last"),
                                     "hold_all": r["hold_all"].get("avg"), "hold_pick": r["hold_pick"].get("avg"),
                                     "lift": r.get("lift"), "top_q": q[-1].get("avg"), "bottom_q": q[0].get("avg"),
                                     "verdict": (r.get("verdict") or {}).get("code")}) + "\n")
    return {"secs": out["secs"], "lab": (out.get("lab") or {}).get("verdict"), "bench": (out.get("bench") or {}).get("verdict")}


def _slim(r: dict | None) -> dict | None:
    if not r:
        return r
    return {k: v for k, v in r.items() if k != "live"}


def view(data_dir, data5=None) -> dict:
    """For the Strategy Lab page: both sources (and the 5-year history when there), the learning curve and the forward
    check of the live scores."""
    out = {}
    f = Path(data_dir) / "lab" / OUT
    try:
        j = json.loads(f.read_text()) if f.exists() else {}
    except Exception as e:
        j = {"error": str(e)}
    out["built"] = j.get("built")
    out["lab"] = _slim(j.get("lab"))
    out["bench"] = _slim(j.get("bench"))
    if data5 is not None:
        f5 = Path(data5) / "lab" / OUT
        try:
            out["lab5"] = _slim(json.loads(f5.read_text()).get("lab")) if f5.exists() else None
        except Exception:
            out["lab5"] = None
    runs = []
    rf = Path(data_dir) / "lab" / RUNS
    if rf.exists():
        for ln in rf.read_text().splitlines()[-60:]:
            try:
                runs.append(json.loads(ln))
            except Exception:
                pass
    out["runs"] = runs
    live = (j.get("bench") or {}).get("live") or {}
    try:
        out["forward"] = forward(Path(data_dir) / "bench.db", live.get("through"), live.get("pick_q"), cfg()["bench"]["style"])
    except Exception as e:
        out["forward"] = {"error": str(e)}
    return out


def forward(db_path: Path, through, pick_q, style: str = "buy") -> dict:
    """The live scores judged on days after the model's last learning day (the only days it could not have seen)."""
    if not through or not Path(db_path).exists():
        return {"n": 0, "note": "no live model yet"}
    from live.practice import PRACTICE
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        s = pd.read_sql("select sig, source, day, tag from bench_signals where kind like 'candle_%' and day > ? "
                        "and tag like '%\"pq\"%'", con, params=(str(through),))
        t = pd.read_sql("select sig, r_mult from bench_trades where kind like 'candle_%' and style = ? and day > ? "
                        "and exit_ts is not null", con, params=(style, str(through)))
    finally:
        con.close()
    s = s[~s["source"].astype(str).str.contains("#") & ~s["source"].astype(str).isin(PRACTICE)]
    if s.empty or t.empty:
        return {"n": 0, "since": through, "note": "no scored patterns closed after the model was built yet"}
    df = s.merge(t.drop_duplicates("sig"), on="sig")
    df["q"] = df["tag"].map(lambda x: (json.loads(x) or {}).get("pq") if x else None)
    df = df[df["q"].notna()]
    y, q = df["r_mult"].astype(float).to_numpy(), df["q"].astype(float).to_numpy()
    pq_ = float(pick_q if pick_q is not None else 70)
    buckets = []
    for lo, hi in ((0, 40), (40, 60), (60, 80), (80, 101)):
        m = (q >= lo) & (q < hi)
        buckets.append({"q": f"{lo}-{min(hi, 100)}", **_stats(y[m])})
    allx, pick = _stats(y), _stats(y[q >= pq_])
    return {"since": through, "days": int(df["day"].nunique()), "n": int(len(df)), "all": allx, "picked": pick,
            "pick_q": pq_, "lift": round(pick["avg"] - allx["avg"], 4) if pick["n"] and allx["n"] else None,
            "buckets": buckets}


# ---------------------------------------------------------------- live scoring (shadow)
class Live:
    """Scores one candle pattern the test bench takes, with the model learned on every bench day so far."""

    def __init__(self, data_dir):
        self.f = Path(data_dir) / "lab" / OUT
        self.mtime = None
        self.m = None

    def _load(self):
        try:
            mt = self.f.stat().st_mtime
        except OSError:
            self.m = None
            return
        if mt != self.mtime:
            self.mtime = mt
            try:
                self.m = (json.loads(self.f.read_text()).get("bench") or {}).get("live")
            except Exception:
                self.m = None

    def score(self, kind: str, variant, direction: str, ctx: dict | None, why: bool = False) -> dict | None:
        self._load()
        m = self.m
        if not m or not str(kind).startswith("candle_"):
            return None
        d = 1 if direction == "up" else -1 if direction == "down" else 0
        if not d:
            return None
        var = str(variant or "5m")
        facts = dict(ctx or {})
        facts.update(tf=var.split("@")[0], early="1" if "@" in var else "0", dir=float(d))
        dims = m.get("dims") or []
        cols = {sp["fact"]: [facts.get(sp["fact"])] for sp in dims if sp.get("fact")}
        labs = labels(cols, [d], dims)
        models = m.get("models") or {}
        model = models.get(kind) or models.get("*")
        if not model:
            return None
        e = float(expected(model, labs, float(m.get("k") or 50))[0])
        cuts = (m.get("cuts") or {}).get(kind) or (m.get("cuts") or {}).get("*") or []
        out = {"q": int(quality([e], cuts)[0]), "exp": round(e, 3), "pick": int(quality([e], cuts)[0]) >= float(m.get("pick_q") or 70)}
        if why:
            out.update(explain(model, {k: v[0] for k, v in labs.items()}, dims, float(m.get("k") or 50)))
        return out
