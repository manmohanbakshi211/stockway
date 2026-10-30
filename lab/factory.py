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

from .data import UNDERLYINGS, day_table, iter_days, register_stocks, vix_table
from .engine import charges, slip
from .exits import STYLES, atr14, first_exit, risk_pts

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
# indicators with the settings traders use (several settings each, so the Lab can tell which works best)
FEATURES.update({
    "fc_pos": ("position vs the first 5-min candle (9:15-9:20; ±0.5 = at its high/low)", ""),
    "or30_pos": ("position vs the 30-min opening range (±0.5 = at its edge)", ""),
    "pm_pos": ("position vs the 14:15-14:20 candle (±0.5 = at its high/low)", ""),
    "st_10_3": ("Supertrend 10,3 direction (+1 up / -1 down)", ""), "st_7_3": ("Supertrend 7,3 direction", ""),
    "st_10_2": ("Supertrend 10,2 direction", ""), "st_flip": ("bars since Supertrend 10,3 flipped", ""),
    "bb_pctb": ("Bollinger 20,2 %B (0 lower band, 1 upper band)", ""), "bb_width": ("Bollinger 20,2 width", "%"),
    "bb15_pctb": ("Bollinger 20,1.5 %B", ""), "vwap_z": ("distance from the day's average price in std-devs (VWAP bands)", ""),
    "macd_h": ("MACD 12,26,9 histogram", "%"), "adx": ("ADX 14 (trend strength)", ""), "di_diff": ("+DI minus -DI (14)", ""),
    "rsi2": ("RSI 2 (Connors)", ""), "rsi7": ("RSI 7", ""), "ema5_13": ("EMA 5 minus EMA 13", "%"),
    "vs_sma20": ("price vs SMA 20 (5-min)", "%"), "cpr_width": ("CPR width (today's pivot band)", "%"),
    "cpr_pos": ("price vs the CPR (+ above top, - below bottom, 0 inside)", ""),
})
INDEX_SYMS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX")
STOCKS = "STOCKS"             # in a rule's syms: any F&O stock (tested on every stock with downloaded history)


def sym_ok(syms, sym) -> bool:
    return sym in syms or (STOCKS in syms and sym not in INDEX_SYMS)


def lab_syms(data_dir) -> list:
    register_stocks(data_dir)
    return ["NIFTY", "BANKNIFTY"] + sorted(k for k, v in UNDERLYINGS.items() if v.get("stock"))


WINDOWS = [(15, 120), (15, 330), (60, 240), (120, 330), (15, 75), (180, 330), (45, 180), (305, 330)]

# selling the at-the-money straddle (both legs short), each leg with its own stop, out at 15:15
SELL_EXITS = {"ss25": 25, "ss35": 35, "ss50": 50, "fly30": 30, "fly60": 60}
FLY_WINGS = 4                 # iron fly: long wings 4 strikes away (Nifty ±200, Bank Nifty ±400): fits a ~₹40-60K margin
EXIT_WORDS.update({"ss25": "sell ATM call + put, 25% stop on each leg, out by 15:15",
                   "ss35": "sell ATM call + put, 35% stop on each leg, out by 15:15",
                   "ss50": "sell ATM call + put, 50% stop on each leg, out by 15:15",
                   "fly30": "iron fly: sell ATM call + put, buy wings 4 strikes away; out if the loss reaches 30% of the credit, or 15:15",
                   "fly60": "iron fly (wings 4 strikes away); out at a loss of 60% of the credit, or 15:15"})


def _wilder(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def _tr(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]])
    return np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))


def supertrend(h, l, c, n=10, m=3.0):
    """Supertrend direction per bar (+1 / -1; 0 until there is enough data). Standard final-band rules."""
    atr = _wilder(_tr(h, l, c), n)
    mid = (h + l) / 2
    bu, bl = mid + m * atr, mid - m * atr
    d = np.zeros(len(c))
    fu = fl = np.nan
    trend = 1
    for i in range(len(c)):
        if np.isnan(atr[i]):
            continue
        if np.isnan(fu):
            fu, fl = bu[i], bl[i]
        else:
            fu = bu[i] if (bu[i] < fu or c[i - 1] > fu) else fu
            fl = bl[i] if (bl[i] > fl or c[i - 1] < fl) else fl
        if trend < 0 and c[i] > fu:
            trend = 1
        elif trend > 0 and c[i] < fl:
            trend = -1
        d[i] = trend
    return d


def adx(h, l, c, n=14):
    upm = np.concatenate([[0], np.diff(h)])
    dnm = np.concatenate([[0], -np.diff(l)])
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    atr = _wilder(_tr(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * _wilder(pdm, n) / atr
        ndi = 100 * _wilder(ndm, n) / atr
        dx = 100 * abs(pdi - ndi) / (pdi + ndi)
    ok = ~np.isnan(dx)
    a = np.full(len(c), np.nan)
    if ok.sum() >= n:
        first = np.argmax(ok)
        a[first:] = _wilder(np.nan_to_num(dx[first:]), n)
    return a, pdi - ndi


def _box_pos(c, hi, lo):
    rng = hi - lo
    return (c - (hi + lo) / 2) / rng if rng and rng > 0 else np.full(len(c), np.nan)


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
    # opening candles
    f["fc_pos"] = _box_pos(c, h[0], l[0]) if n else np.full(n, np.nan)
    f["fc_pos"][:1] = np.nan
    f["or30_pos"] = np.full(n, np.nan)
    if n > 6:
        f["or30_pos"][6:] = _box_pos(c[6:], h[:6].max(), l[:6].min())
    f["pm_pos"] = np.full(n, np.nan)
    if n > 61:                                           # bar 60 closes at 14:20
        f["pm_pos"][61:] = _box_pos(c[61:], h[60], l[60])
    # Supertrend (three settings) and how fresh the 10,3 trend is
    f["st_10_3"], f["st_7_3"], f["st_10_2"] = supertrend(h, l, c, 10, 3), supertrend(h, l, c, 7, 3), supertrend(h, l, c, 10, 2)
    fl = np.zeros(n)
    for i in range(1, n):
        fl[i] = 0 if f["st_10_3"][i] != f["st_10_3"][i - 1] else fl[i - 1] + 1
    f["st_flip"] = np.where(f["st_10_3"] == 0, np.nan, fl)
    # Bollinger bands
    s = pd.Series(c)
    for key, k in (("bb_pctb", 2.0), ("bb15_pctb", 1.5)):
        ma, sd = s.rolling(20).mean(), s.rolling(20).std(ddof=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            f[key] = ((s - (ma - k * sd)) / (2 * k * sd)).to_numpy()
        if key == "bb_pctb":
            f["bb_width"] = (4 * sd / ma * 100).to_numpy()
    # VWAP bands (indices have no volume: the day's average price and its spread)
    tw = np.cumsum(c) / (np.arange(n) + 1)
    dev = pd.Series(c - tw).expanding(min_periods=6).std(ddof=0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f["vwap_z"] = np.where(dev > 0, (c - tw) / dev, np.nan)
    macd = _ema(c, 12) - _ema(c, 26)
    f["macd_h"] = (macd - _ema(macd, 9)) / c * 100
    f["macd_h"][:26] = np.nan
    f["adx"], f["di_diff"] = adx(h, l, c, 14)
    f["rsi2"], f["rsi7"] = _rsi(c, 2), _rsi(c, 7)
    f["ema5_13"] = (_ema(c, 5) - _ema(c, 13)) / c * 100
    f["vs_sma20"] = (c / s.rolling(20).mean().to_numpy() - 1) * 100
    ph, pl, pcl = ctx.get("prev_high"), ctx.get("prev_low"), ctx.get("prev_close")
    if ph and pl and pcl:
        piv = (ph + pl + pcl) / 3
        bc = (ph + pl) / 2
        tc = 2 * piv - bc
        top, bot = max(tc, bc), min(tc, bc)
        f["cpr_width"] = np.full(n, (top - bot) / piv * 100)
        f["cpr_pos"] = np.where(c > top, (c - top) / piv * 100, np.where(c < bot, (c - bot) / piv * 100, 0.0))
    else:
        f["cpr_width"], f["cpr_pos"] = np.full(n, np.nan), np.full(n, np.nan)
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


SELL_FIRST_TOD = 5            # the straddle can be sold from 09:20 (the famous 9:20 straddle)
ALL_EXITS = list(EXITS) + list(SELL_EXITS) + list(STYLES)
BUY_EXITS = list(EXITS) + list(STYLES)                  # premium-% exits (current) + chart-level exits (challengers)
EXIT_WORDS.update({k: v["words"] for k, v in STYLES.items()})


def hold_of(ex: str) -> int:
    return EXITS[ex][2] if ex in EXITS else STYLES[ex]["max"] if ex in STYLES else 360


def _sim_lv(c, sp, m0, entry, sym, lot, d, style, atr, bar_lo, bar_hi):
    """Option bought at m0, out when a chart level (lab/exits.py) is touched; the option's price at that minute."""
    end = min(M_EXIT, m0 + STYLES[style]["max"])
    seg, sseg = c[m0 + 1:end + 1], sp[m0 + 1:end + 1]
    if not len(seg):
        return math.nan
    R = risk_pts(style, sp[m0], d, atr, bar_lo, bar_hi)
    if not math.isfinite(R):
        return math.nan
    k, _ = first_exit(style, sseg, seg, sp[m0], entry, d, R)
    k = len(seg) - 1 if k is None else k
    x = seg[k]
    if math.isnan(x):
        ok = np.where(~np.isnan(seg[:k + 1]))[0]
        if not len(ok):
            return math.nan
        x = seg[ok[-1]]
    ex = max(0.05, x - slip(sym, x))
    return (ex - entry) * lot - charges([(1, entry), (-1, ex)], lot)


def _sim_short(legs, m0, sym, lot, sl_pct):
    """Sell the ATM call and put at m0; each leg is bought back at its own stop (+sl %) or at 15:15."""
    pnl, fills = 0.0, []
    for px in legs:
        c = px["c"]
        e = c[m0]
        if math.isnan(e) or e <= 0.5:
            return math.nan
        entry = max(0.05, e - slip(sym, e))
        seg = c[m0 + 1:M_EXIT + 1]
        hit = np.nan_to_num(seg, nan=-1) >= entry * (1 + sl_pct / 100)
        if hit.any():
            x = seg[int(np.argmax(hit))]
        else:
            ok = np.where(~np.isnan(seg))[0]
            if not len(ok):
                return math.nan
            x = seg[ok[-1]]
        ex = x + slip(sym, x)
        pnl += (entry - ex) * lot
        fills += [(-1, entry), (1, ex)]
    return pnl - charges(fills, lot)


def _sim_fly(legs, wings, m0, sym, lot, loss_pct):
    """Iron fly at m0: short ATM call + put, long the wings. Out when the position has lost loss_pct of the credit, or 15:15."""
    sc, spp = legs[0]["c"], legs[1]["c"]
    wc, wp = wings[0]["c"], wings[1]["c"]
    e = [sc[m0], spp[m0], wc[m0], wp[m0]]
    if any(math.isnan(x) or x <= 0 for x in e[:2]) or any(math.isnan(x) for x in e[2:]):
        return math.nan
    sells = [max(0.05, x - slip(sym, x)) for x in e[:2]]
    buys = [x + slip(sym, x) for x in e[2:]]
    credit = sum(sells) - sum(buys)
    if credit <= 0:
        return math.nan
    seg = np.vstack([sc[m0 + 1:M_EXIT + 1], spp[m0 + 1:M_EXIT + 1], wc[m0 + 1:M_EXIT + 1], wp[m0 + 1:M_EXIT + 1]])
    value = seg[0] + seg[1] - seg[2] - seg[3]                  # cost to close now (mid)
    hit = np.nan_to_num(value, nan=-1e9) >= credit * (1 + loss_pct / 100)
    j = int(np.argmax(hit)) if hit.any() else None
    if j is None:
        ok = np.where(~np.isnan(value))[0]
        if not len(ok):
            return math.nan
        j = ok[-1]
    x = seg[:, j]
    if np.isnan(x).any():
        return math.nan
    close_sells = [v + slip(sym, v) for v in x[:2]]
    close_buys = [max(0.05, v - slip(sym, v)) for v in x[2:]]
    exit_cost = sum(close_sells) - sum(close_buys)
    fills = [(-1, sells[0]), (-1, sells[1]), (1, buys[0]), (1, buys[1]),
             (1, close_sells[0]), (1, close_sells[1]), (-1, close_buys[0]), (-1, close_buys[1])]
    return (credit - exit_cost) * lot - charges(fills, lot)


def build(data_dir, syms=None, log=print, should_stop=lambda: False) -> pd.DataFrame:
    """One row per (symbol, day, bar, direction): the bar's facts and the option-buy P&L per lot under each exit.
    Stocks get option-buying rows only (selling a stock straddle needs lakhs of margin)."""
    syms = syms or lab_syms(data_dir)
    vix = vix_table(data_dir)
    vmap = dict(zip(pd.to_datetime(vix["date"]).dt.date, vix["vix_prev"])) if len(vix) else {}
    rows = []
    for sym in syms:
        lot = UNDERLYINGS[sym]["lot"]
        stock = bool(UNDERLYINGS[sym].get("stock"))
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
            atr = atr14(h, l, c)
            for i in range(nb):
                tod = (i + 1) * 5
                if tod < SELL_FIRST_TOD or tod > LAST_ENTRY_TOD:
                    continue
                m0 = 5 * i + 5
                if m0 >= M_EXIT:
                    continue
                k = day.atm(m0)
                base = {"sym": sym, "date": day.date, "bar": i, **{name: float(f[name][i]) for name in FEATURES}}
                legs = [day.px.get((float(k), sd)) for sd in ("CE", "PE")]
                if not stock and all(lg is not None for lg in legs):        # selling the ATM straddle / iron fly (non-directional)
                    step = day.step
                    wings = [day.px.get((float(k + FLY_WINGS * step), "CE")), day.px.get((float(k - FLY_WINGS * step), "PE"))]
                    row = {**base, "dir": 0, "premium": round(sp[m0] * lot * 0.12, 0)}
                    for rule, sl in SELL_EXITS.items():
                        if rule.startswith("fly"):
                            row["pnl_" + rule] = _sim_fly(legs, wings, m0, sym, lot, sl) if all(w is not None for w in wings) else math.nan
                        else:
                            row["pnl_" + rule] = _sim_short(legs, m0, sym, lot, sl)
                    rows.append(row)
                if tod < FIRST_ENTRY_TOD:
                    continue
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
                    for style in STYLES:
                        row["pnl_" + style] = _sim_lv(px["c"], sp, m0, entry, sym, lot, d, style, atr[i], l[i], h[i])
                    rows.append(row)
            if (di + 1) % 100 == 0:
                log(f"  {sym}: {di + 1} of {len(days)} days ({time.time() - t0:.0f}s)")
        log(f"{sym}: {len(days)} days ready")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["date", "sym", "bar", "dir"]).reset_index(drop=True)
        for c_ in ["pnl_" + r for r in ALL_EXITS]:
            if c_ not in df:
                df[c_] = np.nan
            df[c_] = df[c_].astype("float32")
    return df


def cache_path(data_dir) -> Path:
    return Path(data_dir) / "lab" / "factory_bars.parquet"


def load_or_build(data_dir, log=print, should_stop=lambda: False, rebuild=False) -> pd.DataFrame:
    f = cache_path(data_dir)
    hist = Path(data_dir) / "history"
    newest = max((p.stat().st_mtime for p in hist.glob("*/raw_*.parquet")), default=0)
    if f.exists() and not rebuild and f.stat().st_mtime >= newest:
        df = pd.read_parquet(f)
        if all("pnl_" + s in df.columns for s in STYLES):
            return df
        log("New exit styles to test: rebuilding the bar table once…")
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
    side = ("Buy the ATM call" if s["dir"] > 0 else "Buy the ATM put" if s["dir"] < 0 else
            "Sell an iron fly (margin ~₹40-60K)" if s["exit"].startswith("fly") else "Sell the ATM straddle (needs ~₹1-2 lakh margin)")
    names = {"BANKNIFTY": "Bank Nifty", "NIFTY": "Nifty", STOCKS: "any F&O stock"}
    return (f"{side} on {' / '.join(names.get(x, x) for x in s['syms'])} "
            f"between {hm(s['win'][0])} and {hm(s['win'][1])} when " + " and ".join(cs) +
            f". Exit {EXIT_WORDS[s['exit']]}. Max {s['max_day']} trade{'s' if s['max_day'] > 1 else ''} a day, none after a loss.")


def validate(s: dict) -> dict | None:
    """Clean a strategy coming from outside (ebook / Claude); None if it can't be tested."""
    try:
        conds = [[str(f), str(o), float(v)] for f, o, v in s.get("conds", [])]
        if not conds or len(conds) > 4 or any(f not in FEATURES or o not in (">", "<") for f, o, _ in conds):
            return None
        d0 = int(s.get("dir", 1))
        d = 1 if d0 > 0 else -1 if d0 < 0 else 0
        w = [int(s.get("win", [15, 330])[0]), int(s.get("win", [15, 330])[1])]
        w = [max(FIRST_ENTRY_TOD, min(w)), min(LAST_ENTRY_TOD, max(w))]
        ex = s.get("exit")
        if d == 0:
            ex = ex if ex in SELL_EXITS else "ss25"
            w = [max(SELL_FIRST_TOD, min(int(x) for x in s.get("win", [5, 330]))), w[1]]
        else:
            ex = ex if ex in EXITS or ex in STYLES else "std"
        syms = [x for x in (s.get("syms") or ["NIFTY", "BANKNIFTY"]) if x in UNDERLYINGS or x == STOCKS] or ["NIFTY", "BANKNIFTY"]
        if d == 0:
            syms = [x for x in syms if x in ("NIFTY", "BANKNIFTY")] or ["NIFTY", "BANKNIFTY"]
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
        self.pnl = {r: df["pnl_" + r].to_numpy(dtype=float) for r in ALL_EXITS if "pnl_" + r in df}
        self.symcode = df["sym"].to_numpy()
        self.prem = df["premium"].to_numpy(dtype=float) if "premium" in df else np.zeros(len(df))
        names = sorted(set(self.symcode))
        self.n_sym = max(1, len(names))
        code = {n: i for i, n in enumerate(names)}
        self.symi = np.array([code[x] for x in self.symcode], dtype=np.int64)
        self.stock_syms = [n for n in names if n not in INDEX_SYMS]
        self.is_stock = np.isin(self.symcode, self.stock_syms)
        self.sym_choices = [["NIFTY"], ["BANKNIFTY"], ["BANKNIFTY", "NIFTY"]] + ([[STOCKS], [STOCKS], [STOCKS]] if self.stock_syms else [])
        self.n_days = {0: a, 1: b - a, 2: len(dates) - b}

    def quantiles(self, feat, part=0):
        x = self.cols[feat][self.part == part]
        x = x[~np.isnan(x)]
        return np.unique(np.round(np.quantile(x, [0.1, 0.25, 0.5, 0.75, 0.9]), 3)) if len(x) else np.array([])

    def trades(self, s: dict, part: int | None = None, max_premium: float | None = None) -> pd.DataFrame:
        m = (self.cols["dir"] == s["dir"]) & (self.cols["tod"] >= s["win"][0]) & (self.cols["tod"] <= s["win"][1])
        sm = np.isin(self.symcode, s["syms"])
        if STOCKS in s["syms"]:
            sm |= self.is_stock
        m &= sm
        if part is not None:
            m &= self.part == part
        if max_premium:                                     # only options whose lot the account can pay for
            m &= self.prem <= max_premium
        for feat, op, v in s["conds"]:
            x = self.cols[feat]
            m &= (x > v) if op == ">" else (x < v)
        idx = np.nonzero(m)[0]
        if s["exit"] not in self.pnl:
            return pd.DataFrame(columns=["day", "sym", "tod", "pnl", "part"])
        pnl = self.pnl[s["exit"]][idx]
        ok = ~np.isnan(pnl)
        idx, pnl = idx[ok], pnl[ok]
        if not len(idx):
            return pd.DataFrame(columns=["day", "sym", "tod", "pnl", "part"])
        grp = self.symi[idx]
        if STOCKS in s["syms"]:                             # all stocks share one slot: a ₹40K account holds one trade at a time
            grp = np.where(self.is_stock[idx], self.n_sym, grp)
        key = self.dix[idx] * (self.n_sym + 1) + grp
        tod = self.cols["tod"][idx]

        def firsts(ix):                                     # earliest signal of each key (ties: first row)
            o = ix[np.lexsort((tod[ix], key[ix]))]
            _, f = np.unique(key[o], return_index=True)
            return o[f]
        first = firsts(np.arange(len(idx)))
        take = list(first)
        if s["max_day"] > 1:                                # a second trade only after a winning first, once it is over
            gap = max(30, hold_of(s["exit"]))
            won = {key[j]: tod[j] + gap for j in first if pnl[j] > 0}
            if won:
                k_after = np.array([won.get(k, np.inf) for k in key])
                cand = np.nonzero(tod >= k_after)[0]
                if len(cand):
                    take += list(firsts(cand))
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


# ------------------------------------------------------------------ ₹40K: what a small account can actually trade
SMALL_CAPITAL = 40000
SMALL_LOT_MAX = 10000          # the same "fits ₹40K" line the live alerts use (option lot ≤ ₹10K)


def equity40(t: pd.DataFrame, capital=SMALL_CAPITAL) -> dict:
    """Day-by-day account from ₹40K: end, worst day, deepest fall, months to double, and whether it ever lost half."""
    if t is None or not len(t):
        return {"n": 0}
    daily = t.groupby("day")["pnl"].sum()
    eq = capital + daily.cumsum()
    dd = float((eq - eq.cummax()).min())
    avg = float(daily.mean())
    return {"n": int(len(t)), "days": int(len(daily)), "end": round(float(eq.iloc[-1])), "ret_pct": round(float(eq.iloc[-1] / capital - 1) * 100, 1),
            "avg_day": round(avg), "per_month": round(avg * 21), "worst_day": round(float(daily.min())),
            "max_dd": round(dd), "max_dd_pct": round(dd / capital * 100, 1), "green_pct": round(float((daily > 0).mean() * 100), 1),
            "lost_half": bool(eq.min() <= capital / 2)}


def plan40(book: Book, rules: list, top=8) -> dict:
    """Ways to start with ₹40K: option-buying rules re-tested only on trades whose lot costs ≤ ₹10K, chosen on the
    VALIDATION period and judged on the HOLDOUT (choosing on holdout would flatter them)."""
    out = []
    for r in rules:
        if r.get("dir") == 0 or r.get("stage") in ("failed train", "duplicate"):
            continue
        tr = book.trades(r, max_premium=SMALL_LOT_MAX)
        if not len(tr):
            continue
        v, h = tr[tr["part"] == 1], tr[tr["part"] == 2]
        sv = stats(v, book.n_days[1])
        if sv.get("n", 0) < 10 or (sv.get("avg") or 0) <= 0:
            continue
        out.append({"id": r["id"], "rule": r["rule"], "exit": r["exit"], "syms": r["syms"], "stocks": STOCKS in r["syms"],
                    "valid": sv, "hold": stats(h, book.n_days[2]), "acct": equity40(h)})
    out.sort(key=lambda x: (-(x["valid"].get("t") or 0), len(x["rule"])))
    seen, uniq = set(), []
    for x in out:                                         # the same trades under different wording count once
        sig = (x["valid"].get("n"), x["valid"].get("total"), x["hold"].get("n"), x["hold"].get("total"))
        if sig not in seen:
            seen.add(sig)
            uniq.append(x)
    out = uniq
    ok = [x for x in out if x["hold"].get("n", 0) >= 10 and (x["hold"].get("avg") or 0) > 0]
    return {"capital": SMALL_CAPITAL, "lot_max": SMALL_LOT_MAX, "candidates": len(out), "held_up": len(ok),
            "best": out[:top], "verdict": (f"{len(ok)} of the {len(out)} rules that made money on validation also made money on "
                                           f"the holdout with lots ≤ ₹{SMALL_LOT_MAX:,}." if out else
                                           "No option-buying rule that fits ₹40K made money on validation yet.")}


def exit_board(book: Book, rules: list, top=40) -> dict:
    """Which way of exiting works best: (1) every possible entry (random entries) per exit, (2) the best rules re-run with
    each exit, the exit picked on VALIDATION and checked on HOLDOUT. Chart-level styles are challengers to the % exits."""
    ex = [e for e in BUY_EXITS if e in book.pnl]
    buy = book.cols["dir"] != 0
    base = {}
    for e in ex:
        x = book.pnl[e][buy & (book.part == 2)]
        x = x[~np.isnan(x)]
        base[e] = {"avg": round(float(x.mean()), 1) if len(x) else None, "win_pct": round(float((x > 0).mean() * 100), 1) if len(x) else None}
    cand = sorted((r for r in rules if r.get("dir") != 0 and r.get("stage") != "duplicate"),
                  key=lambda r: -(r.get("valid", {}).get("t") or -9))[:top]
    tally = {e: {"valid_sum": 0.0, "hold_sum": 0.0, "n": 0, "best_on_valid": 0} for e in ex}
    for r in cand:
        res = {}
        for e in ex:
            tr = book.trades({**r, "exit": e})
            v, h = tr[tr["part"] == 1]["pnl"], tr[tr["part"] == 2]["pnl"]
            res[e] = (float(v.mean()) if len(v) else np.nan, float(h.mean()) if len(h) else np.nan)
        good = {e: x for e, x in res.items() if np.isfinite(x[0])}
        if not good:
            continue
        best = max(good, key=lambda e: good[e][0])
        tally[best]["best_on_valid"] += 1
        for e, (va, ho) in good.items():
            if np.isfinite(ho):
                tally[e]["valid_sum"] += va
                tally[e]["hold_sum"] += ho
                tally[e]["n"] += 1
    board = [{"exit": e, "words": EXIT_WORDS.get(e, e), "kind": "chart level" if e in STYLES else "premium %",
              "who": STYLES[e]["who"] if e in STYLES else "current", "random_hold": base[e],
              "rules_valid_avg": round(t["valid_sum"] / t["n"], 1) if t["n"] else None,
              "rules_hold_avg": round(t["hold_sum"] / t["n"], 1) if t["n"] else None, "best_on_valid": t["best_on_valid"]}
             for e, t in tally.items()]
    board.sort(key=lambda b: -(b["rules_valid_avg"] if b["rules_valid_avg"] is not None else -1e9))
    styles = [b for b in board if b["exit"] in STYLES and b["rules_valid_avg"] is not None]
    champ = [b for b in board if b["exit"] in EXITS and b["rules_valid_avg"] is not None]
    # the style shown on alerts: best worst-case over the two periods it wasn't fitted on (robust, not the luckiest)
    best_style = max(styles, key=lambda b: min(b["rules_valid_avg"], b["rules_hold_avg"] if b["rules_hold_avg"] is not None else -1e9))["exit"] \
        if styles else "atr2r"
    beats = bool(styles and champ and styles[0]["rules_valid_avg"] > champ[0]["rules_valid_avg"]
                 and (styles[0]["rules_hold_avg"] or -1e9) > (champ[0]["rules_hold_avg"] or -1e9))
    return {"board": board, "rules_compared": len(cand), "best_style": best_style, "style_beats_current": beats}


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
    d = rng.choice([1, -1, 1, -1, 0]) if "ss25" in book.pnl else rng.choice([1, -1])
    win = list(rng.choice(WINDOWS + ([(5, 20), (5, 75), (75, 120)] if d == 0 else [])))
    s = {"dir": d, "conds": conds, "win": win, "exit": rng.choice(list(SELL_EXITS) if d == 0 else [e for e in BUY_EXITS if e in book.pnl]),
         "max_day": rng.choice([1, 1, 2]), "syms": rng.choice(book.sym_choices), "source": "generated"}
    if d == 0:
        s["syms"] = rng.choice(book.sym_choices[:3])
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
        m["exit"] = rng.choice(list(SELL_EXITS) if m["dir"] == 0 else [e for e in BUY_EXITS if e in book.pnl])
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
        m["syms"] = rng.choice(book.sym_choices[:3] if m["dir"] == 0 else book.sym_choices)
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
    try:
        st["exit_board"] = exit_board(book, list(keep.values()))
        st["plan40"] = plan40(book, list(keep.values()))
        log(f"Exits: best chart-level style {st['exit_board']['best_style']} "
            f"({'beats' if st['exit_board']['style_beats_current'] else 'does not beat'} the current % exits on validation + holdout). "
            f"₹40K: {st['plan40']['verdict']}")
    except Exception as e:
        log(f"Exit board / ₹40K report failed: {e}")
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
    order = {"promoted": 0, "failed holdout": 1, "failed validation": 2, "failed train": 3, "duplicate": 4}
    lib = {}
    for r in st.get("rules", {}).values():
        if not str(r.get("source", "")).startswith("library:"):
            continue
        name = r["source"][8:]
        cur = lib.get(name)
        key = (order.get(r["stage"], 9), -(r["hold"].get("avg") or -1e9) if r["stage"] == "promoted" else -(r["train"].get("t") or -9))
        if cur is None or key < cur["_k"]:
            lib[name] = {"_k": key, "best": r, "n_rules": 0}
        lib[name]["n_rules"] = lib[name].get("n_rules", 0) + 1
    library = sorted(({"name": k, "n_rules": v["n_rules"], "best": v["best"]} for k, v in lib.items()),
                     key=lambda x: (order.get(x["best"]["stage"], 9), -(x["best"]["hold"].get("avg") or -1e9)))
    for r in rules:
        r["is_live"] = is_live(r)
    return {"updated": st.get("updated"), "tried_total": st.get("tried_total", 0), "library": library, "luck_expected": round(st.get("tried_total", 0) * P_LUCK, 1),
            "period": st.get("period"), "n_days": st.get("n_days"), "runs": st.get("runs", [])[-10:], "by_stage": by,
            "rules": rules[:120], "features": {k: v[0] for k, v in FEATURES.items()}, "exits": EXIT_WORDS,
            "pending_ideas": len(st.get("ideas", [])), "exit_board": st.get("exit_board"), "plan40": st.get("plan40")}


def add_ideas(data_dir, ideas: list, source: str) -> dict:
    """Queue strategies from an ebook or Claude; they are tested on the next run (train/validation/holdout as usual)."""
    st = load(data_dir)
    ok, bad = [], 0
    for x in ideas:
        v = validate({**x, "source": x.get("source") or source})
        if v:
            ok.append(v)
        else:
            bad += 1
    st["ideas"] = (st.get("ideas", []) + ok)[-500:]
    save(data_dir, st)
    return {"queued": len(ok), "rejected": bad}


def is_live(r: dict) -> bool:
    """Promoted rules trade on paper when their holdout evidence is moderate or strong, or when you switched them on."""
    if r.get("stage") != "promoted" or r.get("live") == "off":
        return False
    return r.get("live") == "on" or r.get("evidence") in ("strong", "moderate")


def promoted(data_dir) -> list:
    return [r for r in load(data_dir).get("rules", {}).values() if is_live(r)]


def signal_now(rule: dict, feats: dict, sym: str, i: int | None = None) -> bool:
    """Does a promoted rule fire on the latest closed bar of `sym`? feats: output of bar_features for today."""
    if not sym_ok(rule["syms"], sym):
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
