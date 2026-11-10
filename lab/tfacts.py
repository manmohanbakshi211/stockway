"""2.47 Bigger-timeframe and multi-day facts (gun): the same code in the Lab's bar table and in the live app.

Why: book strategies read a 30-minute Bollinger band, a 1-hour RSI or Ichimoku, a 200-bar average on 5-minute
candles, yesterday's pivot points, a Fibonacci retracement of the last swing... The day-only facts of bar_features
can't see those (their indicators start fresh every morning).

How:
  * 5-minute bars of many days (each day aligned to 09:15) are joined into one series and resampled into 15m / 30m /
    1H candles (aligned to 09:15; a day's last candle may be shorter, as on every Indian chart).
  * Indicators are bullets: functions in IND (name -> outputs); which outputs run on which timeframe is data in
    rules/tf_facts.json. Fact name = "<output>_<tf>", e.g. "wr_pull_30m", "tk_x_1H", "sma200_up_5m".
  * A 5-minute bar only ever sees the last CLOSED candle of a bigger timeframe (no look-ahead). Two helpers per
    timeframe: new_<tf> = 1 on the 5-minute bar that closes a candle; ph_brk_<tf> = +1 when the 5-minute close is above
    the high of the last candle that closed before this bar began (-1 below its low): "buy above the candle's high".
  * Day facts on 5-minute bars: classic pivot points from yesterday's high / low / close (P, R1, R2, S1, S2).
  * The Lab computes on the whole history once; live computes on the last `history_days` days plus today. Every
    indicator here settles well inside that window (tests/test_tfacts.py checks both give the same facts).
  * Each indicator is isolated: one that fails leaves only its own facts NaN; nothing else stops.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

RULES = Path(__file__).resolve().parent.parent / "rules" / "tf_facts.json"
TF_BARS = {"5m": 1, "15m": 3, "30m": 6, "1H": 12, "4H": 48}        # 4H: 09:15-13:15 and 13:15-15:30


# ------------------------------------------------------------------ small tools (numpy, causal)
def _ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def _sma(x, n):
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def _wilder(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def _atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return _wilder(tr, n)


def _rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = _wilder(np.clip(d, 0, None), n), _wilder(np.clip(-d, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + up / dn)
    return np.where(dn == 0, np.where(up > 0, 100.0, 50.0), r)


def _rmax(x, n):
    return pd.Series(x).rolling(n, min_periods=n).max().to_numpy()


def _rmin(x, n):
    return pd.Series(x).rolling(n, min_periods=n).min().to_numpy()


def _shift(x, k=1):
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def _cross(a, b):
    """+1 where a crossed above b on this candle, -1 below, else 0 (NaN-safe)."""
    a0, b0 = _shift(a), _shift(b)
    up = (a > b) & (a0 <= b0)
    dn = (a < b) & (a0 >= b0)
    return np.where(up, 1.0, np.where(dn, -1.0, 0.0))


def _age(x):
    """Signed candles since the last non-zero event in x (+ after an up event, - after a down one); NaN before any."""
    out = np.full(len(x), np.nan)
    last, sign = None, 0
    for i, v in enumerate(x):
        if v and not math.isnan(v):
            last, sign = i, (1 if v > 0 else -1)
        if last is not None:
            out[i] = sign * (i - last + 1)
    return out


def _swings(h, l, n):
    """Last confirmed swing high / low (value and index) per candle. A swing high at j is known at j + n."""
    m = len(h)
    sh, sl = np.full(m, np.nan), np.full(m, np.nan)
    shi, sli = np.full(m, -1), np.full(m, -1)
    ch = cl = np.nan
    chi = cli = -1
    for t in range(m):
        j = t - n
        if j >= n:
            w_h, w_l = h[j - n:j + n + 1], l[j - n:j + n + 1]
            if h[j] == np.max(w_h) and not np.isnan(h[j]):
                ch, chi = h[j], j
            if l[j] == np.min(w_l) and not np.isnan(l[j]):
                cl, cli = l[j], j
        sh[t], sl[t], shi[t], sli[t] = ch, cl, chi, cli
    return sh, sl, shi, sli


# ------------------------------------------------------------------ indicators (bullets): fn(o, h, l, c) -> {output: array}
def ind_ema9(o, h, l, c):
    e = _ema(c, 9)
    return {"ema9": (c / e - 1) * 100, "ema9_up": np.sign(e - _shift(e, 3))}


def ind_sma(o, h, l, c):
    out = {}
    for n in (14, 50, 200):
        s = _sma(c, n)
        out[f"sma{n}"] = (c / s - 1) * 100
        out[f"sma{n}_up"] = np.sign(s - _shift(s, 10))
    return out


def ind_bb(o, h, l, c):
    s = pd.Series(c)
    ma, sd = s.rolling(20, min_periods=20).mean().to_numpy(), s.rolling(20, min_periods=20).std(ddof=0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        pctb = (c - (ma - 2 * sd)) / (4 * sd)
        w = 4 * sd / ma * 100
        wup = w / _shift(w, 3)
    lowq = pd.Series(w).rolling(100, min_periods=40).quantile(0.2).to_numpy()
    return {"bbp": pctb, "bbw": w, "bbw_up": wup, "bb_sq": np.where(np.isnan(lowq), np.nan, (w <= lowq).astype(float))}


def ind_wr(o, h, l, c):
    hh, ll = _rmax(h, 14), _rmin(l, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        wr = np.where(hh > ll, (hh - c) / (hh - ll) * -100, -50.0)
    wr = np.where(np.isnan(hh), np.nan, wr)
    # pullback to -50 after the overbought zone, turning back up (+1); mirror after oversold (-1)
    was_ob = pd.Series(np.where(np.isnan(wr), -100, wr)).shift(2).rolling(10, min_periods=1).max().to_numpy() > -20
    was_os = pd.Series(np.where(np.isnan(wr), 0, wr)).shift(2).rolling(10, min_periods=1).min().to_numpy() < -80
    dip = pd.Series(np.where(np.isnan(wr), 0, wr)).rolling(3, min_periods=1).min().to_numpy() <= -45
    pop = pd.Series(np.where(np.isnan(wr), -100, wr)).rolling(3, min_periods=1).max().to_numpy() >= -55
    w1 = _shift(wr)
    pull = np.where(was_ob & dip & (wr > w1) & (wr > -80), 1.0, np.where(was_os & pop & (wr < w1) & (wr < -20), -1.0, 0.0))
    return {"wr": wr, "wr_pull": np.where(np.isnan(wr), np.nan, pull)}


def ind_macd(o, h, l, c):
    m = _ema(c, 12) - _ema(c, 26)
    sg = _ema(m, 9)
    x = _cross(m, sg)
    x[:34] = 0
    hs = np.sign(m - sg)
    hs[:34] = np.nan
    return {"macd_x": x, "macd_age": _age(x), "macd_hs": hs}


def ind_ichi(o, h, l, c):
    ten = (_rmax(h, 9) + _rmin(l, 9)) / 2
    kij = (_rmax(h, 26) + _rmin(l, 26)) / 2
    sa, sb = _shift((ten + kij) / 2, 26), _shift((_rmax(h, 52) + _rmin(l, 52)) / 2, 26)   # the cloud under today's candle
    top, bot = np.fmax(sa, sb), np.fmin(sa, sb)
    cloud = np.where(np.isnan(top) | np.isnan(bot), np.nan, np.where(c > top, 1.0, np.where(c < bot, -1.0, 0.0)))
    tk = _cross(ten, kij)
    cx = np.where((cloud == 1) & (_shift(cloud) < 1), 1.0, np.where((cloud == -1) & (_shift(cloud) > -1), -1.0, 0.0))
    with np.errstate(invalid="ignore"):
        span_a = (c - sa) / c * 100
    return {"tk_x": tk, "tk_age": _age(tk), "cloud": cloud, "cloud_x": np.where(np.isnan(cloud), np.nan, cx), "span_a": span_a}


def ind_psar(o, h, l, c, step=0.02, mx=0.2):
    n = len(c)
    d = np.full(n, np.nan)
    age = np.full(n, np.nan)
    if n < 3:
        return {"psar": d, "psar_age": age}
    up = c[1] >= c[0]
    sar = l[0] if up else h[0]
    ep = h[1] if up else l[1]
    af, k = step, 0
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, af, k = False, ep, l[i], step, 0
            elif h[i] > ep:
                ep, af = h[i], min(mx, af + step)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af, k = True, ep, h[i], step, 0
            elif l[i] < ep:
                ep, af = l[i], min(mx, af + step)
        k += 1
        d[i], age[i] = (1.0 if up else -1.0), k
    return {"psar": d, "psar_age": age}


def ind_ha(o, h, l, c):
    hc = (o + h + l + c) / 4
    ho = np.empty(len(c))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hh, hl = np.maximum(h, np.maximum(ho, hc)), np.minimum(l, np.minimum(ho, hc))
    col = np.sign(hc - ho)
    tol = 1e-9 + 0.02 * (hh - hl)
    nw = np.where((col > 0) & (np.minimum(ho, hc) - hl <= tol), 1.0, np.where((col < 0) & (hh - np.maximum(ho, hc) <= tol), -1.0, 0.0))
    return {"ha": col, "ha_nw": nw}


def ind_don(o, h, l, c, n=20):
    up, dn = _shift(_rmax(h, n)), _shift(_rmin(l, n))                     # the channel of the n candles before this one
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = (c - dn) / (up - dn)
    brk = np.where(c > up, 1.0, np.where(c < dn, -1.0, 0.0))
    # touch of a band, a pullback towards the middle, then a break of the band again (the book's Donchian pullback)
    m = len(c)
    pb = np.zeros(m)
    for t in range(m):
        if brk[t] == 0 or np.isnan(up[t]):
            continue
        lo, hi = max(0, t - 15), t - 1
        if hi - lo < 3:
            continue
        w = up[t] - dn[t]
        if not w > 0:
            continue
        if brk[t] > 0:
            touch = [j for j in range(lo, hi) if h[j] >= up[j] - 0.05 * w]
            if touch and touch[0] + 1 < t and np.nanmin(l[touch[0] + 1:t]) <= up[t] - 0.3 * w:
                pb[t] = 1.0
        else:
            touch = [j for j in range(lo, hi) if l[j] <= dn[j] + 0.05 * w]
            if touch and touch[0] + 1 < t and np.nanmax(h[touch[0] + 1:t]) >= dn[t] + 0.3 * w:
                pb[t] = -1.0
    bad = np.isnan(up)
    return {"don_pos": pos, "don_brk": np.where(bad, np.nan, brk), "don_pb": np.where(bad, np.nan, pb)}


def ind_rsi(o, h, l, c):
    r = _rsi(c, 14)
    r[:14] = np.nan
    x30 = np.where((r > 30) & (_shift(r) <= 30), 1.0, 0.0)
    x70 = np.where((r < 70) & (_shift(r) >= 70), -1.0, 0.0)
    # divergence: a new swing low in price (vs the previous swing low within 30 candles) with a higher RSI low (+1)
    sh, sl, shi, sli = _swings(h, l, 2)
    div = np.zeros(len(c))
    prev_l = prev_h = None
    for t in range(len(c)):
        j = int(sli[t])
        if j >= 0 and (prev_l is None or j != prev_l[0]):
            if prev_l is not None and j - prev_l[0] <= 30 and l[j] < l[prev_l[0]] and r[j] > r[prev_l[0]]:
                div[t] = 1.0
            prev_l = (j,)
        j = int(shi[t])
        if j >= 0 and (prev_h is None or j != prev_h[0]):
            if prev_h is not None and j - prev_h[0] <= 30 and h[j] > h[prev_h[0]] and r[j] < r[prev_h[0]]:
                div[t] = -1.0
            prev_h = (j,)
    return {"rsi": r, "rsi_x30": x30, "rsi_x70": x70, "rsi_div": div}


def ind_srsi(o, h, l, c):
    r = _rsi(c, 14)
    r[:14] = np.nan
    lo, hi = _rmin(r, 14), _rmax(r, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(hi > lo, (r - lo) / (hi - lo) * 100, 50.0)
    k = np.where(np.isnan(lo), np.nan, k)
    return {"srsi": pd.Series(k).rolling(3, min_periods=3).mean().to_numpy()}


def ind_frac(o, h, l, c, n=4):
    """Williams fractals 4/4: a fractal high at j is known when the 4 candles after j have closed."""
    m = len(c)
    fh, fl = np.full(m, np.nan), np.full(m, np.nan)
    cur_h = cur_l = np.nan
    for t in range(m):
        j = t - n
        if j >= n:
            if h[j] == np.max(h[j - n:j + n + 1]):
                cur_h = h[j]
            if l[j] == np.min(l[j - n:j + n + 1]):
                cur_l = l[j]
        fh[t], fl[t] = cur_h, cur_l
    ph, pl = _shift(fh), _shift(fl)
    brk = np.where((c > ph) & (_shift(c) <= ph), 1.0, np.where((c < pl) & (_shift(c) >= pl), -1.0, 0.0))
    return {"frac_brk": brk}


def ind_fib(o, h, l, c):
    """Retracement of the last swing (swings of 3 candles each side): in an up leg (low then high) how far price has
    come back from the high, 0 = at the high, 0.5 = halfway, 1 = back at the low (fib_up); fib_dn the mirror."""
    sh, sl, shi, sli = _swings(h, l, 3)
    rng = sh - sl
    up_leg = (sli < shi) & (sli >= 0) & (rng > 0)
    dn_leg = (shi < sli) & (shi >= 0) & (rng > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        fu = np.where(up_leg, (sh - c) / rng, np.nan)
        fd = np.where(dn_leg, (c - sl) / rng, np.nan)
    return {"fib_up": fu, "fib_dn": fd}


def ind_st(o, h, l, c):
    from .factory import supertrend
    d = supertrend(h, l, c, 7, 3.0)
    d = np.where(d == 0, np.nan, d)
    x = np.where((d > 0) & (_shift(d) < 0), 1.0, np.where((d < 0) & (_shift(d) > 0), -1.0, 0.0))
    return {"st73": d, "st73_x": x}


def ind_ema921(o, h, l, c):
    a, b = _ema(c, 9), _ema(c, 21)
    x = _cross(a, b)
    x[:21] = 0
    return {"e921_x": x, "e921_age": _age(x)}


def ind_emad(o, h, l, c):
    atr = _atr(h, l, c)
    e5, e14 = _ema(c, 5), _ema(c, 14)
    side = np.where(l > e5, 1.0, np.where(h < e5, -1.0, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        return {"ema5_side": side, "ema5_atr": (c - e5) / atr, "ema14_atr": (c - e14) / atr}


def ind_renko(o, h, l, c, box_pct=0.15):
    """Renko bricks on closes, box = box_pct % of price rounded to a step that only changes with the price's size
    (a fixed grid, so the bricks are the same from any starting day once a reversal has happened)."""
    m = len(c)
    d = np.full(m, np.nan)
    turn = np.zeros(m)
    base = None
    dirn = 0
    for t in range(m):
        x = c[t]
        if math.isnan(x):
            continue
        raw = x * box_pct / 100
        mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
        box = round(raw / mag) * mag or mag
        if base is None:
            base = math.floor(x / box) * box
            continue
        if x >= base + box * (2 if dirn < 0 else 1):
            n = int((x - base) // box)
            base += n * box
            if dirn < 0:
                turn[t] = 1.0
            dirn = 1
        elif x <= base - box * (2 if dirn > 0 else 1):
            n = int((base - x) // box)
            base -= n * box
            if dirn > 0:
                turn[t] = -1.0
            dirn = -1
        d[t] = dirn if dirn else np.nan
    return {"rk_dir": d, "rk_turn": turn}


def ind_cons(o, h, l, c, k=5):
    """Consolidation breakout: the close leaves the range of the k candles before it, while that range was tight
    (at most 1.5 ATR)."""
    atr = _atr(h, l, c)
    hi, lo = _shift(_rmax(h, k)), _shift(_rmin(l, k))
    tight = (hi - lo) <= 1.5 * _shift(atr)
    out = np.where(tight & (c > hi), 1.0, np.where(tight & (c < lo), -1.0, 0.0))
    return {"cons_brk": np.where(np.isnan(hi) | np.isnan(atr), np.nan, out)}


def ind_retest(o, h, l, c, k=12):
    """Breakout then retest: within the last k candles the close broke above the high of the k candles before it (level
    L); now the candle dipped back to within 0.2 ATR of L (or below) and closed above it (+1). Mirror -1."""
    atr = _atr(h, l, c)
    hi, lo = _shift(_rmax(h, k)), _shift(_rmin(l, k))
    m = len(c)
    out = np.zeros(m)
    up_lv = dn_lv = None
    up_t = dn_t = -999
    for t in range(m):
        if np.isnan(hi[t]) or np.isnan(atr[t]):
            continue
        if c[t] > hi[t]:
            up_lv, up_t = hi[t], t
        elif c[t] < lo[t]:
            dn_lv, dn_t = lo[t], t
        if up_lv is not None and 2 <= t - up_t <= k and l[t] <= up_lv + 0.2 * atr[t] and c[t] > up_lv:
            out[t] = 1.0
        elif dn_lv is not None and 2 <= t - dn_t <= k and h[t] >= dn_lv - 0.2 * atr[t] and c[t] < dn_lv:
            out[t] = -1.0
    return {"retest": np.where(np.isnan(atr), np.nan, out)}


def ind_legs(o, h, l, c):
    """Two-legged pullback in a trend (swings of 2 candles): uptrend (the last swing high above the one before), then a
    first low, a lower high, a second lower low; now a green candle closes above the previous candle's high (+1).
    Mirror for downtrends (-1)."""
    sh, sl, shi, sli = _swings(h, l, 2)
    m = len(c)
    out = np.zeros(m)
    hs, ls = [], []                                      # confirmed swings in order: (index, value)
    for t in range(m):
        if shi[t] >= 0 and (not hs or hs[-1][0] != shi[t]):
            hs.append((int(shi[t]), sh[t]))
        if sli[t] >= 0 and (not ls or ls[-1][0] != sli[t]):
            ls.append((int(sli[t]), sl[t]))
        if t < 1 or len(hs) < 2 or len(ls) < 2:
            continue
        (h0i, h0), (h1i, h1) = hs[-2], hs[-1]
        (l0i, l0), (l1i, l1) = ls[-2], ls[-1]
        green, red = c[t] > o[t] and c[t] > h[t - 1], c[t] < o[t] and c[t] < l[t - 1]
        # up: H0 (top) -> L0 (leg 1) -> H1 (< H0) -> L1 (< L0, leg 2) and nothing since
        if green and h0i < l0i < h1i < l1i and h1 < h0 and l1 < l0 and t - l1i <= 6:
            prior = [v for i, v in hs[:-2]][-1:] or [None]
            if prior[0] is None or h0 > prior[0]:
                out[t] = 1.0
        if red and l0i < h0i < l1i < h1i and l1 > l0 and h1 > h0 and t - h1i <= 6:
            prior = [v for i, v in ls[:-2]][-1:] or [None]
            if prior[0] is None or l0 < prior[0]:
                out[t] = -1.0
    return {"leg2": out}


def ind_candle(o, h, l, c):
    from live import candles as _C
    p = _C.all_patterns(o, h, l, c)
    return {"green": np.sign(c - o), "pin": np.asarray(p.get("cdl_pin", np.zeros(len(c))), float),
            "hammer": np.asarray(p.get("cdl_hammer", np.zeros(len(c))), float),
            "engulf": np.asarray(p.get("cdl_engulf", np.zeros(len(c))), float)}


IND = {"ema9": ind_ema9, "sma": ind_sma, "bb": ind_bb, "wr": ind_wr, "macd": ind_macd, "ichi": ind_ichi, "psar": ind_psar,
       "ha": ind_ha, "don": ind_don, "rsi": ind_rsi, "srsi": ind_srsi, "frac": ind_frac, "fib": ind_fib, "st": ind_st,
       "ema921": ind_ema921, "emad": ind_emad, "renko": ind_renko, "cons": ind_cons, "retest": ind_retest,
       "legs": ind_legs, "candle": ind_candle}

WORDS = {
    "ema9": ("close vs EMA 9", "%"), "ema9_up": ("EMA 9 rising (+1) / falling (-1) over 3 candles", ""),
    "sma14": ("close vs SMA 14", "%"), "sma50": ("close vs SMA 50", "%"), "sma200": ("close vs SMA 200", "%"),
    "sma14_up": ("SMA 14 rising (+1) / falling (-1)", ""), "sma50_up": ("SMA 50 rising (+1) / falling (-1)", ""),
    "sma200_up": ("SMA 200 rising (+1) / falling (-1) over 10 candles", ""),
    "bbp": ("Bollinger 20,2 %B (0 lower band, 1 upper band)", ""), "bbw": ("Bollinger 20,2 width", "%"),
    "bbw_up": ("Bollinger width now vs 3 candles ago", "×"), "bb_sq": ("Bollinger squeeze: width in its lowest 20% of 100 candles (1)", ""),
    "wr": ("Williams %R 14 (0 top, -100 bottom)", ""),
    "wr_pull": ("Williams %R: after overbought, pulled back to -50 and turning up (+1); mirror after oversold (-1)", ""),
    "macd_x": ("MACD 12,26,9 line crossed its signal on this candle (+1 up, -1 down)", ""),
    "macd_age": ("candles since the last MACD cross (+ after up, - after down)", ""), "macd_hs": ("MACD above (+1) / below (-1) its signal", ""),
    "tk_x": ("Ichimoku conversion line crossed the base line (+1 up, -1 down)", ""),
    "tk_age": ("candles since the last Ichimoku conversion / base cross (+ up, - down)", ""),
    "cloud": ("close above the Ichimoku cloud (+1), below (-1), inside (0)", ""),
    "cloud_x": ("close moved above (+1) / below (-1) the Ichimoku cloud on this candle", ""),
    "span_a": ("close vs Ichimoku leading span A (the cloud edge)", "%"),
    "psar": ("Parabolic SAR below price (+1) / above (-1)", ""), "psar_age": ("candles since the Parabolic SAR flipped", ""),
    "ha": ("Heikin Ashi candle green (+1) / red (-1)", ""), "ha_nw": ("strong Heikin Ashi: green with no lower wick (+1) / red with no upper wick (-1)", ""),
    "don_pos": ("place in the Donchian 20 channel (0 low, 1 high)", ""), "don_brk": ("close beyond the Donchian 20 channel (+1 up, -1 down)", ""),
    "don_pb": ("Donchian: touched the band, pulled back, broke it again (+1 up, -1 down)", ""),
    "rsi": ("RSI 14 (carried over days)", ""), "rsi_x30": ("RSI crossed up through 30 (+1)", ""), "rsi_x70": ("RSI crossed down through 70 (-1)", ""),
    "rsi_div": ("RSI divergence at the latest swing: bullish (+1) / bearish (-1)", ""),
    "srsi": ("Stochastic RSI 14,14,3 (0-100)", ""),
    "frac_brk": ("close broke the last Williams fractal (4/4) high (+1) / low (-1)", ""),
    "fib_up": ("Fibonacci retracement of the last up swing (0 at its high, 0.5 halfway, 1 back at its low)", ""),
    "fib_dn": ("Fibonacci retracement of the last down swing (0 at its low, 1 back at its high)", ""),
    "st73": ("Supertrend 7,3 direction (carried over days)", ""), "st73_x": ("Supertrend 7,3 flipped on this candle (+1 up, -1 down)", ""),
    "e921_x": ("EMA 9 crossed EMA 21 on this candle (+1 up, -1 down)", ""), "e921_age": ("candles since the last EMA 9/21 cross (+ up, - down)", ""),
    "ema5_side": ("whole candle above EMA 5 (+1) / below (-1), touching it (0)", ""),
    "ema5_atr": ("close vs EMA 5, in ATRs", ""), "ema14_atr": ("close vs EMA 14, in ATRs", ""),
    "rk_dir": ("Renko brick direction (box 0.15% of price)", ""), "rk_turn": ("first Renko brick against the run (+1 green, -1 red)", ""),
    "cons_brk": ("breakout of a tight 5-candle range (+1 up, -1 down)", ""),
    "retest": ("breakout then retest held: back at the broken level and closed beyond it (+1 up, -1 down)", ""),
    "leg2": ("two-legged pullback in a trend, then a confirming candle (+1 buy, -1 sell)", ""),
    "green": ("candle green (+1) / red (-1)", ""), "pin": ("pin bar (+1 long lower tail, -1 long upper tail)", ""),
    "hammer": ("hammer after a fall (+1) / shooting star after a rise (-1)", ""), "engulf": ("engulfing candle (+1 / -1)", ""),
    "new": ("this 5-min bar closes a candle of the timeframe (1)", ""),
    "ph_brk": ("5-min close above the high (+1) / below the low (-1) of the last closed candle", ""),
    "piv_pos": ("price vs yesterday's pivots: 0 = P, +1 = R1, +2 = R2, -1 = S1, -2 = S2", ""),
    "piv_x": ("5-min close crossed a pivot level (P, R1, R2, S1, S2) up (+1) / down (-1)", ""),
    "piv_rt": ("broke a pivot level earlier today, came back to it and held (+1 above, -1 below)", ""),
    "pd_back": ("after a sweep of yesterday's low, closes back inside for N bars (+N); after a sweep of yesterday's high (-N)", ""),
    "pd_rt": ("yesterday's low swept, closed back inside, retested and a bullish rejection candle (+1); mirror at the high (-1)", ""),
    "eq_rt": ("equal lows swept, closed back above, retested and a bullish rejection candle (+1); mirror at equal highs (-1)", ""),
    "xrt": ("a level of the bigger chart broke on the smaller chart, then a retest held: +1 resistance broken up, -1 support broken down", ""),
}
DAY_FACTS = {"pivot": ("piv_pos", "piv_x", "piv_rt"), "sweep": ("pd_back", "pd_rt", "eq_rt")}


def config() -> dict:
    try:
        return json.loads(RULES.read_text())
    except Exception:
        return {"history_days": 12, "tfs": {}}


def fact_names(cfg: dict | None = None) -> dict:
    """{fact name: (words, unit)} of every fact the config asks for (for FEATURES)."""
    cfg = cfg or config()
    out = {}
    for tf, inds in (cfg.get("tfs") or {}).items():
        if tf not in TF_BARS:
            continue
        lab = {"5m": "5-min", "15m": "15-min", "30m": "30-min", "1H": "1-hour", "4H": "4-hour"}[tf]
        for ind, outs in inds.items():
            for o_ in outs:
                w, u = WORDS.get(o_, (o_, ""))
                out[f"{o_}_{tf}"] = (f"{w} ({lab} candles)", u)
        if tf != "5m":
            out[f"new_{tf}"] = (WORDS["new"][0].replace("the timeframe", lab), "")
        out[f"ph_brk_{tf}"] = (WORDS["ph_brk"][0] + f" ({lab})", "")
    for k in (cfg.get("day") or []):
        if k in WORDS:
            out[k] = WORDS[k]
    for x in (cfg.get("xretest") or []):
        lt, et = x.get("level_tf"), x.get("exec_tf")
        if lt in TF_BARS and et in TF_BARS:
            out[f"xrt_{lt}_{et}"] = (f"the last {lt} swing high / low broke on the {et} chart, then a retest held "
                                     "(+1 resistance broken up, -1 support broken down)", "")
    return out


# ------------------------------------------------------------------ the engine
def _candles(o, h, l, c, day, pos, n):
    """Resample 5-min bars to n-bar candles per day. Returns candle arrays and, per 5-min bar, the index of the last
    closed candle at its close (cl) and at its open (pr). -1 = none yet."""
    if n == 1:
        idx = np.arange(len(c))
        return (o, h, l, c), idx, idx - 1
    k = pos // n
    key = day.astype(np.int64) * 1000 + k
    starts = np.r_[0, np.nonzero(np.diff(key))[0] + 1]
    ends = np.r_[starts[1:], len(c)]
    co = o[starts]
    ch = np.maximum.reduceat(h, starts)
    cl_ = np.minimum.reduceat(l, starts)
    cc = c[ends - 1]
    cid = np.repeat(np.arange(len(starts)), ends - starts)       # candle of each 5-min bar
    last_bar_of_candle = (pos % n) == n - 1
    # at a bar's close: its own candle if this bar ends it, else the candle before it (which may be yesterday's last)
    cl = np.where(last_bar_of_candle, cid, cid - 1)
    pr = cid - 1                                                 # at a bar's open the forming candle is never closed
    return (co, ch, cl_, cc), cl, pr


def compute(o, h, l, c, day, pos, cfg: dict | None = None) -> dict:
    """Facts for every 5-min bar of a multi-day series. day: an increasing day number per bar; pos: the bar's place in
    its day (0 = 09:15-09:20). Returns {fact: array (len of the series)}."""
    cfg = cfg or config()
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    day, pos = np.asarray(day), np.asarray(pos)
    N = len(c)
    out = {}
    for tf, inds in (cfg.get("tfs") or {}).items():
        n = TF_BARS.get(tf)
        if not n:
            continue
        (co, ch, cl_, cc), at_close, at_open = _candles(o, h, l, c, day, pos, n)
        valid = at_close >= 0
        for ind, outs in inds.items():
            fn = IND.get(ind)
            try:
                res = fn(co, ch, cl_, cc) if fn else {}
            except Exception:
                res = {}
            for o_ in outs:
                arr = res.get(o_)
                v = np.full(N, np.nan)
                if arr is not None and len(arr) == len(cc):
                    v[valid] = np.asarray(arr, float)[at_close[valid]]
                out[f"{o_}_{tf}"] = v
        if tf != "5m":
            out[f"new_{tf}"] = ((pos % n) == n - 1).astype(float)
        # 5-min close vs the last candle closed before this bar began
        ok = at_open >= 0
        hi, lo = np.full(N, np.nan), np.full(N, np.nan)
        hi[ok], lo[ok] = ch[at_open[ok]], cl_[at_open[ok]]
        out[f"ph_brk_{tf}"] = np.where(np.isnan(hi), np.nan, np.where(c > hi, 1.0, np.where(c < lo, -1.0, 0.0)))
    want = set(cfg.get("day") or [])
    for name, fn in (("pivot", pivot_facts), ("sweep", sweep_facts)):
        keys = [k for k in DAY_FACTS[name] if k in want]
        if not keys:
            continue
        try:
            res = fn(o, h, l, c, day, pos)
            out.update({k: res[k] for k in keys})
        except Exception:
            for k in keys:
                out[k] = np.full(N, np.nan)
    for x in (cfg.get("xretest") or []):                  # 2.47: James Berry: 4H level, 30m break and retest
        lt, et = x.get("level_tf"), x.get("exec_tf")
        if lt not in TF_BARS or et not in TF_BARS:
            continue
        try:
            out[f"xrt_{lt}_{et}"] = xretest(o, h, l, c, day, pos, TF_BARS[lt], TF_BARS[et])
        except Exception:
            out[f"xrt_{lt}_{et}"] = np.full(N, np.nan)
    return out


def sweep_facts(o, h, l, c, day, pos, tol_atr=0.15) -> dict:
    """Liquidity sweeps of yesterday's high / low and of equal highs / lows (5-min bars).
    pd_back: price went below yesterday's low and a later close came back above it: +1 on that bar, +2 the next bar
    while every close stays above, ...; a close below again ends it (a new sweep can start). Mirror (-N) at the high.
    pd_rt / eq_rt: after such a sweep and return, the level is retested (the bar dips to within tol_atr ATR of it or
    beyond, without closing beyond) and the bar is a bullish rejection (green, closes above the previous bar's
    close): +1. Mirror -1. Equal lows = the last two confirmed swing lows (2 bars each side) within tol_atr ATR."""
    N = len(c)
    back, prt, ert = np.full(N, np.nan), np.zeros(N), np.zeros(N)
    atr = _atr(h, l, c)
    sh, sl, shi, sli = _swings(h, l, 2)
    prev = None
    lows, highs = [], []
    used = {"lo": set(), "hi": set()}                       # equal-level pairs already traded
    for d in np.unique(day):
        ix = np.nonzero(day == d)[0]
        if prev is not None:
            H, L = h[prev].max(), l[prev].min()
            st = {"lo": [False, 0, False], "hi": [False, 0, False]}      # swept, bars back inside, retest done
            eqs = {"lo": None, "hi": None}
            for t_, g in enumerate(ix):
                a = atr[g] if not math.isnan(atr[g]) else 0.0
                green, red = c[g] > o[g] and c[g] > c[g - 1], c[g] < o[g] and c[g] < c[g - 1]
                # yesterday's low
                s_ = st["lo"]
                if l[g] < L and c[g] < L:
                    s_[:] = [True, 0, False]
                elif l[g] < L:
                    s_[0] = True
                    s_[1] = 1 if c[g] > L else s_[1]
                elif s_[0] and c[g] > L:
                    s_[1] = s_[1] + 1 if s_[1] else 1
                    if s_[1] >= 2 and l[g] <= L + tol_atr * a and green and not s_[2]:
                        prt[g], s_[2] = 1.0, True
                s_ = st["hi"]
                if h[g] > H and c[g] > H:
                    s_[:] = [True, 0, False]
                elif h[g] > H:
                    s_[0] = True
                    s_[1] = 1 if c[g] < H else s_[1]
                elif s_[0] and c[g] < H:
                    s_[1] = s_[1] + 1 if s_[1] else 1
                    if s_[1] >= 2 and h[g] >= H - tol_atr * a and red and not s_[2]:
                        prt[g], s_[2] = -1.0, True
                lo_b, hi_b = st["lo"][1] if st["lo"][0] else 0, st["hi"][1] if st["hi"][0] else 0
                if lo_b and (not hi_b or lo_b <= hi_b):
                    back[g] = float(lo_b)
                elif hi_b:
                    back[g] = -float(hi_b)
                else:
                    back[g] = 0.0
                # equal lows / highs from confirmed swings
                if sli[g] >= 0 and (not lows or lows[-1][0] != sli[g]):
                    lows.append((int(sli[g]), sl[g]))
                if shi[g] >= 0 and (not highs or highs[-1][0] != shi[g]):
                    highs.append((int(shi[g]), sh[g]))
                if (len(lows) >= 2 and a and abs(lows[-1][1] - lows[-2][1]) <= tol_atr * a and lows[-1][0] not in used["lo"]
                        and (eqs["lo"] is None or eqs["lo"][0] != lows[-1][0])):
                    eqs["lo"] = [lows[-1][0], min(lows[-1][1], lows[-2][1]), 0]
                if (len(highs) >= 2 and a and abs(highs[-1][1] - highs[-2][1]) <= tol_atr * a and highs[-1][0] not in used["hi"]
                        and (eqs["hi"] is None or eqs["hi"][0] != highs[-1][0])):
                    eqs["hi"] = [highs[-1][0], max(highs[-1][1], highs[-2][1]), 0]
                e = eqs["lo"]                                # [swing index, level, state 0 armed / 1 swept / 2 back]
                if e is not None:
                    lv_ = e[1]
                    if e[2] == 0 and l[g] < lv_:
                        e[2] = 2 if c[g] > lv_ else 1
                    elif e[2] == 1 and c[g] > lv_:
                        e[2] = 2
                    elif e[2] == 2 and c[g] < lv_:
                        e[2] = 1
                    elif e[2] == 2 and l[g] <= lv_ + tol_atr * a and green:
                        ert[g], eqs["lo"] = 1.0, None
                        used["lo"].add(e[0])
                e = eqs["hi"]
                if e is not None:
                    lv_ = e[1]
                    if e[2] == 0 and h[g] > lv_:
                        e[2] = 2 if c[g] < lv_ else 1
                    elif e[2] == 1 and c[g] < lv_:
                        e[2] = 2
                    elif e[2] == 2 and c[g] > lv_:
                        e[2] = 1
                    elif e[2] == 2 and h[g] >= lv_ - tol_atr * a and red:
                        ert[g], eqs["hi"] = -1.0, None
                        used["hi"].add(e[0])
        prev = ix
    have = ~np.isnan(back)
    return {"pd_back": back, "pd_rt": np.where(have, prt, np.nan), "eq_rt": np.where(np.isnan(atr), np.nan, ert)}


def xretest(o, h, l, c, day, pos, n_lv: int, n_ex: int, k_max: int = 12) -> np.ndarray:
    """Bigger-chart level, smaller-chart break and retest (James Berry): levels = the last confirmed swing high / low
    (2 candles each side) of the n_lv-bar chart, known at that chart's last closed candle; on the n_ex-bar chart a close
    beyond the level marks the break; within k_max candles a candle that comes back to within 0.25 ATR of the level
    and closes on the break side = the retest held (-1 support broken down, +1 resistance broken up). Per 5-min bar:
    the value of the last closed n_ex candle."""
    N = len(c)
    (lo_, lh, ll, lc), lv_close, _ = _candles(o, h, l, c, day, pos, n_lv)
    sh, sl, _shi, _sli = _swings(lh, ll, 2)
    (eo, eh, el, ec), ex_close, _ = _candles(o, h, l, c, day, pos, n_ex)
    # for each execution candle: the level candle closed at its last 5-min bar
    ex_idx = np.full(len(ec), -1)
    lv_at = np.full(len(ec), -1)
    for b in range(N):                                    # the 5-min bar that closes each execution candle
        j = ex_close[b]
        if j >= 0:
            ex_idx[j] = b
    for j in range(len(ec)):
        b = ex_idx[j]
        lv_at[j] = lv_close[b] if b >= 0 else -1
    atr = _atr(eh, el, ec)
    sig = np.zeros(len(ec))
    brk_dn = brk_up = None                                # (level, candle index of the break)
    for j in range(1, len(ec)):
        q = lv_at[j]
        if q < 0 or math.isnan(atr[j]):
            continue
        sup, res = sl[q], sh[q]
        if not math.isnan(sup) and ec[j] < sup <= ec[j - 1]:
            brk_dn = (sup, j)
        if not math.isnan(res) and ec[j] > res >= ec[j - 1]:
            brk_up = (res, j)
        if brk_dn and 1 <= j - brk_dn[1] <= k_max and eh[j] >= brk_dn[0] - 0.25 * atr[j] and ec[j] < brk_dn[0]:
            sig[j], brk_dn = -1.0, None
        elif brk_up and 1 <= j - brk_up[1] <= k_max and el[j] <= brk_up[0] + 0.25 * atr[j] and ec[j] > brk_up[0]:
            sig[j], brk_up = 1.0, None
        if brk_dn and j - brk_dn[1] > k_max:
            brk_dn = None
        if brk_up and j - brk_up[1] > k_max:
            brk_up = None
    out = np.full(N, np.nan)
    ok = ex_close >= 0
    out[ok] = sig[ex_close[ok]]
    return out


def pivot_facts(o, h, l, c, day, pos) -> dict:
    """Classic floor pivots from yesterday's high / low / close: P = (H+L+C)/3, R1 = 2P-L, S1 = 2P-H, R2 = P+(H-L),
    S2 = P-(H-L)."""
    N = len(c)
    pp, px, prt = np.full(N, np.nan), np.zeros(N), np.zeros(N)
    atr = _atr(h, l, c)
    days = np.unique(day)
    prev = None
    for d in days:
        ix = np.nonzero(day == d)[0]
        if prev is not None:
            H, L, C = h[prev].max(), l[prev].min(), c[prev][-1]
            P = (H + L + C) / 3
            R1, S1, R2, S2 = 2 * P - L, 2 * P - H, P + (H - L), P - (H - L)
            lv = np.array([S2, S1, P, R1, R2])
            broke_up, broke_dn = {}, {}
            for t_, g in enumerate(ix):
                x = c[g]
                if x >= P:
                    pp[g] = (x - P) / (R1 - P) if x <= R1 else 1 + (x - R1) / max(R2 - R1, 1e-9)
                else:
                    pp[g] = -(P - x) / (P - S1) if x >= S1 else -1 - (S1 - x) / max(S1 - S2, 1e-9)
                if t_ > 0:
                    x0 = c[ix[t_ - 1]]
                    for j, v in enumerate(lv):
                        if x0 <= v < x:
                            px[g], broke_up[j] = 1.0, t_
                        elif x0 >= v > x:
                            px[g], broke_dn[j] = -1.0, t_
                a = atr[g] if not math.isnan(atr[g]) else 0
                for j, t0 in broke_up.items():
                    if t_ - t0 >= 2 and l[g] <= lv[j] + 0.1 * a and x > lv[j]:
                        prt[g] = 1.0
                for j, t0 in broke_dn.items():
                    if t_ - t0 >= 2 and h[g] >= lv[j] - 0.1 * a and x < lv[j]:
                        prt[g] = -1.0
        prev = ix
    have = ~np.isnan(pp)
    return {"piv_pos": pp, "piv_x": np.where(have, px, np.nan), "piv_rt": np.where(have, prt, np.nan)}


def bars5(spot_by_day: dict):
    """{date: 1-minute spot array} -> 5-min o/h/l/c of all days joined, with day number and bar position; and the
    dates in order with each day's first index. Same bars as the Lab's build (nb = whole 5-min bars present)."""
    o, h, l, c, dy, ps, starts = [], [], [], [], [], [], {}
    for di, d in enumerate(sorted(spot_by_day)):
        sp = np.asarray(spot_by_day[d], float)
        nb = int(np.sum(~np.isnan(sp)) // 5)
        if nb < 10:
            continue
        seg = sp[:5 * nb].reshape(nb, 5)
        starts[d] = (len(c), nb)
        o.append(seg[:, 0]), h.append(seg.max(1)), l.append(seg.min(1)), c.append(seg[:, -1])
        dy.append(np.full(nb, di)), ps.append(np.arange(nb))
    if not c:
        return None
    cat = np.concatenate
    return cat(o), cat(h), cat(l), cat(c), cat(dy), cat(ps), starts


def by_day(spot_by_day: dict, cfg: dict | None = None) -> dict:
    """The Lab: {date: {fact: array of that day's 5-min bars}} computed once over the whole history."""
    b = bars5(spot_by_day)
    if b is None:
        return {}
    o, h, l, c, dy, ps, starts = b
    f = compute(o, h, l, c, dy, ps, cfg)
    return {d: {k: v[s:s + nb] for k, v in f.items()} for d, (s, nb) in starts.items()}


def live(hist_days: list, o, h, l, c, cfg: dict | None = None) -> dict:
    """Live: hist_days = [(o, h, l, c) of each earlier day, oldest first] (5-min bars from 09:15), then today's bars so
    far. Returns {fact: array of today's bars}."""
    cfg = cfg or config()
    keep = int(cfg.get("history_days") or 12)
    parts = list(hist_days[-keep:]) + [(o, h, l, c)]
    oo, hh, ll, cc, dy, ps = [], [], [], [], [], []
    for di, (a, b, e, f_) in enumerate(parts):
        n = len(f_)
        oo.append(np.asarray(a, float)), hh.append(np.asarray(b, float)), ll.append(np.asarray(e, float)), cc.append(np.asarray(f_, float))
        dy.append(np.full(n, di)), ps.append(np.arange(n))
    cat = np.concatenate
    f = compute(cat(oo), cat(hh), cat(ll), cat(cc), cat(dy), cat(ps), cfg)
    n0 = len(cc[-1])
    return {k: v[len(v) - n0:] for k, v in f.items()}
