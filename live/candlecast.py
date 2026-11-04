"""Candle forecast (gun, 2.41): what the forming candle becomes, with an honest strength.

Mandeep (4 Nov): from the timeframe cascade, say which candle is forming on the real chart (hammer, shooting star,
doji, marubozu, engulfing, inside / outside bar ...) and where it closes; give it strength from the times the chart
looked like this before; aim for 90%+, counted only on predictions made before the candle closed.

How:
- Years of 1-minute history (data/history/<SYM>/minutes.parquet, Jan 2022 on) cut at every stage of every candle
  (after 1, 2, 3 ... small candles), each described by the 2.39 facts plus the shape of the last 12 candles.
- Voters (bullets in rules/fortune.json "cast"): a gradient-boosting model on those facts (colour); the most similar
  past charts (nearest shapes and what their candle did); separate models for the shape family and for every
  multi-candle pattern; quantile models for the close.
- Strength ladder: the days are split train 60 / validation 20 / holdout 20. The strength from which calls were 90%
  right on validation is the PAKKA bar; every number shown is from the holdout days no model or bar ever saw, next to
  the plain rule "the candle keeps the colour it has so far" on the same candles and the calls against it.
- 90% close zone: quantile models, widened on validation until 90% of closes landed inside (conformal).
- Live: every stage of every forming candle is predicted as its small candles close and graded when it closes
  (candlecast table), so the live ladder is the real record.
Training runs in a child process (lab/proc.py job "candlecast"): once when no models exist, then weekly after the
close. Information only: never a signal, never a trade.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import numpy as np

from . import candles as C
from . import nextcandle as N
from .clock import IST
from .fortune import FILE as FORTUNE_FILE, IST_SHIFT, groups, ohlc

FAMS = ["Doji", "Hammer type", "Shooting star type", "Spinning top", "Marubozu up", "Marubozu down", "Up candle", "Down candle"]
PATTERN_NAMES = {"engulfing": ("Bullish engulfing", "Bearish engulfing"), "harami": ("Bullish harami", "Bearish harami"),
                 "inside_bar": ("Inside bar", "Inside bar"), "outside_bar": ("Outside bar, closing up", "Outside bar, closing down"),
                 "star": ("Morning star", "Evening star"), "tweezer": ("Tweezer bottom", "Tweezer top"),
                 "pin_bar": ("Pin bar, long lower tail", "Pin bar, long upper tail"), "hammer": ("Hammer after a fall", "Shooting star after a rise")}
DEFAULTS = {"on": True, "symbols": ["NIFTY", "BANKNIFTY"], "history_days": 2000, "min_days": 60, "target": 0.90,
            "split": [0.6, 0.2], "min_calls_share": 0.003, "min_calls": 40, "window": 12,
            "model": {"iters": 300, "lr": 0.05, "leaves": 31},
            "similar": {"on": True, "neighbors": 50, "pool": 15000, "show": 3},
            "shape": {"on": True, "iters": 150, "leaves": 15},
            "patterns": {"list": ["engulfing", "harami", "inside_bar", "outside_bar", "star", "tweezer", "pin_bar", "hammer"],
                         "iters": 150, "leaves": 15, "min_pos": 50, "min_calls": 20},
            "zone": {"cover": 0.90, "iters": 200, "leaves": 15},
            "no_volume": True, "retrain_days": 7, "train_after": "16:15", "train_before": "08:45"}
SQL = """CREATE TABLE IF NOT EXISTS candlecast (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, day TEXT, sym TEXT, tf TEXT, start REAL, k INTEGER, n_sub INTEGER,
  made_ts REAL, pred TEXT, actual TEXT, dir TEXT, strength REAL, pakka INTEGER, right INTEGER, simple_right INTEGER,
  fam_right INTEGER, fam_plain_right INTEGER, zone_in INTEGER, pat_calls INTEGER, pat_right INTEGER);
CREATE INDEX IF NOT EXISTS candlecast_tf ON candlecast(source, tf, day);"""
_C: dict = {}
_TRAIN = threading.Lock()                 # one training at a time (it is heavy)


def cfg() -> dict:
    """rules/fortune.json "cast" over the defaults (one level of nested dicts merged). A broken file keeps the last good one."""
    try:
        mt = FORTUNE_FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = (json.loads(FORTUNE_FILE.read_text()) or {}).get("cast") or {}
            out = {}
            for k, v in DEFAULTS.items():
                r = raw.get(k, v)
                out[k] = {**v, **r} if isinstance(v, dict) and isinstance(r, dict) else r
            _C.update(mt=mt, cfg=out, error=None)
    except Exception as e:
        _C.update(cfg=_C.get("cfg") or json.loads(json.dumps(DEFAULTS)), error=str(e)[:160])
    return _C["cfg"]


# ------------------------------------------------------------------ shapes
def family(o, h, l, c) -> int:
    """The finished candle's shape family (index into FAMS)."""
    rng = h - l
    if rng <= 0:
        return 0
    body = abs(c - o) / rng
    up, lo = (h - max(o, c)) / rng, (min(o, c) - l) / rng
    if body <= 0.1:
        return 0
    if body <= 0.35 and lo >= 0.6 and up <= 0.15:
        return 1
    if body <= 0.35 and up >= 0.6 and lo <= 0.15:
        return 2
    if body <= 0.35:
        return 3
    if body >= 0.8:
        return 4 if c > o else 5
    return 6 if c > o else 7


def fam_name(j: int, trend: int) -> str:
    """The trader's name for a family, given the move before it (-1 fell, +1 rose)."""
    if j == 1:
        return "Hammer" if trend < 0 else "Hanging man" if trend > 0 else "Hammer type"
    if j == 2:
        return "Shooting star" if trend > 0 else "Inverted hammer" if trend < 0 else "Shooting star type"
    return FAMS[j]


def _trend(cands, i) -> int:
    if i < 4:
        return 0
    d = cands[i - 1][4] - cands[i - 4][4]
    return 1 if d > 0 else -1 if d < 0 else 0


def shape_vec(cands, i, ref, a, n) -> np.ndarray:
    """The last n finished candles before candle i (open, high, low, close) against the price now, in ATR."""
    lo = max(0, i - n)
    w = np.asarray(cands[lo:i, 1:5], float)
    v = ((w - ref) / a).ravel()
    if len(w) < n:
        v = np.r_[np.zeros((n - len(w)) * 4), v]
    return v


def feature_names(n=12) -> list:
    return list(N.FEATURES) + [f"shape{j}" for j in range(4 * n)] + ["sym"]


def dataset(rows: list, mrows: list | None, tf: str, sub: str, ks, sym_idx: int, n: int = 12) -> dict:
    """Every finished candle of `tf` cut at each stage k: facts + chart shape, and how it ended."""
    t = N.table(rows, tf, sub, ks, mkt=mrows)
    meta = [m for m in t["meta"] if m["done"]]
    keep = [j for j, m in enumerate(t["meta"]) if m["done"]]
    if not meta:
        return {"X": np.zeros((0, len(feature_names(n))), np.float32), "n": 0}
    cands = t["cands"]
    X = t["X"][keep]
    Y = t["Y"][keep]
    ii = np.asarray([m["i"] for m in meta])
    S = np.asarray([shape_vec(cands, m["i"], m["c"], m["atr"], n) for m in meta])
    fam = np.asarray([family(*x) for x in cands[:, 1:5]])
    pats = C.all_patterns(cands[:, 1], cands[:, 2], cands[:, 3], cands[:, 4])
    so_far = np.asarray([family(m["o"], m["h"], m["l"], m["c"]) for m in meta])
    return {"X": np.c_[X, S, np.full(len(meta), sym_idx)].astype(np.float32), "A": np.c_[S, X[:, :3]].astype(np.float32),
            "green": Y[:, 3].astype(np.int8), "rest": Y[:, 0].astype(np.float32), "k": np.asarray([m["k"] for m in meta]),
            "day": np.asarray([m["day"] for m in meta]), "fam": fam[ii], "fam_so_far": so_far,
            "pats": {name: (pats[fact][ii] != 0).astype(np.int8) for name, (_, fact, _) in C.PATTERNS.items()},
            "simple": (X[:, 0] > 0).astype(np.int8), "start": cands[ii, 0], "n": len(meta)}


def _concat(parts: list) -> dict:
    parts = [p for p in parts if p.get("n")]
    if not parts:
        return {"n": 0}
    out = {}
    for key in parts[0]:
        if key == "n":
            out[key] = sum(p["n"] for p in parts)
        elif key == "pats":
            out[key] = {nm: np.concatenate([p["pats"][nm] for p in parts]) for nm in parts[0]["pats"]}
        else:
            out[key] = np.concatenate([p[key] for p in parts])
    return out


# ------------------------------------------------------------------ training
def bar(conf: np.ndarray, right: np.ndarray, min_n: int, target: float) -> float | None:
    """The smallest strength from which calls were at least `target` right (validation days only)."""
    for t in np.arange(0.5, 0.996, 0.005):
        m = conf >= t
        if m.sum() < min_n:
            break
        if right[m].mean() >= target:
            return round(float(t), 3)
    return None


def _hgb(kind, iters, lr, leaves, **kw):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    cls = HistGradientBoostingClassifier if kind == "c" else HistGradientBoostingRegressor
    return cls(max_iter=int(iters), learning_rate=float(lr), max_leaf_nodes=int(leaves), l2_regularization=1.0,
               early_stopping=False, random_state=1, **kw)


def _pct(x) -> float | None:
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x) * 100, 1)


def train_tf(d: dict, tf: str, sub: str, c: dict, log=print, should_stop=lambda: False) -> tuple:
    """(bundle, report) for one timeframe. Honest: models and bars from train / validation days, numbers from holdout."""
    if not d.get("n"):
        raise ValueError(f"{tf}: no history to learn from")
    days = sorted(set(d["day"].tolist()))
    if len(days) < int(c["min_days"]):
        raise ValueError(f"{tf}: only {len(days)} days of history (needs {c['min_days']})")
    a, b = c["split"]
    tr_d, va_d = set(days[: int(len(days) * a)]), set(days[int(len(days) * a): int(len(days) * (a + b))])
    part = np.where(np.isin(d["day"], list(tr_d)), 0, np.where(np.isin(d["day"], list(va_d)), 1, 2))
    hold = days[int(len(days) * (a + b)):]
    target = float(c["target"])
    mc, sc, pc, zc, simc = c["model"], c["shape"], c["patterns"], c["zone"], c["similar"]
    stages, rep = {}, []
    for k in sorted(set(d["k"].tolist())):
        if should_stop():
            raise RuntimeError("stopped")
        t0 = time.time()
        m = d["k"] == k
        X, y, p = d["X"][m], d["green"][m].astype(int), part[m]
        tr, va, ho = p == 0, p == 1, p == 2
        if tr.sum() < 200 or va.sum() < 50 or ho.sum() < 50:
            continue
        min_n = max(int(c["min_calls"]), int(va.sum() * float(c["min_calls_share"])))
        simple = d["simple"][m].astype(int)
        st = {"k": int(k)}
        r = {"k": int(k), "n_hold": int(ho.sum()), "label": f"after {k} × {sub}"}
        # colour, the strength ladder and the PAKKA bar
        mdl = _hgb("c", mc["iters"], mc["lr"], mc["leaves"]).fit(X[tr], y[tr])
        pg = mdl.predict_proba(X)[:, 1]
        pred = (pg >= 0.5).astype(int)
        conf = np.maximum(pg, 1 - pg)
        right = pred == y
        bt = bar(conf[va], right[va], min_n, target)
        st.update(colour=mdl, bar=bt)
        r.update(all_right=_pct(right[ho].mean()), simple_all=_pct((simple == y)[ho].mean()), bar=bt)
        if bt is not None:
            cm = ho & (conf >= bt)
            fl = cm & (pred != simple)
            r.update(calls=int(cm.sum()), cover=_pct(cm.sum() / ho.sum()),
                     right=_pct(right[cm].mean()) if cm.sum() else None, simple_same=_pct((simple == y)[cm].mean()) if cm.sum() else None,
                     flips=int(fl.sum()), flips_right=_pct(right[fl].mean()) if fl.sum() else None)
        lad = []
        for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)):
            mm = ho & (conf >= lo) & (conf < hi)
            if mm.sum():
                lad.append({"lo": lo, "hi": min(hi, 1.0), "n": int(mm.sum()), "right": _pct(right[mm].mean()),
                            "simple": _pct((simple == y)[mm].mean())})
        st["ladder"] = lad
        r["ladder"] = lad
        # the most similar past charts: a pool from the training days
        if simc.get("on", True):
            A = d["A"][m]
            pool = np.where(tr)[0]
            rng = np.random.default_rng(1)
            if len(pool) > int(simc["pool"]):
                pool = np.sort(rng.choice(pool, int(simc["pool"]), replace=False))
            st["pool"] = {"A": A[pool].astype(np.float32), "green": y[pool].astype(np.int8), "rest": d["rest"][m][pool].astype(np.float32),
                          "start": d["start"][m][pool].astype(np.int64), "sym": X[pool, -1].astype(np.int8)}
            q = np.where(ho)[0]
            q = q if len(q) <= 4000 else np.sort(rng.choice(q, 4000, replace=False))
            g = np.asarray([similar(st["pool"], A[j], int(simc["neighbors"]))["green"] for j in q])
            agree = ((g >= 0.5).astype(int) == pred[q])
            r["similar"] = {"all_right": _pct(((g >= 0.5).astype(int) == y[q]).mean()), "agree": _pct(agree.mean()),
                            "right_when_agree": _pct(right[q][agree].mean()) if agree.sum() else None}
        # 90% close zone (conformal on validation)
        rest = d["rest"][m]
        q_lo = _hgb("r", zc["iters"], 0.05, zc["leaves"], loss="quantile", quantile=(1 - float(zc["cover"])) / 2).fit(X[tr], rest[tr])
        q_hi = _hgb("r", zc["iters"], 0.05, zc["leaves"], loss="quantile", quantile=1 - (1 - float(zc["cover"])) / 2).fit(X[tr], rest[tr])
        lo_, hi_ = q_lo.predict(X), q_hi.predict(X)
        s = np.maximum(lo_ - rest, rest - hi_)
        adj = float(np.quantile(s[va], min(1.0, float(zc["cover"]) * (1 + 1 / max(1, va.sum())))))
        inz = (rest >= lo_ - adj) & (rest <= hi_ + adj)
        st["zone"] = {"lo": q_lo, "hi": q_hi, "adj": adj}
        r["zone"] = {"held": _pct(inz[ho].mean()), "width_atr": round(float(np.mean((hi_ - lo_ + 2 * adj)[ho])), 2)}
        # shape family of the finished candle
        if sc.get("on", True):
            fy = d["fam"][m].astype(int)
            fm = _hgb("c", sc["iters"], 0.06, sc["leaves"]).fit(X[tr], fy[tr])
            pf = fm.predict_proba(X)
            top = fm.classes_[pf.argmax(axis=1)]
            fr = top == fy
            plain = d["fam_so_far"][m] == fy
            per = {}
            for j in range(len(FAMS)):
                cj = ho & (top == j)
                if cj.sum() >= 10:
                    per[FAMS[j]] = {"n": int(cj.sum()), "right": _pct(fr[cj].mean())}
            st["shape"] = fm
            r["shape"] = {"all_right": _pct(fr[ho].mean()), "plain_all": _pct(plain[ho].mean()), "per": per}
        # multi-candle patterns this candle completes
        st["patterns"], r["patterns"] = {}, {}
        for name in pc.get("list") or []:
            try:
                yy = d["pats"][name][m].astype(int)
                if yy[tr].sum() < int(pc["min_pos"]):
                    continue
                pm = _hgb("c", pc["iters"], 0.06, pc["leaves"]).fit(X[tr], yy[tr])
                pp = pm.predict_proba(X)[:, 1]
                pb = None
                for t_ in np.arange(0.5, 0.996, 0.005):
                    mm = va & (pp >= t_)
                    if mm.sum() < int(pc["min_calls"]):
                        break
                    if yy[mm].mean() >= target:
                        pb = round(float(t_), 3)
                        break
                row = {"base_rate": _pct(yy[ho].mean())}
                if pb is not None:
                    cm = ho & (pp >= pb)
                    row.update(bar=pb, calls=int(cm.sum()), right=_pct(yy[cm].mean()) if cm.sum() else None,
                               caught=_pct((cm & (yy == 1)).sum() / max(1, (ho & (yy == 1)).sum())))
                    st["patterns"][name] = {"model": pm, "bar": pb, "hold": row.get("right")}
                r["patterns"][name] = row
            except Exception as ex:                   # a bad pattern bullet is skipped, the rest still train
                r["patterns"][name] = {"error": str(ex)[:120]}
        stages[int(k)] = st
        r["secs"] = round(time.time() - t0, 1)
        rep.append(r)
        pk = (f"PAKKA {r.get('cover')}% of candles, {r.get('right')}% right (plain rule {r.get('simple_same')}% on the same)"
              if bt is not None else "no 90% level at this stage")
        log(f"{tf} {r['label']}: {pk} · shape {r.get('shape', {}).get('all_right')}% vs {r.get('shape', {}).get('plain_all')}% "
            f"so far · zone held {r['zone']['held']}%")
    report = {"tf": tf, "from": sub, "stages": rep, "days": len(days), "first_day": days[0], "last_day": days[-1],
              "train_to": max(tr_d), "hold_from": hold[0] if hold else None, "hold_days": len(hold), "rows": int(d["n"])}
    bundle = {"tf": tf, "from": sub, "stages": stages, "window": int(c["window"]), "symbols": list(c["symbols"]),
              "features": feature_names(int(c["window"])), "made": time.time(), "report": report}
    return bundle, report


def similar(pool: dict, a: np.ndarray, n: int) -> dict:
    """The n most similar past charts in the pool: share green, middle of the rest of the move, the closest few."""
    A = pool["A"]
    dd = ((A - a.astype(np.float32)) ** 2).sum(axis=1)
    n = min(n, len(dd))
    idx = np.argpartition(dd, n - 1)[:n]
    idx = idx[np.argsort(dd[idx])]
    return {"n": int(n), "green": float(pool["green"][idx].mean()), "rest": float(np.median(pool["rest"][idx])),
            "idx": idx}


# ------------------------------------------------------------------ history and the training job
def history(data_dir, sym: str, base_min: int, days: int, no_volume: bool, cache: dict | None = None) -> list:
    """1-minute history (data/history/<SYM>/minutes.parquet, else the chart cache), at `base_min` minutes."""
    cache = cache if cache is not None else {}
    key = (sym, 1)
    if key not in cache:
        rows = []
        f = Path(data_dir) / "history" / sym / "minutes.parquet"
        if f.exists():
            import pandas as pd
            df = pd.read_parquet(f, columns=["ts", "open", "high", "low", "close", "volume"])
            rows = df.values.tolist()
        else:
            g = Path(data_dir) / "charts" / f"{sym}_1.json"
            if g.exists():
                raw = json.loads(g.read_text())
                rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
        cut = time.time() - days * 86400
        rows = [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), 0.0 if no_volume else float(r[5] or 0)]
                for r in rows if r and r[0] >= cut]
        cache[key] = rows
    rows = cache[key]
    if base_min == 1:
        return rows
    k2 = (sym, base_min)
    if k2 not in cache:
        from .mtf import resample
        cache[k2] = resample(rows, base_min)
    return cache[k2]


def model_dir(data_dir) -> Path:
    return Path(data_dir) / "candlecast"


def run(data_dir, log=print, should_stop=lambda: False, tfs=None) -> dict:
    """The training job (lab/proc.py "candlecast", in a child process): every cascade timeframe, saved with its report."""
    import joblib
    c = cfg()
    out_dir = model_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    reports, errors = {}, {}
    for tf, pr in N.pairs().items():
        if tfs and tf not in tfs:
            continue
        sub = pr["from"]
        n_sub = max(2, round(N.TF_MIN[tf] / N.SUB_MIN[sub]))
        ks = list(range(1, n_sub))
        try:
            parts = []
            for j, sym in enumerate(c["symbols"]):
                rows = history(data_dir, sym, N.SUB_MIN[sub], int(c["history_days"]), bool(c["no_volume"]), cache)
                ref = (N.cfg().get("market_ref") or {})
                ms = ref.get(sym, ref.get("default"))
                mrows = history(data_dir, ms, N.SUB_MIN[sub], int(c["history_days"]), bool(c["no_volume"]), cache) if ms and ms != sym else None
                if not rows:
                    log(f"{tf}: no history for {sym}")
                    continue
                parts.append(dataset(rows, mrows or None, tf, sub, ks, j, int(c["window"])))
                log(f"{tf}: {sym} {parts[-1]['n']:,} moments from {len(rows):,} {sub} bars")
            bundle, rep = train_tf(_concat(parts), tf, sub, c, log=log, should_stop=should_stop)
            tmp = out_dir / f"{tf}.joblib.tmp"
            joblib.dump(bundle, tmp, compress=3)
            tmp.replace(out_dir / f"{tf}.joblib")
            reports[tf] = rep
        except Exception as ex:
            errors[tf] = str(ex)[:200]
            log(f"{tf}: not trained ({ex})")
            if str(ex) == "stopped":
                break
    old = {}
    try:
        old = json.loads((out_dir / "report.json").read_text())
    except Exception:
        pass
    allrep = {**(old.get("tfs") or {}), **reports}
    meta = {"trained_at": time.time(), "tfs": allrep, "errors": errors, "symbols": c["symbols"], "target": c["target"]}
    tmp = out_dir / "report.json.tmp"
    tmp.write_text(json.dumps(meta, default=str))
    tmp.replace(out_dir / "report.json")
    return {"trained": sorted(reports), "errors": errors}


# ------------------------------------------------------------------ live
def _hm(s):
    h, m = str(s).split(":")
    return dt.time(int(h), int(m))


class Cast:
    """Loads the trained models, predicts every stage of the forming candle, grades it at the close, serves the chart."""

    def __init__(self, engine):
        self.e = engine
        self.dir = model_dir(engine.data_dir)
        self.models: dict = {}            # tf -> (mtime, bundle) | (mtime, {"error"})
        self.cur: dict = {}               # (sym, tf) -> {"start", "k", "stages": {k: pred}}
        self.status = {"predicted": 0, "graded": 0, "errors": {}, "training": False, "last_train": None, "train_error": None}
        self._rep = (None, None)
        self._tried = 0.0
        try:
            with engine.store.lock:
                engine.store.db.executescript(SQL)
                engine.store.db.commit()
        except Exception:
            pass

    # ---------------- models
    def report(self) -> dict | None:
        f = self.dir / "report.json"
        try:
            mt = f.stat().st_mtime
        except Exception:
            return None
        if self._rep[0] != mt:
            try:
                self._rep = (mt, json.loads(f.read_text()))
            except Exception:
                return self._rep[1]
        return self._rep[1]

    def bundle(self, tf):
        f = self.dir / f"{tf}.joblib"
        try:
            mt = f.stat().st_mtime
        except Exception:
            return None
        hit = self.models.get(tf)
        if hit and hit[0] == mt:
            return hit[1] if "error" not in hit[1] else None
        try:
            import joblib
            b = joblib.load(f)
        except Exception as ex:                       # a bad file is reported, the rest keeps running
            self.models[tf] = (mt, {"error": str(ex)[:160]})
            self.status["errors"][f"load {tf}"] = str(ex)[:160]
            return None
        self.models[tf] = (mt, b)
        return b

    def ensure(self, now: float | None = None) -> None:
        """Train in the background (child process) when no models exist, or weekly outside market hours."""
        c = cfg()
        if not c.get("on", True) or self.status["training"] or time.time() - self._tried < 6 * 3600:
            return
        rep = self.report()
        h = Path(self.e.data_dir) / "history"
        if not any((h / s / "minutes.parquet").exists() for s in c["symbols"]) and not any(
                (Path(self.e.data_dir) / "charts" / f"{s}_1.json").exists() for s in c["symbols"]):
            return
        t = dt.datetime.now(IST)
        quiet = t.time() >= _hm(c["train_after"]) or t.time() < _hm(c["train_before"]) or t.weekday() >= 5
        missing = rep is not None and any(tf not in (rep.get("tfs") or {}) for tf in N.pairs())   # 2.42: a failed run is retried
        stale = rep is None or missing or time.time() - float(rep.get("trained_at") or 0) > float(c["retrain_days"]) * 86400
        if rep is not None and not (stale and quiet):
            return
        if not _TRAIN.acquire(blocking=False):
            return
        self._tried = time.time()
        self.status["training"] = True

        def go():
            try:
                from lab import proc
                proc.run("candlecast", self.e.data_dir, log=lambda m: self.e.log(f"Candle forecast: {m}"))
                self.status.update(last_train=time.time(), train_error=None)
                self.models.clear()
            except Exception as ex:
                self.status["train_error"] = str(ex)[:200]
                try:
                    self.e.log(f"Candle forecast: training failed ({ex})")
                except Exception:
                    pass
            finally:
                self.status["training"] = False
                _TRAIN.release()
        threading.Thread(target=go, daemon=True, name="candlecast-train").start()

    # ---------------- predict / grade
    def step(self, sym, tf, sub, rows, mrows, now, today) -> None:
        """rows: history tail + today's small bars (any volume); called every few seconds, cheap when nothing closed."""
        c = cfg()
        if not c.get("on", True) or sym not in c["symbols"]:
            return
        sm = N.SUB_MIN[sub] * 60
        closed = [r for r in rows if r[0] + sm <= now + 1e-6]
        if not closed:
            return
        g = groups(closed, tf)
        start, subs = g[-1]
        tf_s = N.TF_MIN[tf] * 60
        forming = start + tf_s > now + 1e-6 and N._day(start).isoformat() == today
        cur = self.cur.get((sym, tf))
        if cur and (not forming or cur["start"] != start):
            fin = next((x for x in g if x[0] == cur["start"]), None)
            if fin is not None:
                self._grade(sym, tf, cur, g, fin, today)
            self.cur.pop((sym, tf), None)
            cur = None
        if not forming:
            return
        k = len(subs)
        if cur and cur["k"] == k:
            return
        b = self.bundle(tf)
        if b is None or k not in b["stages"]:
            return
        nv = bool(c["no_volume"])
        z = (lambda rr: [[r[0], r[1], r[2], r[3], r[4], 0.0] for r in rr]) if nv else (lambda rr: rr)
        n_hist = len(groups([r for r in closed if r[0] < start], tf))
        t = N.table(z(rows), tf, sub, [k], now=now, mkt=z(mrows) if mrows else None, start_from=max(1, n_hist - 1))
        fm = [(j, m) for j, m in enumerate(t["meta"]) if not m["done"] and m["start"] == start and m["k"] == k]
        if not fm:
            return
        j, m = fm[-1]
        pred = self.predict(b, sym, t, j, m)
        if pred is None:
            return
        pred.update(k=k, n_sub=max(2, round(N.TF_MIN[tf] / N.SUB_MIN[sub])), made=now, start=start)
        cur = cur or {"start": start, "stages": {}}
        cur["k"] = k
        cur["stages"][k] = pred
        cur["cands_i"] = m["i"]
        self.cur[(sym, tf)] = cur
        self.status["predicted"] += 1

    def predict(self, b: dict, sym: str, t: dict, j: int, m: dict) -> dict | None:
        st = b["stages"].get(m["k"])
        if not st:
            return None
        try:
            sym_idx = b["symbols"].index(sym)
        except ValueError:
            return None
        cands, a, n = t["cands"], m["atr"], int(b.get("window", 12))
        S = shape_vec(cands, m["i"], m["c"], a, n)
        x = np.r_[t["X"][j], S, sym_idx].astype(np.float32)[None, :]
        pg = float(st["colour"].predict_proba(x)[0, 1])
        up = pg >= 0.5
        conf = max(pg, 1 - pg)
        bt = st.get("bar")
        band = next((l for l in st.get("ladder") or [] if l["lo"] <= conf < l["hi"] or (l["hi"] >= 1.0 and conf >= l["lo"])), None)
        simple = "up" if m["c"] > m["o"] else "down" if m["c"] < m["o"] else None
        trend = _trend(cands, m["i"])
        out = {"dir": "up" if up else "down", "strength": round(conf * 100, 1), "p_green": round(pg * 100, 1),
               "pakka": bool(bt is not None and conf >= bt), "bar": round(bt * 100, 1) if bt is not None else None,
               "band": band, "simple": simple, "agrees": simple is None or simple == ("up" if up else "down"),
               "so_far": round(m["c"], 2), "open": round(m["o"], 2), "atr": round(a, 2)}
        z = st.get("zone")
        if z:
            lo = float(z["lo"].predict(x)[0]) - z["adj"]
            hi = float(z["hi"].predict(x)[0]) + z["adj"]
            out["zone"] = [round(m["c"] + lo * a, 2), round(m["c"] + hi * a, 2)]
        if st.get("shape") is not None:
            pf = st["shape"].predict_proba(x)[0]
            order = np.argsort(pf)[::-1][:3]
            out["shape"] = [{"fam": int(st["shape"].classes_[q]), "name": fam_name(int(st["shape"].classes_[q]), trend),
                             "pct": round(float(pf[q]) * 100)} for q in order if pf[q] >= 0.05]
            sf = family(m["o"], m["h"], m["l"], m["c"])
            out["shape_so_far"], out["shape_so_far_fam"] = fam_name(sf, trend), sf
        calls = []
        for name, pm in (st.get("patterns") or {}).items():
            try:
                pp = float(pm["model"].predict_proba(x)[0, 1])
            except Exception:
                continue
            if pp >= pm["bar"]:
                nm = PATTERN_NAMES.get(name, (name, name))[0 if up else 1]
                formed = None
                if name == "outside_bar" and m["i"] >= 1:
                    formed = bool(m["h"] > cands[m["i"] - 1][2] and m["l"] < cands[m["i"] - 1][3])
                calls.append({"key": name, "name": nm, "pct": round(pp * 100), "hold": pm.get("hold"), "already": formed})
        out["patterns"] = calls
        pool = st.get("pool")
        if pool is not None and len(pool["A"]):
            simc = cfg()["similar"]
            av = np.r_[S, t["X"][j][:3]].astype(np.float32)
            sm_ = similar(pool, av, int(simc["neighbors"]))
            show = []
            for q in sm_["idx"][: int(simc.get("show", 3))]:
                s0 = float(pool["start"][q])
                si = int(pool["sym"][q]) if "sym" in pool else None
                show.append({"time": int(s0 + IST_SHIFT), "green": bool(pool["green"][q]), "rest_atr": round(float(pool["rest"][q]), 2),
                             "sym": b["symbols"][si] if si is not None and 0 <= si < len(b["symbols"]) else None})
            out["similar"] = {"n": sm_["n"], "green_pct": round(sm_["green"] * 100), "close": round(m["c"] + sm_["rest"] * a, 2),
                              "agrees": (sm_["green"] >= 0.5) == up, "closest": show}
        return out

    def _grade(self, sym, tf, cur, g, fin, today):
        start, subs = fin
        act = ohlc(start, subs)
        o, h, l, cl = act[1], act[2], act[3], act[4]
        green = cl > o
        cands = np.asarray([ohlc(s, r) for s, r in g if s <= start], float)
        i = len(cands) - 1
        trend = _trend(cands, i)
        fam = family(o, h, l, cl)
        try:
            pats = C.all_patterns(cands[:, 1], cands[:, 2], cands[:, 3], cands[:, 4])
            done = {name for name, (_, fact, _) in C.PATTERNS.items() if pats[fact][i] != 0}
        except Exception:
            done = set()
        actual = {"open": o, "high": h, "low": l, "close": cl, "fam": fam, "shape": fam_name(fam, trend), "patterns": sorted(done)}
        rows = []
        for k, p in sorted(cur["stages"].items()):
            right = int((p["dir"] == "up") == green)
            simple_right = None if p.get("simple") is None else int((p["simple"] == "up") == green)
            top = (p.get("shape") or [{}])[0].get("fam")
            fam_right = None if top is None else int(top == fam)
            so_far_f = p.get("shape_so_far_fam")
            zone_in = None if not p.get("zone") else int(p["zone"][0] <= cl <= p["zone"][1])
            pc = p.get("patterns") or []
            rows.append((self.e.source, today, sym, tf, start, k, p.get("n_sub"), p.get("made"), json.dumps(p, default=str), json.dumps(actual),
                         p["dir"], p["strength"], int(p["pakka"]), right, simple_right, fam_right,
                         None if so_far_f is None else int(so_far_f == fam), zone_in, len(pc), sum(1 for x in pc if x["key"] in done)))
        if not rows:
            return
        with self.e.store.lock:
            self.e.store.db.executemany("INSERT INTO candlecast(source, day, sym, tf, start, k, n_sub, made_ts, pred, actual, dir, strength, "
                                        "pakka, right, simple_right, fam_right, fam_plain_right, zone_in, pat_calls, pat_right) "
                                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.e.store.db.commit()
        self.status["graded"] += len(rows)

    # ---------------- the chart and the report
    def live_stats(self, tf, sym=None, day=None) -> list:
        q = ("SELECT k, COUNT(*), SUM(right), SUM(simple_right), SUM(pakka), SUM(CASE WHEN pakka=1 THEN right END), "
             "SUM(CASE WHEN pakka=1 THEN simple_right END), SUM(CASE WHEN pakka=1 AND simple_right IS NOT NULL AND right<>simple_right THEN 1 END), "
             "SUM(CASE WHEN pakka=1 AND simple_right IS NOT NULL AND right<>simple_right AND right=1 THEN 1 END), "
             "SUM(fam_right), COUNT(fam_right), SUM(fam_plain_right), COUNT(fam_plain_right), SUM(zone_in), COUNT(zone_in), "
             "SUM(pat_calls), SUM(pat_right) FROM candlecast WHERE source=? AND tf=?")
        args = [self.e.source or "upstox", tf]
        if sym:
            q += " AND sym=?"
            args.append(sym)
        if day:
            q += " AND day=?"
            args.append(day)
        q += " GROUP BY k ORDER BY k"
        try:
            with self.e.store.lock:
                rs = self.e.store.db.execute(q, args).fetchall()
        except Exception:
            rs = []
        pc = lambda a, b: round(100.0 * a / b, 1) if b else None
        out = []
        for k, n, r_, s_, pk, pr, ps, fl, flr, fr, fn, fp, fpn, zi, zn, pcn, prt in rs:
            out.append({"k": k, "n": n, "all_right": pc(r_ or 0, n), "simple_all": pc(s_ or 0, n), "calls": pk or 0,
                        "right": pc(pr or 0, pk or 0), "simple_same": pc(ps or 0, pk or 0), "flips": fl or 0, "flips_right": pc(flr or 0, fl or 0),
                        "shape_right": pc(fr or 0, fn), "shape_plain": pc(fp or 0, fpn), "zone_held": pc(zi or 0, zn),
                        "pattern_calls": pcn or 0, "pattern_right": pc(prt or 0, pcn or 0)})
        return out

    def view(self, sym, tf) -> dict:
        c = cfg()
        out = {"on": bool(c.get("on", True)), "target": c["target"], "symbols": c["symbols"], "training": self.status["training"],
               "train_error": self.status["train_error"]}
        if not out["on"]:
            return out
        try:
            self.ensure()
        except Exception as ex:
            self.status["errors"]["ensure"] = str(ex)[:120]
        rep = self.report() or {}
        out["trained_at"] = rep.get("trained_at")
        r = (rep.get("tfs") or {}).get(tf)
        if sym not in c["symbols"]:
            return {**out, "status": "symbol", "message": f"Trained for {', '.join(c['symbols'])} only (they have years of 1-minute history)."}
        if r is None:
            err = (rep.get("errors") or {}).get(tf)
            return {**out, "status": "training" if self.status["training"] else "error" if err else "waiting", "error": err}
        cur = self.cur.get((sym, tf))
        if cur and cur["stages"]:
            k = max(cur["stages"])
            out["now"] = {**cur["stages"][k], "time": int(cur["start"] + IST_SHIFT)}
        out.update(status="ready", report=r, live=self.live_stats(tf, sym=sym),
                   today=self.live_stats(tf, sym=sym, day=N._day(time.time()).isoformat()))
        return out
