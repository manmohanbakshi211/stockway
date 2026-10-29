"""Every chart pattern (live/patterns.py) replayed on years of 1-minute index history, with what happened next.

Patterns are read on 5-minute bars exactly as the live chart does. From the close of the confirmation bar, the
next 90 minutes of 1-minute prices decide: target first, stop first, or time out. Results are in R (multiples of
the risk to the stop), so they apply to any position size; costs are not included here (index points).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from live import patterns as P
from live.analog import Library, N

HOLD = 90


def replay(lib: Library, log=print) -> list:
    rows = []
    for di in range(1, len(lib.days)):
        C, Hh, Ll = lib.C[di], lib.H[di], lib.L[di]
        seg = lambda a, f: f(a[:375].reshape(75, 5), 1)
        c5 = C[4::5][:75]
        b = {"o": np.r_[C[0], c5[:-1]], "h": seg(Hh, np.max), "l": seg(Ll, np.min), "c": c5}
        pc = lib.C[di - 1]
        lv = [("yesterday's high", float(lib.H[di - 1].max())), ("yesterday's low", float(lib.L[di - 1].min())),
              ("yesterday's close", float(pc[-1]))]
        seen = set()
        for i in range(8, 72):
            bi = {k: v[:i + 1] for k, v in b.items()}
            for p in P.detect(bi, lv, recent=1):
                key = (p["pattern"], p["direction"], round(p["level"] or 0, 0))
                if key in seen:
                    continue
                seen.add(key)
                d = 1 if p["direction"] == "up" else -1
                m0 = 5 * i + 4
                entry, stop, tgt = p["entry"], p["stop"], p["target"]
                risk = abs(entry - stop)
                end = min(N - 1, m0 + HOLD)
                res, how, mx = None, "time", end
                for m in range(m0 + 1, end + 1):
                    if (d > 0 and Ll[m] <= stop) or (d < 0 and Hh[m] >= stop):
                        res, how, mx = -1.0, "stop", m
                        break
                    if (d > 0 and Hh[m] >= tgt) or (d < 0 and Ll[m] <= tgt):
                        res, how, mx = p["rr"], "target", m
                        break
                if res is None:
                    res = (C[end] - entry) * d / risk
                mfe = (max(Hh[m0 + 1:mx + 1]) - entry if d > 0 else entry - min(Ll[m0 + 1:mx + 1])) / risk if mx > m0 else 0
                rows.append({"sym": lib.sym, "date": lib.days[di].isoformat(), "minute": m0, "pattern": p["pattern"],
                             "sub": p["name"], "dir": d, "rr": p["rr"], "r": round(float(res), 3), "how": how,
                             "mfe_r": round(float(mfe), 2), "risk_pct": round(risk / entry * 100, 3)})
    return rows


def table(rows: list) -> list:
    import pandas as pd
    if not rows:
        return []
    df = pd.DataFrame(rows)
    out = []
    for (sym, pat, d), g in df.groupby(["sym", "pattern", "dir"]):
        r = g["r"]
        n = len(r)
        t = r.mean() / (r.std(ddof=1) / math.sqrt(n)) if n > 2 and r.std() > 0 else 0.0
        out.append({"sym": sym, "pattern": pat, "dir": int(d), "n": n, "avg_r": round(float(r.mean()), 3),
                    "win_pct": round(float((r > 0).mean() * 100), 1), "target_pct": round(float((g["how"] == "target").mean() * 100), 1),
                    "stop_pct": round(float((g["how"] == "stop").mean() * 100), 1), "t": round(float(t), 2),
                    "avg_rr": round(float(g["rr"].mean()), 2), "per_month": round(n / max(1, g["date"].str[:7].nunique()), 1)})
    return sorted(out, key=lambda x: -x["avg_r"])


def run(libs: dict, data_dir, log=print) -> dict:
    rows = []
    for sym, lib in libs.items():
        if lib is None or len(lib.days) < 30:
            continue
        t = time.time()
        rr = replay(lib, log)
        rows += rr
        log(f"Patterns: {sym} {len(lib.days)} days, {len(rr)} patterns ({time.time() - t:.0f}s)")
    rep = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": len(rows), "table": table(rows),
           "period": {s: [l.days[0].isoformat(), l.days[-1].isoformat()] for s, l in libs.items() if l and l.days}}
    d = Path(data_dir) / "lab"
    d.mkdir(parents=True, exist_ok=True)
    (d / "patterns.json").write_text(json.dumps(rep))
    return rep
