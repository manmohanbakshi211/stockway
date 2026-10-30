"""Claude reviews for the Test & research pages: Time Machine results, the Strategy desk, the Scorecard.

Each review sends the page's own numbers (never keys or personal data) to the AI chosen in Live setup and asks for an
honest read: what the numbers say, how much to trust them (sample size, out-of-sample or not), and what to test next.
Stockway places no orders, so the AI is told to frame every idea as a paper trade, an alert or a test.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .notify import _compact_json

DROP = {"eq", "equity", "curve", "hist", "fan", "days", "trades", "closed", "open_trades", "rows", "series", "candles",
        "settings", "config", "deals_all", "filings", "errors", "legs"}

LANG = {"hinglish": "Reply in simple Hinglish (Hindi in Latin script mixed with English trading terms).",
        "english": "Reply in simple English."}

RULES = ("You are the head of research for an Indian F&O trader's personal app, Stockway. Review the numbers below the way "
         "a sceptical quant would. Be direct and short: a one-line verdict first, then at most 6 bullets. Cover: what the "
         "numbers actually say; how much to trust them (sample size, in-sample vs out-of-sample, t-stat, worst case, costs); "
         "the biggest risk; and the next 1-3 concrete tests to run in the app (Time Machine, backtest settings, paper trading). "
         "Never call anything guaranteed. Stockway never places real orders: phrase every idea as a paper trade, an alert or "
         "a test. Use only numbers present in the data; if something needed is missing, say what.")

ASK = {
    "timemachine": "This is a Time Machine test of one intraday plan (10,000 bootstrap replays of the rest of the day from the "
                   "symbol's last 60 sessions, plus the same plan on each real past session). Judge the plan: is the edge real "
                   "or noise, is the stop or target badly placed given the price fan, and what variation is worth testing.",
    "desk": "These are the Strategy desk results: intraday walk-forward backtests, the 10-year end-of-day test (iron condor / "
            "iron fly / straddles), the 'days like today' check (how each strategy did when VIX, trend and option prices "
            "looked like now), the chart-setup edge study and the live paper desks. Say which strategies deserve paper "
            "money, which to drop, and what to test next.",
    "scorecard": "This is the alert Scorecard: every live alert followed for 60 minutes and graded. Say which alert types work, "
                 "which to mute, whether the sample is big enough, and what to change.",
}


def slim(x, depth=0, max_list=12):
    """Keep summaries, drop long series, round floats, cap list lengths."""
    if depth > 6:
        return None
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: slim(v, depth + 1, max_list) for k, v in x.items() if k not in DROP and v is not None}
    if isinstance(x, list):
        return [slim(v, depth + 1, max_list) for v in x[:max_list]]
    return x


def _read(f: Path):
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except Exception:
        return None


def desk_context(e, data_dir: Path) -> dict:
    lab = data_dir / "lab"
    d = {"intraday_backtest": slim(_read(lab / "results.json")), "ten_year_eod": slim(_read(lab / "eod_results.json"), max_list=20),
         "setup_edge_study": slim(_read(lab / "meta.json")), "days_like_today": slim(_read(lab / "scenario.json"), max_list=20)}
    try:
        d["expiry_desk_paper"] = slim(e.expiry.view())
    except Exception:
        pass
    try:
        d["intraday_desk_paper"] = slim(e.desk.view(None))
    except Exception:
        pass
    if not any(d.get(k) for k in ("intraday_backtest", "ten_year_eod", "setup_edge_study")):
        raise ValueError("No test results yet: run the 10-year test or the backtest on the Strategy desk first.")
    return d


def scorecard_context(e) -> dict:
    v = e.scorecard_view(None)
    return slim(v, max_list=30)


def review(e, topic: str, data: dict | None, data_dir: Path) -> dict:
    if topic not in ASK:
        raise ValueError("Unknown review.")
    if not e.ai.enabled():
        raise PermissionError("Connect Claude (your Pro plan) in Live setup → 4. AI first.")
    if topic == "timemachine":
        if not data or "mc" not in data:
            raise ValueError("Run a Time Machine test first.")
        ctx = slim(data)
    elif topic == "desk":
        ctx = desk_context(e, data_dir)
    else:
        ctx = scorecard_context(e)
    body = _compact_json(ctx)
    if len(body) > 24000:
        body = body[:24000] + " …(cut)"
    lang = LANG.get(((e.cfg().get("ai") or {}).get("language") or "hinglish"), LANG["hinglish"])
    prompt = f"{RULES} {lang}\n\nTASK: {ASK[topic]}\n\nDATA:\n{body}"
    t0 = time.time()
    text = e.ai.ask(prompt, 800, auto=False)
    if not text:
        raise RuntimeError(e.ai.last_error or "The AI did not answer. Try again in a minute.")
    return {"text": text, "by": e.ai.label(), "secs": round(time.time() - t0, 1)}
