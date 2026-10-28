"""The two jobs the app runs: train (full history) and forecast (next session).
Both save their results in output/ so the app can serve them instantly."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, features, market, model, news, report

KEY_FEATURES = ["vix", "vix_chg1", "bn_implied_daily", "bn_range", "bn_gap", "bn_ret1", "bn_ret5",
                "sp500_ret1", "nasdaq_ret1", "us_vix_lvl", "usdinr_ret1", "brent_ret1"]


def _out(cfg) -> Path:
    return Path(cfg["output_dir"])


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (pd.Timestamp, dt.date)):
        return str(o)[:10]
    return o


def write_json(path: Path, obj):
    path.write_text(json.dumps(_jsonable(obj), indent=1))


def get_data(cfg: dict, demo: bool, use_news: bool, refresh: bool = True, log=print,
             should_stop=lambda: False):
    log("Step 1/6: Market data")
    if demo:
        from .synthetic import make_demo
        mkt = make_demo(cfg["start_date"])
        log("  using demo (synthetic) prices")
    else:
        mkt = market.load_all(cfg, refresh=refresh)
    if should_stop():
        raise news.Stopped()
    log("Step 2/6: News")
    gd, hl, parts = pd.DataFrame(), pd.DataFrame(), []
    if not demo:
        if use_news and cfg["news"].get("use_gdelt"):
            budget = float(cfg["news"].get("train_fetch_budget_min", 0) or 0) * 60
            gd = news.gdelt_daily(cfg, refresh=budget > 0, log=log, should_stop=should_stop,
                                  budget_sec=budget or None)
            if gd.empty:
                log("  no saved news yet (use Download news in Data & settings)")
            else:
                parts.append(gd)
        hl_daily, hl = news.headlines_daily(cfg)
        if not hl_daily.empty:
            parts.append(hl_daily)
    news_daily = pd.concat(parts, axis=1, sort=True) if parts else pd.DataFrame()
    if news_daily.empty:
        log("  no news features in this run")
    if should_stop():
        raise news.Stopped()
    return mkt, news_daily, gd, hl


def download_news(cfg: dict, log=print, should_stop=lambda: False) -> dict:
    log("Step 1/2: Downloading news from GDELT (saved as it goes; safe to stop)")
    prog = news.download_gdelt(cfg, log=log, should_stop=should_stop)
    log("Step 2/2: Done")
    log(f"Done. {prog['done']} of {prog['total']} topic-years saved. Retrain to use them.")
    return prog


def train(cfg: dict, demo: bool = False, use_news: bool = True, log=print, should_stop=lambda: False) -> dict:
    t0 = dt.datetime.now()
    out = _out(cfg)
    mkt, news_daily, gd, hl = get_data(cfg, demo, use_news, log=log, should_stop=should_stop)

    log("Step 3/6: Building features")
    X, y = features.build_dataset(mkt, news_daily, cfg)
    X, y = X.iloc[200:], y.iloc[200:]
    log(f"  {len(X)} trading days x {X.shape[1]} factors")

    if should_stop():
        raise news.Stopped()
    log("Step 4/6: Walk-forward training (each year tested on data the model never saw)")
    oos, importance = model.walk_forward(X, y, cfg, log=log)
    mets = model.metrics(oos)
    bundle = model.train_final(X, y, cfg, extra={"demo": demo, "news": bool(not news_daily.empty)})

    log("Step 5/6: Option-buying proxy backtest")
    bt = backtest.straddle_proxy(oos, X, y, cfg)
    bt_sum = backtest.summarize(bt)

    log("Step 6/6: Saving results")
    factors = report.factor_table(X, y, importance)
    biggest = report.missed_opportunities(
        y, oos, X, lambda rows: model.drivers(bundle, rows, top=3),
        gd if not gd.empty else None, hl if not hl.empty else None)
    X.to_parquet(out / "features.parquet")
    y.to_parquet(out / "labels.parquet")
    mkt["banknifty"].to_parquet(out / "banknifty.parquet")
    if not news_daily.empty:
        news_daily.to_parquet(out / "news_daily.parquet")
    elif (out / "news_daily.parquet").exists():
        (out / "news_daily.parquet").unlink()
    oos.to_csv(out / "oos_predictions.csv", index=False)
    bt.to_csv(out / "backtest_proxy.csv")
    importance.to_csv(out / "importance.csv", header=["importance"])
    factors.to_csv(out / "crucial_factors.csv", index=False)
    biggest.to_csv(out / "biggest_days.csv", index=False)
    write_json(out / "metrics.json", mets)
    write_json(out / "backtest_summary.json", bt_sum.to_dict(orient="records"))
    report.build_report(cfg, mkt, X, y, oos, mets, bt, bt_sum, importance, factors, biggest, out / "report.html")
    run = {"finished_at": dt.datetime.now().isoformat(timespec="seconds"),
           "seconds": int((dt.datetime.now() - t0).total_seconds()),
           "demo": demo, "news": bool(not news_daily.empty),
           "data_start": X.index.min(), "data_end": X.index.max(),
           "days": len(X), "factors": X.shape[1]}
    write_json(out / "last_training.json", run)
    m = mets["big_move"]
    log(f"Done. Big-move AUC {m['auc']:.2f}, flagged days big {m['precision_when_flagged']:.0%} "
        f"(base {m['base_rate']:.0%}), big days caught {m['recall_of_actual_days']:.0%}")
    return run


def forecast(cfg: dict, demo: bool | None = None, use_news: bool = True, log=print, should_stop=lambda: False) -> dict:
    out = _out(cfg)
    bundle = model.load_bundle(cfg)
    if bundle is None:
        raise RuntimeError("No trained model yet. Train the model first.")
    demo = bundle.get("demo", False) if demo is None else demo
    want_news = use_news and bundle.get("news", False) and not demo
    if want_news and cfg["news"].get("use_gdelt"):
        log("  refreshing this year's news (up to 3 minutes)")
        news.download_gdelt(cfg, log=log, should_stop=should_stop, budget_sec=180)
    mkt, news_daily, _, _ = get_data(cfg, demo, want_news, log=log, should_stop=should_stop)
    log("Step 3/6: Building tomorrow's factors")
    X, _ = features.build_dataset(mkt, news_daily, cfg, include_next=True)
    row = X.reindex(columns=bundle["features"]).tail(1)
    day = row.index[0]
    log("Step 4/6: Scoring")
    p_big = float(model.predict(bundle, row, "big_move")[0])
    p_up = float(model.predict(bundle, row, "up_day")[0])
    thr = bundle["thresholds"]["big_move"]
    drv = model.drivers(bundle, row, top=8)[0]
    bn = mkt["banknifty"]
    last_close = float(bn["Close"].iloc[-1])
    implied = float(row["bn_implied_daily"].iloc[0])
    events = [c for c in row.columns if c.startswith("is_") and row[c].iloc[0] == 1]
    fc = {
        "for_date": day, "made_at": dt.datetime.now().isoformat(timespec="seconds"),
        "last_date": bn.index[-1], "last_close": last_close,
        "prob_big": p_big, "threshold": thr, "flag": p_big >= thr,
        "prob_up": p_up, "implied_pct": implied, "implied_pts": implied * last_close,
        "events": events,
        "drivers": [{"feature": f, "label": report.nice(f), "value": v, "effect": e} for f, v, e in drv],
        "demo": demo,
    }
    log("Step 5/6: Saving forecast")
    write_json(out / "forecast_latest.json", fc)
    logf = out / "forecast_log.csv"
    rec = pd.DataFrame([{"for_date": str(day.date()), "made_at": fc["made_at"], "prob_big": p_big,
                         "threshold": thr, "flag": int(fc["flag"]), "prob_up": p_up,
                         "implied_pct": implied, "demo": int(demo)}])
    if logf.exists():
        old = pd.read_csv(logf, dtype={"for_date": str})
        rec = pd.concat([old[old["for_date"] != str(day.date())], rec], ignore_index=True)
    rec.sort_values("for_date").to_csv(logf, index=False)
    # keep the price file current so the app can grade past forecasts
    bn.to_parquet(out / "banknifty.parquet")
    log("Step 6/6: Telegram")
    send_telegram(cfg, forecast_text(fc), log)
    log(f"Done. {day.date()}: big-move probability {p_big:.0%} "
        f"({'opportunity day' if fc['flag'] else 'normal day'})")
    return fc


def forecast_text(fc: dict) -> str:
    day = pd.Timestamp(fc["for_date"]).strftime("%a %d %b %Y")
    lines = [
        f"Bank Nifty radar for {day}",
        f"{'Opportunity day' if fc['flag'] else 'Normal day'}: big-move chance {fc['prob_big']:.0%} "
        f"(flag at {fc['threshold']:.0%})",
        f"Expected 1-sigma move: {fc['implied_pct']:.2%} (about {fc['implied_pts']:,.0f} points)",
        f"Direction lean: {'up' if fc['prob_up'] >= 0.5 else 'down'} ({fc['prob_up']:.0%} chance of an up close)",
    ]
    if fc["events"]:
        lines.append("Events: " + ", ".join(report.nice(e) for e in fc["events"]))
    lines.append("Top drivers: " + "; ".join(d["label"] for d in fc["drivers"][:3]))
    return "\n".join(lines)


def send_telegram(cfg: dict, text: str, log=print) -> bool:
    tg = cfg.get("telegram", {})
    if not tg.get("bot_token") or not tg.get("chat_id"):
        log("  Telegram not set up (optional)")
        return False
    import requests
    try:
        r = requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                          data={"chat_id": tg["chat_id"], "text": text}, timeout=20)
        ok = r.status_code == 200
        log("  Telegram sent" if ok else f"  Telegram failed: {r.text[:120]}")
        return ok
    except Exception as e:
        log(f"  Telegram failed: {e}")
        return False
