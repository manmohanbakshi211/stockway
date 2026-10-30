"""Strategy factory: test thousands of intraday option-buying rules at once, honestly.

How it works
1. Data (built once, then extended): every 5-minute bar of every day in the 1-minute option history (Nifty, Bank Nifty)
   gets ~20 facts known when the bar closes (`bar_features`, the same function the live app uses) and the result of
   buying the at-the-money call (long) or put (short) one minute later, under five exit rules, per lot, after charges
   and slippage (`outcomes`).
2. A strategy is a small rule in plain words: direction, 1-3 conditions on those facts, a time window, an exit rule,
   at most 1-2 trades a day. Rules come from a random search, from mutating the best, from ebooks and from Claude.
3. Every rule is scored on three separate periods, in time order: TRAIN (first 60% of days) to find it, VALIDATION
   (next 20%) to confirm it, HOLDOUT (last 20%, never used to choose anything) to prove it. Only rules that pass all
   three are promoted to live paper trading. The report also says how many would pass by pure luck given how many
   were tried, so a big search can't fool you.
4. Day results are computed the small-account way: 1 lot, the day stops after a losing trade, so "green days %" and
   "worst day" are what a ₹40K account would have lived through.
Market data only; nothing here can place an order.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .data import UNDERLYINGS, day_table, iter_days, vix_table
from .engine import charges, slip

M_EXIT = 360                 # 15:15
LAST_ENTRY_TOD = 330         # no entries after 14:45 (minutes since 09:15)
FIRST_ENTRY_TOD = 15         # bars closing at 09:30 or later

# exit rules on the option price: target %, stop %, max minutes held, trailing (start %, keep fraction)
EXITS = {"quick": (15, 10, 30, None), "std": (20, 15, 60, None), "swing": (30, 20, 90, None),
         "runner": (50, 25, 120, None), "trail": (None, 20, 150, (20, 0.5))}
EXIT_WORDS = {"quick": "+15% / −10% on the option, max 30 min", "std": "+20% / −15%, max 60 min",
              "swing": "+30% / −20%, max 90 min", "runner": "+50% / −25%, max 2 h",
              "trail": "trail: after +20% keep half the gain, stop −20%, max 2.5 h"}

# facts known at a bar's close: name -> (plain words, unit)
FEATURES = {
    "tod": ("time since 09:15", "min"), "ret_b1": ("last 5-min move", "%"), "ret_b3": ("15-min move", "%"),
    "ret_b6": ("30-min move", "%"), "day_ret": ("move vs yesterday's close", "%"), "gap": ("opening gap", "%"),
    "vs_twap": ("price vs the day's average price", "%"), "ema_gap": ("EMA 9 minus EMA 21 (5-min)", "%"),
    "rsi": ("RSI 14 (5-min)", ""), "or_pos": ("position vs opening range (±0.5 = at its edge)", ""),
    "pdh_dist": ("distance from yesterday's high", "%"), "pdl_dist": ("distance from yesterday's low", "%"),
    "range_x": ("this bar's range vs recent bars", "×"), "day_range": ("day's range so far", "%"),
    "pos_in_range": ("position in today's range (0 low, 1 high)", ""), "streak": ("bars in a row up (+) / down (−)", ""),
    "vix": ("India VIX (yesterday)", ""), "iv": ("ATM implied volatility", ""), "iv_chg3": ("IV change over 15 min", "pts"),
    "prem_pct": ("ATM straddle as % of price (priced move)", "%"), "dte": ("trading days to expiry", "d"), "dow": ("weekday (0 Mon)", ""),
}
WINDOWS = [(15, 120), (15, 330), (60, 240), (120, 330), (15, 75), (180, 330), (45, 180)]


# ------------------------------------------------------------------ features (shared with the live app)
def _ema(x, n):
    a = 2 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n, len(c)):
        if i > n:
            au = (au * (n - 1) + up[i]) / n
            ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100 - 100 / (1 + au / ad) if ad > 0 else 100.0
    return out


def bar_features(o, h, l, c, ctx: dict, iv=None, prem=None) -> dict:
    """Arrays (one value per 5-minute bar of today) of every fact in FEATURES. o/h/l/c: today's 5-min bars so far.
    ctx: prev_close, prev_high, prev_low, gap, vix, dte, dow. iv / prem: per-bar ATM IV and straddle % (or None)."""
    c = np.asarray(c, float)
    n = len(c)
    o, h, l = (np.asarray(x, float) for x in (o, h, l))
    pc = ctx.get("prev_close") or c[0]
    f = {"tod": (np.arange(n) + 1) * 5.0}
    def ret(k):
        r = np.full(n, np.nan)
        r[k:] = (c[k:] / c[:-k] - 1) * 100 if n > k else r[k:]
        return r
    f["ret_b1"], f["ret_b3"], f["ret_b6"] = ret(1), ret(3), ret(6)
    f["day_ret"] = (c / pc - 1) * 100
    f["gap"] = np.full(n, ctx.get("gap") if ctx.get("gap") is not None else np.nan)
    f["vs_twap"] = (c / (np.cumsum(c) / (np.arange(n) + 1)) - 1) * 100
    f["ema_gap"] = (_ema(c, 9) - _ema(c, 21)) / c * 100
    f["rsi"] = _rsi(c)
    orh, orl = (h[:3].max(), l[:3].min()) if n >= 3 else (np.nan, np.nan)
    rng = (orh - orl) or np.nan
    f["or_pos"] = (c - (orh + orl) / 2) / rng
    f["or_pos"][:3] = np.nan
    f["pdh_dist"] = (c / ctx["prev_high"] - 1) * 100 if ctx.get("prev_high") else np.full(n, np.nan)
    f["pdl_dist"] = (c / ctx["prev_low"] - 1) * 100 if ctx.get("prev_low") else np.full(n, np.nan)
    br = h - l
    avg = pd.Series(br).shift(1).rolling(12, min_periods=3).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f["range_x"] = np.where(avg > 0, br / avg, np.nan)
    hh, ll = np.maximum.accumulate(h), np.minimum.accumulate(l)
    f["day_range"] = (hh - ll) / c[0] * 100
    with np.errstate(divide="ignore", invalid="ignore"):
        f["pos_in_range"] = np.where(hh > ll, (c - ll) / (hh - ll), 0.5)
    st = np.zeros(n)
    for i in range(1, n):
        d = np.sign(c[i] - c[i - 1])
        st[i] = (st[i - 1] + d) if d != 0 and np.sign(st[i - 1]) in (0, d) else d
    f["streak"] = st
    f["vix"] = np.full(n, ctx.get("vix") if ctx.get("vix") is not None else np.nan)
    ivv = np.asarray(iv, float) if iv is not None else np.full(n, np.nan)
    f["iv"] = ivv
    f["iv_chg3"] = np.concatenate([np.full(min(3, n), np.nan), ivv[3:] - ivv[:-3]]) if n > 3 else np.full(n, np.nan)
    f["prem_pct"] = np.asarray(prem, float) if prem is not None else np.full(n, np.nan)
    f["dte"] = np.full(n, ctx.get("dte") if ctx.get("dte") is not None else np.nan)
    f["dow"] = np.full(n, ctx.get("dow", np.nan))
    return f


# ------------------------------------------------------------------ outcomes
def _sim(c, sp, m0, entry, sym, lot, rule):
    tgt, stp, hold, trail = EXITS[rule]
    end = min(M_EXIT, m0 + hold)
    seg = c[m0 + 1:end + 1]
    if not len(seg):
        return math.nan
    g = (seg / entry - 1) * 100
    hit = g <= -stp
    if tgt is not None:
        hit |= g >= tgt
    if trail:
        peak = np.maximum.accumulate(np.nan_to_num(g, nan=-1e9))
        hit |= (peak >= trail[0]) & (g <= peak * trail[1])
    hit &= ~np.isnan(g)
    k = int(np.argmax(hit)) if hit.any() else len(seg) - 1
    x = seg[k]
    if math.isnan(x):
        ok = np.where(~np.isnan(seg[:k + 1]))[0]
        if not len(ok):
            return math.nan
        x = seg[ok[-1]]
    ex = max(0.05, x - slip(sym, x))
    return (ex - entry) * lot - charges([(1, entry), (-1, ex)], lot)


def build(data_dir, syms=("NIFTY", "BANKNIFTY"), log=print, should_stop=lambda: False) -> pd.DataFrame:
    """One row per (symbol, day, bar, direction): the bar's facts and the option-buy P&L per lot under each exit."""
    vix = vix_table(data_dir)
    vmap = dict(zip(pd.to_datetime(vix["date"]).dt.date, vix["vix_prev"])) if len(vix) else {}
    rows = []
    for sym in syms:
        lot = UNDERLYINGS[sym]["lot"]
        days = list(iter_days(data_dir, sym))
        if not days:
            continue
        t0 = time.time()
        dtab = day_table(data_dir, sym, days).set_index("date")
        for di, day in enumerate(days):
            if should_stop():
                return pd.DataFrame(rows)
            r = dtab.loc[day.date] if day.date in dtab.index else None
            sp = day.spot
            nb = int(np.sum(~np.isnan(sp)) // 5)
            if nb < 10:
                continue
            seg = sp[:5 * nb].reshape(nb, 5)
            o, h, l, c = seg[:, 0], seg.max(1), seg.min(1), seg[:, -1]
            ivs, prem = [], []
            for i in range(nb):
                m = 5 * i + 4
                k = day.atm(m)
                v = [day.price(k, s, m, "iv") for s in ("CE", "PE")]
                v = [x for x in v if not math.isnan(x)]
                ivs.append(float(np.mean(v)) if v else math.nan)
                ce, pe = day.price(k, "CE", m), day.price(k, "PE", m)
                prem.append((ce + pe) / sp[m] * 100 if not (math.isnan(ce) or math.isnan(pe)) else math.nan)
            ctx = {"prev_close": None if r is None else r["prev_close"], "prev_high": None if r is None else r["prev_high"],
                   "prev_low": None if r is None else r["prev_low"], "gap": None if r is None else r["gap_pct"],
                   "vix": vmap.get(day.date), "dte": None if r is None or pd.isna(r["dte"]) else r["dte"], "dow": day.date.weekday()}
            for key in ("prev_close", "prev_high", "prev_low", "gap"):
                if ctx[key] is not None and (isinstance(ctx[key], float) and math.isnan(ctx[key])):
                    ctx[key] = None
            f = bar_features(o, h, l, c, ctx, ivs, prem)
            for i in range(nb):
                tod = (i + 1) * 5
                if tod < FIRST_ENTRY_TOD or tod > LAST_ENTRY_TOD:
                    continue
                m0 = 5 * i + 5
                if m0 >= M_EXIT:
                    continue
                k = day.atm(m0)
                base = {"sym": sym, "date": day.date, "bar": i, **{name: float(f[name][i]) for name in FEATURES}}
                for d, side in ((1, "CE"), (-1, "PE")):
                    px = day.px.get((float(k), side))
                    if px is None:
                        continue
                    e = px["c"][m0]
                    if math.isnan(e) or e <= 0.5:
                        continue
                    entry = e + slip(sym, e)
                    row = {**base, "dir": d, "premium": round(entry * lot, 0)}
                    for rule in EXITS:
                        row["pnl_" + rule] = _sim(px["c"], sp, m0, entry, sym, lot, rule)
                    rows.append(row)
            if (di + 1) % 100 == 0:
                log(f"  {sym}: {di + 1} of {len(days)} days ({time.time() - t0:.0f}s)")
        log(f"{sym}: {len(days)} days ready")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["date", "sym", "bar", "dir"]).reset_index(drop=True)
        for c_ in ("pnl_" + r for r in EXITS):
            df[c_] = df[c_].astype("float32")
    return df


def cache_path(data_dir) -> Path:
    return Path(data_dir) / "lab" / "factory_bars.parquet"


def load_or_build(data_dir, log=print, should_stop=lambda: False, rebuild=False) -> pd.DataFrame:
    f = cache_path(data_dir)
    hist = Path(data_dir) / "history"
    newest = max((p.stat().st_mtime for p in hist.glob("*/raw_*.parquet")), default=0)
    if f.exists() and not rebuild and f.stat().st_mtime >= newest:
        return pd.read_parquet(f)
    log("Building the bar table from the option history (once; later runs reuse it)…")
    df = build(data_dir, log=log, should_stop=should_stop)
    if not df.empty:
        f.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(f, index=False)
    return df


# ------------------------------------------------------------------ strategies
def sid(s: dict) -> str:
    key = json.dumps({k: s[k] for k in ("dir", "conds", "win", "exit", "max_day", "syms")}, sort_keys=True)
    return "S" + hashlib.sha1(key.encode()).hexdigest()[:8]


def words(s: dict) -> str:
    """The rule in plain words."""
    ops = {">": "above", "<": "below"}
    cs = []
    for feat, op, v in s["conds"]:
        name, unit = FEATURES.get(feat, (feat, ""))
        cs.append(f"{name} {ops.get(op, op)} {v:g}{unit if unit in ('%', '×', 'min', 'd', 'pts') else ''}")
    hm = lambda t: (dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=t)).strftime("%H:%M")
    side = "Buy the ATM call" if s["dir"] > 0 else "Buy the ATM put"
    return (f"{side} on {' / '.join(x.replace('BANKNIFTY', 'Bank Nifty').replace('NIFTY', 'Nifty') for x in s['syms'])} "
            f"between {hm(s['win'][0])} and {hm(s['win'][1])} when " + " and ".join(cs) +
            f". Exit {EXIT_WORDS[s['exit']]}. Max {s['max_day']} trade{'s' if s['max_day'] > 1 else ''} a day, none after a loss.")


def validate(s: dict) -> dict | None:
    """Clean a strategy coming from outside (ebook / Claude); None if it can't be tested."""
    try:
        conds = [[str(f), str(o), float(v)] for f, o, v in s.get("conds", [])]
        if not conds or len(conds) > 4 or any(f not in FEATURES or o not in (">", "<") for f, o, _ in conds):
            return None
        d = 1 if int(s.get("dir", 1)) > 0 else -1
        w = [int(s.get("win", [15, 330])[0]), int(s.get("win", [15, 330])[1])]
        w = [max(FIRST_ENTRY_TOD, min(w)), min(LAST_ENTRY_TOD, max(w))]
        ex = s.get("exit") if s.get("exit") in EXITS else "std"
        syms = [x for x in (s.get("syms") or ["NIFTY", "BANKNIFTY"]) if x in UNDERLYINGS] or ["NIFTY", "BANKNIFTY"]
        out = {"dir": d, "conds": conds, "win": w, "exit": ex, "max_day": 2 if int(s.get("max_day", 1)) > 1 else 1,
               "syms": sorted(syms), "source": str(s.get("source") or "manual")[:80], "note": str(s.get("note") or "")[:300]}
        out["id"] = sid(out)
        return out
    except Exception:
        return None


class Book:
    """The bar table split into train / validation / holdout by date, with fast rule evaluation."""

    def __init__(self, df: pd.DataFrame, cuts=(0.6, 0.8)):
        self.df = df.reset_index(drop=True)
        dates = np.array(sorted(df["date"].unique()))
        self.dates = dates
        a, b = int(len(dates) * cuts[0]), int(len(dates) * cuts[1])
        self.split_dates = {"train": (dates[0], dates[a - 1]), "valid": (dates[a], dates[b - 1]), "hold": (dates[b], dates[-1])}
        di = {d: i for i, d in enumerate(dates)}
        self.dix = df["date"].map(di).to_numpy()
        self.part = np.where(self.dix < a, 0, np.where(self.dix < b, 1, 2))
        self.cols = {c: df[c].to_numpy() for c in list(FEATURES) + ["dir", "sym"]}
        self.pnl = {r: df["pnl_" + r].to_numpy(dtype=float) for r in EXITS}
        self.symcode = df["sym"].to_numpy()
        self.symi = (self.symcode == "BANKNIFTY").astype(np.int64)
        self.n_days = {0: a, 1: b - a, 2: len(dates) - b}

    def quantiles(self, feat, part=0):
        x = self.cols[feat][self.part == part]
        x = x[~np.isnan(x)]
        return np.unique(np.round(np.quantile(x, [0.1, 0.25, 0.5, 0.75, 0.9]), 3)) if len(x) else np.array([])

    def trades(self, s: dict, part: int | None = None) -> pd.DataFrame:
        m = (self.cols["dir"] == s["dir"]) & (self.cols["tod"] >= s["win"][0]) & (self.cols["tod"] <= s["win"][1])
        m &= np.isin(self.symcode, s["syms"])
        if part is not None:
            m &= self.part == part
        for feat, op, v in s["conds"]:
            x = self.cols[feat]
            m &= (x > v) if op == ">" else (x < v)
        idx = np.nonzero(m)[0]
        pnl = self.pnl[s["exit"]][idx]
        ok = ~np.isnan(pnl)
        idx, pnl = idx[ok], pnl[ok]
        if not len(idx):
            return pd.DataFrame(columns=["day", "sym", "tod", "pnl", "part"])
        key = self.dix[idx] * 2 + self.symi[idx]
        tod = self.cols["tod"][idx]
        _, first = np.unique(key, return_index=True)        # rows are in time order: first signal of each day/symbol
        take = list(first)
        if s["max_day"] > 1:                                # a second trade only after a winning first, once it is over
            gap = max(30, EXITS[s["exit"]][2])
            won = {key[j]: tod[j] + gap for j in first if pnl[j] > 0}
            if won:
                k_after = np.array([won.get(k, np.inf) for k in key])
                cand = np.nonzero(tod >= k_after)[0]
                if len(cand):
                    _, f2 = np.unique(key[cand], return_index=True)
                    take += list(cand[f2])
        take = np.array(sorted(take), int)
        j = idx[take]
        return pd.DataFrame({"day": self.dix[j], "sym": self.symcode[j], "tod": self.cols["tod"][j], "pnl": pnl[take], "part": self.part[j]})


def stats(t: pd.DataFrame, n_days: int) -> dict:
    if t is None or not len(t):
        return {"n": 0}
    x = t["pnl"].to_numpy()
    n = len(x)
    win, loss = x[x > 0].sum(), -x[x < 0].sum()
    sd = x.std(ddof=1) if n > 2 else 0
    daily = t.groupby("day")["pnl"].sum()
    eq = daily.cumsum()
    dd = float((eq - eq.cummax()).min()) if len(eq) else 0.0
    return {"n": int(n), "avg": round(float(x.mean()), 1), "total": round(float(x.sum()), 0), "win_pct": round(float((x > 0).mean() * 100), 1),
            "pf": round(float(win / loss), 2) if loss else 99.0, "t": round(float(x.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0,
            "days": int(len(daily)), "green_pct": round(float((daily > 0).mean() * 100), 1), "worst_day": round(float(daily.min()), 0),
            "best_day": round(float(daily.max()), 0), "max_dd": round(dd, 0), "per_day": round(float(x.sum() / max(1, n_days)), 1)}


PASS = {"train": dict(n=30, pf=1.15, t=2.0), "valid": dict(n=10, pf=1.05, t=1.0), "hold": dict(n=10, pf=1.0, t=0.0)}
P_LUCK = 0.023 * 0.16 * 0.5          # chance a rule with no edge passes all three (normal approximation, before costs)


def passes(st: dict, rule: dict) -> bool:
    return st.get("n", 0) >= rule["n"] and st.get("avg", 0) > 0 and st.get("pf", 0) >= rule["pf"] and st.get("t", 0) >= rule["t"]


def score(book: Book, s: dict) -> dict:
    tr = book.trades(s)
    out = {"id": s["id"], "rule": words(s), **{k: s[k] for k in ("dir", "conds", "win", "exit", "max_day", "syms")},
           "source": s.get("source", "generated"), "note": s.get("note", "")}
    for p, name in ((0, "train"), (1, "valid"), (2, "hold")):
        out[name] = stats(tr[tr["part"] == p], book.n_days[p])
    if not passes(out["train"], PASS["train"]):
        out["stage"] = "failed train"
    elif not passes(out["valid"], PASS["valid"]):
        out["stage"] = "failed validation"
    elif not passes(out["hold"], PASS["hold"]):
        out["stage"] = "failed holdout"
    else:
        out["stage"] = "promoted"
    t = out["hold"].get("t") or 0
    out["evidence"] = "strong" if t >= 2 else "moderate" if t >= 1 else "weak"
    return out


# ------------------------------------------------------------------ search
def random_rule(book: Book, rng: random.Random) -> dict:
    feats = [f for f in FEATURES if f not in ("tod",)]
    conds = []
    for feat in rng.sample(feats, rng.choice([1, 2, 2, 3])):
        q = book.quantiles(feat)
        if not len(q):
            continue
        conds.append([feat, rng.choice([">", "<"]), float(rng.choice(list(q)))])
    if not conds:
        conds = [["ret_b3", ">", 0.1]]
    s = {"dir": rng.choice([1, -1]), "conds": conds, "win": list(rng.choice(WINDOWS)), "exit": rng.choice(list(EXITS)),
         "max_day": rng.choice([1, 1, 2]), "syms": rng.choice([["NIFTY"], ["BANKNIFTY"], ["BANKNIFTY", "NIFTY"]]), "source": "generated"}
    s["id"] = sid(s)
    return s


def mutate(book: Book, s: dict, rng: random.Random) -> dict:
    m = json.loads(json.dumps({k: s[k] for k in ("dir", "conds", "win", "exit", "max_day", "syms")}))
    what = rng.choice(["thr", "thr", "exit", "win", "add", "drop", "syms"])
    if what == "thr" and m["conds"]:
        c = rng.choice(m["conds"])
        q = list(book.quantiles(c[0]))
        if q:
            c[2] = float(rng.choice(q))
    elif what == "exit":
        m["exit"] = rng.choice(list(EXITS))
    elif what == "win":
        m["win"] = list(rng.choice(WINDOWS))
    elif what == "add" and len(m["conds"]) < 3:
        feat = rng.choice([f for f in FEATURES if f not in [c[0] for c in m["conds"]] and f != "tod"])
        q = list(book.quantiles(feat))
        if q:
            m["conds"].append([feat, rng.choice([">", "<"]), float(rng.choice(q))])
    elif what == "drop" and len(m["conds"]) > 1:
        m["conds"].pop(rng.randrange(len(m["conds"])))
    elif what == "syms":
        m["syms"] = rng.choice([["NIFTY"], ["BANKNIFTY"], ["BANKNIFTY", "NIFTY"]])
    m["source"] = s.get("source", "generated") if s.get("source", "").startswith(("ebook", "claude")) else "mutation"
    m["note"] = s.get("note", "")
    m["id"] = sid(m)
    return m


def quick_train(book: Book, s: dict) -> dict:
    return stats(book.trades(s, part=0), book.n_days[0])


def search(book: Book, n_random=2000, n_mutate=1000, seeds: list | None = None, log=print, should_stop=lambda: False,
           seed=None, tried: set | None = None) -> tuple[list, int]:
    """Random rules + mutations of the best on TRAIN only; returns (rules passing train, number tried)."""
    rng = random.Random(seed)
    tried = tried if tried is not None else set()
    scored = []
    def test(s):
        if s["id"] in tried:
            return
        tried.add(s["id"])
        st = quick_train(book, s)
        scored.append((s, st))
    for s in seeds or []:
        test(s)
    t0 = time.time()
    for i in range(n_random):
        if should_stop():
            break
        test(random_rule(book, rng))
        if (i + 1) % 500 == 0:
            log(f"  random rules: {i + 1}/{n_random} ({time.time() - t0:.0f}s)")
    good = sorted([x for x in scored if x[1].get("n", 0) >= 20], key=lambda x: -(x[1].get("t") or 0))[:60]
    for i in range(n_mutate):
        if should_stop() or not good:
            break
        parent = rng.choice(good)[0]
        test(mutate(book, parent, rng))
        if (i + 1) % 250 == 0:
            good = sorted([x for x in scored if x[1].get("n", 0) >= 20], key=lambda x: -(x[1].get("t") or 0))[:60]
    passed = [s for s, st in scored if passes(st, PASS["train"])]
    return passed, len(scored)


# ------------------------------------------------------------------ the run
def lab_file(data_dir) -> Path:
    return Path(data_dir) / "lab" / "factory.json"


def load(data_dir) -> dict:
    f = lab_file(data_dir)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"tried_total": 0, "runs": [], "rules": {}, "ideas": []}


def save(data_dir, st: dict):
    f = lab_file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st, default=str))


def run(data_dir, n_random=2000, n_mutate=1000, log=print, should_stop=lambda: False, extra: list | None = None) -> dict:
    """One research round: (re)score known rules on all periods, search new ones, keep the honest tally."""
    t0 = time.time()
    df = load_or_build(data_dir, log=log, should_stop=should_stop)
    if df.empty:
        raise RuntimeError("No 1-minute option history yet: download it on the Strategy desk first.")
    book = Book(df)
    st = load(data_dir)
    known = st.get("rules", {})
    seeds = [validate(x) for x in (extra or [])] + [validate({**r, "source": r.get("source")}) for r in st.get("ideas", [])]
    seeds = [s for s in seeds if s]
    tried = set(known)
    log(f"Testing on {len(book.dates)} days: train {book.split_dates['train'][0]} → {book.split_dates['train'][1]}, "
        f"validation → {book.split_dates['valid'][1]}, holdout → {book.split_dates['hold'][1]}")
    passed, n = search(book, n_random, n_mutate, seeds=seeds, log=log, should_stop=should_stop, tried=tried)
    st["tried_total"] = int(st.get("tried_total", 0)) + n
    log(f"Tried {n:,} new rules ({st['tried_total']:,} in total); {len(passed)} pass the TRAIN bar; checking them on later periods…")
    for s in passed:
        known[s["id"]] = score(book, s)
    # outside ideas are always kept with their result, even when they fail (so you see what the book claimed)
    for s in seeds:
        if s["id"] not in known:
            known[s["id"]] = score(book, s)
    for k in list(known):                                 # rescore everything on the current data
        r = known[k]
        s = validate({**r, "source": r.get("source")}) or r
        known[k] = {**score(book, {**s, "id": k}), "live": r.get("live"), "found": r.get("found") or dt.date.today().isoformat()}
    # the same trades under different wording count once: keep the simplest rule of each identical result
    sig = {}
    for k, v in sorted(known.items(), key=lambda kv: len(kv[1]["conds"])):
        if v["stage"] != "promoted":
            continue
        key = json.dumps([v[p].get(x) for p in ("train", "valid", "hold") for x in ("n", "total")])
        if key in sig:
            v["stage"], v["dup_of"] = "duplicate", sig[key]
        else:
            sig[key] = k
    # keep the file small: everything promoted / from outside, plus the 300 best others by validation result
    keep = {k: v for k, v in known.items() if v["stage"] == "promoted" or not v["source"] in ("generated", "mutation")}
    rest = sorted((v for k, v in known.items() if k not in keep), key=lambda v: -(v["valid"].get("t") or -9))[:300]
    keep.update({v["id"]: v for v in rest})
    st["rules"] = keep
    st["ideas"] = []
    prom = [v for v in keep.values() if v["stage"] == "promoted"]
    run_rec = {"at": dt.datetime.now().isoformat(timespec="seconds"), "tried": n, "train_pass": len(passed),
               "promoted": len(prom), "luck_expected": round(st["tried_total"] * P_LUCK, 1), "secs": round(time.time() - t0)}
    st["runs"] = (st.get("runs", []) + [run_rec])[-50:]
    st["period"] = {k: [str(a), str(b)] for k, (a, b) in book.split_dates.items()}
    st["n_days"] = len(book.dates)
    st["updated"] = run_rec["at"]
    save(data_dir, st)
    log(f"Done: {len(prom)} rules promoted (passed train, validation and holdout). By pure luck about "
        f"{run_rec['luck_expected']} of {st['tried_total']:,} tried would pass. ({run_rec['secs']}s)")
    return st


def view(data_dir) -> dict:
    st = load(data_dir)
    rules = sorted(st.get("rules", {}).values(), key=lambda r: (r["stage"] != "promoted", -(r["hold"].get("t") or -9)))
    by = {}
    for r in rules:
        by[r["stage"]] = by.get(r["stage"], 0) + 1
    return {"updated": st.get("updated"), "tried_total": st.get("tried_total", 0), "luck_expected": round(st.get("tried_total", 0) * P_LUCK, 1),
            "period": st.get("period"), "n_days": st.get("n_days"), "runs": st.get("runs", [])[-10:], "by_stage": by,
            "rules": rules[:120], "features": {k: v[0] for k, v in FEATURES.items()}, "exits": EXIT_WORDS,
            "pending_ideas": len(st.get("ideas", []))}


def add_ideas(data_dir, ideas: list, source: str) -> dict:
    """Queue strategies from an ebook or Claude; they are tested on the next run (train/validation/holdout as usual)."""
    st = load(data_dir)
    ok, bad = [], 0
    for x in ideas:
        v = validate({**x, "source": source})
        if v:
            ok.append(v)
        else:
            bad += 1
    st["ideas"] = (st.get("ideas", []) + ok)[-500:]
    save(data_dir, st)
    return {"queued": len(ok), "rejected": bad}


def promoted(data_dir) -> list:
    return [r for r in load(data_dir).get("rules", {}).values() if r.get("stage") == "promoted" and r.get("live") != "off"]


def signal_now(rule: dict, feats: dict, sym: str, i: int | None = None) -> bool:
    """Does a promoted rule fire on the latest closed bar of `sym`? feats: output of bar_features for today."""
    if sym not in rule["syms"]:
        return False
    i = len(feats["tod"]) - 1 if i is None else i
    tod = feats["tod"][i]
    if not (rule["win"][0] <= tod <= rule["win"][1]):
        return False
    for feat, op, v in rule["conds"]:
        x = feats[feat][i]
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return False
        if not ((x > v) if op == ">" else (x < v)):
            return False
    return True


# ------------------------------------------------------------------ Claude as a researcher
def ideas_prompt(data_dir) -> str:
    """What has been tried and how it went, so Claude proposes new rules worth testing (not repeats)."""
    v = view(data_dir)
    def brief(r):
        return {"rule": r["rule"], "stage": r["stage"], "train": {k: r["train"].get(k) for k in ("n", "avg", "t")},
                "valid": {k: r["valid"].get(k) for k in ("n", "avg", "t")}, "hold": {k: r["hold"].get(k) for k in ("n", "avg", "t")}}
    rules = v["rules"]
    best = [brief(r) for r in rules[:12]]
    feats = "; ".join(f"{k} = {x}" for k, x in v["features"].items())
    return ("You are a quantitative researcher for an Indian index-options desk (Nifty, Bank Nifty). The backtester buys the "
            "at-the-money call (long) or put (short) one minute after a 5-minute bar closes, per lot, after charges and "
            "slippage. Random entries lose about ₹220 per trade (premium decay + costs), so a rule must beat that. "
            f"Facts known at a bar's close: {feats}. Exits: {json.dumps(EXIT_WORDS)}. Time is minutes since 09:15.\n"
            f"So far {v['tried_total']} rules were tried; by luck about {v['luck_expected']} would pass all three periods. "
            f"Best and near-miss results: {json.dumps(best)}\n"
            "Propose 12 NEW rules with a market reason behind each (e.g. opening-drive continuation on gap days, "
            "mean reversion after an exhausted move when options are expensive, expiry-day behaviour, volatility "
            "expansion from a tight range). Keep each rule simple (1-3 conditions): simple rules hold up on new data. "
            'Reply with JSON only: {"strategies": [{"dir": 1 or -1, "conds": [["feature", ">" or "<", number]], '
            '"win": [from_min, to_min], "exit": "std", "max_day": 1, "syms": ["NIFTY", "BANKNIFTY"], "note": "the reason, one line"}]}')


def ask_ideas(data_dir, ai, auto=False) -> dict:
    import re as _re
    if not ai or not ai.enabled():
        raise PermissionError("Connect Claude (your Pro plan) in Live setup → AI first.")
    text = ai.ask(ideas_prompt(data_dir), 2000, auto=auto)
    m = _re.search(r"\{.*\}", text or "", _re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except Exception:
        out = {}
    return add_ideas(data_dir, out.get("strategies") or [], source="claude")


# ------------------------------------------------------------------ live record -> demotion
def apply_live_record(data_dir, trades: list, min_n=15) -> list:
    """Paper trades of promoted rules (alert kind 'factory'): a rule losing money live over min_n trades is switched off.
    trades: [{'rule': id, 'net': ₹}]"""
    st = load(data_dir)
    by = {}
    for t in trades:
        by.setdefault(t["rule"], []).append(t["net"] or 0)
    off = []
    for k, r in st.get("rules", {}).items():
        xs = by.get(k)
        if not xs:
            continue
        r["live_record"] = {"n": len(xs), "net": round(sum(xs), 0), "win_pct": round(sum(x > 0 for x in xs) / len(xs) * 100, 1)}
        if len(xs) >= min_n and sum(xs) < 0 and r.get("live") != "off":
            r["live"] = "off"
            off.append(k)
    save(data_dir, st)
    return off


def set_live(data_dir, rule_id: str, on: bool) -> dict:
    st = load(data_dir)
    r = st.get("rules", {}).get(rule_id)
    if not r:
        raise KeyError(rule_id)
    r["live"] = "on" if on else "off"
    save(data_dir, st)
    return r
