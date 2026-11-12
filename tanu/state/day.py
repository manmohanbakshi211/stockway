"""What a "normal day" looks like for an instrument, known before the day starts (point-in-time).

- med_range: median full-day range (% of the previous close) of the last `lookback` days;
- frac[m]: median share of the full-day range already reached by minute m of the session (m = 0..374), over the last
  `frac_days` days.
The live state of the day (`classify`) compares today's range so far with med_range x frac[m]: the day is
"quiet" (well below normal so far), "trend" (at least normal and most of it in one direction) or "range".
Thresholds are the same as the legacy situation picker (quiet < 0.6, trend: >= 0.8 with 60% efficiency).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

N_MIN = 375
QUIET, TREND_REL, TREND_EFF, FLAT_REL = 0.6, 0.8, 0.6, 0.15


@dataclass(frozen=True)
class DayContext:
    prev_close: float
    med_range: float            # % of the previous close
    frac: np.ndarray            # length N_MIN

    def expected_range_pct(self, m: int) -> float:
        return self.med_range * max(float(self.frac[min(max(m, 0), N_MIN - 1)]), 0.05)


def build_contexts(days: list, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, lookback: int = 20,
                   frac_days: int = 60, min_days: int = 10) -> dict:
    """days: sorted dates; highs / lows / closes: arrays [n_days, N_MIN]. Returns {day: DayContext} for every day that
    has enough earlier days (nothing from the day itself or later days is used)."""
    n = len(days)
    prev_close = np.r_[np.nan, closes[:-1, -1]]
    rng_full = (highs.max(1) - lows.min(1)) / prev_close * 100
    so_far = (np.maximum.accumulate(highs, 1) - np.minimum.accumulate(lows, 1)) / prev_close[:, None] * 100
    share = so_far / rng_full[:, None]
    out = {}
    for i in range(1, n):
        a, b = max(1, i - lookback), i
        if b - a < min_days:
            continue
        fa = max(1, i - frac_days)
        out[days[i]] = DayContext(prev_close=float(prev_close[i]), med_range=float(np.nanmedian(rng_full[a:b])),
                                  frac=np.nanmedian(share[fa:i], axis=0))
    return out


def classify(rng_pct: float, move_pct: float, m: int, ctx: DayContext) -> tuple[str, float, int]:
    """(day type, rel, move sign) from today's range so far and the move since the open (both % of the previous
    close) at minute m. move sign is 0 when the move is too small to call."""
    exp = ctx.expected_range_pct(m)
    rel = rng_pct / exp if exp > 0 else 0.0
    eff = abs(move_pct) / rng_pct if rng_pct > 0 else 0.0
    day = "quiet" if rel < QUIET else "trend" if (rel >= TREND_REL and eff >= TREND_EFF) else "range"
    sign = 0 if abs(move_pct) < FLAT_REL * exp else (1 if move_pct > 0 else -1)
    return day, rel, sign
