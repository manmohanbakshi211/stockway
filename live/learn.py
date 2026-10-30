"""P5: market regime, and a model that learns which alerts actually work."""
from __future__ import annotations

import json
import time
from pathlib import Path

MIN_TRAIN = 120          # graded alerts needed before the model is trusted


def regime(m: dict, mins: float, marks: dict, vix_chg: float | None = None) -> dict:
    """Trend / range / two-way / quiet, from how far and how cleanly price travelled vs what options priced."""
    spot, o, h, l = m.get("spot"), m.get("day_o"), m.get("day_h"), m.get("day_l")
    dm = (m.get("opt") or {}).get("daily_move_pct")
    vw = m.get("vwap")
    if spot and vw:
        side = 1 if spot >= vw else -1
        if marks.get("vw_side") is not None and side != marks["vw_side"]:
            marks["vw_cross"] = marks.get("vw_cross", 0) + 1
        marks["vw_side"] = side
    out = {"label": "Opening", "detail": "First 30 minutes: let the opening range form.", "code": "opening"}
    if mins < 30 or not (spot and o and h and l and dm):
        return out
    rng = (h - l) / o * 100
    travel = (spot - o) / o * 100
    rr = rng / dm if dm else 0
    eff = abs(spot - o) / (h - l) if h > l else 0
    crosses = marks.get("vw_cross", 0)
    if eff >= 0.55 and rr >= 0.55 and crosses <= 3:
        code = "trend_up" if travel > 0 else "trend_down"
        label = "Trend up" if travel > 0 else "Trend down"
        detail = f"Clean one-way move: {travel:+.2f}%, {rr:.1f}× the priced daily move. Trade with the trend, buy dips."
    elif rr >= 1.1 and eff < 0.4:
        code, label = "volatile", "Volatile two-way"
        detail = f"Big swings both ways ({rr:.1f}× priced move, VWAP crossed {crosses}×). Smaller size, quick profits."
    elif rr < 0.5:
        code, label = "quiet", "Quiet range"
        detail = f"Only {rr:.1f}× the priced move. Option buyers bleed here; wait for a real breakout."
    else:
        code, label = "range", "Range"
        detail = f"Back-and-forth around VWAP ({crosses} crosses). Fade extremes, don't chase breakouts."
    if vix_chg is not None and vix_chg >= 5:
        detail += " VIX rising fast: expect wider swings."
    return {"label": label, "detail": detail, "code": code, "range_x": round(rr, 2), "efficiency": round(eff, 2),
            "vwap_crosses": crosses}


def regime_adjust(code: str | None, kind: str, direction: str) -> int:
    if not code:
        return 0
    if code in ("trend_up", "trend_down"):
        with_trend = (code == "trend_up") == (direction == "up")
        return 8 if with_trend else -10
    if code in ("range", "quiet") and kind in ("orb", "day_high", "day_low", "sharp_move", "volume_surge"):
        return -8
    return 0


def features(a: dict, m: dict | None, ev: list, reg: dict | None, mins: float, ctx: dict) -> dict:
    m = m or {}
    return {"kind": a["kind"], "severity": a["severity"], "is_index": int(bool(m.get("is_index"))),
            "direction": a["direction"], "agree": sum(e["agree"] for e in ev), "against": sum(not e["agree"] for e in ev),
            "rel_vol": min(10, m.get("rel_vol") or 0), "z5": min(10, abs(m.get("z_5m") or 0)),
            "minute_bucket": int(mins // 60), "regime": (reg or {}).get("code") or "na",
            "iv_hv": round(ctx.get("iv_hv") or 0, 2), "dte": (m.get("opt") or {}).get("days_to_expiry") or 0,
            "score_bias": (m.get("score") or 0) * (1 if a["direction"] == "up" else -1)}


class Ranker:
    """Logistic regression on graded alerts: P(hit) for a new alert. Retrained at the end of each day."""

    def __init__(self, data_dir: Path, source: str, log=print):
        self.path = Path(data_dir) / f"ranker_{source}.joblib"
        self.log = log
        self.model = None
        self.info = {"trained": False, "n": 0}
        if self.path.exists():
            try:
                import joblib
                self.model, self.info = joblib.load(self.path)
            except Exception:
                self.model = None

    def train(self, rows: list) -> dict:
        rows = [r for r in rows if r.get("result") and r.get("features")]
        if len(rows) < MIN_TRAIN:
            self.info = {"trained": False, "n": len(rows), "need": MIN_TRAIN}
            return self.info
        try:
            import joblib
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.model_selection import cross_val_score
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            X = [json.loads(r["features"]) for r in rows]
            y = [1 if r["result"] == "hit" else 0 for r in rows]
            if len(set(y)) < 2:
                self.info = {"trained": False, "n": len(rows), "note": "all outcomes the same"}
                return self.info
            pipe = make_pipeline(DictVectorizer(sparse=False), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
            cv = cross_val_score(pipe, X, y, cv=min(5, max(2, len(rows) // 40)), scoring="roc_auc")
            pipe.fit(X, y)
            self.model = pipe
            self.info = {"trained": True, "n": len(rows), "base_rate": round(sum(y) / len(y) * 100),
                         "auc": round(float(cv.mean()), 3), "when": time.time()}
            joblib.dump((self.model, self.info), self.path)
            self.log(f"Alert model retrained on {len(rows)} graded alerts (AUC {self.info['auc']})")
        except Exception as e:
            self.info = {"trained": False, "n": len(rows), "error": str(e)[:120]}
        return self.info

    def predict(self, feats: dict) -> float | None:
        if self.model is None or not self.info.get("trained") or (self.info.get("auc") or 0) < 0.55:
            return None
        try:
            return float(self.model.predict_proba([feats])[0][1])
        except Exception:
            return None
