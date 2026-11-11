"""Day-type forecaster (2.66): at 09:45, how likely is today to end a trend day, a range day or a quiet day?

Mandeep, 11 Nov 2026: "kya kya cheej bhot jaldi pakad sakta hai". The playbook depends on the day type, and the live
day type ("Market: Range") is only known as the day unfolds.

Gun: from the Lab's bar table (every 5-minute bar of the index history with the facts known at its close), each
day x index gets a label from how the whole day went (09:15 to 14:45):
  trend = it travelled cleanly (|move since the open| / day's range >= trend_eff) and its range was at least
          trend_rel x the index's typical day (median of the previous 20 days);
  quiet = range under quiet_rel x the typical day;   range = the rest.
The facts known at 09:45 (range so far vs the typical day, how cleanly it has moved so far, gap, VIX, days to expiry,
opening-range position, distance from yesterday's high / low, CPR width, ADX ...) go into each candidate model
(rules/daytype.json "models": logistic regression, small gradient-boosted trees). Each is judged walk-forward by month
(a month is predicted only by a model trained on the months before) against the plain base rates of the earlier
months; the one with the best log-loss skill is the live model (champion vs challenger, every night).
Honest numbers shown: how much better than the base rates, how often the 20% most trend-like mornings really trended.
Live: the 09:45 forecast for Nifty and Bank Nifty, graded at the close (data/daytype_live.json). Information only,
"on trial" until the walk-forward clears rules/daytype.json "trust" and the live record agrees. Market data only.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CFG_FILE = ROOT / "rules" / "daytype.json"
MODEL = "daytype.joblib"
OUT = "daytype.json"
LIVE = "daytype_live.json"
CLASSES = ["trend", "range", "quiet"]
DERIVED = ["rel_range", "eff_now", "is_bank"]
BASE_COLS = ["sym", "date", "tod", "dir", "gap", "day_ret", "day_range"]


def cfg() -> dict:
    return json.loads(CFG_FILE.read_text())


def label(gap, day_ret, day_range, med20, c: dict) -> str | None:
    """trend / range / quiet from the day's whole move and range (all in % of yesterday's close / the open)."""
    vals = (day_range, med20)
    if any(v is None or not math.isfinite(float(v)) for v in vals) or med20 <= 0:
        return None
    eff = abs((day_ret or 0) - (gap or 0)) / day_range if day_range > 0 else 0.0
    rel = day_range / med20
    if rel < float(c.get("quiet_rel", 0.6)):
        return "quiet"
    if eff >= float(c.get("trend_eff", 0.6)) and rel >= float(c.get("trend_rel", 0.8)):
        return "trend"
    return "range"


def derive(vals: dict, med20, sym: str) -> dict:
    """Facts made from the bar facts: range so far vs a typical whole day, how cleanly it has moved so far, which index."""
    rng = vals.get("day_range")
    mv = (vals.get("day_ret") or 0) - (vals.get("gap") or 0)
    ok = rng is not None and math.isfinite(float(rng))
    return {"rel_range": float(rng) / med20 if ok and med20 else np.nan,
            "eff_now": abs(mv) / float(rng) if ok and rng > 0 else np.nan,
            "is_bank": 1.0 if sym == "BANKNIFTY" else 0.0}


def table(df, c: dict):
    """One row per day x index: the facts at the forecast bar, the typical-day range before it, the label.
    The index facts are the same on a bar's call, put and no-option rows; one row per bar is kept (call first)."""
    at = int(c.get("at_tod", 30))
    d = df[df["sym"].isin(c.get("syms") or ["NIFTY", "BANKNIFTY"])]
    pref = d["dir"].map({1: 0, -1: 1, 0: 2}).fillna(3) if "dir" in d.columns else 0
    d = d.assign(_p=pref).sort_values(["date", "sym", "tod", "_p"]).drop_duplicates(["date", "sym", "tod"])
    feats = [f for f in c["facts"] if f in d.columns and f not in DERIVED]
    early = d[d["tod"] == at][["date", "sym"] + sorted(set(feats) | {"gap", "day_ret", "day_range"})].copy()
    last = d.groupby(["date", "sym"]).tail(1)[["date", "sym", "gap", "day_ret", "day_range"]]
    last = last.rename(columns={"gap": "g_end", "day_ret": "r_end", "day_range": "rng_end"})
    t = early.merge(last, on=["date", "sym"], how="inner").sort_values(["sym", "date"]).reset_index(drop=True)
    t["med20"] = t.groupby("sym")["rng_end"].transform(lambda s: s.shift(1).rolling(20, min_periods=10).median())
    der = [derive(r, m, s) for r, m, s in zip(t[["day_range", "day_ret", "gap"]].to_dict("records"), t["med20"], t["sym"])]
    for k in DERIVED:
        t[k] = [x[k] for x in der]
    t["label"] = [label(g, r, x, m, c) for g, r, x, m in zip(t["g_end"], t["r_end"], t["rng_end"], t["med20"])]
    t["trend_dir"] = np.sign(t["r_end"] - t["g_end"])
    t = t[t["label"].notna()].sort_values(["date", "sym"]).reset_index(drop=True)
    cols = [f for f in c["facts"] if f in t.columns]
    return t, cols


def make_model(name: str, spec: dict):
    """The candidate models (bullets: rules/daytype.json "models")."""
    if name.startswith("logit"):
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(C=float(spec.get("C", 0.3)), max_iter=1000))
    if name.startswith("hgb"):
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=int(spec.get("iters", 60)), learning_rate=0.05,
                                              max_leaf_nodes=int(spec.get("leaves", 4)), min_samples_leaf=int(spec.get("leaf", 40)),
                                              l2_regularization=1.0, random_state=3)
    raise ValueError(f"unknown day-type model {name!r}")


def _proba(m, X) -> np.ndarray:
    """Probabilities in CLASSES order (a class missing from the training months gets 0)."""
    out = np.zeros((len(X), len(CLASSES)))
    out[:, [int(k) for k in m.classes_]] = m.predict_proba(X)
    return out


def walk(t, cols: list, name: str, spec: dict, c: dict) -> dict:
    """Month-by-month walk-forward of one model next to the earlier months' base rates."""
    X = t[cols].to_numpy(np.float32)
    y = np.array([CLASSES.index(v) for v in t["label"]])
    month = t["date"].astype(str).str[:7].to_numpy()
    months = sorted(set(month))
    P, Y, B, M = [], [], [], []
    for mo in months[int(len(months) * float(c.get("first_test_share", 0.5))):]:
        tr, te = month < mo, month == mo
        if tr.sum() < 60 or not te.any():
            continue
        keep = ~np.all(np.isnan(X[tr]), axis=0)          # a fact missing in every earlier month cannot be learnt yet
        m = make_model(name, spec).fit(X[tr][:, keep], y[tr])
        P.append(_proba(m, X[te][:, keep]))
        Y.append(y[te])
        B.append(np.tile(np.bincount(y[tr], minlength=3) / tr.sum(), (int(te.sum()), 1)))
        M += [mo] * int(te.sum())
    if not P:
        return {"model": name, "tested_days": 0}
    P, Y, B = np.concatenate(P), np.concatenate(Y), np.concatenate(B)
    oh = np.eye(3)[Y]
    ll = -np.mean(np.log(np.clip((P * oh).sum(1), 1e-6, 1)))
    llb = -np.mean(np.log(np.clip((B * oh).sum(1), 1e-6, 1)))
    k = max(1, int(len(P) * 0.2))
    top_t, bot_t, top_q = np.argsort(-P[:, 0])[:k], np.argsort(P[:, 0])[:k], np.argsort(-P[:, 2])[:k]
    called = P.argmax(1)
    by = {}
    for i, cl in enumerate(CLASSES):
        cc, rr = called == i, Y == i
        by[cl] = {"called": int(cc.sum()), "real": int(rr.sum()),
                  "right_when_called": round(100 * float((cc & rr).sum()) / cc.sum(), 1) if cc.any() else None,
                  "caught": round(100 * float((cc & rr).sum()) / rr.sum(), 1) if rr.any() else None}
    return {"model": name, "tested_days": int(len(Y)), "months": len(set(M)),
            "logloss_skill": round((1 - ll / llb) * 100, 1),
            "accuracy": round(100 * float((called == Y).mean()), 1),
            "base_accuracy": round(100 * float((B.argmax(1) == Y).mean()), 1),
            "trend_all": round(100 * float((Y == 0).mean()), 1), "quiet_all": round(100 * float((Y == 2).mean()), 1),
            "trend_top20": round(100 * float((Y[top_t] == 0).mean()), 1),
            "trend_bottom20": round(100 * float((Y[bot_t] == 0).mean()), 1),
            "quiet_top20": round(100 * float((Y[top_q] == 2).mean()), 1), "by_class": by}


def trusted(w: dict, c: dict) -> bool:
    tc = c.get("trust") or {}
    return bool(w.get("tested_days", 0) >= int(tc.get("min_tested_days", 200))
                and (w.get("logloss_skill") or 0) >= float(tc.get("min_logloss_skill", 3))
                and (w.get("trend_top20") or 0) >= (w.get("trend_all") or 0) + float(tc.get("min_trend_lift", 5)))


def _read_table(data_dir, c: dict):
    """Only the columns needed, from the Lab's cached bar table (never builds it: the nightly Lab does that)."""
    import pandas as pd
    from .factory import cache_path
    f = cache_path(data_dir)
    if not f.exists():
        return None
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(f).schema.names)
    want = [k for k in dict.fromkeys(BASE_COLS + list(c["facts"])) if k in have]
    return pd.read_parquet(f, columns=want)


def learn(data_dir, log=print, df=None, c: dict | None = None) -> dict:
    t0 = time.time()
    c = c or cfg()
    data_dir = Path(data_dir)
    if df is None:
        df = _read_table(data_dir, c)
    if df is None or not len(df):                         # nothing to learn from: an earlier result and model stay
        return _save(data_dir, {"note": "No Lab bar table here yet.", "made": time.time()}, keep=True)
    t, cols = table(df, c)
    cols = [k for k in cols if t[k].notna().mean() >= float(c.get("min_fact_share", 0.5))]   # facts this history really has
    if len(t) < int(c.get("min_days", 120)):
        return _save(data_dir, {"note": f"needs {c.get('min_days', 120)}+ index days with a label (has {len(t)})", "made": time.time()})
    runs = []
    for name, spec in (c.get("models") or {"logit": {}}).items():
        try:
            runs.append(walk(t, cols, name, spec, c))
        except Exception as e:                            # one broken candidate is skipped, the rest still run
            runs.append({"model": name, "error": str(e)[:160], "tested_days": 0})
    ok = [w for w in runs if w.get("tested_days")]
    if not ok:
        return _save(data_dir, {"note": "no model could be tested", "runs": runs, "made": time.time()})
    champ = max(ok, key=lambda w: w["logloss_skill"])
    X = t[cols].to_numpy(np.float32)
    y = np.array([CLASSES.index(v) for v in t["label"]])
    model = make_model(champ["model"], (c.get("models") or {}).get(champ["model"]) or {}).fit(X, y)
    med = t.groupby("sym")["rng_end"].apply(lambda s: float(s.tail(20).median())).to_dict()
    tr_days = t[t["label"] == "trend"]
    hint = np.sign(tr_days["day_ret"].fillna(0) - tr_days["gap"].fillna(0)) == tr_days["trend_dir"]
    res = {"made": time.time(), "secs": round(time.time() - t0, 1), "days": int(len(t)), "syms": sorted(t["sym"].unique().tolist()),
           "first": str(t["date"].min()), "last": str(t["date"].max()), "facts": cols, "at_tod": int(c.get("at_tod", 30)),
           "share": {k: round(float((t["label"] == k).mean()) * 100, 1) for k in CLASSES},
           "runs": runs, "champion": champ["model"], "walk": champ, "trusted": trusted(champ, c),
           "trend_dir_hint": round(float(hint.mean()) * 100, 1) if len(tr_days) else None}
    try:
        import joblib
        joblib.dump({"model": model, "cols": cols, "med20": med, "info": res}, data_dir / MODEL)
    except Exception as e:
        res["save_error"] = str(e)[:120]
    log(f"Day type: {len(t)} index days; champion {champ['model']}: {champ['tested_days']} unseen days, "
        f"{champ['logloss_skill']:+.1f}% better than the base rates; the 20% most trend-like mornings trended "
        f"{champ['trend_top20']}% (all days {champ['trend_all']}%); {'trusted' if res['trusted'] else 'on trial'}")
    return _save(data_dir, res)


def _save(data_dir, res: dict, keep: bool = False) -> dict:
    p = Path(data_dir) / OUT
    if keep and (Path(data_dir) / MODEL).exists():
        return res
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(p)
    return res


_C: dict = {}


def _load(p: Path):
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    hit = _C.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        import joblib
        obj = joblib.load(p)
    except Exception:
        obj = None
    _C[str(p)] = (mt, obj)
    return obj


def predict(dirs, sym: str, f_now: dict) -> dict | None:
    """f_now: today's index facts at the 09:45 bar. Of the models in `dirs` (1-year and 5-year Labs) that know this
    index, the one with the best walk-forward skill answers. None without a model."""
    best = None
    for d in dirs if isinstance(dirs, (list, tuple)) else [dirs]:
        o = _load(Path(d) / MODEL)
        if o and sym in (o.get("med20") or {}):
            if best is None or (o["info"]["walk"].get("logloss_skill") or -99) > (best["info"]["walk"].get("logloss_skill") or -99):
                best = o
    if best is None:
        return None
    vals = {k: float(v) for k, v in f_now.items() if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool)}
    vals.update(derive(vals, best["med20"].get(sym), sym))
    x = np.array([[vals[k] if k in vals and math.isfinite(vals[k]) else np.nan for k in best["cols"]]], np.float32)
    pr = _proba(best["model"], x)[0]
    w = best["info"]["walk"]
    probs = {k: round(float(v) * 100, 1) for k, v in zip(CLASSES, pr)}
    return {"probs": probs, "call": max(probs, key=probs.get), "usual": best["info"].get("share"),
            "trusted": bool(best["info"].get("trusted")), "model": best["info"].get("champion"),
            "skill": w.get("logloss_skill"), "trend_top20": w.get("trend_top20"), "trend_all": w.get("trend_all"),
            "dir_hint": int(np.sign(vals.get("day_ret", 0) - vals.get("gap", 0))), "med20": best["med20"].get(sym),
            "days": best["info"].get("days")}


# ------------------------------------------------------------------ live record (graded at the close)
def live_load(data_dir) -> list:
    try:
        return json.loads((Path(data_dir) / LIVE).read_text()).get("days", [])
    except Exception:
        return []


def live_save(data_dir, rows: list):
    p = Path(data_dir) / LIVE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"days": rows[-1000:]}, default=str))
    tmp.replace(p)


def grade(row: dict, f_last: dict, c: dict) -> dict:
    """Close of the day: the real label from the last bar's facts and the morning's typical-day range."""
    lab = label(f_last.get("gap"), f_last.get("day_ret"), f_last.get("day_range"), row.get("med20"), c)
    if lab is None:
        return row
    p = (row.get("probs") or {}).get(lab)
    row.update(label=lab, right=row.get("call") == lab, p_real=p,
               trend_dir=int(np.sign((f_last.get("day_ret") or 0) - (f_last.get("gap") or 0))))
    return row


def live_view(data_dir) -> dict:
    rows = live_load(data_dir)
    g = [r for r in rows if r.get("label")]
    out = {"forecasts": len(rows), "graded": len(g), "today": [r for r in rows[-4:] if not r.get("label")]}
    if g:
        out["right_pct"] = round(100 * sum(1 for r in g if r["right"]) / len(g), 1)
        usual = [r for r in g if r.get("usual")]
        if usual:      # log-loss skill of the live forecasts next to the usual shares known that morning
            ll = -np.mean([math.log(max(1e-4, (r.get("p_real") or 0) / 100)) for r in usual])
            llb = -np.mean([math.log(max(1e-4, (r["usual"].get(r["label"]) or 0) / 100)) for r in usual])
            out["skill"] = round((1 - ll / llb) * 100, 1) if llb else None
        out["recent"] = g[-10:][::-1]
    return out


def today(data_dir, day: str) -> dict:
    """Today's forecasts with the live record so far; 'trusted' only when the walk-forward cleared the trust rule AND the
    live record has min_live_days graded days and beats the usual shares."""
    c = cfg()
    lv = live_view(data_dir)
    ok_live = lv["graded"] >= int((c.get("trust") or {}).get("min_live_days", 20)) and (lv.get("skill") or 0) > 0
    rows = [dict({k: r.get(k) for k in ("sym", "probs", "call", "usual", "dir_hint", "late", "label", "right", "skill",
                                        "trend_top20", "trend_all", "model")}, trusted=bool(ok_live and r.get("trusted")))
            for r in live_load(data_dir) if r.get("day") == day]
    return {"rows": rows, "live_graded": lv["graded"], "live_right": lv.get("right_pct"), "live_skill": lv.get("skill")}


def view(data_dir, dirs=None) -> dict:
    def one(d):
        try:
            return json.loads((Path(d) / OUT).read_text())
        except Exception:
            return {"note": "Not trained yet: it trains in each nightly Lab round."}
    dirs = dirs or [data_dir]
    return {"labs": {("5y" if Path(d) != Path(data_dir) else "1y"): one(d) for d in dirs}, "live": live_view(data_dir)}
