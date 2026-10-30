"""Position model, costs and exit simulation, shared by the backtest and the live paper runner."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

N_MIN = 375

# per-leg slippage per side, in option price points: max(abs, pct x price)
SLIP = {"NIFTY": (0.30, 0.004), "BANKNIFTY": (0.80, 0.004), "FINNIFTY": (0.40, 0.005), "MIDCPNIFTY": (0.30, 0.006)}


def slip(sym, px):
    a, p = SLIP.get(sym, (0.25, 0.01))           # stock options: wider spreads, 1% of the price per side
    return max(a, p * px)


def charges(fills: list, lot_units: int) -> float:
    """Indian F&O option charges for a list of fills [(side, price)] where side is +1 buy / -1 sell, per lot."""
    buy = sum(p for s, p in fills if s > 0) * lot_units
    sell = sum(p for s, p in fills if s < 0) * lot_units
    orders = len(fills)
    brokerage = 20.0 * orders
    stt = 0.0015 * sell                 # 0.15% on the sell premium (from 1 Apr 2026)
    exch = 0.0003503 * (buy + sell)
    sebi = 10 / 1e7 * (buy + sell)
    gst = 0.18 * (brokerage + exch + sebi)
    stamp = 0.00003 * buy
    return brokerage + stt + exch + sebi + gst + stamp


@dataclass
class Leg:
    strike: float
    side: str             # CE / PE
    q: int                # +1 bought, -1 sold
    entry: float = math.nan
    exit: float = math.nan
    exit_m: int | None = None
    sl: float | None = None
    key: str | None = None   # live instrument key


@dataclass
class Position:
    sym: str
    family: str
    legs: list
    exit_m: int = 360                    # 15:15
    groups: list = field(default_factory=list)   # [[short_idx, wing_idx], ...] closed together on a leg stop
    leg_sl_pct: float | None = None      # stop on each short leg at entry x (1 + pct)
    trail_to_cost: bool = False          # after one side stops, move the other side's stop to its entry
    stop_x: float | None = None          # combined: exit when loss >= stop_x x |net premium|
    target_x: float | None = None        # combined: exit when profit >= target_x x |net premium|
    spot_stop: tuple | None = None       # (level, "below"/"above"): exit all if spot crosses
    spot_target: tuple | None = None     # (level, "above"/"below"): take profit when spot reaches it
    entry_m: int = 0
    reason: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def net_premium(self) -> float:      # >0 credit received, <0 debit paid (points)
        return -sum(l.q * l.entry for l in self.legs)

    def max_loss_pts(self) -> float:
        """Structural worst case at expiry (points), for sizing."""
        ks = sorted({l.strike for l in self.legs})
        grid = [ks[0] * 0.9] + ks + [ks[-1] * 1.1]
        worst = 0.0
        for S in grid:
            v = sum(l.q * (max(0.0, S - l.strike) if l.side == "CE" else max(0.0, l.strike - S)) for l in self.legs)
            worst = min(worst, v + self.net_premium)
        return -worst

    def planned_loss_pts(self) -> float:
        """Loss if the planned stops trigger, with a 50% buffer for gaps and slippage; never above the worst case."""
        worst = self.max_loss_pts()
        prem = abs(self.net_premium)
        if self.stop_x:
            p = self.stop_x * prem * 1.5
        elif self.leg_sl_pct:
            sides = [self.leg_sl_pct * l.entry for l in self.legs if l.q < 0]
            p = max(sum(sides), max(sides) * 1.5) if sides else self.max_loss_pts()
        else:
            p = worst
        return min(worst, max(p, 0.25 * worst))

    def margin_est(self, spot, lot) -> float:
        """Rough margin per lot: max loss for defined-risk shorts (plus a buffer), premium for debit spreads."""
        if self.net_premium > 0:
            return max(self.max_loss_pts() * 1.15, 0.02 * spot) * lot
        return -self.net_premium * lot


def fill_entry(pos: Position, price_at) -> bool:
    for l in pos.legs:
        p = price_at(l.strike, l.side)
        if p is None or math.isnan(p) or p <= 0:
            return False
        s = slip(pos.sym, p)
        l.entry = p + s if l.q > 0 else max(0.05, p - s)
        if l.q < 0 and pos.leg_sl_pct:
            l.sl = l.entry * (1 + pos.leg_sl_pct)
    return True


def simulate(day, pos: Position) -> dict | None:
    """Run the position through the day's minute data (backtest). Fills at minute close +- slippage;
    a short leg's stop triggers on the minute high and fills 2% worse than the stop."""
    m0 = pos.entry_m
    mx = min(pos.exit_m, N_MIN - 1)
    if mx <= m0 + 1:
        return None
    if not fill_entry(pos, lambda k, s: day.price(k, s, m0)):
        return None
    T = np.arange(m0 + 1, mx + 1)
    C, H = [], []
    for l in pos.legs:
        a = day.px.get((float(l.strike), l.side))
        if a is None:
            return None
        c = a["c"][T]
        h = a["h"][T]
        c = np.where(np.isnan(c), l.entry, c)
        h = np.where(np.isnan(h), c, h)
        C.append(c)
        H.append(h)
    C, H = np.array(C), np.array(H)
    alive = np.ones(len(pos.legs), dtype=bool)
    exit_at = np.full(len(pos.legs), -1)
    exit_px = np.full(len(pos.legs), np.nan)
    reason = "time"
    prem = abs(pos.net_premium) or 1.0
    spot = day.spot[T]
    groups = pos.groups or [[i] for i in range(len(pos.legs))]
    for j in range(len(T)):
        # 1) per-leg stops on short legs (close the leg's group)
        if pos.leg_sl_pct:
            for g in groups:
                si = next((i for i in g if pos.legs[i].q < 0), None)
                if si is None or not alive[si] or pos.legs[si].sl is None:
                    continue
                if H[si, j] >= pos.legs[si].sl:
                    for i in g:
                        if alive[i]:
                            alive[i] = False
                            exit_at[i] = j
                            exit_px[i] = pos.legs[si].sl * 1.02 if i == si else C[i, j]
                    reason = "leg stop"
                    if pos.trail_to_cost:
                        for g2 in groups:
                            for i in g2:
                                if alive[i] and pos.legs[i].q < 0:
                                    pos.legs[i].sl = min(pos.legs[i].sl or 1e18, pos.legs[i].entry)
            if not alive.any():
                break
        # 2) combined stop / target on the live legs' marked-to-market (closes)
        mtm = sum(pos.legs[i].q * ((C[i, j] if alive[i] else exit_px[i]) - pos.legs[i].entry) for i in range(len(pos.legs)))
        hit = None
        if pos.stop_x and mtm <= -pos.stop_x * prem:
            hit = "stop"
        elif pos.target_x and mtm >= pos.target_x * prem:
            hit = "target"
        elif pos.spot_stop and ((pos.spot_stop[1] == "below" and spot[j] < pos.spot_stop[0]) or
                                (pos.spot_stop[1] == "above" and spot[j] > pos.spot_stop[0])):
            hit = "spot stop"
        elif pos.spot_target and ((pos.spot_target[1] == "above" and spot[j] >= pos.spot_target[0]) or
                                  (pos.spot_target[1] == "below" and spot[j] <= pos.spot_target[0])):
            hit = "spot target"
        if hit:
            for i in range(len(pos.legs)):
                if alive[i]:
                    alive[i] = False
                    exit_at[i] = j
                    exit_px[i] = C[i, j]
            reason = hit
            break
    for i in range(len(pos.legs)):
        if alive[i]:
            exit_at[i] = len(T) - 1
            exit_px[i] = C[i, -1]
    fills = []
    gross = 0.0
    for i, l in enumerate(pos.legs):
        px = exit_px[i]
        s = slip(pos.sym, px)
        l.exit = px - s if l.q > 0 else px + s           # selling a long gets less, buying back a short costs more
        l.exit_m = int(T[exit_at[i]])
        gross += l.q * (l.exit - l.entry)
        fills += [(l.q, l.entry), (-l.q, l.exit)]
    return {"gross_pts": gross, "fills": fills, "reason": reason,
            "exit_m": int(max(l.exit_m for l in pos.legs)), "premium": pos.net_premium,
            "max_loss_pts": pos.max_loss_pts()}
