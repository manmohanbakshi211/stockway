"""Support and resistance from every source the app has, merged into a few clear levels.

A level that several sources agree on (put OI wall + yesterday's low + S1, say) is one zone with more weight than a
lone pivot. Sources and weights are bullets in SOURCES below; the merging is the same for any index or stock.
"""
from __future__ import annotations

SOURCES = {                                   # name: weight (how much a trader would trust it on its own)
    "Put OI wall": 3, "Call OI wall": 3, "Max pain": 1,
    "Yesterday's high": 2, "Yesterday's low": 2, "Yesterday's close": 1,
    "Pivot": 1, "R1": 1, "R2": 1, "S1": 1, "S2": 1,
    "Last week's high": 2, "Last week's low": 2, "20-day high": 2, "20-day low": 2,
    "52-week high": 3, "52-week low": 3, "All-time high": 3,
    "Day high": 1, "Day low": 1, "Round number": 1,
}


def _round_levels(spot: float) -> list:
    """Psychological round numbers near the price (every 500 for big indices, scaled for smaller prices)."""
    step = 500 if spot > 20000 else 100 if spot > 5000 else 50 if spot > 1000 else 10 if spot > 200 else 5
    base = int(spot // step) * step
    return [base - step, base, base + step, base + 2 * step]


def key_levels(spot: float | None, named: list, band_pct: float = 0.15, n: int = 3) -> dict:
    """named: [(source name, price)]. Returns {"support": [...], "resistance": [...]} nearest first, each
    {"price", "names", "strength", "dist_pct"}; levels within `band_pct` % of each other are merged into one zone."""
    if not spot:
        return {"support": [], "resistance": []}
    pts = [(nm, float(px)) for nm, px in named if isinstance(px, (int, float)) and px > 0 and abs(px / spot - 1) < 0.12]
    pts += [("Round number", float(x)) for x in _round_levels(spot)]
    pts.sort(key=lambda x: x[1])
    zones, cur = [], []
    for nm, px in pts:
        if cur and (px / cur[-1][1] - 1) * 100 > band_pct:
            zones.append(cur)
            cur = []
        cur.append((nm, px))
    if cur:
        zones.append(cur)
    out = []
    for z in zones:
        w = sum(SOURCES.get(nm, 1) for nm, _ in z)
        real = [x for x in z if x[0] != "Round number"] or z
        price = sum(px * SOURCES.get(nm, 1) for nm, px in real) / sum(SOURCES.get(nm, 1) for nm, _ in real)
        names = list(dict.fromkeys(nm for nm, _ in z))
        if names == ["Round number"]:
            w = 0.5                                          # a round number alone is a weak level
        out.append({"price": round(price, 2), "names": names, "strength": w, "dist_pct": round((price / spot - 1) * 100, 2)})
    gap = 0.0005 * spot                                      # the price itself is neither support nor resistance
    sup = sorted((z for z in out if z["price"] < spot - gap and z["strength"] >= 1), key=lambda z: -z["price"])[:n]
    res = sorted((z for z in out if z["price"] > spot + gap and z["strength"] >= 1), key=lambda z: z["price"])[:n]
    return {"support": sup, "resistance": res}


def strength_word(w: float) -> str:
    return "strong" if w >= 5 else "medium" if w >= 3 else "light"
