"""Alert cards (an engine listener, ADR-0004): what, when, why, stop, target, evidence. Only setups on trial or live
send cards, and only for symbols the sniper account trades; shadow setups never ping the phone (they show on the
Today page). Paper only: a card is information for Mandeep, Tanu never places an order (ADR-0001)."""
from __future__ import annotations

import datetime as dt

from ..core import log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("alerts")


def _hm(t: float | None) -> str:
    return dt.datetime.fromtimestamp(t, IST).strftime("%H:%M") if t else "-"


class Alerts:
    def __init__(self, sender, accounts=None, account_id: str = "sniper", evidence: dict | None = None):
        """sender: object with send(text); accounts: tanu.paper.accounts.Accounts (to know what the account took);
        evidence: {setup_id: one-line evidence text}."""
        self.sender, self.evidence = sender, evidence or {}
        self.acct = next((a for a in getattr(accounts, "accounts", []) if a.id == account_id), None)
        self.taken: set = set()
        self.sent: list[str] = []

    def _setup(self, engine, sid):
        return next((s for s in engine.setups if s.id == sid), None)

    def _eligible(self, engine, p) -> bool:
        s = self._setup(engine, p.setup)
        if s is None or s.status not in ("trial", "live"):
            return False
        syms = (self.acct.spec.get("symbols") if self.acct else None) or []
        return not syms or p.sym in syms

    def _send(self, text: str) -> None:
        self.sent.append(text)
        try:
            self.sender.send(text)
        except Exception as e:                          # alerts never stop the engine
            _log.warning("alert_failed", error=str(e))

    def on_signal(self, engine, p) -> None:
        if not self._eligible(engine, p):
            return
        s = self._setup(engine, p.setup)
        st = engine.sym.get(p.sym)
        atr = (st.atr or (st.hi - st.lo) / 3) if st else None
        stop = p.state["spot"] - p.d * s.exit.atr_init * atr if atr else None
        ob = engine.opt.bar(p.sym, p.day, p.strike, p.side, p.signal_t)
        lines = [f"ENTRY (paper) {p.sym} {p.strike:g} {p.side} at {_hm(p.signal_t + 60)}",
                 f"Setup: {s.name} [{s.status}]",
                 f"Why: {p.state.get('day')} day, move {'up' if p.d > 0 else 'down'}; {s.why}",
                 f"Option last: ₹{ob[3]:.2f}" if ob else "Option price: next minute's open",
                 f"Stop: {p.sym} {'below' if p.d > 0 else 'above'} {stop:,.0f} or premium {s.exit.premium_stop_pct:g}%"
                 if stop else f"Stop: premium {s.exit.premium_stop_pct:g}%",
                 "Target: trailing exit, the exit card will come",
                 self.evidence.get(p.setup, "")]
        self._send("\n".join(x for x in lines if x))

    def on_fill(self, engine, p) -> None:
        if self.acct is None or id(p) not in self.acct.open:
            return
        self.taken.add(id(p))

    def on_close(self, engine, p) -> None:
        if id(p) not in self.taken:
            return
        self.taken.discard(id(p))
        lot = engine.lots.get(p.sym) or engine.facts.lot(p.sym, p.day) or 1
        qty = lot * (self.acct.spec.get("lots", 1) if self.acct else 1)
        if p.exit is None or p.entry is None:
            self._send(f"EXIT (paper) {p.sym} {p.strike:g} {p.side}: closed without a price ({p.reason})")
            return
        ch = engine.facts.option_charges(p.entry, p.exit, qty, p.day)
        pnl = (p.exit - p.entry) * qty - ch
        self._send(f"EXIT (paper) {p.sym} {p.strike:g} {p.side} at {_hm(p.exit_t + 60)}\n"
                   f"Reason: {p.reason.replace('_', ' ')}\n"
                   f"₹{p.entry:.2f} -> ₹{p.exit:.2f}, {qty} qty: ₹{pnl:,.0f} after costs ({pnl / (p.entry * qty) * 100:+.1f}%)")

    def system(self, text: str) -> None:
        self._send(f"Tanu: {text}")
