"""Positions built from strategy Plans: fills, exits, trailing, charges, and daily portfolio rules.
Used identically by the backtest and by live paper trading."""
from __future__ import annotations

from dataclasses import dataclass, field

SPREAD = 0.004      # half bid-ask + slippage as a share of the option price (min one tick)
TICK = 0.05


def fill(px: float, buy: bool) -> float:
    adj = max(TICK, px * SPREAD)
    return round(px + adj, 2) if buy else round(max(TICK, px - adj), 2)


def leg_costs(buy_px: float, sell_px: float, qty: int) -> float:
    """Round-trip charges for one option leg (₹20 per order discount broker)."""
    buy, sell = buy_px * qty, sell_px * qty
    brokerage = 40.0
    stt = 0.0015 * sell                  # 0.15% on the sell-side premium
    exch = 0.0003503 * (buy + sell)
    sebi = 10 / 1e7 * (buy + sell)
    gst = 0.18 * (brokerage + exch + sebi)
    stamp = 0.00003 * buy
    return brokerage + stt + exch + sebi + gst + stamp


@dataclass
class Position:
    sym: str
    strategy: str
    direction: str
    structure: str
    legs: list                      # [(strike, side, sign)]
    lot: int
    lots: int
    entry_ts: float
    entry_px: list                  # fill price per leg
    entry_spot: float
    stop_und: float | None
    target_und: float | None
    tp_frac: float | None
    sl_mult: float | None
    trail_r: float | None
    exit_min: int
    note: str = ""
    last_px: list = field(default_factory=list)
    best_spot: float | None = None
    exit_ts: float | None = None
    exit_px: list = field(default_factory=list)
    reason: str | None = None
    gross: float = 0.0
    costs: float = 0.0
    net: float = 0.0
    peak_pnl: float = 0.0

    @property
    def qty(self):
        return self.lot * self.lots

    @property
    def entry_value(self):
        """Net premium paid per unit (negative = credit received)."""
        return sum(s * p for (_, _, s), p in zip(self.legs, self.entry_px))

    def value(self, prices):
        return sum(s * p for (_, _, s), p in zip(self.legs, prices))

    def pnl(self, prices):
        return (self.value(prices) - self.entry_value) * self.qty

    def max_loss(self):
        """Worst case for defined-risk structures (per position), None for a plain long (premium)."""
        if self.structure == "credit_ic":
            k = sorted(x[0] for x in self.legs if x[1] == "CE")
            w = k[-1] - k[0]
            return (w + self.entry_value) * self.qty
        return self.entry_value * self.qty

    def update(self, minute: int, spot_hi: float, spot_lo: float, spot: float, prices: list):
        """Check exits on a new bar. prices: current option price per leg (None if unknown)."""
        if any(p is None for p in prices):
            prices = [p if p is not None else lp for p, lp in zip(prices, self.last_px or self.entry_px)]
        self.last_px = prices
        pnl = self.pnl(prices)
        self.peak_pnl = max(self.peak_pnl, pnl)
        reason = None
        if self.structure == "credit_ic":
            credit = -self.entry_value * self.qty
            if self.tp_frac and pnl >= self.tp_frac * credit:
                reason = "profit target"
            elif self.sl_mult and pnl <= -self.sl_mult * credit:
                reason = "stop loss"
            else:
                shorts = [(k, sd) for k, sd, s in self.legs if s < 0]
                for k, sd in shorts:
                    if (sd == "CE" and spot_hi >= k) or (sd == "PE" and spot_lo <= k):
                        reason = "short strike touched"
        else:
            up = self.direction == "up"
            self.best_spot = spot if self.best_spot is None else (max(self.best_spot, spot_hi) if up else min(self.best_spot, spot_lo))
            r = abs(self.entry_spot - self.stop_und) if self.stop_und else None
            if self.trail_r and r:
                gain = (self.best_spot - self.entry_spot) if up else (self.entry_spot - self.best_spot)
                if gain >= r:
                    trail = self.best_spot - self.trail_r * r if up else self.best_spot + self.trail_r * r
                    be = self.entry_spot
                    new = max(trail, be) if up else min(trail, be)
                    self.stop_und = max(self.stop_und, new) if up else min(self.stop_und, new)
            if self.stop_und is not None and ((up and spot_lo <= self.stop_und) or (not up and spot_hi >= self.stop_und)):
                reason = "stop" if (up and self.stop_und < self.entry_spot) or (not up and self.stop_und > self.entry_spot) else "trailing stop"
            elif self.target_und is not None and ((up and spot_hi >= self.target_und) or (not up and spot_lo <= self.target_und)):
                reason = "target"
        if reason is None and minute >= self.exit_min:
            reason = "time exit"
        return reason

    def close(self, ts, prices, reason):
        if any(p is None for p in prices):
            prices = [p if p is not None else lp for p, lp in zip(prices, self.last_px or self.entry_px)]
        self.exit_ts = ts
        self.reason = reason
        self.exit_px = [fill(p, buy=(s < 0)) for (_, _, s), p in zip(self.legs, prices)]
        self.gross = round(self.pnl(self.exit_px), 2)
        c = 0.0
        for (_, _, s), pin, pout in zip(self.legs, self.entry_px, self.exit_px):
            c += leg_costs(pin, pout, self.qty) if s > 0 else leg_costs(pout, pin, self.qty)
        self.costs = round(c, 2)
        self.net = round(self.gross - self.costs, 2)
        return self

    def as_dict(self):
        return {k: getattr(self, k) for k in ("sym", "strategy", "direction", "structure", "legs", "lot", "lots", "entry_ts",
                                               "entry_px", "entry_spot", "stop_und", "target_und", "exit_min", "note",
                                               "exit_ts", "exit_px", "reason", "gross", "costs", "net")}


def open_position(plan, sym, lot, lots, ts, spot, prices) -> Position | None:
    if any(p is None or p <= 0 for p in prices):
        return None
    entry = [fill(p, buy=(s > 0)) for (_, _, s), p in zip(plan.legs, prices)]
    return Position(sym=sym, strategy=plan.strategy, direction=plan.direction, structure=plan.structure, legs=plan.legs,
                    lot=lot, lots=lots, entry_ts=ts, entry_px=entry, entry_spot=spot, stop_und=plan.stop_und,
                    target_und=plan.target_und, tp_frac=plan.tp_frac, sl_mult=plan.sl_mult, trail_r=plan.trail_r,
                    exit_min=plan.exit_min, note=plan.note, last_px=list(prices), best_spot=spot)


# ---------------------------------------------------------------- daily rules
PORTFOLIO_DEFAULTS = {
    "capital": 100000,
    "risk_pct": 2.0,            # rupees at risk per trade (premium paid, or max loss of a spread) as % of capital
    "max_lots": 3,
    "max_loss_day_pct": 3.0,    # stop for the day after this loss
    "lock_start_pct": 1.5,      # once the day is up this much...
    "lock_giveback": 0.4,       # ...stop trading if it gives back 40% of the day's best
    "max_trades_day": 4,
    "max_open": 2,
    "margin_per_condor": 40000,  # rough hedged-selling margin per lot for sizing checks
}


class DayBook:
    """Daily P&L discipline: loss stop, profit lock, trade cap, one position per index."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = {**PORTFOLIO_DEFAULTS, **(cfg or {})}
        self.reset()

    def reset(self):
        self.booked = 0.0
        self.peak = 0.0
        self.trades = 0
        self.stopped = None

    def lots_for(self, plan_structure, entry_value_per_unit, lot, width=None) -> int:
        c = self.cfg
        budget = c["capital"] * c["risk_pct"] / 100
        if plan_structure == "credit_ic":
            risk_lot = max(1.0, (width - abs(entry_value_per_unit)) * lot) if width else c["margin_per_condor"]
            fits = int(c["capital"] * 0.8 // c["margin_per_condor"])
            return max(0, min(c["max_lots"], fits, int(budget // risk_lot) or 1)) if fits else 0
        risk_lot = abs(entry_value_per_unit) * lot * 0.5        # a stopped option trade loses ~half the premium
        afford = int(c["capital"] * 0.5 // max(1.0, abs(entry_value_per_unit) * lot))
        if afford < 1:
            return 0
        return max(1, min(c["max_lots"], afford, int(budget // max(1.0, risk_lot)) or 1))

    def can_open(self, open_positions: list, sym: str, unreal: float = 0.0):
        c = self.cfg
        if self.stopped:
            return False, self.stopped
        day = self.booked + unreal
        if day <= -c["capital"] * c["max_loss_day_pct"] / 100:
            self.stopped = "Daily loss limit reached"
            return False, self.stopped
        if self.peak >= c["capital"] * c["lock_start_pct"] / 100 and day <= self.peak * (1 - c["lock_giveback"]):
            self.stopped = "Profit locked for the day"
            return False, self.stopped
        if self.trades >= c["max_trades_day"]:
            return False, "Trade limit for the day"
        if len(open_positions) >= c["max_open"]:
            return False, "Max open positions"
        if any(p.sym == sym for p in open_positions):
            return False, "Already in this index"
        return True, None

    def mark(self, unreal: float):
        self.peak = max(self.peak, self.booked + unreal)

    def on_close(self, pos: Position):
        self.booked += pos.net
        self.peak = max(self.peak, self.booked)
