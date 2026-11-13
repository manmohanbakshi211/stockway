"""Paper accounts: listeners on the engine (ADR-0004). When the engine fills a position, each account decides whether
it takes that trade (its setups and its risk rules), and journals the trade or the refusal with the reason. Paper
only: nothing here can reach a broker (ADR-0001).

An account is "buy" (takes bought options) or "sell" (takes the sold-option version "<id>@sell" of each signal).
Risk rules (all optional): max_open, max_trades_day, daily_loss_pct (of capital: stop for the day), stop_after_losses
(consecutive losing trades: stop for the day), max_premium_pct (premium paid <= this % of capital), max_margin_pct
(selling: margin of all open sold options <= this % of capital; margin from facts/margins.json), one_per_group (no
two open positions in the same direction in one group, e.g. Nifty and Bank Nifty), no_entry_after ("HH:MM")."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from ..core import log
from ..data.facts import Facts
from ..journal.store import Journal

SPEC = Path(__file__).with_name("accounts.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("paper")


class Account:
    def __init__(self, spec: dict, journal: Journal, groups: dict | None = None, facts: Facts | None = None):
        self.spec, self.journal, self.facts = spec, journal, facts or Facts()
        self.id, self.risk = spec["id"], spec.get("risk", {})
        self.kind = spec.get("kind", "buy")
        self.group_of = {s: g for g, syms in (groups or {}).items() for s in syms}
        self.day = None
        self.open: dict[int, dict] = {}
        self._reset(None)

    def _reset(self, day):
        self.day, self.pnl_today, self.trades_today, self.losses_in_row, self.stopped = day, 0.0, 0, 0, None

    def wants(self, setup_status: str, setup_id: str) -> bool:
        s = self.spec.get("setups", "all")
        if s == "all":
            return True
        if s == "trial+live":
            return setup_status in ("trial", "live")
        return setup_id in s

    def _lot(self, engine, p) -> int:
        return engine.lots.get(p.sym) or self.facts.lot(p.sym, p.day) or 1

    def margin(self, engine, p) -> float:
        """Margin blocked by a sold option (paper estimate): notional x margin share."""
        pct = self.facts.margin_pct(p.sym, p.day) or 0.2
        spot = p.und_entry or p.state.get("spot") or p.strike
        return spot * self._lot(engine, p) * self.spec.get("lots", 1) * pct

    def refusal(self, engine, p) -> str | None:
        r = self.risk
        if self.spec.get("symbols") and p.sym not in self.spec["symbols"]:
            return f"{p.sym} is not traded by this account"
        if self.stopped:
            return f"stopped for the day: {self.stopped}"
        if r.get("no_entry_after"):
            hh, mm = map(int, r["no_entry_after"].split(":"))
            t = dt.datetime.fromtimestamp(p.entry_t, IST)
            if (t.hour, t.minute) > (hh, mm):
                return f"no new trades after {r['no_entry_after']}"
        if r.get("max_open") is not None and len(self.open) >= r["max_open"]:
            return f"already {len(self.open)} open (max {r['max_open']})"
        if r.get("max_trades_day") is not None and self.trades_today >= r["max_trades_day"]:
            return f"already {self.trades_today} trades today (max {r['max_trades_day']})"
        paid = p.entry * self._lot(engine, p) * self.spec.get("lots", 1)
        if self.kind == "buy" and r.get("max_premium_pct") is not None and paid > self.spec["capital"] * r["max_premium_pct"] / 100:
            return f"premium ₹{paid:,.0f} is over {r['max_premium_pct']}% of capital"
        if self.kind == "sell" and r.get("max_margin_pct") is not None:
            used = sum(o.get("margin", 0) for o in self.open.values()) + self.margin(engine, p)
            if used > self.spec["capital"] * r["max_margin_pct"] / 100:
                return f"margin ₹{used:,.0f} would be over {r['max_margin_pct']}% of capital"
        if r.get("one_per_group"):
            g = self.group_of.get(p.sym)
            if g and any(o["group"] == g and o["d"] == p.d for o in self.open.values()):
                return f"already holding the same direction in group '{g}'"
        return None

    def on_fill(self, engine, p) -> None:
        if getattr(p, "kind", "buy") != self.kind:
            return
        base = p.setup[:-5] if p.setup.endswith("@sell") else p.setup
        setup = next((s for s in engine.setups if s.id == base), None)
        if setup is None or not self.wants(setup.status, setup.id):
            return
        if p.day != self.day:
            self._reset(p.day)
        why = self.refusal(engine, p)
        if why:
            self.journal.blocked(self.id, p.setup, p.sym, p.day, p.d, p.entry_t, why)
            return
        self.open[id(p)] = {"group": self.group_of.get(p.sym), "d": p.d,
                            "margin": self.margin(engine, p) if self.kind == "sell" else 0.0}
        self.trades_today += 1

    def on_close(self, engine, p) -> None:
        held = self.open.pop(id(p), None)
        if held is None:
            return
        lot, lots = self._lot(engine, p), self.spec.get("lots", 1)
        qty = lot * lots
        sg = -1 if self.kind == "sell" else 1                       # a sold option gains when its price falls
        row = {"account": self.id, "setup": p.setup, "kind": self.kind, "margin": round(held.get("margin", 0.0)),
               "sym": p.sym, "day": p.day, "d": p.d, "side": p.side,
               "strike": p.strike, "entry_t": p.entry_t, "exit_t": p.exit_t, "entry": p.entry, "exit": p.exit,
               "lots": lots, "lot": lot, "qty": qty, "reason": p.reason, "und_entry": p.und_entry, "und_exit": p.und_exit,
               "state": {k: v for k, v in p.state.items() if k in ("day", "rel", "burst", "twap_rel", "m", "spot")}}
        if p.exit is not None:
            buy_px, sell_px = (p.exit, p.entry) if sg < 0 else (p.entry, p.exit)
            ch = self.facts.option_charges(buy_px, sell_px, qty, p.day)
            pnl = sg * (p.exit - p.entry) * qty - ch
            best, worst = (p.trough, p.peak) if sg < 0 else (p.peak, p.trough)
            row.update(charges=ch, pnl_rs=round(pnl, 2), pct=round(pnl / (p.entry * qty) * 100, 3),
                       mfe_pct=round(sg * (best / p.entry - 1) * 100, 2), mae_pct=round(sg * (worst / p.entry - 1) * 100, 2),
                       minutes=round((p.exit_t - p.entry_t) / 60, 1))
            self.pnl_today += pnl
            self.losses_in_row = self.losses_in_row + 1 if pnl < 0 else 0
            r = self.risk
            if r.get("daily_loss_pct") is not None and self.pnl_today <= -self.spec["capital"] * r["daily_loss_pct"] / 100:
                self.stopped = f"daily loss ₹{-self.pnl_today:,.0f} reached {r['daily_loss_pct']}% of capital"
            elif r.get("stop_after_losses") and self.losses_in_row >= r["stop_after_losses"]:
                self.stopped = f"{self.losses_in_row} losses in a row"
        self.journal.trade(row)


class Accounts:
    """All accounts from accounts.json, attached to an engine as one listener."""

    def __init__(self, journal: Journal, spec_path: Path = SPEC, facts: Facts | None = None):
        spec = json.loads(Path(spec_path).read_text())
        self.accounts = [Account(a, journal, spec.get("groups"), facts) for a in spec["accounts"]]

    def on_fill(self, engine, p) -> None:
        for a in self.accounts:
            a.on_fill(engine, p)

    def on_close(self, engine, p) -> None:
        for a in self.accounts:
            a.on_close(engine, p)
