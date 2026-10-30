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


def _n(x, cap=None):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return max(-cap, min(cap, x)) if cap else x


def features(a: dict, m: dict | None, ev: list, reg: dict | None, mins: float, ctx: dict) -> dict:
    """Facts at the moment an alert fires, signed so that + means 'agrees with the alert's direction'.
    The stock itself, its options, the index, breadth, its sector, global markets, VIX and fresh news."""
    m = m or {}
    d = 1 if a["direction"] == "up" else -1 if a["direction"] == "down" else 0
    o, f, fl = m.get("opt") or {}, m.get("fut") or {}, m.get("flow") or {}
    mk = ctx.get("mkt") or {}
    nf, br = mk.get("nifty") or {}, mk.get("breadth") or {}
    opt = a.get("option") or {}
    sgn = lambda x: None if x is None else x * d
    ltp, bid, ask = opt.get("ltp"), opt.get("bid"), opt.get("ask")
    spread = (ask - bid) / ((ask + bid) / 2) * 100 if bid and ask and ask > bid > 0 else None
    out = {"kind": a["kind"], "severity": a["severity"], "is_index": int(bool(m.get("is_index"))),
           "direction": a["direction"], "agree": sum(e["agree"] for e in ev), "against": sum(not e["agree"] for e in ev),
           "rel_vol": min(10, m.get("rel_vol") or 0), "z5": min(10, abs(m.get("z_5m") or 0)),
           "minute_bucket": int(mins // 60), "regime": (reg or {}).get("code") or "na",
           "iv_hv": round(ctx.get("iv_hv") or 0, 2), "dte": (m.get("opt") or {}).get("days_to_expiry") or 0,
           "score_bias": (m.get("score") or 0) * (1 if a["direction"] == "up" else -1),
           # 2.17: the stock, aligned with the alert
           "mins": _n(mins), "chg_al": _n(sgn(m.get("chg_pct")), 15), "ret5_al": _n(sgn(m.get("ret_5m")), 5),
           "ret15_al": _n(sgn(m.get("ret_15m")), 8), "vwap_al": _n(sgn(m.get("vs_vwap_pct")), 8),
           "buildup_al": _n(sgn(f.get("buildup_sign"))), "flow_al": _n(sgn(fl.get("delta_pct_5m")), 100),
           "unusual": len(o.get("unusual") or []), "iv": _n(o.get("atm_iv"), 200), "iv_chg15": _n(o.get("iv_chg_15m"), 30),
           "pcr": _n(o.get("pcr"), 5), "iv_pct": _n(ctx.get("iv_pct")),
           # the option you would buy
           "cost_lot": _n(opt.get("cost_per_lot")), "opt_px": _n(ltp), "spread_pct": _n(spread, 50), "delta": _n(opt.get("delta")),
           # the market around it
           "nifty_al": _n(sgn(nf.get("chg_pct")), 5), "nifty15_al": _n(sgn(nf.get("ret_15m")), 3),
           "nifty_vwap_al": _n(sgn(nf.get("vs_vwap_pct")), 3),
           "breadth_al": _n(sgn((br.get("adv", 0) - br.get("dec", 0)) / br["n"]) if br.get("n") else None),
           "sector_al": _n(sgn(mk.get("sector_chg")), 8), "global_al": _n(sgn(mk.get("global")), 3),
           "vix_chg": _n(mk.get("vix_chg"), 30), "news_n": mk.get("news_n") or 0, "news_al": _n(sgn(mk.get("news_tone")), 3)}
    return out


NUM = ["severity", "is_index", "agree", "against", "rel_vol", "z5", "minute_bucket", "iv_hv", "dte", "score_bias", "mins",
       "chg_al", "ret5_al", "ret15_al", "vwap_al", "buildup_al", "flow_al", "unusual", "iv", "iv_chg15", "pcr", "iv_pct",
       "cost_lot", "opt_px", "spread_pct", "delta", "nifty_al", "nifty15_al", "nifty_vwap_al", "breadth_al", "sector_al",
       "global_al", "vix_chg", "news_n", "news_al"]
CAT = ["kind", "direction", "regime"]
TRUST_AUC = 0.60
MIN_DAYS = 5


def _frame(feats: list):
    import numpy as np
    import pandas as pd
    df = pd.DataFrame(feats)
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    for c in CAT:
        df[c] = df[c].astype(str) if c in df else "na"
    return df[NUM + CAT]


def _prep(X, cols, cats):
    """Same columns and category codes as in training, so one live alert is read exactly like the training rows."""
    import pandas as pd
    X = X[cols].copy()
    for c in CAT:
        if c in X:
            known = cats.get(c) or sorted(X[c].unique().tolist())
            X[c] = pd.Categorical(X[c].where(X[c].isin(known)), categories=known)   # unseen value -> missing
    return X


class Ranker:
    """Conviction model: which alerts are worth an option buy. Gradient-boosted trees on every graded alert (option +20%
    before -20% within an hour = worth it), trained on earlier days and judged on the most recent ones it never saw.
    Trusted only with 5+ days of data and a time-ordered holdout AUC of 0.60+. Retrained after each session."""

    def __init__(self, data_dir: Path, source: str, log=print):
        self.path = Path(data_dir) / f"ranker2_{source}.joblib"
        self.log = log
        self.model = None
        self.info = {"trained": False, "n": 0}
        self.cuts = None
        if self.path.exists():
            try:
                import joblib
                self.model, self.info, self.cuts = joblib.load(self.path)
            except Exception:
                self.model = None

    def train(self, rows: list) -> dict:
        import numpy as np
        rows = sorted([r for r in rows if r.get("result") and r.get("features") and r.get("o0")], key=lambda r: r["ts"])
        days = sorted({r["day"] for r in rows})
        if len(rows) < MIN_TRAIN:
            self.info = {"trained": False, "n": len(rows), "days": len(days), "need": MIN_TRAIN}
            return self.info
        try:
            import joblib
            from sklearn.ensemble import HistGradientBoostingClassifier
            from sklearn.metrics import roc_auc_score
            X = _frame([json.loads(r["features"]) for r in rows])
            cols = [c for c in NUM if X[c].notna().any()] + CAT    # facts never seen yet (e.g. no news so far) are left out
            cats = {c: sorted(X[c].unique().tolist()) for c in CAT}
            X = _prep(X, cols, cats)
            y = np.array([1 if r["result"] == "hit" else 0 for r in rows])
            o60 = np.array([r.get("o60") if r.get("o60") is not None else np.nan for r in rows], float)
            # holdout = the most recent 20% of days (at least one day), never used to fit
            hold_days = set(days[-max(1, len(days) // 5):]) if len(days) > 1 else set()
            te = np.array([r["day"] in hold_days for r in rows]) if hold_days else np.arange(len(rows)) >= int(len(rows) * 0.8)
            mk = lambda: HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=50,
                                                         l2_regularization=1.0, categorical_features="from_dtype", random_state=0)
            m = mk().fit(X[~te], y[~te])
            p = m.predict_proba(X[te])[:, 1]
            auc = float(roc_auc_score(y[te], p)) if len(set(y[te])) > 1 else 0.5
            q = np.quantile(p, 0.9)
            top = p >= q
            info = {"n": len(rows), "days": len(days), "hold_n": int(te.sum()), "base_rate": round(float(y[te].mean()) * 100, 1),
                    "auc": round(auc, 3), "top10_hit": round(float(y[te][top].mean()) * 100, 1),
                    "top10_o60": round(float(np.nanmean(o60[te][top])), 2) if top.any() else None,
                    "all_o60": round(float(np.nanmean(o60[te])), 2), "when": time.time(), "cols": cols, "cats": cats}
            info["trained"] = bool(auc >= TRUST_AUC and len(days) >= MIN_DAYS)
            info["why"] = ("trusted" if info["trained"] else
                           f"needs {MIN_DAYS}+ days (has {len(days)})" if len(days) < MIN_DAYS else f"holdout AUC {auc:.2f} < {TRUST_AUC}")
            final = mk().fit(X, y)                    # use every day for live predictions
            self.cuts = np.quantile(final.predict_proba(X)[:, 1], np.linspace(0, 1, 101))
            self.model, self.info = final, info
            try:
                from sklearn.inspection import permutation_importance
                sub = np.where(te)[0][:3000]
                imp = permutation_importance(m, X.iloc[sub], y[sub], n_repeats=3, random_state=0, scoring="roc_auc")
                order = np.argsort(-imp.importances_mean)[:8]
                self.info["drivers"] = [{"feature": X.columns[i], "importance": round(float(imp.importances_mean[i]), 4)} for i in order]
            except Exception:
                pass
            joblib.dump((self.model, self.info, self.cuts), self.path)
            self.log(f"Conviction model: {len(rows)} graded alerts over {len(days)} days, holdout AUC {auc:.2f} ({info['why']})")
        except Exception as e:
            self.info = {"trained": False, "n": len(rows), "days": len(days), "error": str(e)[:160]}
        return self.info

    def predict(self, feats: dict) -> float | None:
        """Probability the alert is worth an option buy, only when the model is trusted."""
        if self.model is None or not self.info.get("trained"):
            return None
        try:
            X = _prep(_frame([feats]), self.info.get("cols") or NUM + CAT, self.info.get("cats") or {})
            return float(self.model.predict_proba(X)[0][1])
        except Exception:
            return None

    def conviction(self, p: float | None) -> int | None:
        """0-100: where this alert ranks among all graded alerts (90 = top 10%)."""
        if p is None or self.cuts is None:
            return None
        import numpy as np
        return int(np.searchsorted(self.cuts, p) - 1)
