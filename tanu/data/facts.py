"""Dated facts (ADR-0003): charges, lot sizes, trading sessions. Each value is valid from its date until the next one,
so a past day always gets the value it had. Files live in tanu/facts/ (bullets); a bad file fails loudly on load."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

FACTS_DIR = Path(__file__).resolve().parents[1] / "facts"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def _pick(entries: list, on: dt.date, key: str = "valid_from"):
    best = None
    for e in sorted(entries, key=lambda e: e[key]):
        if _d(e[key]) <= on:
            best = e
    return best


class Facts:
    def __init__(self, folder: Path | str = FACTS_DIR):
        f = Path(folder)
        self._charges = json.loads((f / "charges.json").read_text())["entries"]
        self._lots = json.loads((f / "lots.json").read_text())["lots"]
        m = f / "margins.json"
        self._margins = json.loads(m.read_text())["margins"] if m.exists() else {}
        cal = json.loads((f / "calendar.json").read_text())
        self._regular = cal["regular"]
        self._holidays = {_d(x) for ys in cal.get("holidays", {}).values() for x in ys}
        self._special = {_d(s["date"]): s for s in cal.get("special", [])}

    def charges(self, on) -> dict:
        e = _pick(self._charges, _d(on))
        if e is None:
            raise LookupError(f"no charges valid on {on}")
        return {k: v for k, v in e.items() if k not in ("valid_from", "source")}

    def margin_pct(self, sym: str, on) -> float | None:
        """Share of the notional blocked to hold one sold option (estimate, facts/margins.json)."""
        e = _pick(self._margins.get(sym) or self._margins.get("*", []), _d(on))
        return float(e["pct_of_notional"]) if e else None

    def lot(self, sym: str, on) -> int | None:
        e = _pick(self._lots.get(sym, []), _d(on))
        return int(e["lot"]) if e else None

    def session(self, on) -> tuple[dt.datetime, dt.datetime] | None:
        """(open, close) in IST for that day, or None when F&O does not trade."""
        d = _d(on)
        sp = self._special.get(d)
        if sp:
            o, c = sp["open"], sp["close"]
        else:
            r = _pick(self._regular, d)
            if r is None or d.weekday() not in r["weekdays"] or d in self._holidays:
                return None
            o, c = r["open"], r["close"]
        mk = lambda hm: dt.datetime.combine(d, dt.time.fromisoformat(hm), IST)
        return mk(o), mk(c)

    def trading_day(self, on) -> bool:
        return self.session(on) is not None

    def option_charges(self, entry: float, exit_: float, qty: int, on) -> float:
        """₹ charges of one bought-then-sold option trade (two orders) at the rates valid on that day."""
        c = self.charges(on)
        buy, sell = entry * qty, exit_ * qty
        exch = (buy + sell) * c["exchange"]
        sebi = (buy + sell) * c["sebi"]
        brok = 2 * c["brokerage_per_order"]
        return round(brok + sell * c["stt_sell"] + exch + sebi + buy * c["stamp_buy"] + c["gst"] * (brok + exch + sebi), 2)
