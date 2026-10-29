"""Expiry desk: the end-of-day strategy that passed the 10-year test, paper-traded live, held to expiry.

Plan: for each index, the variant the walk-forward chose for the current year (lab/eod_study.py). If nothing
passed for that index this year, the desk stays in cash for it and says why.
Entry: at about 15:20 on the entry day (1 or 2 trading days before expiry, or the first day of a new expiry
cycle), at live bid/ask with slippage. Legs are subscribed from 15:00 so prices are live at entry.
Exit: held to expiry and settled at intrinsic value from the index close (as the backtest assumes). Marked to
market while open. Paper only: nothing here can place an order.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

from .data import UNDERLYINGS
from .engine import charges
from .eod_study import STEP, legs_for

ENTRY_FROM, ENTRY_TO = "15:20", "15:28"
PREP_FROM = "15:00"


def parse_pid(pid: str) -> tuple[str, dict]:
    fam, rest = pid.split("|", 1)
    p = {}
    for kv in rest.split(","):
        k, v = kv.split("=")
        try:
            p[k] = float(v) if "." in v else int(v)
        except ValueError:
            p[k] = v
    return fam, p


def weekdays_before(exp: dt.date, k: int) -> dt.date:
    d, n = exp, 0
    while n < k:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


class ExpiryDesk:
    def __init__(self, engine):
        self.e = engine
        self.plan = []
        self.pending = {}            # sym -> {"exp", "legs": [(strike, side, q, key)]}
        self.why = {}
        self.load_plan()

    # ------------------------------------------------------------ plan
    def load_plan(self):
        self.plan, self.why = [], {}
        f = Path(self.e.data_dir) / "lab" / "eod_results.json"
        if not f.exists():
            self.why["_"] = "Run the 10-year end-of-day test first (Strategy desk)."
            return
        try:
            rep = json.loads(f.read_text())
        except Exception:
            return
        year = dt.datetime.now().year
        for key, r in (rep.get("results") or {}).items():
            if r["family"] == "long_straddle":
                continue
            yrs = [y for y in r["years"] if y["year"] == year]
            y = yrs[0] if yrs else None
            if y and y.get("pid"):
                fam, p = parse_pid(y["pid"])
                self.plan.append({"sym": r["sym"], "family": fam, "params": p, "pid": y["pid"], "name": r["name"],
                                  "train": y.get("train")})
            else:
                self.why[key] = f"{r['name']} on {r['sym']}: nothing passed the test for {year}. Cash."
        # one strategy per index: the one with the better training average
        best = {}
        for p in self.plan:
            if p["sym"] not in best or (p["train"] or {}).get("avg", 0) > (best[p["sym"]]["train"] or {}).get("avg", 0):
                best[p["sym"]] = p
        self.plan = list(best.values())

    # ------------------------------------------------------------ book
    def _key(self):
        return f"expiry_desk:{self.e.source or 'none'}"

    def trades(self) -> list:
        return self.e.store.get(self._key(), []) or []

    def _save(self, t):
        self.e.store.put(self._key(), t[-500:])

    def needed_keys(self) -> set:
        keys = set()
        for t in self.trades():
            if t["status"] == "open":
                keys.update(l["key"] for l in t["legs"] if l.get("key"))
        for p in self.pending.values():
            keys.update(l[3] for l in p["legs"] if l[3])
        return keys

    def _entry_day(self, u, p, today) -> tuple[bool, dt.date | None]:
        exp = u.near_expiry(today)
        if not exp:
            return False, None
        e_key = p["params"].get("entry")
        if isinstance(e_key, int):
            if exp == today:                      # entry for this expiry was before today
                return False, exp
            return weekdays_before(exp, e_key) == today, exp
        # "after_expiry": the first day of a new cycle (the previous expiry was the last trading day)
        mark = self.e.store.get(f"expiry_desk:cycle:{u.symbol}")
        if mark != exp.isoformat():
            return True, exp
        return False, exp

    def tick(self, now: dt.datetime):
        if not self.plan or not self.e.uni or not self.e.state:
            return
        today, hm = now.date(), now.strftime("%H:%M")
        trades = self.trades()
        self._settle(trades, now)
        for p in self.plan:
            u = self.e.uni.underlyings.get(p["sym"])
            if not u:
                continue
            ok, exp = self._entry_day(u, p, today)
            if not ok or any(t["sym"] == p["sym"] and t["expiry"] == exp.isoformat() for t in trades):
                continue
            spot = (self.e.state.get(u.key).ltp if self.e.state.get(u.key) else None)
            if not spot:
                continue
            if hm >= PREP_FROM and p["sym"] not in self.pending:
                legs = []
                for k, side, q in legs_for(p["family"], p["params"], spot, STEP.get(p["sym"], 50)):
                    o = u.chains.get(exp, {}).get(float(k), {}).get(side)
                    legs.append((float(k), side, q, o.key if o else None))
                self.pending[p["sym"]] = {"exp": exp, "legs": legs}
            if ENTRY_FROM <= hm <= ENTRY_TO and p["sym"] in self.pending:
                self._enter(p, u, self.pending.pop(p["sym"]), spot, now, trades)
        # mark to market
        for t in trades:
            if t["status"] != "open":
                continue
            mtm = 0.0
            for l in t["legs"]:
                i = self.e.state.get(l["key"]) if l.get("key") else None
                if i and i.ltp:
                    l["last"] = i.ltp
                mtm += l["q"] * ((l.get("last") or l["entry"]) - l["entry"]) * t["lot"] * t["lots"]
            t["mtm"] = round(mtm, 0)
        self._save(trades)

    def _enter(self, p, u, pend, spot, now, trades):
        legs, missing = [], []
        for k, side, q, key in pend["legs"]:
            i = self.e.state.get(key) if key else None
            if not i or not i.ltp:
                missing.append(f"{k:g} {side}")
                continue
            px = (i.ask or i.ltp) if q > 0 else (i.bid or i.ltp)
            sl = max(0.5, 0.01 * px)
            fill = px + sl if q > 0 else max(0.05, px - sl)
            legs.append({"strike": k, "side": side, "q": q, "key": key, "entry": round(fill, 2), "last": i.ltp})
        if missing:
            self.e.log(f"Expiry desk: {p['sym']} skipped, no price for {', '.join(missing)}")
            return
        lot = UNDERLYINGS.get(p["sym"], {}).get("lot") or u.lot
        credit = -sum(l["q"] * l["entry"] for l in legs)          # >0 when the position took in premium
        wing = max((abs(a["strike"] - b["strike"]) for a in legs for b in legs if a["side"] == b["side"]), default=0)
        max_loss_pts = max(0.0, wing - credit) if credit > 0 else -credit
        lc = (self.e.cfg().get("lab") or {})
        capital = float(lc.get("capital") or 500000)
        # held to expiry with no stop: the loss is capped by the wings, so size on that cap (the worst case),
        # limited to worst_pct of capital (5% by default, as in the backtests)
        budget = capital * float(lc.get("worst_pct") or 5.0) / 100
        per_lot = max_loss_pts * lot
        lots = min(10, int(budget // per_lot)) if per_lot > 0 else 1
        if lots < 1:
            self.e.log(f"Expiry desk: {p['sym']} skipped, one lot risks ₹{per_lot:,.0f} (limit ₹{budget:,.0f})")
            return
        t = {"id": int(time.time() * 1000), "sym": p["sym"], "family": p["family"], "pid": p["pid"], "expiry": pend["exp"].isoformat(),
             "entry_ts": now.timestamp(), "spot": spot, "legs": legs, "lot": lot, "lots": lots, "credit": round(credit, 2),
             "max_loss": round(per_lot * lots, 0), "status": "open", "mtm": 0.0}
        trades.append(t)
        self.e.store.put(f"expiry_desk:cycle:{p['sym']}", pend["exp"].isoformat())
        self.e.log(f"Expiry desk paper entry: {p['sym']} {p['family']} {lots} lot(s), credit {credit:.1f} pts, max loss ₹{t['max_loss']:,.0f}")

    def _settle(self, trades, now):
        for t in trades:
            if t["status"] != "open":
                continue
            exp = dt.date.fromisoformat(t["expiry"])
            if now.date() < exp or (now.date() == exp and now.strftime("%H:%M") < "15:30"):
                continue
            u = self.e.uni.underlyings.get(t["sym"]) if self.e.uni else None
            i = self.e.state.get(u.key) if u else None
            S = i.ltp if i and i.ltp and now.date() == exp else None
            how = "settled at the expiry close"
            if S is None:                 # the app was closed on expiry day: use the last close we know
                S = (i.cp if i and i.cp else None) or t.get("spot")
                how = "settled at the last known close (app was not running at expiry)"
            fills, pts = [], 0.0
            for l in t["legs"]:
                intr = max(0.0, S - l["strike"]) if l["side"] == "CE" else max(0.0, l["strike"] - S)
                pts += l["q"] * (intr - l["entry"])
                fills.append((l["q"], l["entry"]))
                if intr > 0:
                    fills.append((-l["q"], intr))
            q = t["lot"] * t["lots"]
            gross = pts * q
            cost = charges(fills, t["lot"]) * t["lots"]
            t.update(status="closed", settle=round(S, 2), gross=round(gross, 0), costs=round(cost, 0),
                     net=round(gross - cost, 0), exit_ts=now.timestamp(), reason=how)
            self.e.log(f"Expiry desk settled {t['sym']} {t['expiry']}: net ₹{t['net']:,.0f}")

    def view(self) -> dict:
        tr = self.trades()
        closed = [t for t in tr if t["status"] == "closed"]
        return {"plan": self.plan, "cash": list(self.why.values()), "open": [t for t in tr if t["status"] == "open"],
                "closed": sorted(closed, key=lambda t: -(t.get("exit_ts") or 0))[:50],
                "total": round(sum(t.get("net") or 0 for t in closed), 0), "n": len(closed),
                "won": sum(1 for t in closed if (t.get("net") or 0) > 0)}
