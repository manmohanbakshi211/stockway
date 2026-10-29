"""What history says about a live setup signal: the setup's record on 1-minute option data (lab/edge.py) and, if
it passed its out-of-sample test, the signal filter's score (lab/meta.py)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from lab.meta import FEATURES, SETUPS

HISTORY_SYMS = {"NIFTY", "BANKNIFTY"}


class EdgeBook:
    def __init__(self, data_dir):
        self.dir = Path(data_dir) / "lab"
        self.mtime = None
        self.bundle = None
        self.report = None
        self.load()

    def load(self):
        f = self.dir / "meta.json"
        if not f.exists():
            self.report, self.bundle, self.mtime = None, None, None
            return
        mt = f.stat().st_mtime
        if mt == self.mtime:
            return
        self.mtime = mt
        try:
            self.report = json.loads(f.read_text())
        except Exception:
            self.report = None
        try:
            import joblib
            self.bundle = joblib.load(self.dir / "meta.joblib") if (self.dir / "meta.joblib").exists() else None
        except Exception:
            self.bundle = None

    def record(self, sym: str, setup: str, direction: str) -> dict | None:
        if sym not in HISTORY_SYMS or not self.report:
            return None
        d = 1 if direction == "up" else -1
        for r in self.report.get("setups") or []:
            if r["sym"] == sym and r["setup"] == setup and int(r["dir"]) == d:
                return r
        return None

    def filter_ok(self) -> bool:
        return bool(self.bundle and self.bundle.get("ok"))

    def score(self, row: dict) -> float | None:
        if not self.filter_ok():
            return None
        try:
            import pandas as pd
            X = {f: row.get(f, math.nan) for f in FEATURES}
            X["is_bn"] = int(row.get("sym") == "BANKNIFTY")
            for s in SETUPS:
                X["s_" + s] = int(row.get("setup") == s)
            df = pd.DataFrame([X])[self.bundle["features"]].apply(pd.to_numeric, errors="coerce")
            return float(self.bundle["model"].predict_proba(df)[:, 1][0])
        except Exception:
            return None

    def verdict(self, sym, setup, direction, row: dict) -> dict:
        """{'line': text for the alert, 'grade': 'edge'|'weak'|'losing'|'unknown', 'p': score or None}"""
        r = self.record(sym, setup, direction)
        if not r:
            return {"line": None, "grade": "unknown", "p": None}
        n, win, avg, t = int(r["n"]), r["win_pct"], r["avg_rs"], r.get("t") or 0
        per = self.report.get("period") or {}
        span = f"{per.get('from', '')[:7]} to {per.get('to', '')[:7]}"
        line = (f"History ({span}): {n} such signals, {win:.0f}% won, avg {'+' if avg >= 0 else '−'}₹{abs(avg):,.0f}/lot "
                f"after costs (ATM option buy)")
        grade = "edge" if avg > 0 and t >= 2 else "losing" if avg < 0 and t <= -1 else "weak"
        p = self.score({**row, "sym": sym, "setup": setup})
        if p is not None:
            thr = self.bundle.get("threshold", 0.5)
            line += f"; filter score {p * 100:.0f}% ({'take' if p >= thr else 'skip'})"
            if p < thr and grade != "losing":
                grade = "weak"
        if grade == "losing":
            line += ". Historically losing: info only (no paper trade)."
        elif grade == "weak":
            line += ". No proven edge yet."
        return {"line": line, "grade": grade, "p": p}
