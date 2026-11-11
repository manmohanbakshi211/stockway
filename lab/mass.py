"""Mass search (2.60): every single and paired condition x time window x direction x exit on the Lab's bar table at
once, then month-by-month walk-forward on the months the search never saw.

Mandeep, 11 Nov 2026: "100 magzine, 1000 magzine ... gun itni smart ho ki saari mag khali karke best bullet ka pata
laga le" and "you have to prove you best".

Gun (knows no strategy): the bar table (lab/factory.py: every 5-minute bar of the index option history, the facts known
at its close, the result of buying the ATM call / put a minute later under each exit, per lot, after charges) is laid
out as one block per day x index x direction with 64 bars (09:30 to 14:45). A condition is a yes/no block; a rule fires
on its first yes-bar inside its time window (the Lab's own "1 trade a day" rule), so a whole rule is one AND and one
argmax over the block: every single condition (each fact x 3 thresholds x above / below) and every pair of the best
singles with all the others is scored in minutes, not days.

The judge (honest, like the multiverse):
- the search sees only TRAIN days (the Lab's first 60%); candidates are ranked by the lower confidence bound;
- walk-forward on the later months: each month's pick comes only from the months before it, then is scored on that
  month; next to it the plain "buy every day at the window's first bar" line with the same exit;
- the luck line: the same search with each day's results shuffled between the days;
- the best candidates go to the Lab as ideas (source "mass search"), where the usual train / validation / holdout exams
  and the promotion bar decide, exactly as for every other rule.
Bullets: rules/mass.json (thresholds, windows, how many singles get pairs, how many ideas go to the Lab).
Market data only: nothing here can place an order. Runs in the Lab's low-priority child process.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / "rules" / "mass.json"
OUT = "mass.json"
NB = 64                         # 5-minute bars 09:30 (tod 15) .. 14:45 (tod 330)


def cfg() -> dict:
    return json.loads(CFG_FILE.read_text())


def _bar(tod):
    return np.clip(np.round((np.asarray(tod, float) - 15) / 5).astype(int), 0, NB - 1)


def layout(df, feats: list, exits: list, syms: list):
    """(G, 64) blocks per fact and per exit; group = day x symbol x direction. Rows outside the indices are left out."""
    import pandas as pd
    d = df[df["sym"].isin(syms) & df["dir"].isin([1, -1]) & (df["tod"] >= 15) & (df["tod"] <= 330)]
    keys = d[["date", "sym", "dir"]].astype(str).agg("|".join, axis=1)
    codes, uniq = pd.factorize(keys, sort=True)
    G = len(uniq)
    b = _bar(d["tod"].to_numpy())
    blocks = {}
    for f in feats:
        a = np.full((G, NB), np.nan, np.float32)
        a[codes, b] = d[f].to_numpy(np.float32)
        blocks[f] = a
    pnl = {}
    for e in exits:
        a = np.full((G, NB), np.nan, np.float32)
        a[codes, b] = d["pnl_" + e].to_numpy(np.float32)
        pnl[e] = a
    parts = [u.split("|") for u in uniq]
    gdate = np.array([p[0] for p in parts])
    gsym = np.array([p[1] for p in parts])
    gdir = np.array([int(p[2]) for p in parts])
    return blocks, pnl, gdate, gsym, gdir


def conditions(blocks: dict, train: np.ndarray, qs: list) -> list:
    """[(feat, op, value)]: each fact at train quantiles qs, above and below."""
    out = []
    for f, a in blocks.items():
        x = a[train].ravel()
        x = x[np.isfinite(x)]
        if len(x) < 500 or np.nanstd(x) == 0:
            continue
        for v in np.unique(np.round(np.quantile(x, qs), 4)):
            out.append((f, ">", float(v)))
            out.append((f, "<", float(v)))
    return out


def cmask(blocks, c):
    f, op, v = c
    a = blocks[f]
    with np.errstate(invalid="ignore"):
        return (a > v) if op == ">" else (a < v)


def fire(mask: np.ndarray, w: tuple):
    """(fired groups, bar index) of the first yes-bar inside window w (tod from, tod to)."""
    b0, b1 = _bar(w[0]), _bar(w[1]) + 1
    sub = mask[:, b0:b1]
    any_ = sub.any(axis=1)
    return any_, sub.argmax(axis=1) + b0


def stats_of(v: np.ndarray, z: float, min_n: int) -> tuple:
    v = v[np.isfinite(v)]
    n = len(v)
    if n < min_n:
        return n, None, None
    m = float(v.mean())
    sd = float(v.std(ddof=1)) if n > 1 else 0.0
    return n, m, m - z * sd / math.sqrt(n)


def run(data_dir, log=print, should_stop=lambda: False, c: dict | None = None, df=None) -> dict:
    t0 = time.time()
    data_dir = Path(data_dir)
    c = c or cfg()
    from . import factory as F
    if df is None:
        df = F.load_or_build(data_dir, log=log, should_stop=should_stop)
    if df is None or not len(df):
        res = {"made": time.time(), "note": "No bar table yet (download the option history first)."}
        _save(data_dir, res)
        return res
    syms = [s for s in c.get("syms") or ["NIFTY", "BANKNIFTY"] if s in set(df["sym"].unique())]
    feats = [f for f in F.FEATURES if f in df.columns and f not in set(c.get("skip_facts") or [])]
    exits = [e for e in (c.get("exits") or []) if "pnl_" + e in df.columns] or \
            [col[4:] for col in df.columns if col.startswith("pnl_") and "@" not in col]
    blocks, pnl, gdate, gsym, gdir = layout(df, feats, exits, syms)
    dates = np.array(sorted(set(gdate)))
    cut = dates[int(len(dates) * float(c.get("train_share", 0.6))) - 1]
    train = gdate <= cut
    z, min_n = float(c.get("lcb_z", 2.0)), int(c.get("min_trades", 30))
    wins = [tuple(w) for w in c["windows"]]
    scopes = [(d, tuple(s)) for d in (1, -1) for s in ([[x] for x in syms] + ([syms] if len(syms) > 1 else []))]
    conds = conditions(blocks, train, c["quantiles"])
    log(f"Mass search: {len(dates)} days ({len(dates[dates <= cut])} train), {len(syms)} indices, {len(conds)} conditions, "
        f"{len(wins)} windows, {len(exits)} exits")
    rng = np.random.default_rng(11)
    perm = np.arange(len(gdate))
    for d_ in (1, -1):                                     # luck: each train day's prices swapped with another train
        for s_ in syms:                                    # day's (same index, same direction); the facts stay put
            idx = np.nonzero(train & (gdir == d_) & (gsym == s_))[0]
            perm[idx] = rng.permutation(idx)
    P = np.stack([pnl[e] for e in exits], axis=2)          # (G, 64, E)
    scope_sel = [(sc, (gdir == sc[0]) & np.isin(gsym, sc[1])) for sc in scopes]

    def score(mask):
        """Best (lcb, scope, window, exit, n, mean) on train, and the same on shuffled results."""
        best, luck = None, None
        for w in wins:
            any_, first = fire(mask, w)
            vals = P[np.arange(len(gdate)), first, :]       # (G, E)
            vals[~any_] = np.nan
            lvals = P[perm, first, :]                       # the same entry bar, another day's prices
            lvals[~any_] = np.nan
            lany = any_
            for sc, sel in scope_sel:
                s = sel & train & any_
                n = int(s.sum())
                if n < min_n:
                    continue
                v = vals[s]
                m = np.nanmean(v, axis=0)
                sd = np.nanstd(v, axis=0, ddof=1)
                cnt = np.isfinite(v).sum(axis=0)
                lcb = m - z * sd / np.sqrt(np.maximum(cnt, 1))
                lcb[cnt < min_n] = -np.inf
                k = int(np.argmax(lcb))
                if best is None or lcb[k] > best[0]:
                    best = (float(lcb[k]), sc, w, exits[k], int(cnt[k]), float(m[k]))
                ls = sel & train & lany
                if int(ls.sum()) >= min_n:
                    lv = lvals[ls]
                    lm = np.nanmean(lv, axis=0)
                    lsd = np.nanstd(lv, axis=0, ddof=1)
                    lc = np.isfinite(lv).sum(axis=0)
                    ll = lm - z * lsd / np.sqrt(np.maximum(lc, 1))
                    ll[lc < min_n] = -np.inf
                    if luck is None or ll.max() > luck:
                        luck = float(ll.max())
        return best, luck
    singles = []
    luck_best = -np.inf
    masks = {}                                             # only the singles that get pairs are kept (memory)
    for i, cnd in enumerate(conds):
        if should_stop():
            break
        m = cmask(blocks, cnd)
        b, lk = score(m)
        if lk is not None:
            luck_best = max(luck_best, lk)
        if b:
            singles.append(((cnd,), b))
    singles.sort(key=lambda x: -x[1][0])
    log(f"Mass search: {len(conds)} single conditions scored ({time.time() - t0:.0f}s)")
    pairs = []
    top = [s[0][0] for s in singles[:int(c.get("pair_top", 60))]]
    for a in top:
        masks[a] = cmask(blocks, a)
    n_pairs = 0
    for a in top:
        if should_stop():
            break
        ma = masks[a]
        for bcond in conds:
            if bcond[0] == a[0]:
                continue
            n_pairs += 1
            b, lk = score(ma & (masks[bcond] if bcond in masks else cmask(blocks, bcond)))
            if lk is not None:
                luck_best = max(luck_best, lk)
            if b:
                pairs.append(((a, bcond), b))
    log(f"Mass search: {n_pairs:,} pairs scored ({time.time() - t0:.0f}s)")
    allc = sorted(singles + pairs, key=lambda x: -x[1][0])
    seen, cands = set(), []
    for conds_, b in allc:
        key = (tuple(sorted(conds_)), b[1], b[2])
        if key in seen:
            continue
        seen.add(key)
        cands.append((conds_, b))
        if len(cands) >= int(c.get("keep", 300)):
            break
    n_tested = (len(conds) + n_pairs) * len(wins) * len(scopes) * len(exits)
    # walk-forward on the months after train: each month's pick from everything before it, scored on that month
    month = np.array([d[:7] for d in gdate])
    later = sorted(set(month[~train]))
    series = []
    for conds_, b in cands:
        m = np.ones_like(blocks[feats[0]], bool)
        for cnd in conds_:
            m = m & masks[cnd] if cnd in masks else m & cmask(blocks, cnd)
        any_, first = fire(m, b[2])
        e = exits.index(b[3])
        v = P[np.arange(len(gdate)), first, e]
        sc = (gdir == b[1][0]) & np.isin(gsym, b[1][1]) & any_
        series.append(np.where(sc, v, np.nan))
    S = np.array(series) if series else np.zeros((0, len(gdate)))
    walk = []
    for mo in later:
        past = month < mo
        now_ = month == mo
        best_i, best_l = None, -np.inf
        for i in range(len(S)):
            n, mean, l = stats_of(S[i][past], z, min_n)
            if l is not None and l > best_l:
                best_i, best_l = i, l
        if best_i is None:
            continue
        v = S[best_i][now_]
        v = v[np.isfinite(v)]
        conds_, b = cands[best_i]
        base = _baseline(P, gdir, gsym, b, exits, now_)
        walk.append({"month": mo, "rule": _words(conds_, b, F), "n": int(len(v)), "total": round(float(v.sum()), 0),
                     "avg": round(float(v.mean()), 1) if len(v) else None, "base_avg": base[0], "base_n": base[1]})
    best = []
    later_mask = ~train
    for i, (conds_, b) in enumerate(cands[:int(c.get("show", 30))]):
        n2, m2, _l2 = stats_of(S[i][later_mask], z, 1) if len(S) else (0, None, None)
        best.append({"rule": _words(conds_, b, F), "train_n": b[4], "train_avg": round(b[5], 1), "train_lcb": round(b[0], 1),
                     "later_n": n2, "later_avg": round(m2, 1) if m2 is not None else None,
                     "dir": b[1][0], "syms": list(b[1][1]), "win": list(b[2]), "exit": b[3],
                     "conds": [list(x) for x in conds_]})
    tot_n = sum(w["n"] for w in walk)
    res = {"made": time.time(), "secs": round(time.time() - t0, 1), "data": str(Path(data_dir).name),
           "days": len(dates), "train_to": str(cut), "later_months": later, "conditions": len(conds), "pairs": n_pairs,
           "windows": len(wins), "scopes": len(scopes), "exits": exits, "tested": int(n_tested),
           "luck_lcb": round(float(luck_best), 1) if np.isfinite(luck_best) else None,
           "best": best, "walk": walk,
           "walk_total": {"months": len(walk), "n": tot_n,
                          "total": round(sum(w["total"] for w in walk), 0),
                          "avg": round(sum(w["total"] for w in walk) / tot_n, 1) if tot_n else None,
                          "base_total": round(sum((w["base_avg"] or 0) * w["base_n"] for w in walk), 0),
                          "base_n": sum(w["base_n"] for w in walk)}}
    ideas = []
    for conds_, b in cands[:int(c.get("to_lab", 40))]:
        if b[0] <= 0:
            continue
        ideas.append({"dir": b[1][0], "conds": [list(x) for x in conds_], "win": list(b[2]), "exit": b[3], "max_day": 1,
                      "syms": list(b[1][1]), "source": "mass search", "note": f"train lower bound ₹{b[0]:.0f} a trade"})
    res["to_lab"] = len(ideas)
    if ideas and c.get("queue_to_lab", True):
        try:
            F.queue_untested(data_dir, ideas, "mass search")
        except Exception as e:
            res["queue_error"] = str(e)[:160]
    _save(data_dir, res)
    log(f"Mass search: {n_tested:,} rules screened in {res['secs']}s; best train lower bound "
        f"{best[0]['train_lcb'] if best else '–'} vs luck {res['luck_lcb']}; walk-forward {len(walk)} months, "
        f"avg ₹{res['walk_total']['avg']} a trade; {len(ideas)} ideas to the Lab exams")
    return res


def _baseline(P, gdir, gsym, b, exits, now_):
    """Buy every day at the window's first bar, same direction / indices / exit: what the rule must beat."""
    b0 = _bar(b[2][0])
    v = P[:, b0, exits.index(b[3])]
    sel = now_ & (gdir == b[1][0]) & np.isin(gsym, b[1][1]) & np.isfinite(v)
    return (round(float(v[sel].mean()), 1) if sel.any() else None), int(sel.sum())


def _words(conds_, b, F) -> str:
    try:
        s = {"dir": b[1][0], "conds": [list(x) for x in conds_], "win": list(b[2]), "exit": b[3], "max_day": 1,
             "syms": list(b[1][1])}
        return F.words(s)
    except Exception:
        return str(conds_)


def _lab_dir(data_dir) -> Path:
    return Path(data_dir) / "lab"


def _save(data_dir, res):
    p = _lab_dir(data_dir) / OUT
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(p)


def view(data_dir) -> dict:
    p = _lab_dir(data_dir) / OUT
    if not p.exists():
        return {"note": "Not run yet: Strategy Lab → Mass search → Run (it also runs after each nightly Lab round)."}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        return {"note": f"Could not read the last result: {e}"}
