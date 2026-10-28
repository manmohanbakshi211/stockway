"""Backtest lab: replay every minute of history through the strategy book, with realistic fills and charges.

1. For each strategy, try every setting in its grid on the TRAIN years (first 70% of days).
2. Pick the best setting by train average P&L per day (needs enough trades and profit factor > 1.05).
3. Run that one setting on the TEST years it never saw.
4. Certify only if the test period also makes money after charges.
5. Simulate the certified strategies together with the daily rules (loss stop, profit lock, trade cap).
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from live.clock import IST
from live.positions import DayBook, PORTFOLIO_DEFAULTS, open_position
from live.strategies import BOOK, LABELS, Ctx, DayArrays

from .data import History, all_expiries, read_meta

TRAIN_FRAC = 0.7
CERT = {"min_trades": 25, "min_pf": 1.1}


def combos(name):
    fn, base, grid = BOOK[name]
    keys = list(grid)
    out = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        out.append({**base, **dict(zip(keys, vals))})
    return out


def _replay_day(h: History, day, runs, vix_first=True):
    """runs: list of (key, strategy_name, params). Returns {key: [trade dicts]} for this day (1 lot each)."""
    bars, quotes, vmap = h.day(day)
    if len(bars) < 30:
        return {}
    d = DayArrays(bars, day)
    (prev, vix_prev) = h.prev(day)
    pc, ph, pl = prev if prev else (None, None, None)
    dte = h.dte(day)
    out = {}
    states = {key: {"st": {}, "pos": None} for key, _, _ in runs}
    for i in range(d.n):
        ts = int(d.ts[i])
        q = quotes.get(ts, {})
        quote = lambda k, s, q=q: (q.get((float(k), s)) or (None,))[0]
        iv_of = lambda k, s, q=q: (q.get((float(k), s)) or (None, None))[1]
        minute = int(d.minute[i])
        ctx = None
        for key, name, p in runs:
            S = states[key]
            pos = S["pos"]
            if pos is not None:
                prices = [quote(k, s) for k, s, _ in pos.legs]
                why = pos.update(minute, float(d.h[i]), float(d.l[i]), float(d.c[i]), prices)
                if why:
                    pos.close(ts, prices, why)
                    out.setdefault(key, []).append(pos.as_dict())
                    S["pos"] = None
                continue
            if ctx is None:
                ctx = Ctx(h.sym, d, i, h.step, h.lot, dte, pc, ph, pl, vmap.get(ts), vix_prev, quote, iv_of)
            plan = BOOK[name][0](ctx, p, S["st"])
            if plan:
                prices = [quote(k, s) for k, s, _ in plan.legs]
                pos = open_position(plan, h.sym, h.lot, 1, ts, ctx.spot, prices)
                if pos:
                    S["pos"] = pos
    last_ts = int(d.ts[-1])
    q = quotes.get(last_ts, {})
    for key, S in states.items():
        pos = S["pos"]
        if pos is not None:
            prices = [(q.get((float(k), s)) or (None,))[0] for k, s, _ in pos.legs]
            pos.close(last_ts, prices, "end of day")
            out.setdefault(key, []).append(pos.as_dict())
    return out


def _worker(args):
    data_dir, sym, start, end, expiry_days, runs = args
    h = History(data_dir, sym, start, end, expiry_days)
    res = {}
    for day in h.days:
        for key, trades in _replay_day(h, day, runs).items():
            for t in trades:
                t["day"] = day.isoformat()
            res.setdefault(key, []).extend(trades)
    return res, len(h.days)


def replay(data_dir, sym, days, expiry_days, runs, workers=None, progress=None):
    """Run many (strategy, settings) at once over the given days, split across processes."""
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))
    chunks = max(1, min(workers * 3, len(days) // 20 or 1))
    size = math.ceil(len(days) / chunks)
    jobs = [(str(data_dir), sym, days[k], days[min(len(days) - 1, k + size - 1)], expiry_days, runs)
            for k in range(0, len(days), size)]
    out = {}
    done = 0
    if workers == 1 or len(jobs) == 1:
        for j in jobs:
            r, n = _worker(j)
            done += n
            for key, tr in r.items():
                out.setdefault(key, []).extend(tr)
            if progress:
                progress(done, len(days))
        return out
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, j) for j in jobs]
        for f in as_completed(futs):
            r, n = f.result()
            done += n
            for key, tr in r.items():
                out.setdefault(key, []).extend(tr)
            if progress:
                progress(done, len(days))
    for key in out:
        out[key].sort(key=lambda t: t["entry_ts"])
    return out


# ---------------------------------------------------------------- metrics
def metrics(trades: list, days: list) -> dict:
    n = len(trades)
    daily = {d.isoformat() if hasattr(d, "isoformat") else d: 0.0 for d in days}
    for t in trades:
        daily[t["day"]] = daily.get(t["day"], 0.0) + t["net"]
    vals = list(daily.values())
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [-t["net"] for t in trades if t["net"] <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    traded = [v for d, v in daily.items() if any(t["day"] == d for t in trades)] if n < 5000 else [v for v in vals if v]
    mean = sum(vals) / len(vals) if vals else 0.0
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)) if len(vals) > 1 else 0.0
    return {"trades": n, "days": len(vals), "net": round(sum(vals), 0), "avg_day": round(mean, 1),
            "avg_trade": round(sum(t["net"] for t in trades) / n, 1) if n else None,
            "win_rate": round(len(wins) / n * 100) if n else None,
            "pf": round(sum(wins) / sum(losses), 2) if losses and sum(losses) > 0 else (99.0 if wins else None),
            "max_dd": round(dd, 0), "worst_day": round(min(vals), 0) if vals else None,
            "best_day": round(max(vals), 0) if vals else None,
            "green_days": round(sum(1 for v in traded if v > 0) / len(traded) * 100) if traded else None,
            "sharpe": round(mean / sd * math.sqrt(252), 2) if sd > 0 else None,
            "costs": round(sum(t["costs"] for t in trades), 0),
            "avg_win": round(sum(wins) / len(wins), 0) if wins else None,
            "avg_loss": round(-sum(losses) / len(losses), 0) if losses else None}


def _score(m):
    if m["trades"] < CERT["min_trades"] * TRAIN_FRAC or (m["pf"] or 0) < 1.05:
        return -1e9 + (m["avg_day"] or 0)
    return m["avg_day"] + 0.1 * (m["sharpe"] or 0)


def _daily_series(trades, days):
    daily = {d.isoformat(): 0.0 for d in days}
    for t in trades:
        daily[t["day"]] = daily.get(t["day"], 0.0) + t["net"]
    return [{"day": d, "net": round(v, 0)} for d, v in sorted(daily.items())]


# ---------------------------------------------------------------- the lab
def run_lab(data_dir, syms=None, strategies=None, log=print, progress=lambda f: None, workers=None):
    data_dir = Path(data_dir)
    meta = read_meta(data_dir)
    syms = syms or meta.get("syms") or ["NIFTY", "BANKNIFTY"]
    strategies = strategies or list(BOOK)
    t0 = time.time()
    results = {"syms": syms, "synthetic": bool(meta.get("synthetic")), "strategies": {}, "made": dt.datetime.now(IST).isoformat(),
               "cert_rule": f"Test years: profit after charges, profit factor ≥ {CERT['min_pf']}, "
                            f"at least {CERT['min_trades']} trades."}
    total_steps = len(syms) * 2
    step = 0
    for sym in syms:
        days, exp = all_expiries(data_dir, sym)
        if len(days) < 40:
            log(f"{sym}: only {len(days)} days of history, skipped")
            continue
        cut = int(len(days) * TRAIN_FRAC)
        train, test = days[:cut], days[cut:]
        runs = [(f"{name}#{j}", name, p) for name in strategies for j, p in enumerate(combos(name))]
        log(f"{sym}: {len(days)} days ({days[0]} to {days[-1]}), training on {len(train)}, testing on {len(test)}. "
            f"{len(runs)} strategy settings.")
        res = replay(data_dir, sym, train, exp, runs, workers,
                     lambda a, b: progress((step + a / max(1, b)) / total_steps))
        step += 1
        best = {}
        for key, name, p in runs:
            m = metrics(res.get(key, []), train)
            if name not in best or _score(m) > _score(best[name][1]):
                best[name] = (p, m, key)
        log(f"{sym}: best settings chosen on training years, now testing on unseen years…")
        test_runs = [(f"{name}#test", name, best[name][0]) for name in best]
        res_t = replay(data_dir, sym, test, exp, test_runs, workers,
                       lambda a, b: progress((step + a / max(1, b)) / total_steps))
        step += 1
        for name, (p, mtr, key) in best.items():
            tr = res_t.get(f"{name}#test", [])
            mte = metrics(tr, test)
            certified = (mte["trades"] >= CERT["min_trades"] and mte["net"] > 0 and (mte["pf"] or 0) >= CERT["min_pf"]
                         and mtr["avg_day"] > 0)
            results["strategies"][f"{sym}:{name}"] = {
                "sym": sym, "strategy": name, "label": LABELS[name], "params": p, "train": mtr, "test": mte,
                "certified": certified, "train_period": [str(train[0]), str(train[-1])], "test_period": [str(test[0]), str(test[-1])],
                "test_daily": _daily_series(tr, test), "test_trades": tr[-60:]}
            log(f"  {sym} {LABELS[name]}: train ₹{mtr['avg_day']:,.0f}/day, test ₹{mte['avg_day']:,.0f}/day "
                f"({mte['trades']} trades, PF {mte['pf']}) {'✓ CERTIFIED' if certified else '✗'}")
    results["seconds"] = round(time.time() - t0)
    (data_dir / "research").mkdir(exist_ok=True)
    (data_dir / "research" / "lab.json").write_text(json.dumps(results, default=str))
    log(f"Lab finished in {results['seconds'] // 60} min {results['seconds'] % 60} s.")
    return results


def run_portfolio(data_dir, cfg=None, log=print, period="test"):
    """All certified strategies together, with sizing and daily rules, over the test period."""
    data_dir = Path(data_dir)
    lab = load_lab(data_dir)
    if not lab:
        return None
    cert = [v for v in lab["strategies"].values() if v["certified"]]
    cfg = {**PORTFOLIO_DEFAULTS, **(cfg or {})}
    out = {"cfg": cfg, "strategies": [f"{v['sym']}:{v['strategy']}" for v in cert], "synthetic": lab.get("synthetic")}
    if not cert:
        out.update(daily=[], trades=[], metrics=None, note="No strategy is certified yet, so nothing would trade.")
        (data_dir / "research" / "portfolio.json").write_text(json.dumps(out, default=str))
        return out
    hists, days_by = {}, {}
    for sym in sorted({v["sym"] for v in cert}):
        days, exp = all_expiries(data_dir, sym)
        cut = int(len(days) * TRAIN_FRAC)
        sel = days[cut:] if period == "test" else days
        hists[sym] = History(data_dir, sym, sel[0], sel[-1], exp)
        days_by[sym] = set(hists[sym].days)
    all_days = sorted(set().union(*days_by.values()))
    book = DayBook(cfg)
    trades = []
    for day in all_days:
        book.reset()
        feeds = {}
        for sym, h in hists.items():
            if day not in days_by[sym]:
                continue
            bars, quotes, vmap = h.day(day)
            if len(bars) < 30:
                continue
            d = DayArrays(bars, day)
            prev, vprev = h.prev(day)
            feeds[sym] = (h, d, quotes, vmap, prev, vprev, h.dte(day), {v["strategy"]: {} for v in cert if v["sym"] == sym})
        if not feeds:
            continue
        open_pos = []
        n = max(f[1].n for f in feeds.values())
        for i in range(n):
            unreal = 0.0
            for sym, (h, d, quotes, vmap, prev, vprev, dte, sts) in feeds.items():
                if i >= d.n:
                    continue
                ts = int(d.ts[i])
                q = quotes.get(ts, {})
                quote = lambda k, s, q=q: (q.get((float(k), s)) or (None,))[0]
                for pos in [p for p in open_pos if p.sym == sym]:
                    prices = [quote(k, s) for k, s, _ in pos.legs]
                    why = pos.update(int(d.minute[i]), float(d.h[i]), float(d.l[i]), float(d.c[i]), prices)
                    if why:
                        pos.close(ts, prices, why)
                        x = pos.as_dict()
                        x["day"] = day.isoformat()
                        trades.append(x)
                        book.on_close(pos)
                        open_pos.remove(pos)
                    else:
                        unreal += pos.pnl(pos.last_px)
            book.mark(unreal)
            for sym, (h, d, quotes, vmap, prev, vprev, dte, sts) in feeds.items():
                if i >= d.n:
                    continue
                ok, _ = book.can_open(open_pos, sym, unreal)
                if not ok:
                    continue
                ts = int(d.ts[i])
                q = quotes.get(ts, {})
                quote = lambda k, s, q=q: (q.get((float(k), s)) or (None,))[0]
                iv_of = lambda k, s, q=q: (q.get((float(k), s)) or (None, None))[1]
                pc, ph, pl = prev if prev else (None, None, None)
                ctx = Ctx(sym, d, i, h.step, h.lot, dte, pc, ph, pl, vmap.get(ts), vprev, quote, iv_of)
                for v in cert:
                    if v["sym"] != sym:
                        continue
                    plan = BOOK[v["strategy"]][0](ctx, v["params"], sts[v["strategy"]])
                    if not plan:
                        continue
                    prices = [quote(k, s) for k, s, _ in plan.legs]
                    if any(p is None for p in prices):
                        continue
                    ev = sum(s * p for (_, _, s), p in zip(plan.legs, prices))
                    width = None
                    if plan.structure == "credit_ic":
                        ks = sorted(k for k, sd, _ in plan.legs if sd == "CE")
                        width = ks[-1] - ks[0]
                    lots = book.lots_for(plan.structure, ev, h.lot, width)
                    if lots < 1:
                        continue
                    pos = open_position(plan, sym, h.lot, lots, ts, ctx.spot, prices)
                    if pos:
                        open_pos.append(pos)
                        book.trades += 1
                        break
        for pos in open_pos:
            h, d, quotes = feeds[pos.sym][0], feeds[pos.sym][1], feeds[pos.sym][2]
            q = quotes.get(int(d.ts[-1]), {})
            pos.close(int(d.ts[-1]), [(q.get((float(k), s)) or (None,))[0] for k, s, _ in pos.legs], "end of day")
            x = pos.as_dict()
            x["day"] = day.isoformat()
            trades.append(x)
    m = metrics(trades, all_days)
    daily = _daily_series(trades, all_days)
    months = {}
    for r in daily:
        months[r["day"][:7]] = months.get(r["day"][:7], 0.0) + r["net"]
    out.update(metrics=m, daily=daily, months=[{"month": k, "net": round(v, 0)} for k, v in sorted(months.items())],
               trades=trades[-200:], period=[str(all_days[0]), str(all_days[-1])],
               return_pct=round(m["net"] / cfg["capital"] * 100, 1))
    (data_dir / "research" / "portfolio.json").write_text(json.dumps(out, default=str))
    log(f"Portfolio over {len(all_days)} unseen days: ₹{m['avg_day']:,.0f} per day on average, "
        f"{m['green_days']}% green days, worst day ₹{m['worst_day']:,.0f}, max drawdown ₹{m['max_dd']:,.0f}.")
    return out


def load_lab(data_dir):
    f = Path(data_dir) / "research" / "lab.json"
    return json.loads(f.read_text()) if f.exists() else None


def load_portfolio(data_dir):
    f = Path(data_dir) / "research" / "portfolio.json"
    return json.loads(f.read_text()) if f.exists() else None
