"""Bank Nifty Radar - local web app (FastAPI). Start with:  python app.py"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import shutil
import threading
import time
import traceback
import uuid
import warnings
from pathlib import Path

# Library deprecation notices (yfinance on newer pandas) flood the Terminal and hide real problems: keep them out.
warnings.filterwarnings("ignore", message=r".*inplace keyword.*")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"yfinance(\..*)?")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"yfinance(\..*)?")
import radar  # noqa: E402,F401  (installs the quiet warning printer for every thread)

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from radar import features, model, news, pipeline, report
from radar.dates import ns_col, ns_frame
from radar.config import ROOT, SECRET_KEYS, editable_values, load_config, save_user_settings

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Bank Nifty Radar", docs_url=None, redoc_url=None)
try:                                    # 2.50 (Mandeep #7, speed): big JSON answers go compressed (the alerts list is
    from starlette.middleware.gzip import GZipMiddleware      # ~2.7 MB plain, a few hundred KB zipped; phone / tunnel)
    app.add_middleware(GZipMiddleware, minimum_size=4096, compresslevel=5)
except Exception:
    pass


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


_sched = {}


def scheduler_loop():
    while True:
        _sched["tick"] = time.time()
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
            # stock option history: after the close, fetch any newly expired month (only once stocks were downloaded)
            if (now.weekday() < 5 and now.hour >= 17 and state.get("last_stock_hist") != today
                    and any((ROOT / "data" / "history").glob("*/meta.json")) and not STOCK_HIST.running()):
                state["last_stock_hist"] = today
                state_f.write_text(json.dumps(state))
                try:
                    STOCK_HIST.start(months=2, why="nightly")
                except Exception:
                    pass
            # 5-year Lab: Dhan allows ~100k calls a day, so the download continues by itself each evening until complete
            # 5-year download: resumes by itself whenever it can (app reopened, network back, new day, new token)
            if time.time() - _sched.get("d5", 0) > 120:
                _sched["d5"] = time.time()
                try:
                    if DHAN5Y.should_auto_start():
                        DHAN5Y.start(why="auto")
                except Exception:
                    pass
            try:                                  # Dhan token: renew before it expires (24 h tokens), no PIN / TOTP involved
                from lab import dhan_token
                dhan_token.maybe_renew(log=lambda m: print(f"[dhan] {m}"))
            except Exception:
                pass
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
    from lab import dhan_budget, dhan_token
    dhan_budget.setup(ROOT / "data" / "dhan_calls.json")
    dhan_token.setup(ROOT / "data", lambda: (_lab_cfg().get("dhan_client_id"), _lab_cfg().get("dhan_token")),
                     lambda tok: save_user_settings({"lab.dhan_token": tok}))

    def _dead_note(msg):
        from webapp.live_api import engine as _eng
        if _lab_cfg().get("dhan_api_key"):
            msg = ("Stockway: the Dhan token has expired. On the Mac open http://127.0.0.1:8765/dhan/login (or Strategy desk → "
                   "Log in with Dhan) and log in on Dhan's page. The download, news and movers resume by themselves.")
        _eng().telegram.send("🔑 " + msg)
    dhan_token.on_dead = _dead_note
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
        # one capital for the whole app: paper trades, the risk check and the strategy desk size from the same money
        for a, b in (("lab.capital", "live.paper.capital"), ("live.paper.capital", "lab.capital")):
            if a in body and b not in body:
                body[b] = body[a]
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
                  "dhan_token": _dhan_token_status(),
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


@app.get("/api/lab/eod/status")
def lab_eod_status():
    from lab.bhav import Bhavcopy
    return Bhavcopy(ROOT / "data").status()


@app.post("/api/lab/eod/download")
def lab_eod_download():
    from lab.bhav import Bhavcopy

    def job(log, stop):
        Bhavcopy(ROOT / "data", log).download(dt.date(2015, 1, 1), should_stop=stop, progress=_lab_progress(log, "Downloading end-of-day files"))
    return {"id": JOBS.start("lab_eod", "Downloading 10+ years of NSE end-of-day F&O data", job)["id"]}


@app.post("/api/lab/eod/run")
def lab_eod_run():
    from lab.eod_study import run

    def job(log, stop):
        run(ROOT / "data", log=log)
        try:
            from webapp.live_api import engine as _eng
            _eng().expiry.load_plan()
        except Exception:
            pass
        try:
            from lab.scenario import run as scen
            scen(ROOT / "data", log=log)
        except Exception as ex:
            log(f"Days-like-today check skipped: {ex}")
    return {"id": JOBS.start("lab_eod", "Testing strategies on 10+ years of end-of-day data", job)["id"]}


@app.get("/api/lab/scenario")
def lab_scenario():
    """Days like today: refreshed automatically when a newer end-of-day file or a new 10-year test exists."""
    from lab import scenario
    from lab.walk import lab_dir
    f = lab_dir(ROOT / "data") / "scenario.json"
    if scenario.stale(ROOT / "data"):
        try:
            return clean(scenario.run(ROOT / "data", log=lambda m: None))
        except Exception as ex:
            if not f.exists():
                return {"error": str(ex)}
    if not f.exists():
        return None
    return clean(json.loads(f.read_text()))


@app.post("/api/lab/scenario/run")
def lab_scenario_run():
    from lab import scenario
    try:
        return clean(scenario.run(ROOT / "data", log=lambda m: None))
    except Exception as ex:
        raise HTTPException(400, str(ex))


# ----------------------------------------------------------------- Strategy Lab (lab/factory.py, lab/ebooks.py)
@app.get("/api/factory")
def factory_view():
    from lab import ebooks, factory
    out = factory.view(ROOT / "data")
    out["books"] = ebooks.books(ROOT / "data")
    try:
        from lab import mlstrat
        out["ml"] = mlstrat.load(ROOT / "data")
    except Exception:
        out["ml"] = {}
    out["has_history"] = any((ROOT / "data" / "history").glob("*/raw_*.parquet"))
    try:
        from webapp.live_api import engine as _eng
        e = _eng()
        out["live_rules"] = [{"id": r["id"], "rule": r["rule"]} for r in e.factory_rules()]
        out["ai"] = e.ai.enabled()
    except Exception:
        out["live_rules"], out["ai"] = [], False
    return clean(out)


@app.post("/api/factory/run")
async def factory_run(req: Request):
    from lab import factory
    b = await req.json() if (await req.body()) else {}
    n = max(500, min(int(b.get("n") or 6000), 30000))

    def job(log, stop):
        try:
            from lab import library
            q = factory.queue_untested(ROOT / "data", library.rules(), source="library")
            if q["queued"]:
                log(f"{q['queued']} untested library / book rules queued")
        except Exception as e:
            log(f"Library not queued: {e}")
        from lab import proc                              # 2.38: in its own low-priority process (the app stays quick)
        proc.run("factory", ROOT / "data", log=log, should_stop=stop, n_random=n, n_mutate=n // 2)
        if not stop():
            try:
                log("Training model-based strategies (3 models) on the same periods…")
                proc.run("mlstrat", ROOT / "data", log=log, should_stop=stop)
            except Exception as e:
                log(f"ML strategies failed: {e}")
        try:
            from webapp.live_api import engine as _eng
            _eng()._fact_rules = (0.0, [])
        except Exception:
            pass
    return {"id": JOBS.start("factory", f"Strategy Lab: testing {n + n // 2:,} rules on train / validation / holdout", job)["id"]}


@app.post("/api/factory/library")
def factory_library():
    """Queue the strategy library (famous setups, every exit and setting) for the next research run."""
    from lab import factory, library
    return factory.add_ideas(ROOT / "data", library.rules(), source="library")


@app.post("/api/factory/ideas")
def factory_ideas():
    from lab import factory
    from webapp.live_api import engine as _eng
    try:
        return factory.ask_ideas(ROOT / "data", _eng().ai, auto=False)
    except PermissionError as ex:
        raise HTTPException(401, str(ex))


@app.post("/api/factory/ebook")
async def factory_ebook_upload(req: Request, name: str):
    """Upload one ebook (PDF / EPUB / TXT) into data/ebooks."""
    from lab import ebooks
    import re as _re
    safe = _re.sub(r"[^A-Za-z0-9._ ()-]", "_", Path(name).name)[:120]
    if not safe.lower().endswith((".pdf", ".epub", ".txt", ".md")):
        raise HTTPException(400, "PDF, EPUB or TXT only.")
    data = await req.body()
    if len(data) > 80e6:
        raise HTTPException(400, "File too large (80 MB max).")
    (ebooks.book_dir(ROOT / "data") / safe).write_bytes(data)
    return {"ok": True, "name": safe}


@app.post("/api/factory/ebook/read")
async def factory_ebook_read(req: Request):
    from lab import ebooks
    from webapp.live_api import engine as _eng
    b = await req.json()
    name = Path(str(b.get("name", ""))).name
    e = _eng()
    if not e.ai.enabled():
        raise HTTPException(401, "Connect Claude (your Pro plan) in Live setup → AI first.")

    def job(log, stop):
        ebooks.extract(ROOT / "data", name, e.ai, log=log, should_stop=stop)
    return {"id": JOBS.start("factory_book", f"Reading {name} with Claude", job)["id"]}


@app.post("/api/factory/rule/{rid}")
async def factory_rule_live(rid: str, req: Request):
    from lab import factory
    b = await req.json()
    try:
        r = factory.set_live(ROOT / "data", rid, bool(b.get("on")))
    except KeyError:
        raise HTTPException(404, "Unknown rule")
    except PermissionError as ex:                          # 2.43: strategy-file rules stay on the bench
        raise HTTPException(403, str(ex))
    from webapp.live_api import engine as _eng
    _eng()._fact_rules = (0.0, [])
    return {"ok": True, "live": r.get("live")}


# ---- 2.45 trade journeys (live/paths.py): bench signals held to 15:15, built from the day's recording
@app.get("/api/paths")
def paths_view(days: int = 60):
    from live import paths
    return clean(paths.view(ROOT / "data", days=max(1, min(int(days), 365))))


@app.post("/api/paths/run")
def paths_run():
    from lab import proc

    def job(log, stop):
        proc.run("paths", ROOT / "data", log=log, should_stop=stop)
    return {"id": JOBS.start("paths", "Trade journeys: replaying recorded days", job)["id"]}


# ---- 2.58 multiverse (lab/multiverse.py): every bench signal down every timeline, judged walk-forward
@app.get("/api/multiverse")
def multiverse_view():
    from lab import multiverse
    return clean(multiverse.view(ROOT / "data"))


@app.post("/api/multiverse/run")
def multiverse_run():
    from lab import proc

    def job(log, stop):
        proc.run("multiverse", ROOT / "data", log=log, should_stop=stop)
    return {"id": JOBS.start("multiverse", "Multiverse: every signal down every timeline", job)["id"]}


# ---- 2.43 strategy files (lab/packs.py): upload a .json, checked against a fixed vocabulary, data only
@app.get("/api/packs")
def packs_view():
    from lab import packs
    return clean(packs.view(ROOT / "data"))


@app.get("/api/factory/rules_detail")
def factory_rules_detail(ids: str = "", lab: str = "1y"):
    """2.49 (Lab page): saved rules by id with their results split by stock / index (factory_sym.json). Read-only."""
    from lab import factory as F
    d = ROOT / "data" if lab != "5y" else ROOT / "data" / "lab5y"
    rules = F.load(d).get("rules", {})
    syms = F.load_by_sym(d)
    out = {}
    for i in [x for x in ids.split(",") if x][:60]:
        r = rules.get(i)
        if not r:
            out[i] = {"id": i, "missing": True}
            continue
        out[i] = {"id": i, "name": r.get("name"), "rule": r.get("rule"), "stage": r.get("stage"), "evidence": r.get("evidence"),
                  "exit": F.exit_words(r.get("exit", "")), "syms": r.get("syms"), "train": r.get("train"), "valid": r.get("valid"),
                  "hold": r.get("hold"), "by_sym": syms.get(i)}
    return clean({"lab": lab, "rules": out, "period": F.load(d).get("period")})


@app.get("/api/packs/vocabulary")
def packs_vocabulary():
    from lab import packs
    return clean(packs.vocabulary())


@app.get("/api/lines/status")
def lines_status():
    """2.54 smart chart lines: which line book is in use and the live record (all symbols)."""
    from lab import levelbook
    out = levelbook.status(ROOT / "data")
    try:
        from webapp.live_api import engine as _eng
        lg = getattr(_eng(), "linegrade", None)
        out["live"] = lg.summary() if lg is not None else None
    except Exception:
        out["live"] = None
    return clean(out)


@app.post("/api/lines/rebuild")
def lines_rebuild():
    """Rebuild your own line book from the Mac's 1-minute history (background, one at a time)."""
    from lab import levelbook
    if not (ROOT / "data" / "history" / "NIFTY" / "minutes.parquet").exists():
        raise HTTPException(400, "No Nifty 1-minute history on this Mac yet.")
    started = levelbook.rebuild_async(ROOT / "data", log=lambda m: None)
    return {"ok": True, "started": started, "note": None if started else "already rebuilding"}


@app.post("/api/packs/upload")
async def packs_upload(req: Request, name: str = ""):
    from lab import packs
    if not str(name).lower().endswith(".json"):
        raise HTTPException(400, "A strategy file is a .json file.")
    data = await req.body()
    if len(data) > packs.MAX_BYTES:
        raise HTTPException(400, f"File too large ({packs.MAX_BYTES // 1000} KB max).")
    try:
        out = packs.store(ROOT / "data", data, name)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    try:                                                   # the test bench picks the new rules up at once
        from webapp.live_api import engine as _eng
        _eng().charts._lib_cache = None
    except Exception:
        pass
    return clean(out)


@app.post("/api/packs/{pack_id}/on")
async def packs_on(pack_id: str, req: Request):
    from lab import packs
    b = await req.json()
    try:
        out = packs.set_on(ROOT / "data", pack_id, bool(b.get("on")))
    except KeyError:
        raise HTTPException(404, "Unknown strategy file")
    try:
        from webapp.live_api import engine as _eng
        _eng().charts._lib_cache = None
    except Exception:
        pass
    return out


# ---- stock option history: its own background worker (hours long), so Lab runs and other jobs aren't blocked
class StockHist:
    def __init__(self):
        self.st = {"status": "idle", "log": [], "done": 0, "total": 0, "started": None, "finished": None, "error": None}
        self._stop = threading.Event()
        self._t = None

    def running(self):
        return bool(self._t and self._t.is_alive())

    def start(self, months=12, syms=None, why="manual"):
        if self.running():
            raise HTTPException(409, "The stock history download is already running.")
        tok = _upstox_token()
        if not tok:
            raise HTTPException(401, "Log in to Upstox first (Live setup page). Stock option history needs Upstox Plus.")
        self._stop.clear()
        self.st = {"status": "running", "log": [], "done": 0, "total": 0, "why": why, "months": months,
                   "started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None, "error": None}

        def log(m):
            self.st["log"] = (self.st["log"] + [f"{dt.datetime.now():%H:%M:%S} {m}"])[-200:]

        def prog(d, t):
            self.st["done"], self.st["total"] = d, t

        def run():
            from lab import stock_hist
            try:
                keys = stock_hist.stock_keys(ROOT / "data", syms)
                log(f"{len(keys)} F&O stocks: {', '.join(keys)}")
                stock_hist.download(tok, ROOT / "data", keys, months=months, log=log, should_stop=self._stop.is_set, progress=prog)
                self.st["status"] = "stopped" if self._stop.is_set() else "done"
            except Exception as e:
                self.st["status"], self.st["error"] = "failed", str(e) or type(e).__name__
                log("Error: " + self.st["error"])
            finally:
                self.st["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        return self.view()

    def stop(self):
        self._stop.set()
        return self.view()

    def view(self):
        from lab import stock_hist
        from lab.data import register_stocks
        hd = ROOT / "data" / "history"
        syms = register_stocks(ROOT / "data") or []
        have = {p.parent.name for p in hd.glob("*/meta.json")}
        expiries = sum(len(list((hd / s).glob("raw_*.parquet"))) for s in have)
        return {**{k: v for k, v in self.st.items() if k != "log"}, "log": self.st["log"][-12:], "running": self.running(),
                "stocks_on_disk": sorted(have), "expiries_on_disk": expiries, "default": stock_hist.DEFAULT}


STOCK_HIST = StockHist()


class Dhan5y(StockHist):
    """Dhan expired-options archive (2021 →) for the 5-year Lab challenger, in its own background worker."""

    def start(self, months=None, syms=None, why="manual"):
        if self.running():
            raise HTTPException(409, "The 5-year download is already running.")
        c = _lab_cfg()
        if not (c.get("dhan_client_id") and c.get("dhan_token")):
            raise HTTPException(400, "Add your Dhan client ID and access token first (Strategy desk → Data and settings).")
        from lab import dhan_token
        if dhan_token.is_dead():
            raise HTTPException(401, "The Dhan token has expired. Make a new one on web.dhan.co and paste it in Strategy desk → Dhan keys.")
        self._stop.clear()
        if why == "manual":
            self.user_stop = None
        self.st = {"status": "running", "log": self.st.get("log", [])[-50:], "done": 0, "total": 0, "why": why,
                   "started": dt.datetime.now().isoformat(timespec="seconds"), "finished": None, "error": None}

        def log(m):
            self.st["log"] = (self.st["log"] + [f"{dt.datetime.now():%H:%M:%S} {m}"])[-200:]

        def prog(d, t):
            self.st["done"], self.st["total"] = d, t

        def run():
            from lab import dhan_hist
            from lab.dhan import Dhan
            try:
                d = Dhan(c["dhan_client_id"], c["dhan_token"])
                d.background = True                 # 2.38: gives way to charts, news and movers on the shared Dhan pace
                res = dhan_hist.download(d, ROOT / "data", syms, log=log, should_stop=self._stop.is_set, progress=prog)
                self.st["status"] = "stopped" if self._stop.is_set() else res.get("result", "done")
                self.st["result_day"] = dt.date.today().isoformat()
            except PermissionError as e:
                self.st["status"], self.st["error"] = "token", str(e)
                log("Dhan token expired: paste a new one in Strategy desk → Dhan keys; the download resumes by itself.")
            except Exception as e:
                self.st["status"], self.st["error"] = "failed", str(e) or type(e).__name__
                log("Error: " + self.st["error"])
            finally:
                self.st["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()
        return self.view()

    user_stop = None
    _ov = (0.0, None)

    def stop(self):
        self.user_stop = dt.date.today().isoformat()      # a Stop by hand is respected until tomorrow or Update
        return super().stop()

    def overview(self):
        from lab import dhan_hist
        t, ov = self._ov
        if not ov or time.time() - t > 20:
            try:
                ov = dhan_hist.overview(ROOT / "data")
            except Exception as e:
                ov = {"error": str(e)}
            self._ov = (time.time(), ov)
        return ov

    def should_auto_start(self) -> bool:
        """Resume by itself: app reopened, network back, new day's budget, new token. Not after a Stop by hand today."""
        from lab import dhan_budget, dhan_token
        c = _lab_cfg()
        if self.running() or not (c.get("dhan_client_id") and c.get("dhan_token")) or dhan_token.is_dead():
            return False
        if self.user_stop == dt.date.today().isoformat():
            return False
        if not any((ROOT / "data" / "lab5y" / "history").glob("*/raw_????-??.parquet")):
            return False                                   # never started by hand: don't begin on its own
        if dhan_budget.used() >= dhan_hist_cap():
            return False
        st = self.st.get("status")
        if st in ("done",) and self.st.get("result_day") == dt.date.today().isoformat():
            return False                                   # complete for today: tomorrow adds the new day
        if st == "disk" and self.st.get("result_day") == dt.date.today().isoformat():
            return False
        if st == "failed" and self.st.get("finished") and \
                (dt.datetime.now() - dt.datetime.fromisoformat(self.st["finished"])).total_seconds() < 900:
            return False
        return True

    def view(self):
        from lab import dhan_budget, dhan_hist, dhan_token
        hd = dhan_hist.root(ROOT / "data") / "history"
        months = {p.parent.name: 0 for p in hd.glob("*/raw_????-??.parquet")}
        for p in hd.glob("*/raw_????-??.parquet"):
            months[p.parent.name] += 1
        out = {**{k: v for k, v in self.st.items() if k != "log"}, "log": self.st["log"][-12:], "running": self.running(),
               "months": months, "dhan": bool(_lab_cfg().get("dhan_client_id") and _lab_cfg().get("dhan_token")),
               "overview": self.overview(), "token": dhan_token.status(), "calls_today": dhan_budget.used(),
               "user_stop": self.user_stop == dt.date.today().isoformat()}
        try:
            out["compare"] = dhan_hist.compare(ROOT / "data")
        except Exception as e:
            out["compare"] = {"error": str(e)}
        return out


DHAN5Y = Dhan5y()


def dhan_hist_cap():
    from lab import dhan_hist
    return dhan_hist.DAILY_CAP


@app.get("/api/factory/lab5y")
def factory_lab5y():
    return clean(DHAN5Y.view())


@app.post("/api/factory/lab5y/download")
def factory_lab5y_download():
    return clean(DHAN5Y.start())


@app.post("/api/factory/lab5y/stop")
def factory_lab5y_stop():
    return clean(DHAN5Y.stop())


@app.post("/api/factory/lab5y/run")
async def factory_lab5y_run(req: Request):
    from lab import dhan_hist, factory
    b = await req.json() if (await req.body()) else {}
    n = max(500, min(int(b.get("n") or 6000), 30000))
    root = dhan_hist.root(ROOT / "data")
    if not any(root.glob("history/*/raw_*.parquet")):
        raise HTTPException(400, "Download the 5-year history from Dhan first.")

    def job(log, stop):
        dhan_hist.link_upstox(ROOT / "data", ["NIFTY", "BANKNIFTY"] + [p.parent.name for p in (ROOT / "data" / "history").glob("*/meta.json")])
        from lab import library
        q = factory.queue_untested(root, library.rules(), source="library")   # new library / book setups too
        if q["queued"]:
            log(f"{q['queued']} untested library / book rules queued")
        from lab import proc                              # 2.38: in its own low-priority process
        proc.run("factory", root, log=log, should_stop=stop, n_random=n, n_mutate=n // 2)
        if not stop():
            proc.run("mlstrat", root, log=log, should_stop=stop)
    return {"id": JOBS.start("factory5y", f"5-year Lab: testing {n + n // 2:,} rules on 5 years", job)["id"]}


@app.get("/api/factory/stocks")
def factory_stocks():
    return clean(STOCK_HIST.view())


@app.post("/api/factory/stocks/download")
async def factory_stocks_download(req: Request):
    b = await req.json() if (await req.body()) else {}
    return clean(STOCK_HIST.start(months=max(1, min(36, int(b.get("months") or 12))), syms=b.get("syms") or None))


@app.post("/api/factory/stocks/stop")
def factory_stocks_stop():
    return clean(STOCK_HIST.stop())


@app.get("/api/lab/expiry")
def lab_expiry():
    from webapp.live_api import engine as _eng
    return clean(_eng().expiry.view())


@app.get("/api/lab/eod")
def lab_eod_results():
    from lab.walk import lab_dir
    f = lab_dir(ROOT / "data") / "eod_results.json"
    if not f.exists():
        return None                      # not run yet: the page shows how to run it
    return clean(json.loads(f.read_text()))


@app.get("/api/lab/edge")
def lab_edge():
    from lab.walk import lab_dir
    f = lab_dir(ROOT / "data") / "meta.json"
    if not f.exists():
        return None                      # runs with the backtest
    return clean(json.loads(f.read_text()))


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
    return {"ok": True, "profile": {k: p.get(k) for k in ("dhanClientId", "tokenValidity", "activeSegment", "dataPlan", "dataValidity")},
            "token": _dhan_token_status()}


@app.post("/api/lab/dhan/renew")
def lab_dhan_renew():
    """Renew now (to check that auto-renew works with this token)."""
    from lab import dhan_token
    r = dhan_token.maybe_renew(force=True, log=lambda m: None)
    st = dhan_token.status()
    if r != "renewed":
        raise HTTPException(400, st.get("error") or "Dhan did not renew the token.")
    return {"ok": True, "token": st}


@app.post("/api/lab/dhan/auto")
async def lab_dhan_auto_on(req: Request):
    """Turn on TOTP automatic login: checks the PIN + key with Dhan first, then keeps them in the macOS Keychain."""
    from lab import dhan_auto, dhan_token
    b = await req.json()
    pin = str(b.get("pin") or "").strip()
    try:
        secret = dhan_auto.clean_secret(str(b.get("secret") or ""))
    except Exception:
        raise HTTPException(400, "That TOTP key doesn't look right: copy the text key shown under Dhan's QR code (letters A-Z and digits 2-7).")
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise HTTPException(400, "The Dhan PIN is the 4-6 digit number you use to log in to Dhan.")
    cid = _lab_cfg().get("dhan_client_id")
    if not cid:
        raise HTTPException(400, "Save your Dhan client ID first.")
    try:
        with dhan_token.LOCK:
            res = dhan_auto.generate(cid, pin, secret)
            save_user_settings({"lab.dhan_token": res["token"]})
    except dhan_auto.AuthRefused as e:
        raise HTTPException(400, f"Dhan refused this PIN / TOTP key ({e}). Nothing was saved. Is TOTP set up and confirmed on web.dhan.co?")
    except Exception as e:
        raise HTTPException(502, str(e))
    where = dhan_auto.store(pin, secret)
    dhan_auto._set_state({"blocked": None, "last_error": None, "where": where,
                          "last_login": dt.datetime.now().strftime("%d %b %H:%M")})
    return {"ok": True, "where": where, "token": dhan_token.status()}


@app.delete("/api/lab/dhan/auto")
def lab_dhan_auto_off():
    from lab import dhan_auto
    dhan_auto.clear()
    return {"ok": True}


@app.get("/dhan/login")
def dhan_login():
    """Log in with Dhan: API key + secret (12 months) -> Dhan's own login page -> back to /dhan/callback."""
    from fastapi.responses import HTMLResponse, RedirectResponse
    from lab import dhan_token
    c = _lab_cfg()
    if not (c.get("dhan_client_id") and c.get("dhan_api_key") and c.get("dhan_api_secret")):
        return HTMLResponse(_dhan_page("Add your Dhan API key first", "Strategy desk → Dhan keys: client ID, API key and API secret "
                                       "(web.dhan.co → Profile → DhanHQ Trading APIs → API Key, redirect URL "
                                       f"<code>{dhan_token.CALLBACK}</code>).", ok=False))
    try:
        return RedirectResponse(dhan_token.consent_url(c["dhan_client_id"], c["dhan_api_key"], c["dhan_api_secret"]))
    except Exception as e:
        return HTMLResponse(_dhan_page("Dhan login could not start", str(e), ok=False))


@app.get("/dhan/callback")
def dhan_callback(tokenId: str | None = None, tokenid: str | None = None):
    from fastapi.responses import HTMLResponse
    from lab import dhan_token
    tid = tokenId or tokenid
    if not tid:
        return HTMLResponse(_dhan_page("Dhan login was cancelled", "No tokenId came back from Dhan.", ok=False))
    c = _lab_cfg()
    try:
        with dhan_token.LOCK:
            res = dhan_token.consume(tid, c["dhan_api_key"], c["dhan_api_secret"])
            ch = {"lab.dhan_token": res["token"]}
            if res.get("client_id") and not c.get("dhan_client_id"):
                ch["lab.dhan_client_id"] = str(res["client_id"])
            save_user_settings(ch)
    except Exception as e:
        return HTMLResponse(_dhan_page("Dhan login failed", str(e), ok=False))
    exp = dhan_token.status().get("expires")
    return HTMLResponse(_dhan_page("Logged in to Dhan", f"Token saved{f' (valid till {exp})' if exp else ''}. "
                                   "The 5-year download, news and movers resume by themselves. You can close this tab.", ok=True))


def _dhan_page(title, body, ok=True):
    import html as _h
    color = "#1E8A5A" if ok else "#C23B32"
    return (f"<!doctype html><meta charset=utf-8><title>{_h.escape(title)}</title><body style='font-family:system-ui;max-width:640px;"
            f"margin:60px auto;padding:0 16px'><h2 style='color:{color}'>{_h.escape(title)}</h2><p>{body}</p>"
            f"<p><a href='/#/desk'>Back to Stockway</a></p></body>")


def _dhan_token_status():
    try:
        from lab import dhan_token
        return dhan_token.status()
    except Exception:
        return None


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
        d.background = True                     # 2.38: a bulk download gives way to screens, news and movers
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


# ---- System page: every connection, what it is for, its health, all logins, and the no-clash checks
APP_STARTED = time.time()
ROLES = {
    "upstox": ["Live prices of indices, 218 F&O stocks and ~1,900 options (the live scanner, alerts, paper trades)",
               "Option chains, today's instrument list, 1-year daily candles",
               "Data vault (2.31): a full option chain of every F&O stock every 30 minutes, only with spare calls",
               "Login: once a day (Upstox rule)"],
    "dhan": ["5-year options history for the 5-year Lab (expired options, 1-minute)",
             "200-level order book of Nifty / Bank Nifty futures (live)", "News headlines and market movers (live)",
             "Trading-day calendar and stock turnover (for the download)",
             "Chart candles for every timeframe (1-min to monthly, years back; cached on the Mac, older bars as you scroll)",
             "Login: automatic with TOTP"],
    "claude": ["Why-notes on alerts, hourly market notes, Ask Stockway", "Strategy ideas and ebook / transcript reading for the Lab",
               "Login: Claude Code on this Mac (Terminal: claude)"],
    "telegram": ["Alerts and important notices on your phone (token expired, live data stopped)"],
    "yahoo": ["World markets, India VIX history, global context (free, no login)",
              "All-time and 52-week highs / lows of the indices; chart history when Dhan isn't available"],
    "official": ["RBI press releases and notifications, SEBI orders and circulars, Government (PIB) releases, NSE circulars, "
                 "BSE notices (free RSS feeds, no login)", "Event risk on every alert; a new high-impact item to Telegram"],
    "kite": ["2.52: backup live feed and cross-check for Upstox: the same instruments, compared every few seconds; takes over "
             "when Upstox goes quiet only if you switch failover on (Live setup)", "Market data only: never orders",
             "Limits: 3,000 instruments per connection, 3 connections; Kite Connect ₹500 / month",
             "Login: once a day (Zerodha ends it at 6:00 AM)"],
    "stream": ["2.51: open charts get each new price from this app over a websocket (about 0.1 s), instead of asking every "
               "2 seconds", "Reads the app's own prices: no new connection to any broker, no login"],
    "nse": ["Insider trades and company filings, F&O ban list, end-of-day files (free, no login)",
            "Upcoming results / board meetings (event calendar, every 6 hours): warnings on alerts"],
}


def _ago(ts):
    if not ts:
        return None
    s = time.time() - float(ts)
    return f"{int(s)} s ago" if s < 120 else f"{int(s // 60)} min ago" if s < 7200 else f"{s / 3600:.1f} h ago" if s < 172800 else f"{s / 86400:.0f} days ago"


def _svc(id_, name, status, detail, **kw):
    return {"id": id_, "name": name, "status": status, "detail": detail, "roles": ROLES.get(id_, []), **kw}


def _perf_view():
    """2.38: how quickly the app answers, the Lab's last run (own process or not) and the Dhan download giving way."""
    try:
        from lab import dhan_budget, proc
        from webapp import perf
        return {**perf.view(), "lab_last": dict(proc.LAST) or None, "dhan_gave_way": dhan_budget.gave_way()}
    except Exception as ex:
        return {"error": str(ex)[:160]}


@app.get("/api/system")
def system_view():
    from lab import dhan_auto, dhan_budget, dhan_token
    from live import selfcheck
    from webapp.live_api import engine as _eng
    e = _eng()
    c = cfg()
    lc = e.cfg()["live"]
    svcs = []
    # Upstox
    rec = e.tokens.load() if e.tokens else None
    feed = getattr(e, "feed", None)
    tick = getattr(e.state, "last_tick", 0) if getattr(e, "state", None) else 0
    if not (lc.get("upstox_api_key") and lc.get("upstox_api_secret")):
        st, msg = "bad", "API key and secret not set (Live setup)"
    elif not rec:
        st, msg = "warn", "Not logged in today: Log in with Upstox (needed before 9:15)"
    elif e.running and feed is not None and not getattr(feed, "connected", True):
        st, msg = "bad", "Logged in, but the live feed is disconnected (reconnecting)"
    else:
        st, msg = "ok", (f"Logged in{' as ' + rec.get('user_name') if rec.get('user_name') else ''}, valid till "
                         f"{str(rec.get('expires_at', ''))[11:16]} tomorrow" if rec else "")
    svcs.append(_svc("upstox", "Upstox", st, msg, logged_in=bool(rec), scanner=e.status.get("state"),
                     scanner_msg=e.status.get("message"), last_tick=_ago(tick), streaming=len(getattr(e, "opt_subs", None) or [])))
    # Dhan
    tk, au = dhan_token.status(), dhan_auto.status()
    lab = c.get("lab") or {}
    if not lab.get("dhan_client_id"):
        st, msg = "bad", "Client ID not set"
    elif tk.get("dead") and not (au.get("on") and not au.get("blocked")):
        st, msg = "bad", "Token expired: turn on automatic login (TOTP) or paste a new token"
    elif au.get("blocked"):
        st, msg = "bad", f"Automatic login stopped: {au.get('last_error')}"
    elif tk.get("dead"):
        st, msg = "warn", "Token expired: automatic login is making a new one"
    elif tk.get("expires"):
        st, msg = "ok", f"Token valid till {tk['expires']} ({tk.get('hours_left')} h); renews by itself"
    else:
        st, msg = ("warn", "No token yet") if not dhan_token.latest() else ("ok", "Token saved")
    dp = getattr(e, "depth", None)
    intel = getattr(e, "dhan_intel", None)
    svcs.append(_svc("dhan", "Dhan (Data API Pro)", st, msg, token=tk, auto=au, client_id=lab.get("dhan_client_id"),
                     calls_today=dhan_budget.used(), cap=dhan_budget.DAY_LIMIT,
                     depth={k: v for k, v in (dp.status.items() if dp else [])},
                     news_movers=("on" if intel else "off"), movers_at=_ago(getattr(intel, "movers_at", 0)) if intel else None,
                     intel_errors=dict(getattr(intel, "errors", {}) or {}) if intel else {}))
    # Claude
    ai = e.ai
    on = ai.enabled()
    err = ai.last_error
    svcs.append(_svc("claude", "Claude (AI)", "ok" if on and not err else "warn" if on else "off",
                     (err if err else f"{ai.label()} ready") if on else "Not set: Data & settings → AI",
                     used=ai.used, provider=ai.provider()))
    # Telegram
    try:                                                   # 2.52: Kite backup feed + cross-check
        lc_ = e.cfg()["live"]
        ktok = e.kite_tokens.load()
        rv = e.feed.view() if getattr(e, "feed", None) is not None and hasattr(e.feed, "view") else None
        kb = (rv or {}).get("backup") or {}
        cr = (rv or {}).get("cross") or {}
        if not lc_.get("kite_api_key"):
            kst, kmsg = "off", "Not set up (optional): Live setup → Kite"
        elif not ktok:
            kst, kmsg = "warn", "Log in with Kite today (Live setup) to start the backup feed"
        elif rv is None:
            kst, kmsg = "ok", f"Logged in as {ktok.get('user')}; starts with the live scanner"
        else:
            kst = "ok" if kb.get("connected") else "warn"
            kmsg = (f"{kb.get('message') or ''}"
                    + (f" · cross-check: {cr.get('match_pct')}% of {cr.get('compared')} prices agree, "
                       f"Upstox behind on {cr.get('upstox_behind')}" if cr.get("compared") else "")
                    + f" · failover {rv.get('failover')}" + (f" · now from {rv.get('active')}" if rv.get("active") == "Kite" else ""))
        svcs.append(_svc("kite", "Kite (Zerodha) backup feed", kst, kmsg, logged_in=bool(ktok)))
    except Exception:
        pass
    try:                                                   # 2.51: the chart's live stream (inside the app)
        from live import stream as _ST
        sv = _ST.view()
        svcs.append(_svc("stream", "Chart live stream (inside the app)", "ok" if e.running else "off",
                         f"{sv['open']} chart(s) open · {sv['served']:,} prices sent · checks every {sv['every_ms']} ms"
                         + ("" if e.running else " · starts with the live scanner"), **sv))
    except Exception:
        pass
    svcs.append(_svc("telegram", "Telegram", "ok" if e.telegram.enabled() else "off",
                     "Messages go to your phone" if e.telegram.enabled() else "Not set: Data & settings → Telegram"))
    # Yahoo / world
    wu = getattr(e.world, "updated", {}) or {}
    last = max([v for v in wu.values() if v] or [0])
    svcs.append(_svc("yahoo", "Yahoo Finance", "ok" if last and time.time() - last < 3 * 3600 else "warn",
                     f"World data updated {_ago(last)}" if last else "Not fetched yet", updated=_ago(last)))
    ins = getattr(getattr(e, "insiders", None), "updated", None)
    cal = getattr(getattr(e, "context", None), "events_cal", None) or []
    cal_at = ((getattr(getattr(e, "context", None), "updated", None) or {}).get("events"))
    cal_txt = (f" · results calendar: {len(cal):,} board meetings ({sum(1 for x in cal if x.get('results')):,} results), "
               f"updated {_ago(cal_at)}" if cal else " · results calendar: not loaded yet")
    svcs.append(_svc("nse", "NSE (free files)", "ok" if ins and time.time() - ins < 2 * 86400 else "warn",
                     (f"Filings read {_ago(ins)}" if ins else "Not read yet") + cal_txt, updated=_ago(ins)))
    # 2.35 official feeds (event engine)
    evs = getattr(e, "events", None)
    if evs is not None:
        srcs = list(evs.status.items())
        ok = sum(1 for _, v in srcs if v.get("ok"))
        bad_src = [n for n, v in srcs if v.get("ok") is False]
        st = "warn" if not srcs else "ok" if not bad_src else "warn"
        msg = (f"{ok} of {len(srcs)} feeds read, last {_ago(max(v.get('at') or 0 for _, v in srcs))}"
               + (f"; not reachable: {', '.join(bad_src)}" if bad_src else "")) if srcs else "First read within a minute of starting"
        svcs.append(_svc("official", "Official feeds (RBI, SEBI, Govt, NSE, BSE)", st, msg,
                         sources=[{"name": n, **v} for n, v in srcs]))
    # jobs
    d5 = DHAN5Y.view()
    cur = JOBS.current or {}
    from lab import factory
    try:
        fst = factory.load(ROOT / "data")
        lab_last = fst.get("updated")
    except Exception:
        lab_last = None
    jobs = {"dhan5y_running": d5.get("running"), "research5y_running": cur.get("kind") == "factory5y" and cur.get("status") == "running",
            "scheduler_age_s": time.time() - _sched["tick"] if _sched.get("tick") else None}
    job_rows = [
        {"name": "Live scanner", "state": e.status.get("state"), "detail": e.status.get("message")},
        {"name": "5-year download (Dhan)", "state": "running" if d5.get("running") else d5.get("status"),
         "detail": f"{(d5.get('overview') or {}).get('pct', 0)}% complete · {dhan_budget.used():,} Dhan calls today"},
        {"name": "Strategy Lab research", "state": cur.get("status") if cur.get("kind", "").startswith("factory") else "idle",
         "detail": f"last result {lab_last[:16].replace('T', ' ')}" if lab_last else "no run yet"},
        {"name": "Other job", "state": cur.get("status") if cur and not cur.get("kind", "").startswith("factory") else "idle",
         "detail": cur.get("label") if cur and not cur.get("kind", "").startswith("factory") else ""},
    ]
    # recent problems from the app log (last hour)
    import re as _re
    now = dt.datetime.now()
    probs = {}
    for line in list(e.logs)[-400:]:
        m = _re.match(r"(\d\d):(\d\d):(\d\d)\s+(.*)", line)
        if not m or not _re.search(r"fail|error|rejected|refused|stopped|disconnected", m.group(4), _re.I):
            continue
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)))
        if (now - t).total_seconds() > 3600 or (now - t).total_seconds() < -60:
            continue
        key = _re.sub(r"\d+", "#", m.group(4))[:70]
        p = probs.setdefault(key, {"text": m.group(4)[:180], "n": 0, "last": line[:8]})
        p["n"] += 1
        p["last"], p["text"] = line[:8], m.group(4)[:180]
    checks = selfcheck.static_checks(e.cfg()) + selfcheck.runtime_checks(e, ROOT / "data", jobs)
    bad = [x for x in svcs if x["status"] == "bad"] + [x for x in checks if x["ok"] is False]
    warn = [x for x in svcs if x["status"] == "warn"]
    free = shutil.disk_usage(ROOT / "data").free / 1e9
    return clean({"version": RUNNING_VERSION, "up_since": _ago(APP_STARTED), "free_gb": round(free, 1),
                  "overall": "bad" if bad else "warn" if warn else "ok", "services": svcs, "jobs": job_rows,
                  "checks": checks, "problems": sorted(probs.values(), key=lambda p: p["last"], reverse=True)[:12],
                  "bullets": _registry_list(), "rules": _rules_check(), "vault": _vault_view(e),
                  "health": e.health.view() if getattr(e, "health", None) is not None else None,
                  "perf": _perf_view(),
                  "events": _events_view(e)})


def _events_view(e):
    try:
        v = e.events.view() if getattr(e, "events", None) is not None else None
        return {"sources": v["sources"], "error": v.get("error")} if v else None
    except Exception as ex:
        return {"error": str(ex)[:160], "sources": []}


def _vault_view(e):
    try:
        return e.vault_view()
    except Exception as ex:
        return {"error": str(ex)[:160]}


def _registry_list():
    from live import registry
    return registry.view()


def _rules_check():
    from lab import rules
    return rules.check()


@app.get("/api/registry")
def registry_view():
    """The bullet catalogue (live/registry.py) for the screens."""
    from live import registry
    return {"kinds": registry.view()}


@app.post("/api/system/check")
def system_check():
    """Live checks against each service (one small call each; no AI call, no Telegram message)."""
    from lab import dhan_token
    from lab.dhan import Dhan
    from webapp.live_api import engine as _eng
    e = _eng()
    out = []
    t0 = time.time()
    try:
        if not e.tokens.load():
            out.append({"id": "upstox", "ok": None, "detail": "not logged in today"})
        else:
            from live.upstox import UpstoxREST
            rest = getattr(e, "rest", None) or UpstoxREST(e.tokens.load()["access_token"])   # the scanner's own client if running
            p = rest.profile()
            out.append({"id": "upstox", "ok": True, "detail": f"answered as {p.get('user_name') or p.get('user_id') or 'you'} ({time.time() - t0:.1f} s)"})
    except Exception as ex:
        out.append({"id": "upstox", "ok": False, "detail": str(ex)[:160]})
    t0 = time.time()
    c = _lab_cfg()
    try:
        if not (c.get("dhan_client_id") and dhan_token.latest()):
            out.append({"id": "dhan", "ok": None, "detail": "no token"})
        else:
            p = Dhan(c["dhan_client_id"], dhan_token.latest()).profile()
            out.append({"id": "dhan", "ok": True, "detail": f"answered ({time.time() - t0:.1f} s), data plan: {p.get('dataPlan') or '–'}"})
    except Exception as ex:
        out.append({"id": "dhan", "ok": False, "detail": str(ex)[:160]})
    return {"checks": out, "at": dt.datetime.now().strftime("%H:%M:%S")}


@app.middleware("http")
async def _no_stale_ui(request, call_next):
    """The browser must re-check the app's own files on every load, so an update is never hidden by its cache.
    2.38: every /api call is timed (webapp/perf.py), so slow screens show on the System page."""
    t0 = time.perf_counter()
    resp = await call_next(request)
    p = request.url.path
    try:
        from webapp import perf
        perf.record(request.method, p, request.url.query, (time.perf_counter() - t0) * 1000, resp.status_code)
    except Exception:
        pass
    if p == "/" or (p.startswith("/static/") and not p.startswith(("/static/vendor/", "/static/lib/"))):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/", response_class=HTMLResponse)
def index():
    """index.html with ?v=<version> on the app's own CSS/JS, so every update loads fresh files."""
    from fastapi.responses import HTMLResponse as _H
    v = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else str(int(time.time()))
    html = (STATIC / "index.html").read_text()
    for f in ("app.css", "app.js", "live.js"):
        html = html.replace(f'/static/{f}"', f'/static/{f}?v={v}"')
    return _H(html, headers={"Cache-Control": "no-cache"})
