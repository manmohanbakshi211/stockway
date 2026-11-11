"""Daily Radar for every major index (2.67).

Mandeep, 11 Nov 2026: "the radar pages not only for the bank nifty add other all major indices and show a grand
view also".

Gun: the radar engine (radar/pipeline.py) is unchanged; this module runs it for any index by putting that index's
daily prices in the target slot. Each index gets its own models (models/indices/<key>) and results
(output/indices/<key>), its own expiry calendar (dated data) and the same honest test (walk-forward by year on days
the model never saw). Bank Nifty, the main entry, keeps its original folders, Telegram message and schedule exactly.
A failing index (no data, broken prices) is skipped and marked failed; the others keep running.
Bullets: rules/radar_indices.json (which indices, their Yahoo symbol, expiry calendars, on / off).
Grand view: every index's next-session forecast, its record on unseen years and live, and how many flag together.
Information only: nothing trades on it. Market data only (free Yahoo daily prices).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REG_FILE = ROOT / "rules" / "radar_indices.json"
STATUS = "indices_status.json"


class BadData(RuntimeError):
    pass


def reg() -> dict:
    return json.loads(REG_FILE.read_text())


def registry() -> list:
    return reg()["indices"]


def entry(key: str | None) -> dict | None:
    k = (key or "").upper()
    return next((e for e in registry() if e["key"] == k), None)


def main_key() -> str:
    return next((e["key"] for e in registry() if e.get("main")), "BANKNIFTY")


def active() -> list:
    return [e for e in registry() if e.get("on")]


def others() -> list:
    return [e for e in active() if not e.get("main")]


def index_cfg(cfg: dict, e: dict, make: bool = True) -> dict:
    """The radar config for one index. The main index gets the config untouched (its folders, its Telegram)."""
    if e.get("main"):
        return cfg
    c = copy.deepcopy(cfg)
    c["output_dir"] = str(Path(cfg["output_dir"]) / "indices" / e["key"])
    c["model_dir"] = str(Path(cfg["model_dir"]) / "indices" / e["key"])
    if make:
        Path(c["output_dir"]).mkdir(parents=True, exist_ok=True)
        Path(c["model_dir"]).mkdir(parents=True, exist_ok=True)
    if e.get("expiry_rules"):
        c["expiry_rules"] = e["expiry_rules"]
    c["radar_index"] = {"key": e["key"], "name": e["name"], "yahoo": e.get("yahoo")}
    return c


def quality(df: pd.DataFrame, name: str, min_days: int = 750) -> None:
    """Refuse prices the radar cannot learn from (Yahoo's Indian index data has gaps for some indices)."""
    if df is None or len(df) < min_days:
        raise BadData(f"{name}: only {0 if df is None else len(df)} days of daily prices (needs {min_days}+)")
    flat = float(((df["High"] - df["Low"]) <= 0).mean())
    same_open = float((df["Open"] == df["Close"].shift(1)).mean())
    if flat > 0.02 or same_open > 0.10:
        raise BadData(f"{name}: {flat:.0%} flat candles, {same_open:.0%} opens equal to the prior close: no real high-low data")


def _demo_target(cfg: dict, key: str) -> pd.DataFrame:
    from .synthetic import make_demo
    return make_demo(cfg["start_date"], seed=zlib.crc32(key.encode()) % 10_000)["banknifty"]


def swap_target(mkt: dict, cfg: dict, refresh: bool = True, demo: bool = False) -> dict:
    """Called by pipeline.get_data for a non-main index: its daily prices go in the target slot."""
    e = cfg["radar_index"]
    if demo:
        tgt = _demo_target(cfg, e["key"])
    else:
        from . import market
        tgt = market.load_ticker(f"idx_{e['key'].lower()}", e["yahoo"], cfg["start_date"], cfg["cache_dir"], refresh)
    quality(tgt, e["name"])
    out = dict(mkt)
    out["banknifty"] = tgt
    print(f"  target     {e['name']:<14} {len(tgt):>5} days  {tgt.index.min().date()} -> {tgt.index.max().date()}")
    return out


# ------------------------------------------------------------------ running
def _status_path(cfg: dict) -> Path:
    p = Path(cfg["output_dir"]) / "indices"
    p.mkdir(parents=True, exist_ok=True)
    return p / STATUS


def load_status(cfg: dict) -> dict:
    try:
        return json.loads(_status_path(cfg).read_text())
    except Exception:
        return {}


def _save_status(cfg: dict, st: dict):
    p = _status_path(cfg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(p)


def has_model(cfg: dict, e: dict) -> bool:
    from . import model
    return (Path(index_cfg(cfg, e, make=False)["model_dir"]) / model.MODEL_FILE).exists()


def run_job(cfg: dict, mode: str = "train", keys=None, demo: bool = False, log=print, should_stop=lambda: False,
            notify: bool = True) -> dict:
    """mode 'train' (walk-forward + final model) or 'forecast' (next session) for every other index, one at a time;
    a failing index is recorded and skipped. After forecasts, one Telegram message with every index."""
    from . import news, pipeline
    todo = [e for e in others() if not keys or e["key"] in keys]
    if mode == "forecast":
        todo = [e for e in todo if has_model(cfg, e)]
    st = load_status(cfg)
    done = []
    for i, e in enumerate(todo, 1):
        if should_stop():
            raise news.Stopped()
        log(f"Step {i}/{len(todo)}: {e['name']}")
        c = index_cfg(cfg, e)
        rec = st.setdefault(e["key"], {})
        try:
            if mode == "train":
                run = pipeline.train(c, demo=demo, use_news=True, log=lambda m: log("  " + str(m)), should_stop=should_stop)
                rec.update(ok=True, trained=run["finished_at"], error=None, days=run["days"])
            else:
                fc = pipeline.forecast(c, log=lambda m: log("  " + str(m)), should_stop=should_stop)
                rec.update(ok=True, forecast=fc["made_at"], error=None)
            done.append(e["key"])
        except news.Stopped:
            raise
        except Exception as ex:                             # one index can never stop the others
            rec.update(ok=False, error=f"{type(ex).__name__}: {ex}"[:240], failed_at=dt.datetime.now().isoformat(timespec="seconds"))
            log(f"  {e['name']} skipped: {ex}")
        _save_status(cfg, st)
    if mode == "forecast" and notify and (reg().get("grand") or {}).get("telegram", True):
        try:
            g = grand(cfg)
            if any(r.get("forecast") for r in g["rows"]):
                pipeline.send_telegram(cfg, grand_text(g), log)
        except Exception as ex:
            log(f"  all-indices Telegram skipped: {ex}")
    log(f"Done: {len(done)} of {len(todo)} {'trained' if mode == 'train' else 'forecast'}"
        + (f"; skipped: {', '.join(k for k in (e['key'] for e in todo) if k not in done)}" if len(done) < len(todo) else ""))
    return {"mode": mode, "done": done, "tried": [e["key"] for e in todo]}


# ------------------------------------------------------------------ grand view
def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:
        return default


def live_record(c: dict, n: int = 30) -> dict:
    """The last n forecasts graded against what the index really did."""
    from . import features
    from .dates import ns_frame
    out = Path(c["output_dir"])
    logf, px = out / "forecast_log.csv", out / "banknifty.parquet"
    rec = {"graded": 0, "caught": 0, "missed": 0, "false_alarm": 0, "quiet": 0}
    if not logf.exists() or not px.exists():
        return rec
    lab = features.labels({"banknifty": ns_frame(pd.read_parquet(px))}, c)
    lg = pd.read_csv(logf, dtype={"for_date": str}).sort_values("for_date").tail(n)
    for _, r in lg.iterrows():
        d = pd.Timestamp(r["for_date"])
        if d not in lab.index or pd.isna(lab.at[d, "big_move"]):
            continue
        big, flag = bool(lab.at[d, "big_move"]), bool(r["flag"])
        rec["graded"] += 1
        rec["caught" if big and flag else "missed" if big else "false_alarm" if flag else "quiet"] += 1
    return rec


def row(cfg: dict, e: dict) -> dict:
    c = index_cfg(cfg, e, make=False)
    out = Path(c["output_dir"])
    fc = _read(out / "forecast_latest.json")
    mets = (_read(out / "metrics.json") or {})
    run = _read(out / "last_training.json")
    st = load_status(cfg).get(e["key"], {}) if not e.get("main") else {}
    bm, ud = mets.get("big_move") or {}, mets.get("up_day") or {}
    r = {"key": e["key"], "name": e["name"], "main": bool(e.get("main")), "trained": run is not None,
         "trained_at": (run or {}).get("finished_at"), "demo": bool((run or {}).get("demo")),
         "data_from": (run or {}).get("data_start"), "data_to": (run or {}).get("data_end"),
         "error": st.get("error") if st.get("ok") is False else None,
         "auc": bm.get("auc"), "hit_when_flagged": bm.get("precision_when_flagged"), "base_rate": bm.get("base_rate"),
         "lift": bm.get("lift_vs_random"), "caught_share": bm.get("recall_of_actual_days"), "dir_auc": ud.get("auc"),
         "record": live_record(c)}
    if fc:
        r["forecast"] = {k: fc.get(k) for k in ("for_date", "made_at", "last_date", "last_close", "prob_big", "threshold",
                                                 "flag", "prob_up", "implied_pct", "implied_pts", "events")}
        r["forecast"]["drivers"] = [d.get("label") for d in (fc.get("drivers") or [])[:3]]
    return r


def grand(cfg: dict) -> dict:
    rows = [row(cfg, e) for e in active()]
    fcs = [r["forecast"] for r in rows if r.get("forecast")]
    latest = max((f["for_date"] for f in fcs), default=None)
    today = [f for f in fcs if f["for_date"] == latest]
    n_flag = sum(1 for f in today if f["flag"])
    share = float((reg().get("grand") or {}).get("broad_share", 0.6))
    return {"rows": rows, "for_date": latest, "forecasts": len(today), "flagged": n_flag,
            "broad": bool(today) and n_flag >= max(2, int(np.ceil(share * len(today)))),
            "off": [{"key": e["key"], "name": e["name"], "why": e.get("why_off")} for e in registry() if not e.get("on")]}


def grand_text(g: dict) -> str:
    day = pd.Timestamp(g["for_date"]).strftime("%a %d %b") if g.get("for_date") else "next session"
    lines = [f"📡 Daily radar, all indices: {day}"]
    for r in g["rows"]:
        f = r.get("forecast")
        if not f or f["for_date"] != g["for_date"]:
            continue
        mark = "🔴 big day likely" if f["flag"] else "normal"
        lines.append(f"{r['name']}: {mark} {f['prob_big']:.0%} (flag {f['threshold']:.0%}) · ±{f['implied_pct']:.2%}")
    if g["forecasts"]:
        lines.append(f"{g['flagged']} of {g['forecasts']} flag a big day" + (": broad big move likely." if g["broad"] else "."))
    lines.append("Information only. Direction is close to a coin flip; the flag is about range.")
    return "\n".join(lines)
