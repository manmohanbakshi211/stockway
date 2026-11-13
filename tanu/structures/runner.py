"""Replays multi-leg structures over the real 1-minute option prices (legacy expired-option files, read in place).

Fills: each leg at the next minute's option open after the entry minute; sold legs receive open x (1 - half spread)
- 1 tick, bought legs pay open x (1 + half spread) + 1 tick (ADR-0008). While open, every minute the structure is
valued at what it costs to close (sold legs bought back at close + half spread + tick, bought legs sold at close -
half spread - tick); a leg without a price that minute keeps its last one. Exits: stop (loss >= stop_x_credit x
credit), take (kept >= take_pct_credit of the credit), the exit time, or expiry: settled at intrinsic value from the
index close of expiry day (no spread). Charges per leg (dated facts). Margin: defined risk = the widest wing minus
the credit; otherwise notional x margin share (facts/margins.json), one side (a straddle can lose only one way).
Paper only (ADR-0001)."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..data.facts import Facts
from ..engine.options import OptionHistory

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TICK = 0.05


def _t(day: str, hhmm: str) -> int:
    return int(dt.datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp())


class Runner:
    def __init__(self, oh: OptionHistory, minutes: dict[str, pd.DataFrame], spread_pct: dict | None = None,
                 facts: Facts | None = None, lots: dict | None = None):
        """minutes: {sym: DataFrame t, c} index 1-minute closes (for spot at entry and the expiry settlement)."""
        self.oh, self.facts = oh, facts or Facts()
        self.sp = spread_pct or {}
        self.lots = lots or {}
        self.spot = {s: dict(zip(m["t"].astype(int), m["c"].astype(float))) for s, m in minutes.items()}
        self._days: dict[str, list[str]] = {}

    def days(self, sym: str) -> list[str]:
        if sym not in self._days:
            self._days[sym] = sorted(self.oh.days(sym))
        return self._days[sym]

    # -------------------------------------------------------------- calendar
    def plan(self, st, sym: str) -> list[tuple[str, str]]:
        """(entry day, expiry) pairs: the session `dte` sessions before each expiry that has data."""
        days = self.days(sym)
        if st.entry.dte is None:                           # every session, nearest expiry
            return [(d, self.oh.expiry(sym, d)) for d in days if self.oh.expiry(sym, d)]
        by_exp: dict[str, list[str]] = {}
        for d in days:
            e = self.oh.expiry(sym, d)
            if e:
                by_exp.setdefault(e, []).append(d)
        out = []
        for e, ds in sorted(by_exp.items()):
            ds = sorted(ds)
            if e not in ds:                                # no data for expiry day itself: cannot settle
                continue
            i = ds.index(e) - st.entry.dte
            if i >= 0:
                out.append((ds[i], e))
        return out

    # -------------------------------------------------------------- one structure
    def run_one(self, st, sym: str, day: str, expiry: str) -> dict:
        sp = self.sp.get(sym, 0.5) / 100
        t0 = _t(day, st.entry.at)
        spot = self.spot[sym].get(t0)
        row = {"structure": st.id, "sym": sym, "day": day, "expiry": expiry, "entry_t": t0, "spot": spot}
        if spot is None:
            return {**row, "skipped": "no index price at entry"}
        legs = []
        for lg in st.legs:
            want = spot * (1 + lg.pct / 100)
            ks = self.oh.strikes(sym, day, t0, lg.side)
            if not ks:
                return {**row, "skipped": "no option prices at entry"}
            k = min(ks, key=lambda x: (abs(x - want), x))
            if abs(k - want) / spot * 100 > st.max_strike_gap_pct:
                return {**row, "skipped": f"no {lg.side} strike near {want:,.0f} in the data"}
            legs.append({"action": lg.action, "side": lg.side, "strike": k})
        if len({(x["side"], x["strike"]) for x in legs}) < len(legs):
            return {**row, "skipped": "two legs on one strike (strikes too far apart in the data)"}
        fill_t = t0 + 60
        for x in legs:
            b = self.oh.bar(sym, day, x["strike"], x["side"], fill_t)
            if b is None:
                return {**row, "skipped": f"no price for {x['strike']:g} {x['side']} at the fill"}
            o = float(b[0])
            x["entry"] = max(o * (1 - sp / 2) - TICK, 0.05) if x["action"] == "sell" else o * (1 + sp / 2) + TICK
            x["last"] = float(b[3])
        credit = sum(x["entry"] if x["action"] == "sell" else -x["entry"] for x in legs)
        lot = self.lots.get(sym) or self.facts.lot(sym, day) or 1
        qty = lot * st.lots
        row.update(legs=legs, credit=round(credit, 2), qty=qty, entry_fill_t=fill_t)

        def cost_to_close() -> float:
            return sum(x["last"] * (1 + sp / 2) + TICK if x["action"] == "sell" else -(max(x["last"] * (1 - sp / 2) - TICK, 0))
                       for x in legs)

        # walk the minutes from the fill to the exit
        end_day = day if st.exit.at != "expiry" else expiry
        end_t = _t(end_day, "15:29") if st.exit.at == "expiry" else _t(day, st.exit.at)
        days = [d for d in self.days(sym) if day <= d <= end_day]
        reason, exit_t, worst = None, None, 0.0
        for d in days:
            a = _t(d, "09:15")
            for t in range(max(a, fill_t), min(_t(d, "15:29"), end_t) + 1, 60):
                for x in legs:
                    b = self.oh.bar(sym, d, x["strike"], x["side"], t)
                    if b is not None:
                        x["last"] = float(b[3])
                close = cost_to_close()
                pnl_u = credit - close
                worst = min(worst, pnl_u)
                if st.exit.stop_x_credit and credit > 0 and -pnl_u >= st.exit.stop_x_credit * credit:
                    reason, exit_t = "stop", t
                elif st.exit.take_pct_credit and credit > 0 and pnl_u >= st.exit.take_pct_credit / 100 * credit:
                    reason, exit_t = "take", t
                elif t >= end_t and st.exit.at != "expiry":
                    reason, exit_t = "time", t
                if reason:
                    break
            if reason:
                break
        if reason:
            for x in legs:
                x["exit"] = x["last"] * (1 + sp / 2) + TICK if x["action"] == "sell" else max(x["last"] * (1 - sp / 2) - TICK, 0)
        else:                                               # held to expiry: settle at intrinsic value
            s_close = self.spot[sym].get(_t(expiry, "15:29")) or self.spot[sym].get(_t(expiry, "15:28"))
            if s_close is None:
                return {**row, "skipped": "no index close on expiry day"}
            reason, exit_t = "expiry", _t(expiry, "15:29")
            for x in legs:
                x["exit"] = max(s_close - x["strike"], 0) if x["side"] == "CE" else max(x["strike"] - s_close, 0)
            row["settle_spot"] = s_close
        charges = 0.0
        for x in legs:
            buy_px, sell_px = (x["exit"], x["entry"]) if x["action"] == "sell" else (x["entry"], x["exit"])
            charges += self.facts.option_charges(buy_px, sell_px, qty, d if reason != "expiry" else expiry)
        pnl = sum((x["entry"] - x["exit"]) if x["action"] == "sell" else (x["exit"] - x["entry"]) for x in legs) * qty - charges
        if st.defined_risk:
            widths = []
            for side in ("CE", "PE"):
                s_ = [x["strike"] for x in legs if x["side"] == side and x["action"] == "sell"]
                b_ = [x["strike"] for x in legs if x["side"] == side and x["action"] == "buy"]
                if s_ and b_:
                    widths.append(max(abs(bb - ss) for ss in s_ for bb in b_))
            margin = max(max(widths) - credit, credit * 0.1) * qty
        else:
            margin = spot * qty * (self.facts.margin_pct(sym, day) or 0.2)
        row.update(reason=reason, exit_t=exit_t, charges=round(charges, 2), pnl_rs=round(pnl, 2), margin=round(margin),
                   pct=round(pnl / margin * 100, 3) if margin else None, worst_rs=round(worst * qty, 2),
                   days_held=len(days), legs=[{k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.items()} for x in legs])
        return row

    def run(self, structures, syms) -> list[dict]:
        out = []
        for st in structures:
            for sym in syms:
                if sym not in st.symbols or sym not in self.spot:
                    continue
                for day, exp in self.plan(st, sym):
                    out.append(self.run_one(st, sym, day, exp))
        return out
