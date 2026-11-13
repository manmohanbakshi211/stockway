"""Alert cards (an engine listener, ADR-0004): what, when, why, stop, target, evidence. Only setups on trial or live
send cards, and only for symbols the sniper accounts trade (sniper for bought options, sniper_sell for the selling
versions); shadow setups never ping the phone (they show on the Today page). Paper only: a card is information for
Mandeep, Tanu never places an order (ADR-0001)."""
from __future__ import annotations

import datetime as dt

from ..core import log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("alerts")


def _hm(t: float | None) -> str:
    return dt.datetime.fromtimestamp(t, IST).strftime("%H:%M") if t else "-"


class Alerts:
    def __init__(self, sender, accounts=None, account_id: str = "sniper", evidence: dict | None = None,
                 sell_account_id: str = "sniper_sell"):
        """sender: object with send(text); accounts: tanu.paper.accounts.Accounts (to know what the accounts took);
        evidence: {setup_id: one-line evidence text}."""
        self.sender, self.evidence = sender, evidence or {}
        accts = getattr(accounts, "accounts", [])
        self.acct = next((a for a in accts if a.id == account_id), None)
        self.acct_sell = next((a for a in accts if a.id == sell_account_id), None)
        self.taken: dict = {}
        self.sent: list[str] = []

    def _setup(self, engine, sid):
        base = sid[:-5] if sid.endswith("@sell") else sid
        return next((s for s in engine.setups if s.id == base), None)

    def _account(self, p):
        return self.acct_sell if getattr(p, "kind", "buy") == "sell" else self.acct

    def _eligible(self, engine, p) -> bool:
        s = self._setup(engine, p.setup)
        if s is None or s.status not in ("trial", "live"):
            return False
        a = self._account(p)
        syms = (a.spec.get("symbols") if a else None) or []
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
        if getattr(p, "kind", "buy") == "sell":
            self._sell_card(engine, p)
            return
        s = self._setup(engine, p.setup)
        st = engine.sym.get(p.sym)
        atr = (st.atr or (st.hi - st.lo) / 3) if st else None
        stop = p.state["spot"] - p.d * s.exit.atr_init * atr if atr else None
        target = "Target: trailing exit, the exit card will come"
        if s.exit.type == "rr" and p.state.get("stop_level") is not None:     # fixed stop level, R-multiple target
            stop = p.state["stop_level"]
            tgt = p.state["spot"] + p.d * s.exit.target_r * abs(p.state["spot"] - stop)
            target = f"Target: {p.sym} {tgt:,.0f} (1:{s.exit.target_r:g})"
        ob = engine.opt.bar(p.sym, p.day, p.strike, p.side, p.signal_t)
        lines = [f"ENTRY (paper) {p.sym} {p.strike:g} {p.side} at {_hm(p.signal_t + 60)}",
                 f"Setup: {s.name} [{s.status}]",
                 f"Why: {p.state.get('day')} day, move {'up' if p.d > 0 else 'down'}; {s.why}",
                 f"Option last: ₹{ob[3]:.2f}" if ob else "Option price: next minute's open",
                 f"Stop: {p.sym} {'below' if p.d > 0 else 'above'} {stop:,.0f} or premium {s.exit.premium_stop_pct:g}%"
                 if stop else f"Stop: premium {s.exit.premium_stop_pct:g}%",
                 target,
                 self.evidence.get(p.setup, "")]
        self._send("\n".join(x for x in lines if x))

    def _sell_card(self, engine, p) -> None:
        s = self._setup(engine, p.setup)
        st = engine.sym.get(p.sym)
        atr = (st.atr or (st.hi - st.lo) / 3) if st else None
        lvl = p.state.get("stop_level")
        lvl = lvl if lvl is not None else (p.state["spot"] - p.d * s.exit.atr_init * atr if atr else None)
        ob = engine.opt.bar(p.sym, p.day, p.strike, p.side, p.signal_t)
        a = self.acct_sell
        margin = a.margin(engine, p) if a else None
        lines = [f"SELL (paper) {p.sym} {p.strike:g} {p.side} at {_hm(p.signal_t + 60)}",
                 f"Setup: {s.name} [{s.status}], selling version",
                 f"Why: {p.state.get('day')} day, view {'up' if p.d > 0 else 'down'}; {s.why}",
                 f"Premium last: ₹{ob[3]:.2f}" if ob else "Premium: next minute's open",
                 f"Buy back (loss): premium +{s.sell.premium_stop_pct:g}%"
                 + (f" or {p.sym} {'below' if p.d > 0 else 'above'} {lvl:,.0f}" if lvl else ""),
                 f"Buy back (profit): premium -{s.sell.premium_target_pct:g}%; flat by {s.sell.flat_by}",
                 f"Margin about ₹{margin:,.0f} (estimate)" if margin else "",
                 self.evidence.get(p.setup, "")]
        self._send("\n".join(x for x in lines if x))

    def on_fill(self, engine, p) -> None:
        a = self._account(p)
        if a is None or id(p) not in a.open:
            return
        self.taken[id(p)] = a

    def on_close(self, engine, p) -> None:
        a = self.taken.pop(id(p), None)
        if a is None:
            return
        lot = engine.lots.get(p.sym) or engine.facts.lot(p.sym, p.day) or 1
        qty = lot * a.spec.get("lots", 1)
        if p.exit is None or p.entry is None:
            self._send(f"EXIT (paper) {p.sym} {p.strike:g} {p.side}: closed without a price ({p.reason})")
            return
        sell = getattr(p, "kind", "buy") == "sell"
        ch = engine.facts.option_charges(p.exit, p.entry, qty, p.day) if sell else \
            engine.facts.option_charges(p.entry, p.exit, qty, p.day)
        pnl = ((p.entry - p.exit) if sell else (p.exit - p.entry)) * qty - ch
        self._send(f"{'BOUGHT BACK' if sell else 'EXIT'} (paper) {p.sym} {p.strike:g} {p.side} at {_hm(p.exit_t + 60)}\n"
                   f"Reason: {p.reason.replace('_', ' ')}\n"
                   f"₹{p.entry:.2f} -> ₹{p.exit:.2f}, {qty} qty: ₹{pnl:,.0f} after costs ({pnl / (p.entry * qty) * 100:+.1f}%)")

    def system(self, text: str) -> None:
        self._send(f"Tanu: {text}")
