"""2.77 Strict track: which alert types beat luck after every real cost (Tanu's pass test, ADR-0005, copied).

Evidence: the live test bench (live/bench.py). Every directional signal is paper-traded once per style with honest
fills (buy at the ask, sell at the bid: the real spread) and every charge, so the costs are the real measured ones.
The trade measure is R (net after costs / the money at risk), so buying, spreads and selling compare on one scale.

A setup = alert type (+ the Lab rule or idea id, when it has one) + bench style. Its trades split by day: learn (the
first 80% of the days) and holdout (the last 20%, never used for any choice). It passes only when:
  - learn: at least 30 trades, average R above 0 and t at or above the multiple-testing bar (Bonferroni over every
    setup that had enough trades to be tested, 5% two-sided), and
  - holdout: at least 10 trades and average R above 0,
  - and it beats the yardstick in both parts: coin-flip signals of the same style on the same days (a random symbol
    and direction every 5 minutes, live/engine.py), or, until there are enough of those, every signal of that style.
Every result is kept (failed ones too): data/strict.json (the latest), data/strict_runs.jsonl (one line per run).
Nothing here trades or sends anything; the Strict paper account (rules/paper_books.json "strict") reads the verdicts."""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import threading
from pathlib import Path
from statistics import NormalDist

from .clock import IST

COIN = "coin"                    # the yardstick's alert type (kept under source "<feed>#coin" in the bench)
RULES = Path(__file__).resolve().parents[1] / "rules" / "strict.json"
DEFAULTS = {"min_trades": 30, "min_hold": 10, "hold_frac": 0.2, "coin_every_min": 5, "coin_start": "09:20",
            "coin_end": "14:40", "paper_style": "buy", "run_after": "15:40"}
_LOCK = threading.Lock()
_CACHE: dict = {}
_CFG: dict = {}


def cfg() -> dict:
    """rules/strict.json (bullets), re-read when it changes; a missing or broken value keeps its default."""
    try:
        mt = RULES.stat().st_mtime
        if _CFG.get("mt") != mt:
            raw = json.loads(RULES.read_text())
            c = dict(DEFAULTS)
            for k, v in DEFAULTS.items():
                x = raw.get(k, v)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    try:
                        x = type(v)(x)
                        if x <= 0 or (k == "hold_frac" and x >= 0.9):
                            x = v
                    except (TypeError, ValueError):
                        x = v
                elif not isinstance(x, type(v)):
                    x = v
                c[k] = x
            _CFG.update(mt=mt, cfg=c, error=None)
    except Exception as e:
        _CFG.update(error=str(e), cfg=_CFG.get("cfg") or dict(DEFAULTS))
    return _CFG["cfg"]


def setup_id(kind: str, variant: str | None, style: str) -> str:
    return f"{kind}|{variant or ''}|{style}"


def variant_of(a: dict) -> str | None:
    d = a.get("data") or {}
    return (d.get("factory") or {}).get("id") or d.get("trial_key")


# ---------------------------------------------------------------- Tanu's statistics (its tanu/evaluate/report.py, before 2.78)
def stats(x) -> dict:
    x = [float(v) for v in x if v is not None and v == v]
    n = len(x)
    if n == 0:
        return {"n": 0}
    mean = sum(x) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "avg_r": round(mean, 3), "win_pct": round(sum(1 for v in x if v > 0) / n * 100, 1),
            "t": round(mean / (sd / math.sqrt(n)), 2) if n > 1 and sd > 0 else 0.0}


def evaluate(rows: list, min_trades: int | None = None, min_hold: int | None = None, hold_frac: float | None = None) -> dict:
    """rows: closed bench trades [{day, kind, variant, style, r, net, coin}]. Returns the verdict of every setup."""
    c = cfg()
    min_trades = int(min_trades or c["min_trades"])
    min_hold = int(min_hold or c["min_hold"])
    hold_frac = float(hold_frac or c["hold_frac"])
    rows = [r for r in rows if r.get("r") is not None and r.get("day")]
    days = sorted({r["day"] for r in rows})
    if not days:
        return {"days": 0, "first": None, "last": None, "holdout_from": None, "t_bar": None, "k": 0, "yardstick": {},
                "setups": [], "passing": [], "bar": {"min_trades": min_trades, "min_hold": min_hold, "hold_frac": hold_frac}}
    cut = days[min(len(days) - 1, int(len(days) * (1 - hold_frac)))] if len(days) > 1 else days[-1] + "~"
    part = lambda r: "hold" if r["day"] >= cut else "learn"
    groups: dict = {}
    coin: dict = {}
    every: dict = {}
    for r in rows:
        key = (r["style"], part(r))
        if r.get("coin"):
            coin.setdefault(key, []).append(r["r"])
            continue
        every.setdefault(key, []).append(r["r"])
        g = groups.setdefault(setup_id(r["kind"], r.get("variant"), r["style"]), {"learn": [], "hold": [], "net": [],
                                                                                   "kind": r["kind"], "variant": r.get("variant"),
                                                                                   "style": r["style"]})
        g[part(r)].append(r["r"])
        g["net"].append(r.get("net") or 0.0)
    styles = {s for s, _ in list(every) + list(coin)}
    yard = {}
    for s in styles:
        cl, ch = stats(coin.get((s, "learn"), [])), stats(coin.get((s, "hold"), []))
        if cl.get("n", 0) >= min_trades and ch.get("n", 0) >= min_hold:
            yard[s] = {"what": "coin-flip signals", "learn": cl, "hold": ch}
        else:
            yard[s] = {"what": "every signal of this style", "learn": stats(every.get((s, "learn"), [])),
                       "hold": stats(every.get((s, "hold"), [])), "coin_n": cl.get("n", 0) + ch.get("n", 0)}
    tested = [k for k, g in groups.items() if len(g["learn"]) >= min_trades]
    k = max(1, len(tested))
    t_bar = round(NormalDist().inv_cdf(1 - 0.05 / (2 * k)), 2)
    out = {"days": len(days), "first": days[0], "last": days[-1], "holdout_from": cut if cut in days else None,
           "t_bar": t_bar, "k": k, "yardstick": yard, "setups": [], "passing": [],
           "bar": {"min_trades": min_trades, "min_hold": min_hold, "hold_frac": hold_frac}}
    for sid, g in groups.items():
        L, H = stats(g["learn"]), stats(g["hold"])
        y = yard.get(g["style"]) or {}
        beats = all(r_.get("avg_r", -1e9) > (y.get(p) or {}).get("avg_r", -1e9) for p, r_ in (("learn", L), ("hold", H)))
        ok = bool(L.get("n", 0) >= min_trades and L.get("avg_r", -1) > 0 and L.get("t", 0) >= t_bar
                  and H.get("n", 0) >= min_hold and H.get("avg_r", -1) > 0 and beats)
        why = []
        if L.get("n", 0) < min_trades:
            why.append(f"only {L.get('n', 0)} learn trades")
        elif L.get("avg_r", -1) <= 0:
            why.append("loses after costs (learn)")
        elif L.get("t", 0) < t_bar:
            why.append(f"t {L.get('t')} below {t_bar}: luck not ruled out")
        if H.get("n", 0) < min_hold:
            why.append(f"only {H.get('n', 0)} holdout trades")
        elif H.get("avg_r", -1) <= 0:
            why.append("loses after costs on the holdout")
        if not beats and L.get("n", 0) >= min_trades:
            why.append(f"does not beat the yardstick ({y.get('what')})")
        net = g["net"]
        out["setups"].append({"setup": sid, "kind": g["kind"], "variant": g["variant"], "style": g["style"], "learn": L,
                              "hold": H, "rs_per_trade": round(sum(net) / len(net), 0) if net else None,
                              "passes": ok, "why_not": why})
        if ok:
            out["passing"].append(sid)
    out["setups"].sort(key=lambda r: (not r["passes"], -((r["learn"].get("t") or -99)), r["setup"]))
    return out


# ---------------------------------------------------------------- running it on the live bench
def bench_rows(data_dir, source: str = "upstox") -> list:
    """Closed bench trades of the real market and its coin-flip yardstick (read-only connection)."""
    p = Path(data_dir) / "bench.db"
    if not p.exists():
        return []
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    try:
        cur = c.execute("SELECT day, kind, variant, style, r_mult, net, source FROM bench_trades "
                        "WHERE source IN (?, ?) AND exit_ts IS NOT NULL", (source, f"{source}#coin"))
        return [{"day": d, "kind": k, "variant": v, "style": s, "r": r, "net": n, "coin": src.endswith("#coin")}
                for d, k, v, s, r, n, src in cur]
    finally:
        c.close()


def run(data_dir, source: str = "upstox", log=print) -> dict:
    """Runs the strict test on every closed bench trade and saves it (a new file each time, the history appended)."""
    t0 = dt.datetime.now(IST)
    rep = evaluate(bench_rows(data_dir, source))
    rep.update(made=t0.isoformat(timespec="seconds"), source=source,
               rows=sum((s["learn"].get("n", 0) + s["hold"].get("n", 0)) for s in rep["setups"]))
    d = Path(data_dir)
    tmp = d / "strict.json.tmp"
    tmp.write_text(json.dumps(rep, default=str))
    tmp.replace(d / "strict.json")
    with open(d / "strict_runs.jsonl", "a") as f:
        f.write(json.dumps({"made": rep["made"], "days": rep["days"], "k": rep["k"], "t_bar": rep["t_bar"],
                            "setups": len(rep["setups"]), "passing": rep["passing"]}) + "\n")
    with _LOCK:
        _CACHE.clear()
    log(f"Strict test: {len(rep['passing'])} of {rep['k']} tested setups pass ({rep['days']} days, t bar {rep['t_bar']})")
    return rep


def load(data_dir) -> dict:
    p = Path(data_dir) / "strict.json"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    with _LOCK:
        if _CACHE.get("mt") != mt or _CACHE.get("p") != str(p):
            try:
                rep = json.loads(p.read_text())
            except Exception:
                rep = {}
            _CACHE.update(mt=mt, p=str(p), rep=rep, passing=set(rep.get("passing") or []),
                          by={s["setup"]: s for s in rep.get("setups") or []})
        return _CACHE


def verdict(a: dict, data_dir) -> dict | None:
    """The strict test's word on an alert: None when its type has passed in no style, else the styles it passed in
    (and whether the paper accounts' style, Buy ATM, is one of them)."""
    if a.get("direction") not in ("up", "down"):
        return None
    c = load(data_dir)
    if not c.get("passing"):
        return None
    v = variant_of(a)
    ok = [s.split("|")[2] for s in c["passing"] if s.startswith(f"{a.get('kind')}|{v or ''}|")]
    if not ok:
        return None
    b = c["by"].get(setup_id(a["kind"], v, ok[0])) or {}
    return {"styles": ok, "buy": cfg()["paper_style"] in ok, "made": (c.get("rep") or {}).get("made"),
            "learn": b.get("learn"), "hold": b.get("hold")}
