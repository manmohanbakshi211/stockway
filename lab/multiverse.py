"""Multiverse (2.58): every live signal replayed down every timeline at once; the best path found honestly.

Mandeep, 11 Nov 2026: "saari possibilities simultaneously test karke check karke best path as fastest as we can ...
100 magzine, 1000 magzine ... gun itni smart ho ki saari mag khali karke best bullet ka pata laga le".

Gun (this file; knows no strategy, date or lot size):
1. Universe: the recorded days that have both the test bench (data/bench.db: every directional signal, its facts at
   that moment, the ATM / 1-OTM / 1-ITM option it bought at the ask) and the day's minute recording
   (data/recordings/<source>_<day>_minutes.parquet: bid / ask of every streamed option each minute).
2. Paths: each signal's option, minute by minute to 15:15, valued at the bid (what selling would get), as % of the
   price paid. Entering 2 or 5 minutes later uses the ask at that minute.
3. Timelines: filters (which signals) x strikes x entry delays x exits (target x stop x time, trailing). The exits of
   all signals come out of one pass of array maths; every filter x exit total comes out of one matrix product per
   day (filters^T @ outcomes), so millions of timelines cost seconds, not hours.
4. The judge: per strategy n, average net % (after every charge), its spread, win %, green days; ranked by the lower
   confidence bound (average - z x standard error), never by the raw average. Walk-forward: each day's pick is chosen
   only from the days before it and scored on that day (it never saw it), next to the main account's way. The luck
   line: the same search on shuffled data (facts no longer tied to results) shows how good the best of this many
   timelines looks by chance.
5. Output: data/multiverse.json (the page, and the "Multiverse pick" paper account's live picks).

Bullets: rules/multiverse.json (filters, strikes, delays, exit grid, thresholds); a broken filter is skipped and listed.
Read-only towards brokers: nothing here can place an order. Runs in the Lab's low-priority child process (lab/proc.py).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / "rules" / "multiverse.json"
OUT_NAME = "multiverse.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
M = 361                          # minutes after entry kept (6 hours; 15:15 ends every path first)
END_HM = (15, 15)
MIN_REC_BYTES = 1_000_000        # a day's minute recording smaller than this is a stub (scanner barely ran)


def cfg() -> dict:
    return json.loads(CFG_FILE.read_text())


# ------------------------------------------------------------------ exits (the exit magazine)
def exit_specs(c: dict | None = None) -> list:
    """[(kind, a, b, stop, hold)]: ("tsh", target, None, stop, hold) or ("trail", start, keep, stop, hold). None = off."""
    e = (c or cfg())["exits"]
    out = []
    for t in e["targets"]:
        for s in e["stops"]:
            for h in e["holds"]:
                if t is None and s is None and h is None:
                    pass                                   # hold to 15:15: kept, it is a timeline too
                out.append(("tsh", t, None, s, h))
    for a, k in e.get("trails") or []:
        for s in e["stops"]:
            for h in e.get("trail_holds") or [None]:
                out.append(("trail", a, k, s, h))
    return out


def exit_words(x) -> str:
    kind, a, b, s, h = x
    stop = f"stop −{s:g}%" if s is not None else "no stop"
    hold = f"max {h:g} min" if h is not None else "until 15:15"
    if kind == "trail":
        return f"after +{a:g}% keep {int(round(b * 100))}% of the best gain, {stop}, {hold}"
    tgt = f"+{a:g}%" if a is not None else "no target"
    return f"{tgt} / {stop.replace('stop ', '')} on the option, {hold}"


def _first(mask: np.ndarray, none: int) -> np.ndarray:
    """First column where mask is True per row; `none` where it never is."""
    any_ = mask.any(axis=1)
    return np.where(any_, mask.argmax(axis=1), none)


def outcomes(R: np.ndarray, j_end: np.ndarray, exits: list) -> np.ndarray:
    """R: (N, L) the option's value as % change from the price paid, minute 0 = entry (missing minutes already filled).
    j_end: (N,) last minute (15:15). Exits are checked from minute 1. Returns gross % at exit, (N, E) float32."""
    N, L = R.shape
    if N == 0:
        return np.zeros((0, len(exits)), np.float32)
    rows = np.arange(N)
    body = R[:, 1:]
    hi = np.maximum.accumulate(body, axis=1)
    lo = np.minimum.accumulate(body, axis=1)
    big = L + 10
    tcache, scache = {}, {}

    def t_hit(t):
        if t is None:
            return np.full(N, big)
        if t not in tcache:
            tcache[t] = _first(hi >= t, big - 1) + 1
        return tcache[t]

    def s_hit(s):
        if s is None:
            return np.full(N, big)
        if s not in scache:
            scache[s] = _first(lo <= -s, big - 1) + 1
        return scache[s]
    trail_cache = {}
    out = np.empty((N, len(exits)), np.float32)
    for i, (kind, a, b, s, h) in enumerate(exits):
        te = np.minimum(j_end, s_hit(s))
        if h is not None:
            te = np.minimum(te, int(h))
        if kind == "tsh":
            te = np.minimum(te, t_hit(a))
        else:
            if (a, b) not in trail_cache:
                trail_cache[(a, b)] = _first((hi >= a) & (body <= hi * b), big - 1) + 1
            te = np.minimum(te, trail_cache[(a, b)])
        te = np.clip(te, 1, L - 1)
        out[:, i] = R[rows, te]
    return out


def net_pct(gross: np.ndarray, buy_value: np.ndarray, rates: dict) -> np.ndarray:
    """Gross % -> net % of the money paid, after every charge (the same rates as lab/engine.py charge_amount, 2 orders)."""
    bv = np.maximum(np.asarray(buy_value, float), 1.0)[:, None]
    sell = bv * (1 + gross / 100.0)
    brk = 2 * float(rates["brokerage_per_order"])
    es = float(rates["exchange"]) + float(rates["sebi"])
    g = float(rates["gst"])
    cost = brk * (1 + g) + float(rates["stamp_buy"]) * bv + es * (1 + g) * (bv + sell) + float(rates["stt_sell"]) * sell
    return (gross - cost / bv * 100.0).astype(np.float32)


# ------------------------------------------------------------------ the universe (recorded days)
def _end_ts(day: str) -> float:
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(*END_HM), IST).timestamp()


def days_available(data_dir, source: str = "upstox") -> list:
    """Session days with a real minute recording and test-bench signals, oldest first."""
    data_dir = Path(data_dir)
    rec = data_dir / "recordings"
    bdb = data_dir / "bench.db"
    if not bdb.exists() or not rec.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{bdb}?mode=ro", uri=True, timeout=30)
        try:
            bdays = {r[0] for r in con.execute("SELECT DISTINCT day FROM bench_signals WHERE source=?", (source,))}
        finally:
            con.close()
    except Exception:
        return []
    out = []
    try:
        from live.session import session
    except Exception:
        session = None
    for f in sorted(rec.glob(f"{source}_*_minutes.parquet")):
        day = f.name[len(source) + 1:len(source) + 11]
        if day not in bdays or f.stat().st_size < MIN_REC_BYTES:
            continue
        try:
            if session is not None and session(dt.date.fromisoformat(day)) is None:
                continue                                   # a holiday's frozen prices are not a day (10 Nov 2026)
        except Exception:
            pass
        out.append(day)
    return out


def _facts_of(ctx: dict, tag: dict, row: dict) -> dict:
    f = dict(ctx or {})
    f["dir"] = 1.0 if row["direction"] == "up" else -1.0
    f["agree_net"] = (f.get("agree") or 0) - (f.get("against") or 0)
    for k in ("sev", "score", "conv"):
        if tag.get(k) is not None:
            f[k] = tag[k]
    f["focus"] = 1.0 if tag.get("focus") else 0.0
    return f


def load_day(data_dir, day: str, c: dict, source: str = "upstox") -> dict | None:
    """One day's signals, their facts and, per strike, the bid path and the entry price per delay."""
    import pandas as pd
    data_dir = Path(data_dir)
    con = sqlite3.connect(f"file:{data_dir / 'bench.db'}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        sigs = [dict(r) for r in con.execute(
            "SELECT sig, ts, sym, kind, variant, direction, ctx, tag FROM bench_signals WHERE source=? AND day=? "
            "AND direction IN ('up','down') ORDER BY ts", (source, day))]
        styles = list(c["strikes"].values())
        q = ",".join("?" * len(styles))
        trades = {}
        for r in con.execute(f"SELECT sig, style, key1, entry, lot FROM bench_trades WHERE source=? AND day=? AND style IN ({q}) "
                             "AND key1 IS NOT NULL AND entry > 0", (source, day, *styles)):
            trades.setdefault((r["sig"], r["style"]), dict(r))
    finally:
        con.close()
    if not sigs:
        return None
    # one trade per stock, direction and minute: the same move seen by several alert types or timeframes (an inside bar
    # on 15m and 30m at 10:45) is one timeline, not five; it belongs to every alert type that saw it
    groups: dict = {}
    for s in sigs:
        g = groups.setdefault((s["sym"], s["direction"], int(float(s["ts"]) // 60)), [])
        g.append(s)
    merged = []
    for g in groups.values():
        lead = next((s for s in g if any((s["sig"], st) in trades for st in c["strikes"].values())), g[0])
        tags = []
        for s in g:
            try:
                tags.append(json.loads(s["tag"] or "{}") or {})
            except Exception:
                tags.append({})
        tag = {"sev": max((t.get("sev") or 0) for t in tags), "score": max((t.get("score") or 0) for t in tags),
               "focus": any(t.get("focus") for t in tags)}
        cv = [t.get("conv") for t in tags if t.get("conv") is not None]
        if cv:
            tag["conv"] = max(cv)
        merged.append({**lead, "tag": json.dumps(tag), "kinds": sorted({s["kind"] for s in g}), "n_alerts": len(g)})
    sigs = sorted(merged, key=lambda s: s["ts"])
    keys = sorted({t["key1"] for t in trades.values()})
    if not keys:
        return None
    fm = data_dir / "recordings" / f"{source}_{day}_minutes.parquet"
    mins = pd.read_parquet(fm, columns=["ts", "key", "ltp", "bid", "ask"], filters=[("key", "in", keys)])
    by_key = {k: g.sort_values("ts") for k, g in mins.groupby("key")}
    n = len(sigs)
    t0 = np.array([float(s["ts"]) for s in sigs])
    end = _end_ts(day)
    delays = [int(x) for x in c["delays"]]
    W = M + max(delays) + 1
    grid = np.arange(W) * 60.0
    out = {"day": day, "n": n, "sigs": [s["sig"] for s in sigs], "sym": np.array([s["sym"] for s in sigs]),
           "kind": np.array([s["kind"] for s in sigs]), "kinds": [s["kinds"] for s in sigs], "t0": t0,
           "j_end": np.clip(((end - t0) // 60).astype(int), 1, M - 1), "strikes": {}}
    facts = []
    for s in sigs:
        try:
            ctx = json.loads(s["ctx"] or "{}") or {}
        except Exception:
            ctx = {}
        try:
            tag = json.loads(s["tag"] or "{}") or {}
        except Exception:
            tag = {}
        facts.append(_facts_of(ctx, tag, s))
    out["facts"] = facts
    out["regime"] = np.array([str(f.get("regime") or "na") for f in facts])
    for name, style in c["strikes"].items():
        B = np.full((n, W), np.nan, np.float32)
        entry = np.full((n, len(delays)), np.nan, np.float32)
        buyv = np.zeros(n, np.float32)
        for i, s in enumerate(sigs):
            t = trades.get((s["sig"], style))
            if not t:
                continue
            g = by_key.get(t["key1"])
            if g is None or len(g) < 3:
                continue
            ts = g["ts"].to_numpy(float)
            bid = g["bid"].to_numpy(float)
            ltp = g["ltp"].to_numpy(float)
            ask = g["ask"].to_numpy(float)
            val = np.where(np.nan_to_num(bid) > 0, bid, ltp)
            buy = np.where(np.nan_to_num(ask) > 0, ask, ltp)
            j = np.searchsorted(ts, t0[i] + grid, side="right") - 1
            ok = j >= 0
            row = np.full(W, np.nan)
            row[ok] = val[j[ok]]
            row[(row <= 0) | ~np.isfinite(row)] = np.nan
            B[i] = row
            for di, d in enumerate(delays):
                if d == 0:
                    entry[i, di] = float(t["entry"])
                else:
                    jj = j[d] if d < len(j) else -1
                    entry[i, di] = buy[jj] if jj >= 0 and buy[jj] > 0 and t0[i] + d * 60 < end else np.nan
            buyv[i] = float(t["entry"]) * int(t["lot"] or 1)
        out["strikes"][name] = {"bid": B, "entry": entry, "lot_value": buyv}
    return out


def _ffill(B: np.ndarray) -> np.ndarray:
    """Fill missing minutes with the last known value (leading gaps with the first known one)."""
    B = B.copy()
    N, W = B.shape
    if N == 0:
        return B
    idx = np.where(np.isfinite(B), np.arange(W)[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    B = B[np.arange(N)[:, None], idx]
    first = np.where(np.isfinite(B).any(axis=1), np.argmax(np.isfinite(B), axis=1), 0)
    lead = np.arange(W)[None, :] < first[:, None]
    B[lead] = B[np.arange(N), first][:, None].repeat(W, axis=1)[lead]
    return B


def chunk_paths(D: dict, strike: str, di: int, delay: int):
    """(R, j_end, valid, lot_value_at_entry) for one strike and entry delay: R = % change from the entry price."""
    st = D["strikes"][strike]
    B = _ffill(st["bid"])
    ent = st["entry"][:, di].astype(float)
    valid = np.isfinite(ent) & (ent > 0) & np.isfinite(B[:, delay:delay + 2]).all(axis=1)
    R = np.zeros((D["n"], M), np.float32)
    if valid.any():
        seg = B[valid, delay:delay + M]
        R[valid] = ((seg / ent[valid, None]) - 1.0) * 100.0
    R = np.nan_to_num(R, nan=0.0)
    j_end = np.clip(D["j_end"] - delay, 1, M - 1)
    first_px = st["entry"][:, 0].astype(float)
    lv = np.where(np.isfinite(ent) & (first_px > 0), st["lot_value"] * ent / np.where(first_px > 0, first_px, 1), 0)
    return R, j_end, valid & (j_end >= 1), lv


# ------------------------------------------------------------------ filters (the entry magazine)
def kind_label(k: str) -> str:
    try:
        from live.registry import label
        return label(k)
    except Exception:
        return k


def single_masks(facts: list, regime: np.ndarray, singles: list, errors: dict) -> list:
    """[(spec, mask)] for each single filter; a broken one is skipped and listed in errors."""
    out = []
    for sp in singles:
        try:
            if "cat" in sp:
                if sp["cat"] != "regime":
                    raise ValueError(f"unknown category {sp['cat']}")
                m = regime == sp["is"]
            else:
                x = np.array([float(f.get(sp["fact"])) if isinstance(f.get(sp["fact"]), (int, float)) and f.get(sp["fact"]) is not None
                              else np.nan for f in facts])
                v = float(sp["v"])
                m = (x > v) if sp["op"] == ">" else (x < v) if sp["op"] == "<" else None
                if m is None:
                    raise ValueError(f"unknown op {sp['op']}")
                m &= np.isfinite(x)
            out.append((sp, m))
        except Exception as e:
            errors[sp.get("name", "?")] = str(e)[:120]
    return out


def build_filters(pool_kinds: list, singles: list) -> list:
    """Filter definitions: (kind or None) x (single or None). The champion is added by the caller."""
    defs = []
    for k in [None] + list(pool_kinds):
        for si in [None] + list(range(len(singles))):
            if k is None and si is None:
                defs.append({"kind": None, "single": None})
            else:
                defs.append({"kind": k, "single": si})
    return defs


def filter_matrix(D: dict, defs: list, smask: list, champ: dict | None) -> np.ndarray:
    n = D["n"]
    F = np.zeros((n, len(defs)), np.float32)
    kinds = D["kinds"]
    kmask = {}
    for j, f in enumerate(defs):
        if f.get("champion"):
            ck = set(champ["kinds"])
            m = np.array([bool(ck.intersection(ks)) for ks in kinds], bool)
            sev = np.array([float(x.get("sev") or 0) for x in D["facts"]])
            m &= sev >= float(champ.get("min_sev") or 0)
        else:
            m = np.ones(n, bool)
            if f["kind"] is not None:
                if f["kind"] not in kmask:
                    kmask[f["kind"]] = np.array([f["kind"] in ks for ks in kinds], bool)
                m = m & kmask[f["kind"]]
            if f["single"] is not None:
                m = m & smask[f["single"]][1]
        F[:, j] = m
    return F


def filter_words(f: dict, singles: list) -> str:
    if f.get("champion"):
        return "the main account's alert types (strength 2+)"
    parts = []
    if f.get("kind"):
        parts.append(f"{kind_label(f['kind'])} signals")
    if f.get("single") is not None:
        parts.append(singles[f["single"]]["name"])
    return ", ".join(parts) if parts else "every signal"


# ------------------------------------------------------------------ the search
class Acc:
    """Running totals per (filter, exit) for one strike x delay chunk."""

    def __init__(self, K: int, E: int, light: bool = False):
        self.s1 = np.zeros((K, E), np.float32)
        self.s2 = np.zeros((K, E), np.float32)
        self.w = None if light else np.zeros((K, E), np.float32)
        self.n = np.zeros(K, np.float64)
        self.pos = None if light else np.zeros((K, E), np.int16)
        self.days = np.zeros(K, np.int32)
        self.c2 = np.zeros((K, E), np.float32)          # sum over days of (day total)^2: days, not signals, are the
        self.cn = np.zeros((K, E), np.float32)          # independent units (signals of one day move together)
        self.n2 = np.zeros(K, np.float64)

    def add(self, s1, s2, w, n):
        self.s1 += s1
        self.s2 += s2
        self.c2 += s1 * s1
        self.cn += s1 * n[:, None]
        self.n2 += n * n
        if self.w is not None:
            self.w += w
            self.pos += ((s1 > 0) & (n[:, None] > 0)).astype(np.int16)
        self.n += n
        self.days += (n > 0).astype(np.int32)


def day_sums(F: np.ndarray, X: np.ndarray, valid: np.ndarray):
    Fv = F * valid[:, None].astype(np.float32)
    Xv = np.where(valid[:, None], X, 0).astype(np.float32)
    return Fv.T @ Xv, Fv.T @ (Xv * Xv), Fv.T @ (Xv > 0).astype(np.float32), Fv.sum(axis=0)


T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23, 12: 2.18, 15: 2.13,
        20: 2.09, 30: 2.04, 60: 2.00}


def t_crit(df: np.ndarray, z: float) -> np.ndarray:
    """Student's t (two-sided 95%) for `df` days of freedom, scaled so a large sample gives z: with 2 days the bar is
    12.7 standard errors, with 10 days 2.2. Few days can't prove much, whatever the average says."""
    ks = np.array(sorted(T975))
    vs = np.array([T975[k] for k in ks])
    d = np.clip(df, 1, None)
    i = np.searchsorted(ks, d, side="right") - 1
    t = np.where(d > ks[-1], 1.96, vs[np.clip(i, 0, len(ks) - 1)])
    return t * (z / 1.96)


def lcb_table(acc: Acc, z: float, min_n: int, min_days: int):
    """Lower confidence bound of the average net %: mean - z x standard error, the error the larger of the per-signal
    one and the per-day (cluster) one, so 37 signals from one strong morning count as about one day, not 37 trades.
    Needs 2+ days with trades (one day can't show its own spread); the day error uses Student's t for that many days."""
    n = acc.n[:, None]
    G = acc.days[:, None].astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = acc.s1 / n
        var = np.maximum(acc.s2 / n - mean * mean, 0) * (n / np.maximum(n - 1, 1))
        se_iid = np.sqrt(var / n)
        cl = np.maximum(acc.c2 - 2 * mean * acc.cn + mean * mean * acc.n2[:, None], 0)
        se_cl = np.sqrt(cl * G / np.maximum(G - 1, 1)) / n
        lcb = mean - np.maximum(z * se_iid, t_crit(G - 1, z) * se_cl)
    ok = (n >= min_n) & (G >= max(2, min_days))
    return np.where(ok, lcb, -np.inf), mean


def top_of(accs: dict, z, min_n, min_days, k=1, keep=None):
    """The k best (chunk, filter, exit) by lower confidence bound across all chunks. keep(chunk, fi) -> bool."""
    cands = []
    for ck, acc in accs.items():
        lcb, mean = lcb_table(acc, z, min_n, min_days)
        if keep is not None:
            rows = np.array([keep(ck, fi) for fi in range(lcb.shape[0])])
            lcb = np.where(rows[:, None], lcb, -np.inf)
        flat = lcb.ravel()
        if not np.isfinite(flat).any():
            continue
        m = min(k, int(np.isfinite(flat).sum()))
        idx = np.argpartition(-flat, m - 1)[:m]
        for i in idx:
            fi, ei = divmod(int(i), lcb.shape[1])
            cands.append((float(flat[i]), ck, fi, ei, float(mean[fi, ei])))
    cands.sort(key=lambda x: -x[0])
    return cands[:k]


def run(data_dir, log=print, should_stop=lambda: False, days: list | None = None, c: dict | None = None) -> dict:
    t_start = time.time()
    data_dir = Path(data_dir)
    c = c or cfg()
    source = c.get("source") or "upstox"
    days = days or days_available(data_dir, source)
    errors: dict = {}
    if not days:
        res = {"made": time.time(), "days": [], "note": "No recorded day with test-bench signals yet: the multiverse grows "
                                                         "from tomorrow's recording (needs a full market day).", "errors": errors}
        _save(data_dir, res)
        return res
    from .rules import costs
    exits = exit_specs(c)
    E = len(exits)
    strikes = list(c["strikes"])
    delays = [int(x) for x in c["delays"]]
    chunks = [(s, di, d) for s in strikes for di, d in enumerate(delays)]
    z, min_n, min_days = float(c.get("lcb_z", 2.0)), int(c.get("min_trades", 30)), int(c.get("min_days", 1))
    # pass 0: which alert types have enough signals to get their own filters
    log(f"Multiverse: {len(days)} recorded days ({days[0]} to {days[-1]})")
    loaded = {}
    kind_n: dict = {}
    for day in days:
        if should_stop():
            break
        D = load_day(data_dir, day, c, source)
        if D is None:
            continue
        loaded[day] = D
        for ks in D["kinds"]:
            for k in ks:
                kind_n[k] = kind_n.get(k, 0) + 1
    days = [d for d in days if d in loaded]
    if not days:
        res = {"made": time.time(), "days": [], "note": "The recorded days had no signals with option prices.", "errors": errors}
        _save(data_dir, res)
        return res
    kinds = sorted([k for k, v in kind_n.items() if v >= int(c.get("kinds_min", 60))], key=lambda k: -kind_n[k])
    singles = c["singles"]
    # the single masks are per day; check them once on the first day so a broken one is listed and skipped everywhere
    good = single_masks(loaded[days[0]]["facts"], loaded[days[0]]["regime"], singles, errors)
    singles = [sp for sp, _m in good]
    defs = build_filters(kinds, singles)
    champ = c.get("champion")
    if champ:
        defs.append({"champion": True})
    K = len(defs)
    n_timelines = K * E * len(chunks)
    log(f"Multiverse: {sum(D['n'] for D in loaded.values()):,} signals x {K:,} filters x {E} exits x {len(strikes)} strikes "
        f"x {len(delays)} entry times = {n_timelines:,} timelines")
    accs = {ck: Acc(K, E) for ck in chunks}
    luck = {ck: Acc(K, E, light=True) for ck in chunks}
    rng = np.random.default_rng(7)
    champ_fi = K - 1 if champ else None
    champ_chunk = (champ.get("strike", "atm"), delays.index(int(champ.get("delay", 0))), int(champ.get("delay", 0))) if champ else None
    champ_ei = None
    if champ:
        want = ("tsh", float(champ["exit"][0]), None, float(champ["exit"][1]), float(champ["exit"][2]))
        for i, x in enumerate(exits):
            if x[0] == "tsh" and x[1] == want[1] and x[3] == want[3] and x[4] == want[4]:
                champ_ei = i
    walk = []
    port = int(c.get("portfolio", 5))
    bcfg = c.get("brain") or {}                            # 2.63: what the brain learns from (one fixed way to trade)
    bx = bcfg.get("exit") or [30, 20, 60]
    b_ei = next((i for i, x in enumerate(exits) if x[0] == "tsh" and x[1] == bx[0] and x[3] == bx[1] and x[4] == bx[2]), None)
    brain_days = []
    for day in days:
        if should_stop():
            break
        D = loaded[day]
        smask = single_masks(D["facts"], D["regime"], singles, {})
        F = filter_matrix(D, defs, smask, champ)
        rates = costs(day)
        perm = rng.permutation(D["n"])
        # walk-forward: pick from the days before (never this one), then see what this day did
        picks = top_of(accs, z, min_n, min_days, k=port) if any(a.n.max() > 0 for a in accs.values()) else []
        today = {}
        for ck in chunks:
            strike, di, d = ck
            R, j_end, valid, lv = chunk_paths(D, strike, di, d)
            X = net_pct(outcomes(R, j_end, exits), lv, rates)
            s1, s2, w, n = day_sums(F, X, valid)
            today[ck] = (s1, n)
            accs[ck].add(s1, s2, w, n)
            if b_ei is not None and ck[0] == "atm" and ck[2] == 0:
                brain_days.append({"day": day, "facts": D["facts"], "kinds": D["kinds"], "regime": list(D["regime"]),
                                   "y": X[:, b_ei].astype(float), "valid": valid.copy()})
            ls1, ls2, lw, ln = day_sums(F, X[perm], valid[perm])     # the luck line: results no longer tied to facts
            luck[ck].add(ls1, ls2, lw, ln)
        if picks:
            rec = {"day": day, "picks": []}
            tot, nn = 0.0, 0.0
            for lcb, ck, fi, ei, mean in picks:
                s1, n = today[ck]
                rec["picks"].append({"chunk": list(ck[::2]), "filter": fi, "exit": ei, "train_lcb": round(lcb, 2),
                                     "train_avg": round(mean, 2), "n": int(n[fi]), "sum": round(float(s1[fi, ei]), 2)})
                tot += float(s1[fi, ei])
                nn += float(n[fi])
            rec["best_n"] = rec["picks"][0]["n"]
            rec["best_avg"] = round(rec["picks"][0]["sum"] / rec["picks"][0]["n"], 2) if rec["picks"][0]["n"] else None
            rec["port_n"] = int(nn)
            rec["port_avg"] = round(tot / nn, 2) if nn else None
            if champ_ei is not None:
                s1, n = today[champ_chunk]
                rec["champ_n"] = int(n[champ_fi])
                rec["champ_avg"] = round(float(s1[champ_fi, champ_ei]) / n[champ_fi], 2) if n[champ_fi] else None
            walk.append(rec)
        log(f"Multiverse: {day} done ({D['n']:,} signals)")
    # the final board: every day so far
    best = _distinct(top_of(accs, z, min_n, min_days, k=int(c.get("top", 30)) * 40), int(c.get("top", 30)), by_idea=True)
    luck_best = top_of(luck, z, min_n, min_days, k=1)
    res = {"made": time.time(), "seconds": None, "days": days, "signals": int(sum(D["n"] for D in loaded.values())),
           "filters": K, "exits": E, "strikes": strikes, "delays": delays, "timelines": n_timelines,
           "kinds": [{"kind": k, "label": kind_label(k), "n": kind_n[k]} for k in kinds],
           "errors": errors, "z": z, "min_trades": min_n}

    def describe(ck, fi, ei):
        strike, di, d = ck
        f = defs[fi]
        return {"strike": strike, "delay": d, "filter": _fdef(f, singles), "exit": list(exits[ei]),
                "words": (f"Buy {c['strike_words'].get(strike, strike)} on {filter_words(f, singles)}, "
                          f"{'at once' if d == 0 else f'{d} min later at the ask then'}; exit {exit_words(exits[ei])}")}

    def stats(acc: Acc, fi, ei):
        n = acc.n[fi]
        mean = acc.s1[fi, ei] / n if n else None
        var = max(acc.s2[fi, ei] / n - mean * mean, 0) * n / max(n - 1, 1) if n else None
        lcb, _m = lcb_table(acc, z, 1, 2)
        lv = float(lcb[fi, ei])
        return {"n": int(n), "avg": round(float(mean), 2) if n else None, "sd": round(math.sqrt(var), 2) if n else None,
                "win": round(float(acc.w[fi, ei] / n * 100), 1) if n and acc.w is not None else None,
                "lcb": round(lv, 2) if n and math.isfinite(lv) else None,
                "green_days": int(acc.pos[fi, ei]) if acc.pos is not None else None, "days": int(acc.days[fi])}
    res["best"] = [{**describe(ck, fi, ei), **stats(accs[ck], fi, ei)} for _l, ck, fi, ei, _m in best]
    res["luck"] = {**describe(*luck_best[0][1:4]), **stats(luck[luck_best[0][1]], luck_best[0][2], luck_best[0][3])} if luck_best else None
    if champ_ei is not None:
        res["champion"] = {"name": champ.get("name"), **describe(champ_chunk, champ_fi, champ_ei),
                           **stats(accs[champ_chunk], champ_fi, champ_ei)}
    # the best per day type (regime filters) and per alert type
    by_regime = []
    for si, sp in enumerate(singles):
        if sp.get("cat") != "regime":
            continue
        t = top_of(accs, z, min_n, min_days, k=1, keep=lambda ck, fi, si=si: defs[fi].get("single") == si)
        if t:
            _l, ck, fi, ei, _m = t[0]
            by_regime.append({"regime": sp["is"], "name": sp["name"], **describe(ck, fi, ei), **stats(accs[ck], fi, ei)})
        else:
            by_regime.append({"regime": sp["is"], "name": sp["name"], "n": 0})
    res["by_regime"] = by_regime
    # walk-forward, in words
    for w in walk:
        for p in w["picks"]:
            ck = (p["chunk"][0], delays.index(p["chunk"][1]), p["chunk"][1])
            p.update(describe(ck, p["filter"], p["exit"]))
    res["walk"] = walk
    oos = [w for w in walk if w.get("best_n")]
    res["walk_total"] = {
        "days": len(walk),
        "best_n": int(sum(w["best_n"] for w in walk)),
        "best_avg": round(sum(w["best_avg"] * w["best_n"] for w in oos) / max(1, sum(w["best_n"] for w in oos)), 2) if oos else None,
        "port_n": int(sum(w["port_n"] for w in walk)),
        "port_avg": round(sum((w["port_avg"] or 0) * w["port_n"] for w in walk) / max(1, sum(w["port_n"] for w in walk)), 2)
        if any(w["port_n"] for w in walk) else None,
        "champ_n": int(sum(w.get("champ_n") or 0 for w in walk)),
        "champ_avg": round(sum((w.get("champ_avg") or 0) * (w.get("champ_n") or 0) for w in walk)
                           / max(1, sum(w.get("champ_n") or 0 for w in walk)), 2) if any(w.get("champ_n") for w in walk) else None}
    # live picks for the "Multiverse pick" paper account: delay 0, strikes the paper account can buy, lower bound > 0
    live_strikes = set(c.get("live_strikes") or ["atm"])
    lp = _distinct(top_of(accs, z, min_n, max(min_days, 1), k=int(c.get("live_picks", 5)) * 20,
                          keep=lambda ck, fi: ck[2] == 0 and ck[0] in live_strikes and not defs[fi].get("champion")),
                   int(c.get("live_picks", 5)), by_filter=True)
    res["live_picks"] = [{**describe(ck, fi, ei), **stats(accs[ck], fi, ei)} for lcb, ck, fi, ei, _m in lp if lcb > 0]
    # 2.61 the verdict table: for every filter, its best way (strike, entry time, exit) and record, so a live alert can be
    # told "signals like this one did X" (live/engine.py _mv_verdict; information only)
    table = []
    best_fi = {}
    for ck, acc in accs.items():
        lcb, mean = lcb_table(acc, z, min_n, min_days)
        e_best = np.argmax(lcb, axis=1)
        v_best = lcb[np.arange(lcb.shape[0]), e_best]
        for fi in np.nonzero(np.isfinite(v_best))[0]:
            if defs[fi].get("champion"):
                continue
            if fi not in best_fi or v_best[fi] > best_fi[fi][0]:
                best_fi[fi] = (float(v_best[fi]), ck, int(e_best[fi]))
    for fi, (lv, ck, ei) in best_fi.items():
        d_ = describe(ck, fi, ei)
        st_ = stats(accs[ck], fi, ei)
        table.append({"f": d_["filter"], "strike": d_["strike"], "delay": d_["delay"], "exit": d_["exit"],
                      "how": f"buy {c['strike_words'].get(ck[0], ck[0]).replace('the ', '', 1)}, "
                             f"{'at once' if ck[2] == 0 else f'{ck[2]} min later'}, exit {exit_words(exits[ei])}",
                      "n": st_["n"], "avg": st_["avg"], "lcb": st_["lcb"], "win": st_["win"], "days": st_["days"]})
    res["table"] = table
    if bcfg.get("on", True) and brain_days:                # 2.63: the learned model, judged on unseen days
        try:
            from . import mvbrain
            res["brain"] = mvbrain.learn(brain_days, data_dir, c, log=log)
        except Exception as e:
            res["brain"] = {"error": str(e)[:160]}
    res["seconds"] = round(time.time() - t_start, 1)
    _save(data_dir, res)
    log(f"Multiverse: {n_timelines:,} timelines judged in {res['seconds']}s; best lower bound "
        f"{res['best'][0]['lcb'] if res['best'] else '–'}% vs luck {res['luck']['lcb'] if res['luck'] else '–'}%; "
        f"{len(res['live_picks'])} live pick(s)")
    return res


def _distinct(cands: list, k: int, by_filter: bool = False, by_idea: bool = False) -> list:
    """Drop look-alikes: the same signals with an exit that never fired (a -40% stop no trade reached) give the same
    trades and result; keep the best of each. by_filter: one exit per (strike, entry time, filter); by_idea: one row
    per (strike, filter), whatever the entry time and exit (the board's list of different ideas)."""
    seen, out = set(), []
    for c_ in cands:
        _l, ck, fi, ei, mean = c_
        key = (ck[0], fi) if by_idea else (ck, fi) if by_filter else (ck, fi, round(mean, 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(c_)
        if len(out) >= k:
            break
    return out


def _fdef(f: dict, singles: list) -> dict:
    if f.get("champion"):
        return {"champion": True}
    out = {"kind": f.get("kind")}
    if f.get("single") is not None:
        out["single"] = singles[f["single"]]
    return out


def _save(data_dir, res: dict):
    p = Path(data_dir) / OUT_NAME
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(p)


def view(data_dir) -> dict:
    p = Path(data_dir) / OUT_NAME
    if not p.exists():
        return {"days": [], "note": "Not run yet: it runs after the market closes (after the trade journeys), or press Run."}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {"days": [], "note": f"Could not read the last result: {e}"}


# ------------------------------------------------------------------ live: does an alert match a pick?
def matches(fdef: dict, kind: str, facts: dict) -> bool:
    """The same filter test the search used, on a live alert's facts (live/engine.py signal_facts + its tag)."""
    if fdef.get("champion"):
        return False
    if fdef.get("kind") and fdef["kind"] not in (kind if isinstance(kind, (list, tuple, set)) else [kind]):
        return False
    sp = fdef.get("single")
    if not sp:
        return True
    if "cat" in sp:
        return str(facts.get(sp["cat"]) or "") == sp["is"]
    x = facts.get(sp["fact"])
    if not isinstance(x, (int, float)) or x is None or not math.isfinite(float(x)):
        return False
    return float(x) > float(sp["v"]) if sp["op"] == ">" else float(x) < float(sp["v"])


_VT: dict = {}


def verdict(data_dir, kind, facts: dict) -> dict | None:
    """2.61: what the multiverse says about a live signal like this one: the best-proven filter it matches (its way to
    trade it and its record on the recorded days) and the record of its alert type as a whole. Information only."""
    p = Path(data_dir) / OUT_NAME
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _VT.get("mt") != mt or _VT.get("p") != str(p):
        try:
            r = json.loads(p.read_text())
            _VT.update(mt=mt, p=str(p), table=r.get("table") or [], days=len(r.get("days") or []))
        except Exception:
            _VT.update(mt=mt, p=str(p), table=[], days=0)
    table = _VT.get("table") or []
    if not table:
        return None
    kinds = kind if isinstance(kind, (list, tuple, set)) else [kind]
    hits = [t for t in table if matches(t["f"], kinds, facts) and t.get("lcb") is not None]
    if not hits:
        return None
    best = max(hits, key=lambda t: t["lcb"])
    base = next((t for t in table if (t["f"].get("kind") in kinds) and not t["f"].get("single")), None) or \
        next((t for t in table if not t["f"].get("kind") and not t["f"].get("single") and not t["f"].get("champion")), None)
    word = lambda t: (f"{kind_label(t['f']['kind'])} signals" if t["f"].get("kind") else "all signals") + \
        (f", {t['f']['single']['name']}" if t["f"].get("single") else "")
    return {"days": _VT.get("days"), "n_match": len(hits),
            "best": {"like": word(best), "how": best["how"], "n": best["n"], "avg": best["avg"], "lcb": best["lcb"],
                     "win": best.get("win"), "strike": best["strike"], "delay": best["delay"]},
            "base": {"like": word(base), "n": base["n"], "avg": base["avg"], "lcb": base["lcb"]} if base else None,
            "proven": bool(best["lcb"] > 0)}
