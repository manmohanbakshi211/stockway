"""P3: volatility context, dealer gamma (GEX) and trade structure."""
from __future__ import annotations

import datetime as dt
import math

from .clock import IST

R = 0.065


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_gamma(S, K, T, sig):
    if S <= 0 or K <= 0 or T <= 0 or sig <= 0:
        return 0.0
    st = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + sig * sig / 2) * T) / st
    return _npdf(d1) / (S * st)


def years_to(expiry: dt.date, now: dt.datetime) -> float:
    close = dt.datetime.combine(expiry, dt.time(15, 30), IST)
    return max((close - now).total_seconds(), 600) / (365 * 86400)


# ---------------------------------------------------------------- history-based
def hv(closes: list, n: int = 20) -> float | None:
    """Annualised realised volatility (%) of the last n daily closes."""
    xs = [c for c in closes if c][-(n + 1):]
    if len(xs) < n // 2 + 2:
        return None
    rs = [math.log(b / a) for a, b in zip(xs[:-1], xs[1:]) if a and b]
    if len(rs) < 3:
        return None
    mu = sum(rs) / len(rs)
    var = sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(var * 252) * 100


def percentile(value, history: list) -> float | None:
    hs = [h for h in history if h is not None]
    if value is None or len(hs) < 15:
        return None
    return round(sum(1 for h in hs if h <= value) / len(hs) * 100)


def daily_stats(candles: list) -> dict:
    """Upstox daily candles (newest first): [ts, o, h, l, c, vol, oi]."""
    cs = sorted(candles, key=lambda c: c[0])
    closes = [c[4] for c in cs]
    vols = [c[5] for c in cs[-20:] if c[5]]
    hv20 = hv(closes, 20)
    hv_hist = [hv(closes[max(0, i - 21):i], 20) for i in range(22, len(closes) + 1, 5)]
    today = dt.datetime.now(IST).date().isoformat()
    done = [x for x in cs if str(x[0])[:10] < today]
    prev = {"h": done[-1][2], "l": done[-1][3], "c": done[-1][4], "o": done[-1][1], "date": str(done[-1][0])[:10]} if done else None
    return {"prev": prev, "avg_vol": sum(vols) / len(vols) if vols else None, "hv20": hv20, "hv60": hv(closes, 60),
            "hv_pct": percentile(hv20, hv_hist), "close_1y_hi": max(closes) if closes else None,
            "close_1y_lo": min(closes) if closes else None, "days": len(closes)}


# ---------------------------------------------------------------- live option surface
def skew(rows: list) -> float | None:
    """IV of a ~25-delta put minus a ~25-delta call (vol points). Positive = puts dearer (fear)."""
    puts = [r["PE"] for r in rows if r.get("PE") and r["PE"].get("delta") is not None and r["PE"].get("iv")]
    calls = [r["CE"] for r in rows if r.get("CE") and r["CE"].get("delta") is not None and r["CE"].get("iv")]
    if not puts or not calls:
        return None
    p = min(puts, key=lambda x: abs(x["delta"] + 0.25))
    c = min(calls, key=lambda x: abs(x["delta"] - 0.25))
    if abs(p["delta"] + 0.25) > 0.15 or abs(c["delta"] - 0.25) > 0.15:
        return None
    return round(p["iv"] - c["iv"], 2)


def atm_iv_from_chain(chain: dict, spot: float) -> float | None:
    rows = chain.get("rows") or []
    if not rows:
        return None
    r = min(rows, key=lambda x: abs(x["strike"] - spot))
    ivs = [x["iv"] for x in (r.get("CE"), r.get("PE")) if x and x.get("iv")]
    return sum(ivs) / len(ivs) if ivs else None


def gex(chain: dict, spot: float, now: dt.datetime) -> dict | None:
    """Dealer gamma exposure from the full chain, standard convention: dealers long calls, short puts.
    Positive net gamma: dealers sell rallies and buy dips (moves get dampened, price pins).
    Negative: dealers chase moves (moves accelerate). Values in Rs crore per 1% move."""
    rows = chain.get("rows") or []
    if len(rows) < 8 or not spot:
        return None
    exp = dt.date.fromisoformat(chain["expiry"])
    T = years_to(exp, now)
    legs = []
    for r in rows:
        for side, sgn in (("CE", 1), ("PE", -1)):
            x = r.get(side)
            if not x or not x.get("oi") or not x.get("iv"):
                continue
            legs.append((r["strike"], sgn, x["oi"], x["iv"] / 100))
    if not legs:
        return None

    def net_at(S):
        return sum(sgn * bs_gamma(S, K, T, iv) * oi * S * S * 0.01 for K, sgn, oi, iv in legs)

    profile = {}
    for K, sgn, oi, iv in legs:
        g = sgn * bs_gamma(spot, K, T, iv) * oi * spot * spot * 0.01
        profile[K] = profile.get(K, 0.0) + g
    net = sum(profile.values())
    grid = [spot * (1 + k / 1000) for k in range(-50, 51, 2)]
    vals = [net_at(S) for S in grid]
    flip = None
    best = None
    for (s1, v1), (s2, v2) in zip(zip(grid, vals), zip(grid[1:], vals[1:])):
        if (v1 <= 0 < v2) or (v1 >= 0 > v2):
            x = s1 + (s2 - s1) * (abs(v1) / (abs(v1) + abs(v2)))
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    flip = best
    calls = {K: v for K, v in profile.items() if v > 0}
    puts = {K: v for K, v in profile.items() if v < 0}
    cr = 1e7
    return {"net_cr": round(net / cr, 1), "flip": round(flip, 1) if flip else None,
            "call_wall": max(calls, key=calls.get) if calls else None,
            "put_wall": min(puts, key=puts.get) if puts else None,
            "regime": "pinning" if net > 0 else "accelerating",
            "profile": [{"strike": K, "gex_cr": round(v / cr, 2)} for K, v in sorted(profile.items())]}


# ---------------------------------------------------------------- what to trade
def structure(direction: str, m: dict, ctx: dict) -> dict | None:
    """Buy the option, use a debit spread, or skip, given how expensive options are and the market regime."""
    if direction not in ("up", "down"):
        return None
    o = m.get("opt") or {}
    rows = o.get("rows") or []
    atm, step = o.get("atm"), o.get("step")
    if not rows or not atm or not step:
        return None
    side = "CE" if direction == "up" else "PE"
    by = {r["strike"]: r for r in rows}
    buy = (by.get(atm) or {}).get(side)
    far_k = atm + 2 * step if direction == "up" else atm - 2 * step
    sell = (by.get(far_k) or {}).get(side)
    iv_hv = ctx.get("iv_hv")
    iv_pct = ctx.get("iv_pct")
    dte = o.get("days_to_expiry")
    g = ctx.get("gex") or {}
    reasons = []
    exp_hv = iv_hv is not None and iv_hv >= 1.3
    exp_pct = iv_pct is not None and iv_pct >= 80
    expensive = exp_hv or exp_pct
    cheap = (iv_hv is not None and iv_hv <= 1.0 and not exp_pct) or (iv_pct is not None and iv_pct <= 25 and not exp_hv)
    late = dte == 0 and ctx.get("now") and ctx["now"].time() >= dt.time(13, 30)
    choice = "buy"
    if expensive:
        choice = "spread"
        reasons.append(f"options expensive (IV {iv_hv:.1f}× recent movement)" if exp_hv
                       else f"volatility near a 1-year high ({iv_pct}th percentile)")
    if late:
        choice = "spread"
        reasons.append("expiry afternoon: premium melts fast")
    if g.get("regime") == "pinning" and m.get("is_index") and (ctx.get("regime") or "").startswith(("Range", "Quiet")):
        choice = "skip"
        reasons.append("dealers dampen moves (positive gamma) in a range day")
    if cheap and choice == "buy":
        reasons.append(f"options cheap (IV {iv_hv:.2f}× recent movement)" if iv_hv is not None and iv_hv <= 1.0
                       else "volatility low in its 1-year range")
    if not reasons:
        reasons.append("options fairly priced")
    out = {"type": choice, "why": "; ".join(reasons), "side": side}
    if buy and buy.get("ltp"):
        lot = buy.get("lot") or m.get("lot") or 1
        out["buy"] = {"strike": atm, "ltp": buy["ltp"], "cost": round(buy["ltp"] * lot, 0)}
        if sell and sell.get("ltp"):
            debit = buy["ltp"] - sell["ltp"]
            width = abs(far_k - atm)
            out["spread"] = {"buy": atm, "sell": far_k, "debit": round(debit, 2), "cost": round(debit * lot, 0),
                             "max_profit": round((width - debit) * lot, 0),
                             "breakeven": round(atm + debit if direction == "up" else atm - debit, 2)}
    return out
