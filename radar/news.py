"""News features.

Two free sources:
1. GDELT DOC API timelines: daily average tone + article volume per query, 2017 onward.
2. Optional headlines CSV (e.g. India News Headlines dataset), scored for sentiment.

Every news feature for target day T uses only news dated strictly before T.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_START = pd.Timestamp("2017-01-01")

# Small original finance lexicon used when FinBERT is not installed.
POS = set("""gain gains rise rises rising surge surges surged rally rallies rallied jump jumps jumped
soar soars soared record high highs beat beats upgrade upgraded upgrades profit profits growth
strong strength boost boosts boosted recovery recover recovers rebound rebounds optimism positive
inflow inflows buy buying bullish easing cut cuts approves approval expansion robust outperform
""".split())
NEG = set("""fall falls fell drop drops dropped slump slumps slumped crash crashes crashed plunge plunges
plunged tumble tumbles tumbled decline declines declined loss losses weak weakness downgrade downgraded
fear fears worry worries concern concerns risk risks crisis default defaults fraud scam npa npas bad
selloff sell-off outflow outflows bearish hike hikes inflation war attack attacks probe ban penalty
slowdown recession volatile volatility panic stress shock
""".split())

FIN_KEYWORDS = re.compile(
    r"\b(?:bank|banks|banking|rbi|repo|rate|rates|sensex|nifty|market|markets|stock|stocks|shares|"
    r"rupee|inflation|gdp|fed|budget|economy|npa|loan|loans|credit|deposit|fii|fpi|crude|oil|"
    r"hdfc|icici|sbi|axis|kotak|indusind|yes bank|fiscal|bond|yields?)\b",
    re.I,
)


# ---------------------------------------------------------------- GDELT
class Stopped(Exception):
    """Raised when the user cancels a job."""


class RateLimited(Exception):
    """GDELT keeps refusing requests; stop and try again later."""


def _gdelt_fetch(query: str, mode: str, start: pd.Timestamp, end: pd.Timestamp, ncfg: dict,
                 should_stop=lambda: False) -> list | None:
    """One GDELT timeline request. Returns data points, [] for genuinely no data,
    None if it failed (timeout / network). Raises RateLimited on repeated HTTP 429."""
    import requests

    params = {
        "query": query, "mode": mode, "format": "json",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    limited = 0
    for attempt in range(3):
        if should_stop():
            raise Stopped()
        try:
            r = requests.get(GDELT_URL, params=params, timeout=ncfg.get("gdelt_timeout_sec", 30))
        except Exception:
            time.sleep(ncfg["gdelt_sleep_sec"])
            continue
        time.sleep(ncfg["gdelt_sleep_sec"])
        if r.status_code == 429 or "limit requests" in r.text[:200].lower():
            limited += 1
            if limited >= 2:
                raise RateLimited()
            for _ in range(ncfg.get("gdelt_backoff_sec", 30)):
                if should_stop():
                    raise Stopped()
                time.sleep(1)
            continue
        if r.status_code == 200 and r.text.strip().startswith("{"):
            try:
                tl = r.json().get("timeline", [])
            except ValueError:
                continue
            return tl[0].get("data", []) if tl else []
    return None


def _to_daily(points: list, agg: str = "mean") -> pd.Series:
    """GDELT may return sub-daily points; tone is averaged, volume is summed."""
    if not points:
        return pd.Series(dtype=float)
    s = pd.Series(
        [p.get("value", np.nan) for p in points],
        index=pd.to_datetime([p["date"][:8] for p in points], format="%Y%m%d"),
        dtype=float,
    )
    g = s.groupby(level=0)
    return g.sum() if agg == "sum" else g.mean()


def _periods(today: pd.Timestamp):
    """One request per calendar year (120 requests for 10 years x 6 topics x 2 measures)."""
    for yr in range(GDELT_START.year, today.year + 1):
        start = pd.Timestamp(f"{yr}-01-01")
        end = min(pd.Timestamp(f"{yr}-12-31 23:59:59"), today + pd.Timedelta(hours=23, minutes=59))
        yield yr, start, end, yr < today.year


def _cache_dir(cfg) -> Path:
    d = Path(cfg["cache_dir"]) / "gdelt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(cfg, name: str, yr: int) -> Path:
    return _cache_dir(cfg) / f"{name}_{yr}.json"


def _is_fresh(f: Path, closed: bool) -> bool:
    if not f.exists():
        return False
    try:
        data = json.loads(f.read_text())
    except Exception:
        return False
    if not data.get("complete"):
        return False
    if closed:
        return True
    return time.time() - f.stat().st_mtime < 20 * 3600   # current year: refresh daily


def gdelt_progress(cfg: dict) -> dict:
    today = pd.Timestamp.today().normalize()
    names = list(cfg["news"].get("gdelt_queries", {}))
    total = done = 0
    for name in names:
        for yr, _, _, closed in _periods(today):
            total += 1
            f = _cache_file(cfg, name, yr)
            if f.exists():
                try:
                    if json.loads(f.read_text()).get("complete"):
                        done += 1
                except Exception:
                    pass
    state_f = _cache_dir(cfg) / "_status.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    return {"done": done, "total": total, "last_note": state.get("note"), "last_run": state.get("at")}


def _save_status(cfg, note: str):
    (_cache_dir(cfg) / "_status.json").write_text(json.dumps(
        {"note": note, "at": pd.Timestamp.now().isoformat(timespec="seconds")}))


def download_gdelt(cfg: dict, log=print, should_stop=lambda: False, budget_sec: float | None = None) -> dict:
    """Download missing GDELT years. Saves after every request, so it can be stopped and resumed.
    Stops early if GDELT rate-limits us or the time budget runs out."""
    ncfg = cfg["news"]
    today = pd.Timestamp.today().normalize()
    t0 = time.time()
    todo = [(n, q, yr, s, e, c) for n, q in ncfg.get("gdelt_queries", {}).items()
            for yr, s, e, c in _periods(today) if not _is_fresh(_cache_file(cfg, n, yr), c)]
    prog = gdelt_progress(cfg)
    log(f"  news: {prog['done']} of {prog['total']} topic-years already saved, {len(todo)} to download")
    got = failed = 0
    note = "complete"
    for i, (name, query, yr, start, end, closed) in enumerate(todo, 1):
        if budget_sec is not None and time.time() - t0 > budget_sec:
            note = "time limit reached; the rest continues next time"
            log(f"  news: {note}")
            break
        f = _cache_file(cfg, name, yr)
        try:
            tone = _gdelt_fetch(query, "timelinetone", start, end, ncfg, should_stop)
            vol = _gdelt_fetch(query, "timelinevolraw", start, end, ncfg, should_stop) if tone is not None else None
        except RateLimited:
            note = "GDELT is limiting requests right now; try again later (progress is saved)"
            log(f"  news: {note}")
            break
        if tone is None or vol is None:
            failed += 1
            log(f"  news {i}/{len(todo)}: {name} {yr} failed (network), will retry next time")
            if failed >= 5 and got == 0:
                note = "the news server is not responding; try again later"
                log(f"  news: {note}")
                break
            continue
        f.write_text(json.dumps({"tone": tone, "vol": vol, "complete": True}))
        got += 1
        log(f"  news {i}/{len(todo)}: {name} {yr} saved ({len(tone)} days)")
    else:
        if failed:
            note = f"{failed} topic-years failed; run again to fill gaps"
    _save_status(cfg, note)
    prog = gdelt_progress(cfg)
    log(f"  news: {prog['done']} of {prog['total']} topic-years saved")
    return prog


def gdelt_daily(cfg: dict, refresh: bool = False, log=print, should_stop=lambda: False,
                budget_sec: float | None = None) -> pd.DataFrame:
    """Daily tone + volume per query from the saved files (optionally downloading first)."""
    if refresh:
        download_gdelt(cfg, log=log, should_stop=should_stop, budget_sec=budget_sec)
    today = pd.Timestamp.today().normalize()
    frames = []
    for name in cfg["news"].get("gdelt_queries", {}):
        tone_parts, vol_parts = [], []
        for yr, _, _, _ in _periods(today):
            f = _cache_file(cfg, name, yr)
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text())
            except Exception:
                continue
            tone_parts.append(_to_daily(data.get("tone", [])))
            vol_parts.append(_to_daily(data.get("vol", []), agg="sum"))
        if not tone_parts:
            continue
        tone = pd.concat(tone_parts)
        vol = pd.concat(vol_parts) if vol_parts else pd.Series(dtype=float)
        df = pd.DataFrame({f"gd_{name}_tone": tone, f"gd_{name}_vol": vol})
        frames.append(df[~df.index.duplicated(keep="last")])
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1, sort=True)
    log(f"  news: using {out.shape[1] // 2} topics, {len(out)} days")
    return out


# ---------------------------------------------------------------- headlines
def _sentiment_scores(texts: list[str], backend: str) -> np.ndarray:
    if backend in ("auto", "finbert"):
        try:
            from transformers import pipeline

            clf = pipeline("text-classification", model="ProsusAI/finbert", truncation=True)
            out = clf(texts, batch_size=64)
            sign = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
            print("  sentiment: FinBERT")
            return np.array([sign[o["label"].lower()] * o["score"] for o in out])
        except Exception as e:
            if backend == "finbert":
                raise
            print(f"  sentiment: FinBERT unavailable ({type(e).__name__}), using lexicon")
    scores = []
    for t in texts:
        words = re.findall(r"[a-z\-]+", t.lower())
        p = sum(w in POS for w in words)
        n = sum(w in NEG for w in words)
        scores.append((p - n) / max(p + n, 1))
    return np.array(scores, dtype=float)


def headlines_daily(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (daily features, filtered headlines table for the report)."""
    path = cfg["news"].get("headlines_csv")
    if not path or not Path(path).exists():
        return pd.DataFrame(), pd.DataFrame()
    cache = Path(cfg["cache_dir"]) / "headlines_scored.parquet"
    if cache.exists() and cache.stat().st_mtime > Path(path).stat().st_mtime:
        h = pd.read_parquet(cache)
    else:
        h = pd.read_csv(path, usecols=["publish_date", "headline_text"], dtype=str)
        h["date"] = pd.to_datetime(h["publish_date"].str[:8], format="%Y%m%d", errors="coerce")
        h = h[h["date"] >= pd.Timestamp(cfg["start_date"]) - pd.Timedelta(days=30)]
        h = h[h["headline_text"].fillna("").str.contains(FIN_KEYWORDS)]
        h = h[["date", "headline_text"]].dropna().reset_index(drop=True)
        print(f"  scoring {len(h):,} finance headlines ...")
        h["score"] = _sentiment_scores(h["headline_text"].tolist(), cfg["news"]["sentiment_backend"])
        h.to_parquet(cache)
    g = h.groupby("date")["score"]
    daily = pd.DataFrame({
        "hl_count": g.size(),
        "hl_mean": g.mean(),
        "hl_neg_share": h.assign(neg=h["score"] < -0.2).groupby("date")["neg"].mean(),
    })
    print(f"  headlines: {len(h):,} finance headlines over {len(daily)} days")
    return daily, h


# ---------------------------------------------------------------- features
def news_features(news_daily: pd.DataFrame, target_days: pd.DatetimeIndex) -> pd.DataFrame:
    """Rolling news features on calendar days, then take the last value strictly before T."""
    if news_daily is None or news_daily.empty:
        return pd.DataFrame(index=target_days)
    cal = pd.date_range(news_daily.index.min(), max(news_daily.index.max(), target_days.max()), freq="D")
    nd = news_daily.reindex(cal)
    feats = pd.DataFrame(index=cal)
    for c in nd.columns:
        s = nd[c]
        if c.endswith("_vol") or c == "hl_count":
            base = s.rolling(60, min_periods=20).mean()
            sd = s.rolling(60, min_periods=20).std()
            feats[f"{c}_z3"] = (s.rolling(3, min_periods=1).mean() - base) / sd
        else:
            feats[f"{c}_1d"] = s.ffill(limit=3)
            feats[f"{c}_3d"] = s.rolling(3, min_periods=1).mean()
            feats[f"{c}_chg7"] = s.rolling(3, min_periods=1).mean() - s.rolling(30, min_periods=10).mean()
    feats.index.name = "news_date"
    left = pd.DataFrame({"date": target_days}).sort_values("date")
    right = feats.reset_index().sort_values("news_date")
    merged = pd.merge_asof(left, right, left_on="date", right_on="news_date",
                           allow_exact_matches=False, direction="backward")
    return merged.drop(columns=["news_date"]).set_index("date")
