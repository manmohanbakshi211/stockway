"""Bank Nifty Radar - local web app (FastAPI). Start with:  python app.py"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from radar import features, model, news, pipeline, report
from radar.dates import ns_col, ns_frame
from radar.config import ROOT, SECRET_KEYS, editable_values, load_config, save_user_settings

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Bank Nifty Radar", docs_url=None, redoc_url=None)


# ----------------------------------------------------------------- helpers
def cfg() -> dict:
    return load_config()


def out_dir() -> Path:
    return Path(cfg()["output_dir"])


def clean(o):
    return pipeline._jsonable(o)


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:
        return default


def _num(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except Exception:
        return None


def bn_labels(c: dict) -> pd.DataFrame | None:
    f = Path(c["output_dir"]) / "banknifty.parquet"
    if not f.exists():
        return None
    return features.labels({"banknifty": ns_frame(pd.read_parquet(f))}, c)


def outcome(flag, big):
    if big is None or pd.isna(big):
        return "Pending"
    if flag:
        return "Caught" if big else "False alarm"
    return "Missed" if big else "Quiet, as expected"


# ----------------------------------------------------------------- jobs
class _LogWriter(io.TextIOBase):
    def __init__(self, job):
        self.job, self.buf = job, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self.job["log"].append(line.rstrip())
        return len(s)


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.current: dict | None = None

    def busy(self) -> bool:
        return bool(self.current and self.current["status"] == "running")

    def start(self, kind: str, label: str, fn) -> dict:
        with self.lock:
            if self.busy():
                raise HTTPException(409, "Another job is running. Wait for it to finish or stop it.")
            stop = threading.Event()
            job = {"id": uuid.uuid4().hex[:8], "kind": kind, "label": label, "status": "running",
                   "started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None,
                   "log": [], "error": None, "_stop": stop}
            self.current = job

        def run():
            w = _LogWriter(job)
            try:
                with contextlib.redirect_stdout(w):
                    fn(lambda m: print(m), stop.is_set)
                job["status"] = "done"
            except news.Stopped:
                job["status"] = "stopped"
                job["log"].append("Stopped. Anything already downloaded is saved.")
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e) or type(e).__name__
                job["log"].append("Error: " + job["error"])
                job["log"].extend(l for l in traceback.format_exc().splitlines()[-6:])
            finally:
                if w.buf.strip():
                    job["log"].append(w.buf.strip())
                job["finished"] = dt.datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=run, daemon=True).start()
        return job

    def stop(self) -> bool:
        j = self.current
        if j and j["status"] == "running":
            j["_stop"].set()
            j["log"].append("Stopping…")
            return True
        return False


JOBS = Jobs()


def start_train(demo: bool, use_news: bool = True, label: str | None = None):
    lab = label or ("Training on demo data" if demo else "Training on 10 years of real data")
    return JOBS.start("train", lab, lambda log, stop: pipeline.train(cfg(), demo=demo, use_news=use_news,
                                                                    log=log, should_stop=stop))


def start_forecast(label="Updating forecast"):
    return JOBS.start("forecast", label, lambda log, stop: pipeline.forecast(cfg(), log=log, should_stop=stop))


def start_news(label="Downloading news"):
    return JOBS.start("news", label, lambda log, stop: pipeline.download_news(cfg(), log=log, should_stop=stop))


# ----------------------------------------------------------------- scheduler
SCHED_FILE = "scheduler_state.json"


def scheduler_loop():
    while True:
        try:
            c = cfg()
            s = c.get("schedule", {})
            state_f = Path(c["output_dir"]) / SCHED_FILE
            state = read_json(state_f, {}) or {}
            now = dt.datetime.now()
            hh, mm = (int(x) for x in str(s.get("time", "18:30")).split(":"))
            due = now.hour * 60 + now.minute >= hh * 60 + mm
            today = now.date().isoformat()
            has_model = model.load_bundle(c) is not None
            if s.get("enabled") and due and has_model and not JOBS.busy():
                if now.weekday() < 5 and state.get("last_forecast") != today:
                    state["last_forecast"] = today
                    state_f.write_text(json.dumps(state))
                    start_forecast("Scheduled daily forecast")
                elif now.weekday() == 5 and s.get("weekly_retrain") and state.get("last_retrain") != today:
                    state["last_retrain"] = today
                    state_f.write_text(json.dumps(state))
                    demo = bool(model.load_bundle(c).get("demo"))
                    start_train(demo, True, "Scheduled weekly retrain")
        except Exception:
            pass
        time.sleep(30)


@app.on_event("startup")
def _startup():
    vendor = STATIC / "vendor" / "plotly.min.js"
    if not vendor.exists():
        from plotly.offline import get_plotlyjs
        vendor.parent.mkdir(parents=True, exist_ok=True)
        vendor.write_text(get_plotlyjs(), encoding="utf-8")
    threading.Thread(target=scheduler_loop, daemon=True).start()


# ----------------------------------------------------------------- API: status & jobs
def app_version():
    import subprocess
    name = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "1.0"
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%H|%h|%cd", "--date=format:%d %b %Y"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5)
        full, h, d = out.stdout.strip().split("|")
        return {"name": name, "commit": h, "full": full, "date": d}
    except Exception:
        return {"name": name, "commit": None, "full": None, "date": None}


_VERSION_CHECK = {"at": 0.0, "result": None}
RUNNING_VERSION = app_version()       # what this running app was started with


@app.get("/api/version")
def version_check(force: bool = False):
    """Compare this copy with the latest version on GitHub (checked at most every 15 minutes)."""
    import subprocess
    disk = app_version()
    if force or time.time() - _VERSION_CHECK["at"] > 900:
        remote = None
        try:
            out = subprocess.run(["git", "ls-remote", "origin", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=12, env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"})
            remote = out.stdout.split()[0] if out.returncode == 0 and out.stdout.strip() else None
        except Exception:
            remote = None
        _VERSION_CHECK.update(at=time.time(), result=remote)
    remote = _VERSION_CHECK["result"]
    running = RUNNING_VERSION or {}
    if not running.get("full"):
        state = "unknown"
    elif remote and remote != running["full"]:
        state = "update"            # newer version on GitHub
    elif disk.get("full") and disk["full"] != running["full"]:
        state = "restart"           # downloaded but this window still runs the old one
    elif remote:
        state = "latest"
    else:
        state = "offline"
    return {**running, "state": state, "checked_at": _VERSION_CHECK["at"], "latest": remote[:7] if remote else None}


@app.get("/api/status")
def status():
    c = cfg()
    out = Path(c["output_dir"])
    bundle = model.load_bundle(c)
    data = []
    for name, sym in c["tickers"].items():
        f = Path(c["cache_dir"]) / f"mkt_{name}.parquet"
        if f.exists():
            d = ns_frame(pd.read_parquet(f))
            data.append({"name": name, "symbol": sym, "days": len(d),
                         "from": str(d.index.min().date()), "to": str(d.index.max().date())})
        else:
            data.append({"name": name, "symbol": sym, "days": 0, "from": None, "to": None})
    hl = Path(c["news"]["headlines_csv"])
    ev = Path(c["events_csv"]) if c.get("events_csv") else None
    s = c.get("schedule", {})
    return clean({
        "model": None if bundle is None else {k: bundle.get(k) for k in ("trained_at", "data_end", "demo", "news")},
        "training": read_json(out / "last_training.json"),
        "has_forecast": (out / "forecast_latest.json").exists(),
        "job": job_summary(),
        "data": data,
        "news": {"gdelt": news.gdelt_progress(c), "gdelt_queries": len(c["news"].get("gdelt_queries", {})),
                 "headlines_file": hl.exists(), "headlines_mb": round(hl.stat().st_size / 1e6, 1) if hl.exists() else 0,
                 "events_file": bool(ev and ev.exists()),
                 "events_rows": int(len(pd.read_csv(ev))) if ev and ev.exists() else 0},
        "schedule": {"enabled": s.get("enabled"), "time": s.get("time"), "weekly_retrain": s.get("weekly_retrain")},
        "version": app_version(),
    })


def job_summary(since: int = 0):
    j = JOBS.current
    if not j:
        return None
    steps = [l for l in j["log"] if l.startswith("Step ")]
    return {k: j[k] for k in ("id", "kind", "label", "status", "started", "finished", "error")} | {
        "step": steps[-1] if steps else None, "lines": j["log"][since:], "total_lines": len(j["log"])}


@app.get("/api/jobs/current")
def job_current(since: int = 0):
    return job_summary(since) or {}


@app.post("/api/jobs")
async def job_start(req: Request):
    body = await req.json()
    kind = body.get("kind")
    if kind == "train":
        job = start_train(bool(body.get("demo")), True)
    elif kind == "forecast":
        if model.load_bundle(cfg()) is None:
            raise HTTPException(400, "Train the model first, then update the forecast.")
        job = start_forecast()
    elif kind == "news":
        job = start_news()
    else:
        raise HTTPException(400, "Unknown job")
    return {"id": job["id"]}


@app.post("/api/jobs/stop")
def job_stop():
    return {"stopping": JOBS.stop()}


# ----------------------------------------------------------------- API: forecast
@app.get("/api/forecast")
def forecast():
    c = cfg()
    out = Path(c["output_dir"])
    fc = read_json(out / "forecast_latest.json")
    calls = []
    logf = out / "forecast_log.csv"
    if logf.exists():
        lg = pd.read_csv(logf, dtype={"for_date": str}).sort_values("for_date", ascending=False).head(30)
        lab = bn_labels(c)
        for _, r in lg.iterrows():
            d = pd.Timestamp(r["for_date"])
            big = rng = None
            if lab is not None and d in lab.index:
                big = lab.at[d, "big_move"]
                rng = lab.at[d, "range_pct"]
            calls.append({"date": r["for_date"], "prob_big": r["prob_big"], "flag": bool(r["flag"]),
                          "range_pct": _num(rng), "big": None if big is None or pd.isna(big) else bool(big),
                          "outcome": outcome(bool(r["flag"]), big), "demo": bool(r.get("demo", 0))})
    return clean({"forecast": fc, "calls": calls})


# ----------------------------------------------------------------- API: history
@app.get("/api/history")
def history():
    c = cfg()
    out = Path(c["output_dir"])
    f = out / "banknifty.parquet"
    if not f.exists():
        return {"dates": []}
    bn = ns_frame(pd.read_parquet(f))
    lab = features.labels({"banknifty": bn}, c)
    oos_f = out / "oos_predictions.csv"
    oos = pd.read_csv(oos_f, parse_dates=["date"]).assign(date=lambda d: ns_col(d["date"])) if oos_f.exists() else pd.DataFrame(columns=["date", "target"])
    bm = oos[oos["target"] == "big_move"].set_index("date")
    df = bn.join(lab[["big_move", "range_pct"]]).join(bm[["prob", "flag", "threshold"]])
    return clean({
        "dates": [str(d.date()) for d in df.index],
        "open": df["Open"].round(2).tolist(), "high": df["High"].round(2).tolist(),
        "low": df["Low"].round(2).tolist(), "close": df["Close"].round(2).tolist(),
        "big": df["big_move"].tolist(), "range": df["range_pct"].tolist(),
        "prob": df["prob"].tolist(), "flag": df["flag"].tolist(), "threshold": df["threshold"].tolist(),
    })


@app.get("/api/day/{date}")
def day(date: str):
    c = cfg()
    out = Path(c["output_dir"])
    d = pd.Timestamp(date)
    bn_f = out / "banknifty.parquet"
    if not bn_f.exists():
        raise HTTPException(404, "No data yet. Train the model first.")
    bn = ns_frame(pd.read_parquet(bn_f))
    if d not in bn.index:
        raise HTTPException(404, f"{date} is not a trading day in the data.")
    lab = features.labels({"banknifty": bn}, c)
    row = lab.loc[d]
    oos_f = out / "oos_predictions.csv"
    prob = flag = None
    if oos_f.exists():
        o = pd.read_csv(oos_f, parse_dates=["date"]).assign(date=lambda d: ns_col(d["date"]))
        o = o[(o["target"] == "big_move") & (o["date"] == d)]
        if len(o):
            prob, flag = float(o["prob"].iloc[0]), bool(o["flag"].iloc[0])
    drivers, cues, news_rows = [], [], []
    X = ns_frame(pd.read_parquet(out / "features.parquet")) if (out / "features.parquet").exists() else None
    bundle = model.load_bundle(c)
    if X is not None and d in X.index:
        xr = X.loc[[d]]
        if bundle is not None:
            drivers = [{"feature": f, "label": report.nice(f), "value": v, "effect": e}
                       for f, v, e in model.drivers(bundle, xr, top=8)[0]]
        cues = [{"feature": f, "label": report.nice(f), "value": _num(xr[f].iloc[0])}
                for f in pipeline.KEY_FEATURES if f in xr.columns]
        news_rows = [{"feature": f, "label": report.nice(f), "value": _num(xr[f].iloc[0])}
                     for f in xr.columns if f.startswith("gd_") and f.endswith(("_tone_1d", "_vol_z3"))]
    heads = []
    hp = Path(c["cache_dir"]) / "headlines_scored.parquet"
    if hp.exists():
        h = ns_frame(pd.read_parquet(hp))
        h = h[(h["date"] >= d - pd.Timedelta(days=1)) & (h["date"] <= d)]
        h = h.reindex(h["score"].abs().sort_values(ascending=False).index).head(8)
        heads = [{"date": str(r["date"].date()), "text": r["headline_text"], "score": float(r["score"])}
                 for _, r in h.iterrows()]
    big = row["big_move"]
    return clean({
        "date": date, "open": row["open"], "close": row["close"],
        "high": float(bn.at[d, "High"]), "low": float(bn.at[d, "Low"]),
        "range_pct": row["range_pct"], "oc_pct": row["oc_pct"], "gap_pct": row["gap_pct"],
        "ret_pct": row["ret_pct"], "threshold": row["big_threshold"],
        "big": None if pd.isna(big) else bool(big), "prob": prob, "flag": flag,
        "outcome": outcome(flag, big) if flag is not None else None,
        "drivers": drivers, "cues": cues, "news": news_rows, "headlines": heads,
    })


@app.get("/api/biggest")
def biggest():
    f = out_dir() / "biggest_days.csv"
    if not f.exists():
        return []
    df = pd.read_csv(f)
    return clean(df.replace({np.nan: None}).to_dict(orient="records"))


@app.get("/api/factors")
def factors():
    out = out_dir()
    fac = pd.read_csv(out / "crucial_factors.csv") if (out / "crucial_factors.csv").exists() else pd.DataFrame()
    imp = pd.read_csv(out / "importance.csv", index_col=0)["importance"] if (out / "importance.csv").exists() else pd.Series(dtype=float)
    lab_path = out / "labels.parquet"
    base = float(ns_frame(pd.read_parquet(lab_path))["big_move"].mean()) if lab_path.exists() else None
    rows = []
    if not fac.empty:
        hi_col = [c for c in fac.columns if "when high" in c][0]
        lo_col = [c for c in fac.columns if "when low" in c][0]
        for _, r in fac.iterrows():
            rows.append({"feature": r["feature"], "label": report.nice(r["feature"]),
                         "high": _num(r[hi_col]), "low": _num(r[lo_col])})
    return clean({"base": base, "factors": rows,
                  "importance": [{"feature": f, "label": report.nice(f), "value": _num(v)}
                                 for f, v in imp.head(20).items()]})


@app.get("/api/performance")
def performance():
    out = out_dir()
    mets = read_json(out / "metrics.json")
    if not mets:
        return {}
    bt_f = out / "backtest_proxy.csv"
    eq = {}
    if bt_f.exists():
        bt = ns_frame(pd.read_csv(bt_f, index_col=0, parse_dates=True))
        fl = bt[bt["flag"] == 1]
        eq = {"all_dates": [str(d.date()) for d in bt.index], "all": (bt["pnl_pct"].cumsum() * 100).round(3).tolist(),
              "flag_dates": [str(d.date()) for d in fl.index], "flag": (fl["pnl_pct"].cumsum() * 100).round(3).tolist()}
    return clean({"metrics": mets, "backtest": read_json(out / "backtest_summary.json", []), "equity": eq,
                  "training": read_json(out / "last_training.json")})


# ----------------------------------------------------------------- API: settings & uploads
def masked_settings():
    v = editable_values(cfg())
    for k in SECRET_KEYS:
        if v.get(k):
            v[k] = "••••" + str(v[k])[-4:]
    return clean(v)


@app.get("/api/settings")
def get_settings():
    return masked_settings()


@app.post("/api/settings")
async def post_settings(req: Request):
    body = await req.json()
    try:
        if "schedule.time" in body:
            hh, mm = str(body["schedule.time"]).split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
        body = {k: v for k, v in body.items() if not (isinstance(v, str) and v.startswith("••••"))}
        save_user_settings(body)
    except (ValueError, AssertionError) as e:
        raise HTTPException(400, str(e) or "Time must look like 18:30")
    return masked_settings()


@app.post("/api/upload/{kind}")
async def upload(kind: str, req: Request):
    c = cfg()
    if kind == "headlines":
        dest, need = Path(c["news"]["headlines_csv"]), {"publish_date", "headline_text"}
    elif kind == "events":
        dest, need = Path(c["events_csv"]), {"date", "event"}
    else:
        raise HTTPException(400, "Unknown upload")
    tmp = dest.with_suffix(".uploading")
    with open(tmp, "wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    try:
        head = pd.read_csv(tmp, nrows=5)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "That file isn't a readable CSV.")
    missing = need - set(head.columns)
    if missing:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"The CSV needs these columns: {', '.join(sorted(need))}. Missing: {', '.join(sorted(missing))}.")
    tmp.replace(dest)
    if kind == "headlines":
        (Path(c["cache_dir"]) / "headlines_scored.parquet").unlink(missing_ok=True)
    return {"ok": True, "mb": round(dest.stat().st_size / 1e6, 1)}


@app.post("/api/telegram/test")
def telegram_test():
    lines = []
    ok = pipeline.send_telegram(cfg(), "Bank Nifty Radar: test message. Alerts are working.", lines.append)
    if not ok:
        raise HTTPException(400, (lines[-1] if lines else "Telegram failed").strip())
    return {"ok": True}


@app.get("/report")
def full_report():
    f = out_dir() / "report.html"
    if not f.exists():
        raise HTTPException(404, "Train the model first to create the report.")
    return FileResponse(f)


# ----------------------------------------------------------------- strategy lab
def _lab_cfg():
    return cfg().get("lab") or {}


def _lab_progress(log, label):
    state = {"last": -1}
    def p(done, total):
        pct = int(done / max(1, total) * 100)
        if pct // 5 != state["last"]:
            state["last"] = pct // 5
            log(f"Step 1/2: {label} {pct}%")
    return p


def _clear_synthetic():
    from lab.data import hist_dir
    hd = hist_dir(ROOT / "data")
    if (hd / "SYNTHETIC").exists():
        import shutil
        for d in hd.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        (hd / "SYNTHETIC").unlink(missing_ok=True)


def _lab_reload():
    try:
        from webapp.live_api import engine as _eng
        _eng().desk.load_plan()
    except Exception:
        pass


@app.get("/api/lab/status")
def lab_status():
    from lab.data import have_data, hist_dir
    from lab.walk import lab_dir
    c = _lab_cfg()
    d = lab_dir(ROOT / "data")
    res = None
    if (d / "results.json").exists():
        r = json.loads((d / "results.json").read_text())
        res = {"generated": r.get("generated"), "summary": r.get("summary"), "period": r.get("period")}
    return clean({"data": have_data(ROOT / "data"), "synthetic": (hist_dir(ROOT / "data") / "SYNTHETIC").exists(),
                  "dhan": bool(c.get("dhan_client_id") and c.get("dhan_token")), "upstox": bool(_upstox_token()), "results": res,
                  "years": c.get("years", 3)})


@app.get("/api/lab/results")
def lab_results():
    from lab.walk import lab_dir
    f = lab_dir(ROOT / "data") / "results.json"
    if not f.exists():
        raise HTTPException(404, "No backtest yet.")
    r = json.loads(f.read_text())
    from lab.data import hist_dir
    r["synthetic"] = (hist_dir(ROOT / "data") / "SYNTHETIC").exists()
    return clean(r)


@app.post("/api/lab/dhan/test")
def lab_dhan_test():
    from lab.dhan import Dhan
    c = _lab_cfg()
    if not (c.get("dhan_client_id") and c.get("dhan_token")):
        raise HTTPException(400, "Add your Dhan client ID and access token first.")
    try:
        p = Dhan(c["dhan_client_id"], c["dhan_token"]).profile()
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(502, f"Dhan did not answer: {e}")
    return {"ok": True, "profile": {k: p.get(k) for k in ("dhanClientId", "tokenValidity", "activeSegment", "dataPlan", "dataValidity")}}


def _upstox_token():
    from live.upstox import TokenStore
    rec = TokenStore(ROOT / "data" / "upstox_token.json").load()
    return rec and rec.get("access_token")


@app.post("/api/lab/upstox/test")
def lab_upstox_test():
    from lab.upstox_hist import UpstoxHistory, PlusRequired
    tok = _upstox_token()
    if not tok:
        raise HTTPException(401, "Log in to Upstox first (Live setup page).")
    try:
        up = UpstoxHistory(tok)
        ex = {s: up.expiries(s) for s in ("NIFTY", "BANKNIFTY")}
    except PlusRequired as e:
        raise HTTPException(402, str(e))
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(502, f"Upstox did not answer: {e}")
    return {"ok": True, "expiries": {s: {"n": len(v), "first": v[0] if v else None, "last": v[-1] if v else None}
                                     for s, v in ex.items()}}


@app.post("/api/lab/download")
async def lab_download_start(req: Request):
    body = await req.json() if (await req.body()) else {}
    c = _lab_cfg()
    years = int(c.get("years") or 3)
    has_dhan = bool(c.get("dhan_client_id") and c.get("dhan_token"))
    source = body.get("source") or ("upstox" if _upstox_token() or not has_dhan else "dhan")
    if source == "upstox":
        from lab.upstox_hist import download as up_download
        tok = _upstox_token()
        if not tok:
            raise HTTPException(401, "Log in to Upstox first (Live setup page). Historical option data needs Upstox Plus.")

        def up_job(log, stop):
            _clear_synthetic()
            end = dt.date.today() - dt.timedelta(days=1)
            start = dt.date(end.year - years, end.month, 1)
            log(f"Downloading 1-minute Nifty and Bank Nifty options from Upstox ({max(start, dt.date(2022, 1, 1)):%b %Y} to {end:%b %Y})")
            up_download(tok, ROOT / "data", start=start, end=end, log=log, should_stop=stop,
                        progress=_lab_progress(log, "Downloading history"))
        return {"id": JOBS.start("lab_download", "Downloading option history from Upstox", up_job)["id"]}
    from lab.dhan import Dhan
    from lab.data import download
    if not has_dhan:
        raise HTTPException(400, "Log in to Upstox (Plus) or add Dhan keys first.")

    def job(log, stop):
        _clear_synthetic()
        d = Dhan(c["dhan_client_id"], c["dhan_token"])
        end = dt.date.today() - dt.timedelta(days=1)
        start = dt.date(end.year - years, end.month, 1)
        log(f"Downloading {years} years of 1-minute Nifty and Bank Nifty options ({start:%b %Y} to {end:%b %Y})")
        download(d, ROOT / "data", start=start, end=end, log=log, should_stop=stop,
                 progress=_lab_progress(log, "Downloading history"))
    return {"id": JOBS.start("lab_download", "Downloading option history from Dhan", job)["id"]}


@app.post("/api/lab/backtest")
def lab_backtest_start():
    from lab.walk import run_all
    c = _lab_cfg()
    ucfg = {k: float(c[k]) for k in ("capital", "risk_pct", "alloc_pct") if c.get(k)}

    def job(log, stop):
        run_all(ROOT / "data", ucfg, log=log, should_stop=stop)
        _lab_reload()
    return {"id": JOBS.start("lab_backtest", "Testing strategies on history", job)["id"]}


@app.post("/api/lab/demo")
def lab_demo_start():
    from lab.data import have_data
    from lab.synth import generate
    from lab.walk import run_all
    if any(v["months"] for v in have_data(ROOT / "data").values()) and not (ROOT / "data" / "history" / "SYNTHETIC").exists():
        raise HTTPException(400, "Real history is already downloaded. The demo would replace it, so it is disabled.")

    def job(log, stop):
        log("Step 1/5: making 15 months of synthetic (fake) option data for a demo")
        _clear_synthetic()
        generate(ROOT / "data", dt.date(2025, 1, 1), dt.date(2026, 3, 31))
        run_all(ROOT / "data", {"train_months": 4}, log=log, should_stop=stop)
        _lab_reload()
    return {"id": JOBS.start("lab_backtest", "Demo backtest on synthetic data", job)["id"]}


@app.get("/api/lab/desk")
def lab_desk(source: str | None = None):
    from webapp.live_api import engine as _eng
    return clean(_eng().desk.view(source))


# ----------------------------------------------------------------- live scanner
from webapp.live_api import engine as live_engine, router as live_router  # noqa: E402

app.include_router(live_router)


@app.on_event("startup")
def _start_live():
    threading.Thread(target=live_engine, daemon=True).start()   # starts the 9:00 auto-start watcher


# ----------------------------------------------------------------- UI
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")
