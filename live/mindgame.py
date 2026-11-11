"""MindGame (gun, 2.70): the buyer / seller scene on any timeframe, read from the candles as ACTIVITY.

Mandeep, 12 Nov 2026: learn how buyers and sellers behave at support / resistance from the transcripts; every bullet
has a prediction, a live sign and a record; "points, level ke number nahi, activity kya ho rahi hai ye dekhna hai";
2, 3 or more behaviours can be active at once, so the reading is combined; a short AI message (fast tier) with the
system's own message when AI is not there; shown on the main chart (1m / 3m / 5m) and on every timeframe below it.

Gun (here): what a typical candle is on this timeframe for this symbol (the unit every check is measured in), the
activity checks (a straight run into a level, a rest at a level, lows holding, a tight range and its break, a slow weak
move, a confirmed reversal candle, a side leaving, a retest, the gap, the OI strike), the scene (who is in control,
what phase), the combiner, the message, the record (every new reading graded right / wrong / flat a few candles later).
Bullets (rules/mindgame.json): which behaviours are on, who they are about, the wording, the grading window, the trust
bar. A bullet whose check fails is skipped and named; everything else keeps running. All on trial: no alert, no paper
trade, no Lab result depends on it. Market data only.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import numpy as np

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "mindgame.json"
TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "75m": 4500, "1D": 86400, "1W": 604800}
INTRADAY = {"1m", "3m", "5m", "15m", "1H", "4H", "75m"}
LIVE_FORMING = {"4H", "1D", "1W"}          # read with the candle still forming (the day / week so far); never graded open
IST_SHIFT = 19800
DEFAULTS = {"unit_bars": 50, "min_bars": 20, "grade_bars": {"1m": 10, "3m": 8, "5m": 6, "15m": 6, "1H": 5, "4H": 4, "1D": 5, "1W": 4},
            "grade_units": 1.0, "trust": {"min_n": 30, "min_right_pct": 60},
            "tf_weight": {"1m": 0.5, "3m": 0.75, "5m": 1, "15m": 1.5, "1H": 2, "4H": 2.5, "1D": 3, "1W": 3.5},
            "record_syms": ["NIFTY", "BANKNIFTY"], "record_tfs": ["1m", "3m", "5m", "15m"],
            "ai": {"on": True, "per_day": 60, "min_gap_s": 120}}

# the activity checks' fixed definitions (gun), in typical candles of the timeframe being read
LOOK = 24            # the level: the highest high / lowest low of the 24 candles before
NEAR = 0.6           # near a level: within 0.6 of a typical candle
REST_MIN = 3         # a rest: 3+ candles in a row waiting near the level
RUN_BARS = 8         # the journey into the level: the 8 candles before the rest
RUN_MIN = 4.0        # a straight run: 4+ typical candles of travel ...
ONE_WAY = 0.7        # ... with 70%+ of the candles closing the same way
BREAK_RANGE = 1.0    # a strong break candle: at least a typical candle
SQ_MIN, SQ_MAX, SQ_WIDTH, SQ_CANDLE = 8, 40, 2.5, 0.75  # a tight range: 8+ small candles inside 2.5 typical candles
HOLD_MIN, HOLD_NEAR = 4, 1.0
SLOW_BARS, SLOW_MOVE, SLOW_CANDLE = 8, 2.0, 0.7
LEAVE_MOVE = 3.0     # a side leaving: after a move of 3+ typical candles, from the candle that made the top (bottom)
BIG_MOVE, TAKE_BACK = 4.0, 0.7
GAP_DAY = 0.3        # a big gap: 30%+ of a typical day's range
RETEST_WITHIN, RETEST_NEAR = 12, 0.4
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            st = raw.get("settings") or {}
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in st.items() if k in DEFAULTS}}, raw=raw, error=None)
        return _C["cfg"]
    except Exception as e:
        _C.update(cfg=dict(DEFAULTS), raw={"bullets": []}, error=str(e)[:160])
        return _C["cfg"]


def bullets() -> tuple[list, list, list]:
    """(bullets on trial that the gun can check, bullets waiting for data ('later'), bad entries skipped)."""
    cfg()
    ok, later, bad = [], [], []
    for b in (_C.get("raw") or {}).get("bullets") or []:
        if not isinstance(b, dict) or not b.get("id"):
            bad.append(str(b)[:60])
        elif b.get("status") == "later":
            later.append(b)
        elif b.get("detect") not in DETECT:
            bad.append(f"{b['id']}: no check named {b.get('detect')!r}")
        else:
            ok.append(b)
    return ok, later, bad


def _p(x) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.1f}"


def unit(h, l, c, n=50) -> float:
    """A typical candle on this timeframe: the median true range of the last n candles."""
    h, l, c = h[-n:], l[-n:], c[-n:]
    if len(c) < 2:
        return float(h[-1] - l[-1]) if len(c) else 0.0
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
    return float(np.median(tr))


def is_forming(tf: str, t: float, now: float) -> bool:
    if tf == "1D":
        return bool(dt.datetime.fromtimestamp(t, IST).date() == dt.datetime.fromtimestamp(now, IST).date())
    if tf == "1W":
        return dt.datetime.fromtimestamp(t, IST).date().isocalendar()[:2] == dt.datetime.fromtimestamp(now, IST).date().isocalendar()[:2]
    end = t + TF_SEC.get(tf, 60)
    day_end = dt.datetime.combine(dt.datetime.fromtimestamp(t, IST).date(), dt.time(15, 30), IST).timestamp()
    return bool(min(end, day_end) > now)


# ------------------------------------------------------------------ context
def context(rows, tf: str, now: float | None = None, extra: dict | None = None) -> dict | None:
    """Arrays of the candles to read (closed ones; the forming one too on 4H / 1D / 1W), the typical candle, and the
    previous session (high / low / close / open, a typical day's range) for intraday timeframes."""
    now = now or time.time()
    if not rows:
        return None
    a = np.asarray([r[:6] if len(r) >= 6 else list(r[:5]) + [0.0] for r in rows], dtype=float)
    a = a[np.argsort(a[:, 0], kind="stable")]
    forming = is_forming(tf, a[-1, 0], now)
    if forming and tf not in LIVE_FORMING:
        a, forming = a[:-1], False
    c_ = cfg()
    if len(a) < int(c_["min_bars"]):
        return None
    t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    u = unit(h, l, c, int(c_["unit_bars"]))
    if not u > 0:
        return None
    x = {"tf": tf, "t": t, "o": o, "h": h, "l": l, "c": c, "n": len(c), "u": u, "forming": forming, "now": now,
         "walls": {}, "names": []}
    extra = extra or {}
    if tf in INTRADAY:
        days = np.array([dt.datetime.fromtimestamp(v, IST).date().toordinal() for v in t])
        i0 = int(np.searchsorted(days, days[-1]))
        x["i0"] = i0
        x["s0"] = dt.datetime.combine(dt.date.fromordinal(int(days[-1])), dt.time(9, 15), IST).timestamp()
        prev = np.flatnonzero(days == days[i0 - 1]) if i0 > 0 else np.array([], int)
        if len(prev):
            x.update(pdh=float(h[prev].max()), pdl=float(l[prev].min()), pdc=float(c[prev[-1]]), pdo=float(o[prev[0]]))
            rngs = []
            for d in np.unique(days[:i0])[-10:]:
                m = days == d
                rngs.append(h[m].max() - l[m].min())
            x["u_day"] = float(np.median(rngs)) if rngs else None
    if extra.get("pdc"):
        x["pdc"] = float(extra["pdc"])
    for k in ("oi_call", "oi_put"):
        if extra.get(k):
            x["walls"][k] = float(extra[k])
    for k in ("pdh", "pdl", "pdc"):
        if x.get(k):
            x["names"].append(({"pdh": "yesterday's high", "pdl": "yesterday's low", "pdc": "yesterday's close"}[k], x[k]))
    for k, v in x["walls"].items():
        x["names"].append(("call-OI strike" if k == "oi_call" else "put-OI strike", v))
    return x


def _lv(x, price) -> str:
    """'25,335' or '25,335 (yesterday's high)' when a known level sits there."""
    nm = next((n for n, p in x["names"] if abs(p - price) <= 0.4 * x["u"]), None)
    return f"{_p(price)} ({nm})" if nm else _p(price)


def _it(bid, side, now, nxt, level=None, strength=0.6, **kw) -> dict:
    return {"id": bid, "side": int(side), "now": now, "next": nxt, "level": None if level is None else round(float(level), 2),
            "strength": round(float(max(0.1, min(1.0, strength))), 2), **kw}


# ------------------------------------------------------------------ activity checks
def d_level_break(x) -> list:
    """At today's / recent resistance (support): did price rest there before breaking, or run straight into it?"""
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    if n < LOOK + 3:
        return []
    i, out = n - 1, []
    for up in (True, False):
        lv = float(h[i - LOOK:i].max() if up else l[i - LOOK:i].min())
        broke = c[i] > lv if up else c[i] < lv
        near = h[i] >= lv - NEAR * u if up else l[i] <= lv + NEAR * u
        if not (broke or near):
            continue
        k = 0
        while k < 12 and i - 1 - k >= 0:
            j = i - 1 - k
            if not ((h[j] >= lv - NEAR * u and c[j] <= lv) if up else (l[j] <= lv + NEAR * u and c[j] >= lv)):
                break
            k += 1
        a = max(0, i - k - RUN_BARS)
        rb = list(range(a, i - k)) + ([i] if k == 0 else [])
        if not rb:
            continue
        rb = np.asarray(rb)
        run = ((lv - l[rb].min()) if up else (h[rb].max() - lv)) / u
        one = float(np.mean(c[rb] > o[rb])) if up else float(np.mean(c[rb] < o[rb]))
        s = 1 if up else -1
        word, other = ("resistance", "buyers") if up else ("support", "sellers")
        if broke and k >= REST_MIN and (h[i] - l[i]) >= BREAK_RANGE * u:
            out.append(_it("rest_break", s, f"{k} candles rested at {word} {_lv(x, lv)}, then a strong close {'above' if up else 'below'} it",
                           f"Real {'breakout' if up else 'breakdown'}: old {other} out, new ones in, {other} strong; the move tends to run",
                           lv, 0.5 + 0.1 * min(k, 5)))
        elif k <= 1 and run >= RUN_MIN and one >= ONE_WAY:
            start = l[rb].min() if up else h[rb].max()
            if broke:
                out.append(_it("trap_run", -s, f"Ran straight from {_p(start)} {'up through' if up else 'down through'} {word} {_lv(x, lv)}, no rest",
                               f"Trap risk: {'profit booking' if up else 'short covering'} sits here; a first {'break' if up else 'breakdown'} like this often fails",
                               lv, min(1.0, 0.4 + run / 15)))
            else:
                out.append(_it("trap_run", -s, f"Ran straight into {word} {_lv(x, lv)} ({run:.0f} candles of travel), no rest",
                               f"{'Profit-booking' if up else 'Short-covering'} zone: a break from here is often a trap; a rest here first is healthier",
                               lv, min(0.8, 0.3 + run / 20)))
    return out


def d_holding(x) -> list:
    """Price works near a level and the lows don't break (buyers holding) / the highs don't break (sellers holding)."""
    h, l, c, u, n = x["h"], x["l"], x["c"], x["u"], x["n"]
    if n < LOOK:
        return []
    i, out = n - 1, []
    R, S = float(h[i - LOOK + 1:i + 1].max()), float(l[i - LOOK + 1:i + 1].min())
    for up in (True, False):
        k = 0
        while k < 12 and i - k >= 0 and ((h[i - k] >= R - HOLD_NEAR * u) if up else (l[i - k] <= S + HOLD_NEAR * u)):
            k += 1
        if k < HOLD_MIN:
            continue
        w = slice(i - k + 1, i + 1)
        if up and l[w].min() >= l[i - k + 1] - 0.15 * u and l[i] >= l[i - k + 1]:
            out.append(_it("holding", 1, f"{k} candles working under {_lv(x, R)}, lows not breaking",
                           f"Buyers holding: a break above {_p(R)} is likely", R, 0.4 + 0.05 * k))
        if not up and h[w].max() <= h[i - k + 1] + 0.15 * u and h[i] <= h[i - k + 1]:
            out.append(_it("holding", -1, f"{k} candles working above {_lv(x, S)}, highs not breaking",
                           f"Sellers holding: a break below {_p(S)} is likely", S, 0.4 + 0.05 * k))
    return out


def _band(h, l, end, k):
    return float(h[end - k + 1:end + 1].max()), float(l[end - k + 1:end + 1].min())


def d_range_trap(x) -> list:
    """Small candles in a tight range (both sides building positions); a close out of it traps the other side."""
    h, l, c, u, n = x["h"], x["l"], x["c"], x["u"], x["n"]
    i = n - 1

    def longest(end):
        best = 0
        for k in range(SQ_MIN, min(SQ_MAX, end + 1) + 1):
            hh, ll = _band(h, l, end, k)
            if hh - ll > SQ_WIDTH * u:
                break
            if float(np.mean(h[end - k + 1:end + 1] - l[end - k + 1:end + 1])) <= SQ_CANDLE * u:
                best = k                         # the longest stretch of small candles inside the band
        return best
    k2 = longest(i - 1) if i >= SQ_MIN + 1 else 0
    if k2 >= SQ_MIN:                                 # a strong close out of the range that ended on the candle before
        hh, ll = _band(h, l, i - 1, k2)
        up, dn = c[i] > hh + 0.3 * u, c[i] < ll - 0.3 * u
        if (up or dn) and (h[i] - l[i]) >= BREAK_RANGE * u:
            return [_it("range_trap", 1 if up else -1, f"Broke {'up' if up else 'down'} out of a {k2}-candle range ({_p(ll)}-{_p(hh)})",
                        f"{'Sellers' if up else 'Buyers'} trapped: a fast move {'up' if up else 'down'} is likely",
                        hh if up else ll, min(1.0, 0.5 + k2 / 40), lo=round(ll, 2), hi=round(hh, 2))]
    k = longest(i)
    if k >= SQ_MIN:
        hh, ll = _band(h, l, i, k)
        return [_it("range_trap", 0, f"Consolidating: {k} small candles inside {_p(ll)}-{_p(hh)}",
                    f"Both sides building positions; the side that loses the break gets trapped. Up above {_p(hh)}, down below {_p(ll)}",
                    None, min(1.0, 0.3 + k / 30), lo=round(ll, 2), hi=round(hh, 2))]
    return []


def d_reversal_candle(x) -> list:
    from lab.pafacts import hammer_star
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    w = slice(max(0, n - 40), n)
    hs, _, _ = hammer_star(o[w], h[w], l[w], c[w])
    s = int(hs[-1]) if len(hs) else 0
    if not s:
        return []
    m = min(20, n)
    move = ((h[-m:-1].max() - l[-m:].min()) if s > 0 else (h[-m:].max() - l[-m:-1].min())) / u
    flat = (h[-m:].max() - l[-m:].min()) / u <= 3.0
    nm = "Hammer" if s > 0 else "Shooting star"
    if move >= 3.0 and not flat:
        return [_it("reversal_candle", s, f"{nm} confirmed after a {move:.0f}-candle {'fall' if s > 0 else 'rise'}",
                    f"{'Sellers' if s > 0 else 'Buyers'} booking profit: a {'bounce' if s > 0 else 'pullback'} is likely", None, 0.6)]
    if flat:
        return [_it("reversal_candle", 0, f"{nm} inside a flat range", "Nobody holds positions to book here: it tends to fail", None, 0.3)]
    return []


def d_slow_move(x) -> list:
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    if n <= SLOW_BARS:
        return []
    mv = (c[-1] - c[-1 - SLOW_BARS]) / u
    rg = (h[-SLOW_BARS:] - l[-SLOW_BARS:]) / u
    if rg.mean() > SLOW_CANDLE or rg.max() >= 1.3 or abs(mv) < SLOW_MOVE:
        return []
    if mv < 0:
        return [_it("slow_move", 0, f"Slow, weak fall: {SLOW_BARS} small candles, no selling strength",
                    "A buyer is absorbing underneath: no bounce trade until a strong fall or a clear turn", None, 0.4)]
    return [_it("slow_move", 0, f"Slow, weak rise: {SLOW_BARS} small candles, no buying strength",
                "A seller is absorbing above: no short trade until a strong rise or a clear turn", None, 0.4)]


def d_leaving(x) -> list:
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    i = n - 1
    if n < 12:
        return []
    for s in (1, -1):                               # s = the side that may be leaving: 1 buyers, -1 sellers
        if (c[i] < o[i]) if s > 0 else (c[i] > o[i]):
            j = next((j for j in range(i - 1, i - 6, -1) if ((c[j] > o[j]) if s > 0 else (c[j] < o[j]))), None)
            if j is None or (h[i] - l[i]) < 0.8 * u:
                continue
            top = h[max(0, i - 10):i].max() if s > 0 else l[max(0, i - 10):i].min()
            if (h[j] < top - 0.3 * u) if s > 0 else (l[j] > top + 0.3 * u):
                continue                             # the last buying candle must be the one at the top
            prior = ((c[j] - l[max(0, j - 10):j + 1].min()) if s > 0 else (h[max(0, j - 10):j + 1].max() - c[j])) / u
            if prior >= LEAVE_MOVE and ((c[i] < l[j]) if s > 0 else (c[i] > h[j])):
                lvl = l[j] if s > 0 else h[j]
                return [_it("leaving", -s, f"{'Selling' if s > 0 else 'Buying'} candle closed {'below' if s > 0 else 'above'} the last "
                                           f"{'buying' if s > 0 else 'selling'} candle's {'low' if s > 0 else 'high'} {_p(lvl)}",
                            f"{'Buyers' if s > 0 else 'Sellers'} are leaving: the move tends to turn; book into it", lvl, 0.55)]
    return []


def d_oi_wall(x) -> list:
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    i, out = n - 1, []
    for k, W in x["walls"].items():
        call = k == "oi_call"
        rng = max(h[i] - l[i], 1e-9)
        if call and h[i] >= W - NEAR * u and c[i] < W and (h[i] - max(o[i], c[i])) >= 0.5 * rng:
            out.append(_it("oi_wall", -1, f"Rejected just below the call-OI strike {_p(W)}", "Call writers defending it: price tends to come back down", W, 0.5))
        elif not call and l[i] <= W + NEAR * u and c[i] > W and (min(o[i], c[i]) - l[i]) >= 0.5 * rng:
            out.append(_it("oi_wall", 1, f"Rejected just above the put-OI strike {_p(W)}", "Put writers defending it: price tends to bounce", W, 0.5))
        elif n >= 3 and call and c[i] > W and c[i - 1] > W and c[i - 2] <= W:
            out.append(_it("oi_wall", 1, f"Two closes above the call-OI strike {_p(W)}: accepted", "Call writers covering: the move tends to run", W, 0.55))
        elif n >= 3 and not call and c[i] < W and c[i - 1] < W and c[i - 2] >= W:
            out.append(_it("oi_wall", -1, f"Two closes below the put-OI strike {_p(W)}: accepted", "Put writers covering: the fall tends to run", W, 0.55))
    return out


def d_yclose(x) -> list:
    if x["tf"] not in INTRADAY or not x.get("pdc"):
        return []
    p, c, u = x["pdc"], x["c"][-1], x["u"]
    if c > p + 0.2 * u:
        return [_it("yclose", 1, f"Above yesterday's close {_p(p)}", "Buyers lead the day; yesterday's close acts as support", p, 0.3)]
    if c < p - 0.2 * u:
        return [_it("yclose", -1, f"Below yesterday's close {_p(p)}", "Sellers lead the day; yesterday's close acts as a cap", p, 0.3)]
    return [_it("yclose", 0, f"At yesterday's close {_p(p)}", "The day's control is being decided here", p, 0.3)]


def d_taken_back(x) -> list:
    h, l, c, u, n = x["h"], x["l"], x["c"], x["u"], x["n"]
    i = n - 1
    a = x["i0"] if x["tf"] in INTRADAY and x.get("i0") is not None and i - x["i0"] >= 6 else max(0, i - 30)
    if i - a < 4:
        return []
    jh = a + int(np.argmax(h[a:i]))
    H, L = float(h[jh]), float(l[jh:i].min())
    if (H - L) / u >= BIG_MOVE and H > L and c[i] < H and (c[i] - L) / (H - L) >= TAKE_BACK:
        return [_it("taken_back", 1, f"Took back {(c[i] - L) / (H - L):.0%} of the fall from {_p(H)} to {_p(L)}",
                    f"Most sellers have exited: price tends to reach {_p(H)} again; failing there means a fast fall below {_p(L)}", H, 0.5)]
    jl = a + int(np.argmin(l[a:i]))
    Lo, Hi = float(l[jl]), float(h[jl:i].max())
    if (Hi - Lo) / u >= BIG_MOVE and Hi > Lo and c[i] > Lo and (Hi - c[i]) / (Hi - Lo) >= TAKE_BACK:
        return [_it("taken_back", -1, f"Gave back {(Hi - c[i]) / (Hi - Lo):.0%} of the rise from {_p(Lo)} to {_p(Hi)}",
                    f"Most buyers have exited: price tends to reach {_p(Lo)} again; holding there means a fast rise above {_p(Hi)}", Lo, 0.5)]
    return []


def _early(x, mins) -> bool:
    return x["tf"] in INTRADAY and x.get("s0") is not None and x["now"] - x["s0"] <= mins * 60


def d_gap_booking(x) -> list:
    if not (x.get("pdh") and x.get("u_day") and x.get("i0") is not None) or not _early(x, 75):
        return []
    o, c = x["o"], x["c"]
    i0, ud = x["i0"], x["u_day"]
    first = [j for j in range(i0, x["n"]) if x["t"][j] < x["s0"] + 15 * 60] or [i0]
    day_open, mv = o[i0], c[first[-1]] - o[i0]
    if day_open > x["pdh"] + GAP_DAY * ud and mv < 0:
        return [_it("gap_booking", -1, f"Opened well above yesterday's high {_p(x['pdh'])}, first candles selling",
                    "Yesterday's buyers booking profit: an early dip or stall, no fresh buying yet", x["pdh"], 0.5)]
    if day_open < x["pdl"] - GAP_DAY * ud and mv > 0:
        return [_it("gap_booking", 1, f"Opened well below yesterday's low {_p(x['pdl'])}, first candles buying",
                    "Yesterday's sellers covering: an early bounce or stall, no fresh selling yet", x["pdl"], 0.5)]
    return []


def d_gap_cover(x) -> list:
    if not (x.get("pdc") and x.get("pdo") and x.get("u_day") and x.get("i0") is not None) or not _early(x, 190):
        return []
    i0, p = x["i0"], x["pdc"]
    gap = x["o"][i0] - p
    yday = p - x["pdo"]
    if abs(gap) < 0.25 * x["u_day"] or gap * yday >= 0:
        return []
    filled = x["l"][i0:].min() <= p if gap > 0 else x["h"][i0:].max() >= p
    if filled:
        return []
    up = gap < 0
    return [_it("gap_cover", 1 if up else -1, f"Gap {'down' if up else 'up'} against yesterday's {'rise' if up else 'fall'}, not filled yet",
                f"Trapped {'shorts' if up else 'longs'} tend to cover toward yesterday's close {_p(p)}", p, 0.45)]


def d_retest(x) -> list:
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    i = n - 1
    for b in range(i - 1, max(LOOK, i - RETEST_WITHIN) - 1, -1):
        R, S = h[b - LOOK:b].max(), l[b - LOOK:b].min()
        for up in (True, False):
            lv = R if up else S
            if not ((c[b] > lv) if up else (c[b] < lv)):
                continue
            after = slice(b + 1, i + 1)
            touch = np.flatnonzero(l[after] <= lv + RETEST_NEAR * u) if up else np.flatnonzero(h[after] >= lv - RETEST_NEAR * u)
            held = (c[after] >= lv - 0.2 * u).all() if up else (c[after] <= lv + 0.2 * u).all()
            if len(touch) and held and b + 1 + touch[-1] >= i - 2:
                return [_it("retest", 1 if up else -1, f"Came back to the {'breakout' if up else 'breakdown'} level {_lv(x, lv)} and held",
                            f"Right-entry {'buyers' if up else 'sellers'} defend it: the move tends to continue", lv, 0.55)]
            return []
    return []


DETECT = {"level_break": d_level_break, "holding": d_holding, "range_trap": d_range_trap, "reversal_candle": d_reversal_candle,
          "slow_move": d_slow_move, "leaving": d_leaving, "oi_wall": d_oi_wall, "yclose": d_yclose, "taken_back": d_taken_back,
          "gap_booking": d_gap_booking, "gap_cover": d_gap_cover, "retest": d_retest}


# ------------------------------------------------------------------ the scene on one timeframe
def read(rows, tf: str, now: float | None = None, extra: dict | None = None, stats: dict | None = None) -> dict:
    """Who is in control, what phase, and every behaviour active now on this timeframe (each in isolation)."""
    x = context(rows, tf, now, extra)
    if x is None:
        return {"tf": tf, "items": [], "empty": "Not enough candles yet", "score": 0.0}
    ok, _, bad = bullets()
    by_id = {b["id"]: b for b in ok}
    items, failed = [], list(bad)
    for name in dict.fromkeys(b["detect"] for b in ok):
        try:
            for it in DETECT[name](x) or []:
                b = by_id.get(it["id"])
                if b is None:
                    continue
                it.update(n=b.get("n"), who=b.get("who"), source=b.get("source"))
                rec = (stats or {}).get((it["id"], tf)) or (stats or {}).get(it["id"])
                if rec:
                    it["record"] = rec
                chk = (b.get("check_5y") or {}).get(tf)
                if chk:                                  # the offline 5-year check on this timeframe [right %, decided]
                    it["check"] = {"right_pct": chk[0], "n": chk[1]}
                items.append(it)
        except Exception as ex:
            failed.append(f"{name}: {str(ex)[:80]}")
    o, h, l, c, u, n = x["o"], x["h"], x["l"], x["c"], x["u"], x["n"]
    m = min(12, n)
    body = c[-m:] - o[-m:]
    bal = float(body.sum() / max(np.abs(body).sum(), 1e-9))
    lo, hi = l[-LOOK:].min(), h[-LOOK:].max()
    pos = float((c[-1] - lo) / (hi - lo)) if hi > lo else 0.5
    cs = 0.7 * bal + 0.3 * (pos * 2 - 1)
    control = "buyers" if cs >= 0.25 else "sellers" if cs <= -0.25 else "balanced"
    sq = next((it for it in items if it["id"] == "range_trap"), None)
    mv = (c[-1] - c[-1 - min(8, n - 1)]) / u
    if sq and sq["side"] == 0:
        phase = sq["now"].replace("Consolidating: ", "consolidating, ")
    elif sq:
        phase = f"range broke {'up' if sq['side'] > 0 else 'down'}"
    elif any(it["id"] == "rest_break" for it in items):
        phase = "breakout after a rest" if any(it["id"] == "rest_break" and it["side"] > 0 for it in items) else "breakdown after a rest"
    elif mv >= 3:
        phase = "running up"
    elif mv <= -3:
        phase = "running down"
    else:
        phase = "two-way"
    score = 0.5 * cs
    for it in items:
        r, k = it.get("record") or {}, it.get("check") or {}
        mult = 1.5 if r.get("status") == "trusted" else 0.5 if r.get("status") == "weak" else \
            1.3 if k.get("n", 0) >= 100 and k.get("right_pct", 0) >= 52 else 0.5 if k.get("n", 0) >= 200 and k.get("right_pct", 100) <= 45 else 1.0
        score += it["side"] * it["strength"] * mult * 0.5
    items.sort(key=lambda it: (it["side"] == 0, -it["strength"]))
    word = {"buyers": "Buyers in control", "sellers": "Sellers in control", "balanced": "Balanced"}[control]
    return {"tf": tf, "at": float(x["t"][-1]), "close": float(c[-1]), "unit": round(u, 2), "forming": bool(x["forming"]),
            "control": control, "control_score": round(cs, 2), "phase": phase, "items": items,
            "score": round(float(max(-1.0, min(1.0, score))), 2), "line": f"{word}, {phase}", "failed": failed[:5]}


# ------------------------------------------------------------------ all timeframes together
def combine(reads: dict) -> dict:
    """One reading of every timeframe: weighted lean (bigger timeframes weigh more), where they agree or fight."""
    w = cfg()["tf_weight"]
    tot = sum(w.get(tf, 1) for tf, r in reads.items() if r.get("items") is not None and not r.get("empty"))
    if not tot:
        return {"bias": 0, "lean": "balanced", "agree": [], "fight": [], "sig": ""}
    s = sum(w.get(tf, 1) * r.get("score", 0) for tf, r in reads.items() if not r.get("empty"))
    bias = int(round(s / tot * 100))
    ups = [tf for tf, r in reads.items() if r.get("score", 0) >= 0.2]
    dns = [tf for tf, r in reads.items() if r.get("score", 0) <= -0.2]
    lean = "buyers" if bias >= 15 else "sellers" if bias <= -15 else "balanced"
    sig = "|".join(f"{tf}:{r.get('control')}:" + ",".join(f"{it['id']}{it['side']}" for it in r.get("items") or [])
                   for tf, r in sorted(reads.items()))
    return {"bias": bias, "lean": lean, "up_tfs": ups, "down_tfs": dns, "sig": sig}


def watch_levels(reads: dict) -> tuple:
    """The nearest 'up above' and 'down below' prices the active behaviours name (small timeframes first)."""
    for tf in ("1m", "3m", "5m", "15m", "1H", "4H", "1D", "1W"):
        r = reads.get(tf) or {}
        for it in r.get("items") or []:
            if it["id"] == "range_trap" and it["side"] == 0 and it.get("hi"):
                return _p(it["hi"]), _p(it["lo"])
    return None, None


def system_message(reads: dict, comb: dict, small=("1m", "3m", "5m")) -> list:
    """Short, point-to-point lines: each small timeframe's scene, the strongest behaviour now (small timeframes), the
    bigger picture (and its strongest behaviour), the levels to watch. The lean itself is shown beside it."""
    lines = []
    for tf in small:
        r = reads.get(tf)
        if r and not r.get("empty"):
            lines.append(f"{tf}: {r['line']}")

    def best(tfs):
        tops = [(it["strength"] * (1.0 if it["side"] else 0.8), tf, it) for tf in tfs for it in (reads.get(tf) or {}).get("items") or []
                if it["id"] != "yclose"]
        return max(tops, key=lambda z: z[0]) if tops else None
    b = best([tf for tf in small if tf in reads])
    if b:
        lines.append(f"Now ({b[1]}): {b[2]['now']} → {b[2]['next']}")
    big = [tf for tf in ("15m", "1H", "4H", "1D", "1W") if tf in reads and not reads[tf].get("empty") and tf not in small]
    if big:
        lines.append("Bigger picture: " + ", ".join(f"{tf} {reads[tf]['control']}" for tf in big))
        bb = best(big)
        if bb:
            lines.append(f"{bb[1]}: {bb[2]['now']} → {bb[2]['next']}")
    up, dn = watch_levels(reads)
    if up and dn:
        lines.append(f"Watch {up} up / {dn} down: the side that breaks traps the other")
    return lines[:7]


def ai_prompt(sym: str, reads: dict, comb: dict) -> str:
    parts = []
    for tf, r in reads.items():
        if r.get("empty"):
            continue
        its = "; ".join(f"{it['now']} -> {it['next']}" for it in (r.get("items") or [])[:4])
        parts.append(f"{tf}: {r['line']}. {its}")
    return ("You read buyer / seller behaviour for an Indian F&O trader. Combine these readings of " + sym +
            " into 3 or 4 very short bullet points (max 16 words each, simple English, no jargon): who is in control on the "
            "small timeframes, what the bigger timeframes say, the levels to watch and what a break would mean. Use only "
            "the facts given. No advice to place real orders. Plain text, each point starting with '- '.\n\n"
            + "\n".join(parts) + f"\nOverall lean: {comb.get('lean')} ({comb.get('bias', 0):+d}).")


class MindAI:
    """The AI message (fast tier, its own small daily allowance): asked in the background only when the set of active
    behaviours changes, at most every min_gap_s per symbol; the system message shows until (and unless) it is ready."""

    def __init__(self):
        self.s: dict = {}
        self.lock = threading.Lock()

    def message(self, ai, sym, reads, comb):
        c = cfg()["ai"] or {}
        if not ai or not c.get("on") or not getattr(ai, "enabled", lambda: False)():
            return None
        sig, now = comb.get("sig"), time.time()
        with self.lock:
            st = self.s.setdefault(sym, {"sig": None, "text": None, "at": 0.0, "busy": False, "asked": 0.0})
            text = st["text"] if st["sig"] == sig else None
            if st["sig"] != sig and not st["busy"] and now - st["asked"] >= float(c.get("min_gap_s") or 120):
                st["busy"], st["asked"] = True, now
                threading.Thread(target=self._ask, args=(ai, sym, sig, ai_prompt(sym, reads, comb), int(c.get("per_day") or 60)),
                                 daemon=True).start()
        return text

    def _ask(self, ai, sym, sig, prompt, per_day):
        try:
            t = ai.ask(prompt, 220, auto=True, budget=("mindgame", per_day), task="mindgame")
        except Exception:
            t = None
        with self.lock:
            st = self.s[sym]
            st["busy"] = False
            if t and t.strip():
                st.update(sig=sig, text=t.strip()[:700], at=time.time())

    def wait(self, sym, timeout=5.0):              # tests
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if not self.s.get(sym, {}).get("busy"):
                    return
            time.sleep(0.02)


# ------------------------------------------------------------------ the record
SQL = """CREATE TABLE IF NOT EXISTS mind_reads (
  source TEXT, day TEXT, sym TEXT, tf TEXT, bullet TEXT, side INTEGER, t REAL, price REAL, unit REAL, text TEXT,
  result TEXT, move REAL, graded_at REAL,
  PRIMARY KEY (source, sym, tf, bullet, side, t));
CREATE INDEX IF NOT EXISTS mind_reads_open ON mind_reads(source, sym, tf, result);"""


class Book:
    """Every behaviour that newly becomes active on a closed candle is written down with its price; a few candles later
    (grade_bars of that timeframe) it is graded: right = price went a typical candle (grade_units) its way first,
    wrong = against first, flat = neither (or both inside one candle, order unknown). right % = right / (right + wrong),
    so a coin flip scores about 50%. Information-only bullets (side 0) are not graded."""

    def __init__(self, store, source):
        self.store, self.source = store, source
        self.active: dict = {}
        with store.lock:
            store.db.executescript(SQL)
            store.db.commit()

    def observe(self, sym, tf, r) -> int:
        if not r or r.get("empty") or r.get("forming"):
            return 0
        now_keys = {(it["id"], it["side"]) for it in r.get("items") or [] if it["side"]}
        new = now_keys - self.active.get((sym, tf), set())
        self.active[(sym, tf)] = now_keys
        rows = [(self.source, dt.datetime.fromtimestamp(r["at"], IST).date().isoformat(), sym, tf, it["id"], it["side"], r["at"],
                 r["close"], r["unit"], f"{it['now']} -> {it['next']}"[:300])
                for it in r.get("items") or [] if (it["id"], it["side"]) in new]
        if rows:
            with self.store.lock:
                self.store.db.executemany("INSERT OR IGNORE INTO mind_reads(source, day, sym, tf, bullet, side, t, price, unit, text) "
                                          "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                self.store.db.commit()
        return len(rows)

    def grade(self, sym, tf, rows, now: float | None = None) -> int:
        """rows: closed candles [t, o, h, l, c, ...] of this timeframe."""
        g = int((cfg()["grade_bars"] or {}).get(tf, 6))
        gu = float(cfg()["grade_units"] or 1.0)
        with self.store.lock:
            todo = self.store.db.execute("SELECT bullet, side, t, price, unit FROM mind_reads WHERE source=? AND sym=? AND tf=? "
                                         "AND result IS NULL", (self.source, sym, tf)).fetchall()
        if not todo or not rows:
            return 0
        a = np.asarray([r[:5] for r in rows], dtype=float)
        a = a[np.argsort(a[:, 0], kind="stable")]
        if now is not None and len(a) and is_forming(tf, a[-1, 0], now):
            a = a[:-1]
        done = []
        for bid, side, t, price, u in todo:
            j = int(np.searchsorted(a[:, 0], t, side="right"))
            nxt = a[j:j + g]
            res, mv = None, None
            for b in nxt:
                fav = (b[2] - price) if side > 0 else (price - b[3])
                adv = (price - b[3]) if side > 0 else (b[2] - price)
                if adv >= gu * u and fav >= gu * u:   # both inside one candle: which came first is unknown
                    res, mv = "flat", (b[4] - price) * side / u
                    break
                if adv >= gu * u:
                    res, mv = "wrong", -adv / u
                    break
                if fav >= gu * u:
                    res, mv = "right", fav / u
                    break
            if res is None and len(nxt) >= g:
                res, mv = "flat", (nxt[-1][4] - price) * side / u
            if res:
                done.append((res, round(float(mv), 2), time.time(), self.source, sym, tf, bid, side, t))
        if done:
            with self.store.lock:
                self.store.db.executemany("UPDATE mind_reads SET result=?, move=?, graded_at=? WHERE source=? AND sym=? AND tf=? "
                                          "AND bullet=? AND side=? AND t=?", done)
                self.store.db.commit()
        return len(done)

    def stats(self, days: int = 60) -> dict:
        """{bullet: {...}, (bullet, tf): {...}} with n graded, right, wrong, flat, right_pct, status."""
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        with self.store.lock:
            rows = self.store.db.execute("SELECT bullet, tf, result, COUNT(*) FROM mind_reads WHERE source=? AND day>=? AND result IS NOT NULL "
                                         "GROUP BY bullet, tf, result", (self.source, since)).fetchall()
        tw = cfg()["trust"]
        out: dict = {}
        for bid, tf, res, k in rows:
            for key in (bid, (bid, tf)):
                d = out.setdefault(key, {"n": 0, "right": 0, "wrong": 0, "flat": 0})
                d["n"] += k
                d[res] = d.get(res, 0) + k
        for d in out.values():
            dec = d["right"] + d["wrong"]
            d["right_pct"] = round(d["right"] / dec * 100) if dec else 0
            d["status"] = ("trusted" if d["n"] >= tw["min_n"] and d["right_pct"] >= tw["min_right_pct"] else
                           "weak" if d["n"] >= tw["min_n"] and d["right_pct"] < 45 else "on trial")
        return out

    def table(self, days: int = 60) -> list:
        st = self.stats(days)
        ok, later, _ = bullets()
        rows = []
        for b in ok + later:
            d = st.get(b["id"]) or {"n": 0, "right": 0, "wrong": 0, "flat": 0, "right_pct": None, "status": "on trial"}
            tfs = {k[1]: v for k, v in st.items() if isinstance(k, tuple) and k[0] == b["id"]}
            rows.append({"id": b["id"], "n_bullet": b.get("n"), "who": b.get("who"), "activity": b.get("activity"),
                         "prediction": b.get("prediction"), "source": b.get("source"), "later": b.get("status") == "later",
                         "alerts": False, **d, "by_tf": tfs})
        return rows


def to_rows(candles: list, volume: list | None = None) -> list:
    """Chart candles ({time (IST-shifted), open, high, low, close}) back to [t, o, h, l, c, v] rows."""
    v = {x["time"]: x.get("value", 0) for x in volume or []}
    return [[c["time"] - IST_SHIFT, c["open"], c["high"], c["low"], c["close"], v.get(c["time"], 0)] for c in candles
            if c.get("open") is not None and c.get("close") is not None]


class MindGame:
    """The engine's part: every minute in market hours (in its own thread, never holding up the scanner) the indices'
    1m / 3m / 5m / 15m scenes are read, new behaviours written down and earlier ones graded. The chart reads the same."""

    def __init__(self, engine, store, source, log=print):
        self.e, self.log = engine, log
        self.book = Book(store, source) if store is not None else None
        self.ai = MindAI()
        self.busy = False
        self.big: dict = {}                       # sym -> {"at": ts, "reads": {tf: read}} from the timeframe charts
        self.last: dict = {}
        self._st, self._st_at = {}, 0.0

    def stats(self) -> dict:
        if self.book is None:
            return {}
        if time.time() - self._st_at > 60:
            try:
                self._st, self._st_at = self.book.stats(), time.time()
            except Exception:
                pass
        return self._st

    def extra(self, sym) -> dict:
        m = (getattr(self.e, "metrics", None) or {}).get(sym) or {}
        o = m.get("opt") or {}
        return {"pdc": m.get("cp"), "oi_call": o.get("resistance"), "oi_put": o.get("support")}

    def small(self, sym, tfs=("1m", "3m", "5m"), now: float | None = None) -> tuple[dict, dict]:
        """The scenes of the small timeframes from the chart's own 1-minute candles (a few days, for yesterday's levels)."""
        from .mtf import resample
        mtf = getattr(self.e, "mtf", None)
        if mtf is None:
            from .mtf import MTF
            mtf = self.e.mtf = MTF(self.e)
        d = mtf.candles(sym, "1m", tail=1200)
        r1 = to_rows(d.get("candles") or [])
        rows = {"1m": r1}
        for tf, k in (("3m", 3), ("5m", 5), ("15m", 15)):
            if tf in tfs:
                rows[tf] = resample(r1, k)
        ex, st = self.extra(sym), self.stats()
        gaps = np.diff([r[0] for r in r1[-60:]])
        real1 = bool(len(gaps)) and float(np.median(gaps)) <= 60      # true 1-minute candles (not a 5-minute fallback)
        out = {}
        for tf in tfs:
            if tf not in rows:
                continue
            if tf in ("1m", "3m") and not real1:
                out[tf] = {"tf": tf, "items": [], "score": 0.0, "empty": "No 1-minute candles here yet (they come from the live feed / Dhan)"}
                rows[tf] = []
            else:
                out[tf] = read(rows[tf], tf, now, ex, st)
        return out, rows

    def tick(self, now):
        if self.busy or self.book is None:
            return
        self.busy = True
        threading.Thread(target=self._work, args=(now.timestamp() if hasattr(now, "timestamp") else float(now),), daemon=True).start()

    def _work(self, now):
        try:
            c = cfg()
            for sym in c["record_syms"]:
                try:
                    reads, rows = self.small(sym, tuple(c["record_tfs"]), now)
                    for tf, r in reads.items():
                        self.book.grade(sym, tf, rows.get(tf) or [], now)
                        self.book.observe(sym, tf, r)
                    self.last[sym] = {"at": now, "n": sum(len(r.get("items") or []) for r in reads.values())}
                except Exception as ex:
                    if self.e is not None and hasattr(self.e, "_every") and self.e._every(f"mind_err:{sym}", 600, clock_time=False):
                        self.log(f"MindGame {sym} skipped: {ex}")
        finally:
            self.busy = False

    def note_big(self, sym, reads: dict, rows_by_tf: dict | None = None, now: float | None = None):
        """The timeframe charts' scenes (5m ... 1W): kept for the main chart's bigger picture; 1H and up recorded too."""
        self.big[sym] = {"at": time.time(), "reads": reads}
        if self.book is None:
            return
        rec = set(cfg()["record_tfs"])
        for tf, r in reads.items():
            if tf in rec:
                continue
            try:
                self.book.grade(sym, tf, (rows_by_tf or {}).get(tf) or [], now)
                self.book.observe(sym, tf, r)
            except Exception:
                pass

    def view(self, sym, now: float | None = None) -> dict:
        reads, _ = self.small(sym, ("1m", "3m", "5m"), now)
        b = self.big.get(sym) or {}
        big = {tf: r for tf, r in (b.get("reads") or {}).items() if tf not in reads} if time.time() - b.get("at", 0) < 600 else {}
        allr = {**reads, **big}
        comb = combine(allr)
        sysm = system_message(allr, comb)
        txt = self.ai.message(getattr(self.e, "ai", None), sym, allr, comb)
        return {"sym": sym, "reads": reads, "big": {tf: {k: r.get(k) for k in ("tf", "control", "phase", "line", "score")} for tf, r in big.items()},
                "combined": comb, "message": txt.splitlines() if txt else sysm, "by": "ai" if txt else "system",
                "system": sysm, "note": "MindGame on trial: buyer / seller behaviour read from the candles (activity, measured in each "
                                        "timeframe's typical candle). Recorded and graded; no alert until a behaviour's record is positive."}
