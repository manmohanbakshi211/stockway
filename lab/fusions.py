"""Fusions (2.46): two named strategies that must agree at the same bar, tested like any other rule.

Mandeep (6 Nov): "every trade should be tested with all the strategies and the fusions ... system should prepare the
list of all the fusion strategies like mixing the strategies".

Named strategies = the library / book setups (lab/library.py) and the strategy files (lab/packs.py), each as its entry
(direction, conditions, time window, symbols; exits aside). Mixing all of them with all of them would be tens of
thousands of rules, and with that many tries some pass by luck alone. So, honestly:

  1. each named strategy is ranked by its own result on TRAIN days only (its best exit), per direction;
  2. the best `TOP` of each direction are paired (same direction): conditions joined (at most 4), the time windows'
     overlap, the symbols both trade; a pair with no overlap or over 4 conditions is skipped;
  3. each fusion is tested with a few exits on train / validation / holdout like every rule, and every fusion tried is
     added to the Lab's "tried" count, so the luck number grows with them;
  4. fusions never go on live paper by themselves; promoted ones run on the test bench (kind "fusion") until you
     switch one on.

Validation and holdout days are never used to choose which strategies get paired.
"""
from __future__ import annotations

import itertools
import json

TOP = 24
EXITS = ("std", "atr2r", "be3r")
MIN_TRAIN_N = 30


def named_entries(data_dir) -> list:
    """Every named strategy's entry (one per direction / conditions / window / symbols), with a short name."""
    from . import library, packs
    out, seen = [], set()
    srcs = list(library.rules())
    try:
        srcs += packs.rules(data_dir)
    except Exception:
        pass
    for r in srcs:
        if not r.get("dir"):
            continue
        key = json.dumps([r["dir"], r["conds"], r.get("win"), sorted(r.get("syms") or [])], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        src = str(r.get("source", ""))
        name = src.split(":", 2)[-1] if src.startswith("pack:") else src.split(":", 1)[-1]
        out.append({"dir": int(r["dir"]), "conds": [list(c) for c in r["conds"]], "win": list(r.get("win") or [15, 330]),
                    "syms": [s for s in (r.get("syms") or []) if s != "STOCKS"], "syms_all": list(r.get("syms") or []),
                    "name": name[:60], "source": src})
    return out


def train_rank(data_dir, entries: list) -> list:
    """(train t, train n, entry) for entries the Lab has already tested, best exit by train t (train days only)."""
    from . import factory as F
    known = F.load(data_dir).get("rules", {})
    out = []
    for e in entries:
        best = None
        for ex in F.BUY_EXITS:
            v = F.validate({**e, "syms": e["syms_all"], "exit": ex, "max_day": 1, "source": e["source"],
                            "early": e["win"][0] < F.FIRST_ENTRY_TOD})
            r = known.get(v["id"]) if v else None
            tr = (r or {}).get("train") or {}
            if tr.get("n", 0) >= MIN_TRAIN_N and (best is None or (tr.get("t") or -9) > best[0]):
                best = (tr.get("t") or -9, tr.get("n"))
        if best is not None:
            out.append((best[0], best[1], e))
    out.sort(key=lambda x: -x[0])
    return out


def build(data_dir, top: int = TOP, exits=EXITS) -> list:
    """The fusion rules to test (Lab rule dicts, source "fusion:...")."""
    ranked = train_rank(data_dir, named_entries(data_dir))
    rules = []
    for d in (1, -1):
        best = [e for _t, _n, e in ranked if e["dir"] == d][:top]
        for a, b in itertools.combinations(best, 2):
            conds = []
            for c in a["conds"] + b["conds"]:
                if c not in conds:
                    conds.append(c)
            if len(conds) > 4:
                continue
            win = [max(a["win"][0], b["win"][0]), min(a["win"][1], b["win"][1])]
            syms = sorted(set(a["syms"]) & set(b["syms"]))
            if win[0] > win[1] or not syms or conds == a["conds"] or conds == b["conds"]:
                continue
            name = f"{a['name']} + {b['name']}"
            for ex in exits:
                rules.append({"dir": d, "conds": conds, "win": win, "exit": ex, "max_day": 1, "syms": syms,
                              "source": f"fusion:{name}"[:80], "note": f"Fusion: {a['name']} AND {b['name']} at the same bar."[:300],
                              "early": win[0] < 15})
    return rules


def queue(data_dir) -> dict:
    """Queue the fusions this Lab has not tested yet. Returns how many (they are added to the Lab's tried count)."""
    from . import factory as F
    rs = build(data_dir)
    if not rs:
        return {"queued": 0, "built": 0}
    q = F.queue_untested(data_dir, rs, source="fusion")
    q["built"] = len(rs)
    return q
