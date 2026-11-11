"""Paper trading: buys the suggested option on strong alerts and manages exits, with real costs."""
from __future__ import annotations

import datetime as dt

from . import session as SESSION                     # 2.48: the NSE calendar
from .clock import IST

DEFAULTS = {
    "auto": True,
    "min_severity": 2,
    "kinds": None,                   # filled from the bullet catalogue (live/registry.py) below
    "lots": 1,
    "target_pct": 30.0,
    "stop_pct": 20.0,
    "max_hold_min": 60,
    "exit_time": "15:15",
    "no_entry_after": "14:45",
    "max_open": 6,
    "capital": 500000,          # risk guard
    "risk_pct": 1.0,            # rupees at risk per trade = capital x risk_pct, sizes the lots
    "max_lots": 5,
    "max_daily_loss_pct": 3.0,  # stop new auto trades after this loss in a day
    "max_trades_day": 12,
    "cooloff_min": 30,          # after two stop-losses in a row, no new trade for this long
    # aggressive mode: auto paper-trade every directional idea, even when the risk check says BLOCK (paper only)
    "aggressive": False,
    "aggressive_min_severity": 2,
    "aggressive_max_open": 15,
    "short_margin_pct": 15.0,   # rough margin for a short option: % of the underlying value of the position
    "profit_lock": 0,           # stop new trades for the day once this much is booked (0 = off)
}

from .registry import paper_kinds as _pk  # noqa: E402

DEFAULTS["kinds"] = _pk()

# 2.48 (Mandeep, 10 Nov 2026): no fixed ₹40K preset any more. You type your capital on the Paper page and the account
# rules of its size come from rules/capital.json (live/capital.py).

UND_MAX_AGE_S = 120        # 2.48: the underlying must have traded in the last 2 minutes (a frozen feed opens nothing)

MODES = {0: "auto", 1: "you", 2: "override", 3: "aggressive", 4: "you (chain/chart)"}


def costs(entry: float, exit: float, qty: int) -> float:
    """Indian option-buying charges for one round trip (discount broker)."""
    from lab.engine import charge_amount            # rates from rules/costs.json
    return round(charge_amount(entry * qty, exit * qty, 2), 2)


class PaperBook:
    def __init__(self, store, source: str, cfg: dict | None = None, log=print, on_open=None):
        self.store = store
        self.source = source
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.log = log
        self.on_open = on_open          # 2.48: the engine streams a new trade's option at once (live price from the first second)
        self.skipped: dict = {}         # 2.48: why trades were not taken today (no money, closed market, old quote)

    def _skip(self, why: str):
        self.skipped[why] = self.skipped.get(why, 0) + 1
        return None

    def market_ok(self, state, now, opt_key=None, und_key=None) -> str | None:
        """2.48: None when a paper trade may open now, else the reason. An open NSE session (rules/market_calendar.json
        + holidays), the option's last trade inside today's session and the underlying traded in the last 2 minutes
        (exchange time, not the time the app received it: a reconnect's old snapshot never opens a trade)."""
        if self.source == "sim":
            return None
        if not SESSION.paper_ok(now):
            return "market closed (NSE calendar)"
        i = state.get(opt_key) if (state is not None and opt_key) else None
        if i is not None and not SESSION.quote_fresh(getattr(i, "ltt", None), now):
            return "option quote is from before today's open"
        u = state.get(und_key) if (state is not None and und_key) else None
        if u is not None and not SESSION.quote_fresh(getattr(u, "ltt", None), now, UND_MAX_AGE_S):
            return "underlying price is old (feed not live)"
        return None

    def open_trades(self):
        return self.store.trades(status="open", source=self.source)

    def _index_syms(self, alert: dict | None = None) -> set:
        """Index names: the Lab's index list plus any symbol an alert called an index this session."""
        s = self.__dict__.setdefault("_idx", set())
        if not s:
            try:
                from lab.data import UNDERLYINGS
                s.update(UNDERLYINGS)
            except Exception:
                s.update({"NIFTY", "BANKNIFTY"})
        if alert and alert.get("is_index"):
            s.add(alert["sym"])
        return s

    def try_open(self, alert: dict, state, now: dt.datetime, manual=False, override=False) -> int | None:
        """manual: you clicked Paper trade (needs PASS); override: you confirmed taking a soft BLOCK (1 lot, tagged).
        Automatic trades need PASS, unless aggressive mode is on: then every directional idea is taken (tagged)."""
        c = self.cfg
        opt = alert.get("option")
        if not opt:
            return None
        aggressive = False
        if not manual:
            if not c["auto"]:
                return None
            if c.get("aggressive") and alert.get("direction") in ("up", "down") \
                    and alert["severity"] >= int(c.get("aggressive_min_severity") or 2):
                aggressive = True
            elif alert["severity"] < c["min_severity"] or (c["kinds"] != "all" and alert["kind"] not in c["kinds"]):
                return None
            hm = now.strftime("%H:%M")
            if hm < "09:16" or hm >= (c["exit_time"] if aggressive else c["no_entry_after"]):
                return None                               # never on pre-open or after-close prices
        opens = self.open_trades()
        cap = int(c.get("aggressive_max_open") or 15) if aggressive else int(c["max_open"])
        if any(t["sym"] == alert["sym"] for t in opens) or (not manual and len(opens) >= cap):
            return None
        keep = int(c.get("index_slots") or 0)             # 2.59: slots kept for the indices (0 = as before)
        if keep and not manual and not alert.get("is_index"):
            idx = self._index_syms(alert)
            if sum(1 for t in opens if t["sym"] not in idx) >= cap - keep:
                return self._skip(f"{keep} slot{'s' if keep > 1 else ''} kept for Nifty / Bank Nifty")
        risk = (alert.get("data") or {}).get("risk")
        if not aggressive:
            if self.guard(now)["blocked"]:
                return None
            if risk and risk.get("verdict") != "PASS":
                if not (manual and override and risk.get("can_override")):
                    return None                           # the risk gate said no: no paper trade
        inst = state.get(opt["key"])
        if not inst or not inst.ltp:
            return None
        why = self.market_ok(state, now, opt["key"], alert.get("und_key"))
        if why:
            return self._skip(why)
        entry = inst.ask or inst.ltp
        if override:
            lots = 1
        else:
            lots = int(risk["lots"]) if risk and risk.get("lots") else self.size(entry, opt["lot"])
        manual_code = 3 if aggressive else 2 if override else int(manual)
        if int(c.get("max_lots") or 5) == 1:
            lots = 1
        fac = (alert.get("data") or {}).get("factory") or {}
        ex = fac.get("exit")                              # a Strategy Lab rule brings its own exit
        return self._open(state, now, sym=alert["sym"], kind=alert["kind"], opt=opt, entry=entry, ltp=inst.ltp, lots=lots,
                          direction=1, manual=manual_code, alert_id=alert.get("id"), und_key=alert.get("und_key"),
                          why=alert.get("title", ""), exit=ex, plan=fac.get("plan"))

    def open_manual(self, state, now: dt.datetime, *, sym: str, opt: dict, direction: int, lots: int = 1,
                    und_key: str | None = None, kind="manual") -> dict:
        """Buy (direction 1) or sell (direction -1) an option by hand from the option chain or the chart. Selling an
        option you hold closes it; otherwise it opens a short. Market hours only, live price only. Paper only."""
        if self.source != "sim" and not SESSION.paper_ok(now):   # 2.48: the NSE calendar (holidays, special sessions)
            v = SESSION.view(now)
            nxt = (v.get("next") or {}).get("open")
            raise ValueError("Market closed: paper fills only on live prices in an open NSE session"
                             + (f" (next: {nxt})." if nxt else "."))
        inst = state.get(opt["key"])
        if not inst or not inst.ltp:
            raise ValueError("No live price for this option right now.")
        why = self.market_ok(state, now, opt["key"], und_key)
        if why:
            raise ValueError(f"Not filled: {why}.")
        for t in self.open_trades():
            if t["opt_key"] == opt["key"] and (t.get("dir") or 1) == -direction:
                return {"closed": self.close_manual(t["id"], state, now)}
        if self.guard(now)["blocked"] and not self.cfg.get("aggressive"):
            raise ValueError(self.guard(now)["why"])
        entry = (inst.ask or inst.ltp) if direction > 0 else (inst.bid or inst.ltp)
        tid = self._open(state, now, sym=sym, kind=kind, opt=opt, entry=entry, ltp=inst.ltp, lots=max(1, int(lots)),
                         direction=direction, manual=4, und_key=und_key, why="by hand", strict=True)
        return {"opened": tid}

    def _open(self, state, now, *, sym, kind, opt, entry, ltp, lots, direction, manual, alert_id=None, und_key=None, why="",
              exit=None, plan=None, strict=False):
        c = self.cfg
        und = state.get(und_key) if und_key else None
        # 2.48: never more money than the account has free (Mandeep, 10 Nov: "sometimes it is taking trades more than
        # the amount"). Lots are cut to fit; if one lot does not fit, no trade (strict: say so to the person clicking).
        und_px0 = und.ltp if und and und.ltp else None
        per_lot = self.need(entry, int(opt["lot"]), direction, und_px0)
        free = self.free_cash(state)
        if per_lot > 0 and per_lot * int(lots) > free:
            fit = int(free // per_lot)
            if fit < 1:
                msg = f"needs ₹{per_lot:,.0f} for 1 lot, only ₹{max(0.0, free):,.0f} free"
                self.log(f"Paper skipped {opt['label']}: {msg}")
                if strict:
                    raise ValueError(f"Not enough free money: {msg} in the paper account.")
                return self._skip("not enough free money")
            self.log(f"Paper {opt['label']}: {lots} lots cut to {fit} (₹{free:,.0f} free)")
            lots = fit
        tp, sp = float(c["target_pct"]) / 100, float(c["stop_pct"]) / 100
        hold, trail = None, None
        if exit:                                          # [target %, stop %, max minutes, trail start %, keep fraction]
            tp = (exit[0] or 1000) / 100
            sp = exit[1] / 100
            hold = int(exit[2])
            trail = f"{exit[3]},{exit[4]}" if len(exit) > 4 and exit[3] else None
        t = {"alert_id": alert_id, "source": self.source, "day": now.date().isoformat(), "sym": sym,
             "kind": kind, "opt_key": opt["key"], "label": opt["label"], "side": opt["side"],
             "lot": opt["lot"], "lots": lots, "entry_ts": now.timestamp(), "entry": entry,
             "target": round(entry * (1 + tp) if direction > 0 else entry * (1 - tp), 2),
             "stop": round(entry * (1 - sp) if direction > 0 else entry * (1 + sp), 2),
             "peak": entry, "last": ltp, "status": "open", "manual": manual, "dir": direction,
             "und_key": und_key, "und_entry": und.ltp if und and und.ltp else None, "und_last": und.ltp if und and und.ltp else None,
             "mode": MODES.get(manual, "auto"), "hold_max": hold, "trail": trail}
        # 2.38: quantity (lot size x lots), the money the trade takes (premium paid; for a short, the margin estimate:
        # short_margin_pct of the underlying's value, at least the premium) and the account size at the time
        qty = int(opt["lot"]) * int(lots)
        need = self.need(entry, int(opt["lot"]), direction, t["und_entry"]) * int(lots)
        t.update(qty=qty, capital=round(need, 2), acct_capital=float(c.get("capital") or 0))
        if plan and t["und_entry"] and plan.get("R"):     # chart-level exit: stop / target on the underlying (lab/exits.py)
            import json as _json
            from lab.exits import PREM_STOP, STYLES, LivePlan
            lp = LivePlan(plan["style"], t["und_entry"], 1 if opt["side"] == "CE" else -1, float(plan["R"]), entry, now.timestamp())
            t["plan"] = _json.dumps(lp.to_dict())
            t["target"] = round(entry * 100, 2)            # the chart target decides, not a premium %
            t["stop"] = round(entry * (1 - PREM_STOP / 100), 2)
            t["hold_max"] = STYLES[plan["style"]]["max"]
        if kind == "factory":                             # remember which Strategy Lab rule took it (for its live record)
            import re as _re
            mm = _re.search(r"\b(S[0-9a-f]{8})\b", why or "")
            if mm:
                t["mode"] = f"factory:{mm.group(1)}"
        tid = self.store.add_trade(t)
        self.log(f"Paper {'BUY' if direction > 0 else 'SELL'} {opt['label']} @ {entry:.2f} ({why})")
        if self.on_open:
            try:
                self.on_open(opt["key"], und_key)
            except Exception as e:
                self.log(f"Live price for {opt['label']} not started: {e}")
        return tid

    def need(self, entry: float, lot: int, direction: int, und_px: float | None) -> float:
        """Money one lot takes: the premium for a buy; for a short the margin estimate (short_margin_pct of the
        underlying's value, at least the premium)."""
        if direction > 0:
            return float(entry) * lot
        return max(float(entry) * lot, (und_px or 0) * lot * float(self.cfg.get("short_margin_pct") or 15) / 100)

    def free_cash(self, state=None) -> float:
        """2.48: free money now = capital + booked P&L + open P&L (live prices) - money blocked in open positions:
        the same 'Free' the Paper page shows."""
        trades = self.store.trades(source=self.source, limit=5000)
        realised = sum(t["net"] or 0 for t in trades if t["status"] == "closed")
        unreal = used = 0.0
        for t in trades:
            if t["status"] != "open":
                continue
            i = state.get(t["opt_key"]) if state is not None else None
            px = i.ltp if i is not None and i.ltp else (t.get("last") or t["entry"])
            unreal += (t.get("dir") or 1) * (px - t["entry"]) * t["lot"] * t["lots"]
            used += self.margin(t)
        return float(self.cfg.get("capital") or 0) + realised + unreal - used

    def size(self, entry, lot) -> int:
        """Lots so that hitting the stop loses about risk_pct of capital (at least 1 lot)."""
        c = self.cfg
        per_lot_risk = entry * lot * float(c["stop_pct"]) / 100
        budget = float(c["capital"]) * float(c["risk_pct"]) / 100
        if per_lot_risk <= 0:
            return int(c["lots"])
        return max(1, min(int(c["max_lots"]), int(budget // per_lot_risk) or 1))

    def guard(self, now) -> dict:
        c = self.cfg
        day = now.date().isoformat()
        today = [t for t in self.store.trades(source=self.source, limit=500) if t["day"] == day]
        booked = sum(t["net"] or 0 for t in today if t["status"] == "closed")
        limit = -float(c["capital"]) * float(c["max_daily_loss_pct"]) / 100
        blocked, why = False, None
        lock = float(c.get("profit_lock") or 0)
        if booked <= limit:
            blocked, why = True, f"Daily loss limit hit (₹{booked:,.0f}). No new trades today."
        elif lock > 0 and booked >= lock:
            blocked, why = True, f"Good day locked in (₹{booked:,.0f} booked, lock at ₹{lock:,.0f}). No new trades today."
        elif len(today) >= int(c["max_trades_day"]):
            blocked, why = True, f"{len(today)} trades today: over-trading guard reached."
        else:
            # tilt guard: two stop-losses in a row -> a cool-off before the next entry
            done = sorted((t for t in today if t["status"] == "closed" and t.get("exit_ts")), key=lambda t: t["exit_ts"])
            cool = float(c.get("cooloff_min", 30))
            if len(done) >= 2 and all(t["reason"] == "stop loss" for t in done[-2:]) and cool > 0:
                left = cool - (now.timestamp() - done[-1]["exit_ts"]) / 60
                if left > 0:
                    blocked, why = True, f"Two stop-losses in a row: {left:.0f}-minute cool-off before the next trade."
        return {"blocked": blocked, "why": why, "booked_today": round(booked, 2), "trades_today": len(today),
                "loss_limit": round(limit, 0), "risk_per_trade": round(float(c["capital"]) * float(c["risk_pct"]) / 100, 0)}

    def update(self, state, now: dt.datetime, force_close=False):
        c = self.cfg
        closed = []
        for t in self.open_trades():
            d = t.get("dir") or 1
            und = state.get(t["und_key"]) if t.get("und_key") else None
            inst = state.get(t["opt_key"])
            if not inst or not inst.ltp:
                if force_close:
                    self._close(t, t["last"] or t["entry"], now, "market closed", und)
                continue
            px = (inst.bid or inst.ltp) if d > 0 else (inst.ask or inst.ltp)       # what you would get closing now
            peak = max(t["peak"] or 0, inst.ltp) if d > 0 else min(t["peak"] or inst.ltp, inst.ltp)
            held = (now.timestamp() - t["entry_ts"]) / 60
            reason = None
            lp = None
            if t.get("plan"):
                import json as _json
                from lab.exits import LivePlan
                lp = LivePlan.from_dict(_json.loads(t["plan"]))
                why = lp.check(und.ltp if und and und.ltp else None, inst.ltp, now.timestamp())
                reason = {"stop": "chart stop", "premium stop": "stop loss", "target": "chart target",
                          "time": "time limit"}.get(why) if why else None
            if reason:
                pass
            elif (d > 0 and px >= t["target"]) or (d < 0 and px <= t["target"]):
                reason = "target"
            elif (d > 0 and px <= t["stop"]) or (d < 0 and px >= t["stop"]):
                reason = "stop loss"
            elif t.get("trail") and d > 0 and t["entry"]:
                ts_, keep = (float(x) for x in t["trail"].split(","))
                pk = (peak / t["entry"] - 1) * 100
                if pk >= ts_ and (px / t["entry"] - 1) * 100 <= pk * keep:
                    reason = "trailing stop"
            if reason:
                pass
            elif force_close or now.strftime("%H:%M") >= c["exit_time"]:
                reason = "end of day"
            elif held >= (t.get("hold_max") or c["max_hold_min"]) and t.get("manual") != 4:
                reason = "time limit"                       # your own chain/chart trades are held until you exit
            if reason:
                closed.append(self._close(t, px, now, reason, und))
            else:
                upd = {"peak": peak, "last": inst.ltp}
                if lp:
                    upd["plan"] = _json.dumps(lp.to_dict())
                if und and und.ltp:
                    upd["und_last"] = und.ltp
                self.store.update_trade(t["id"], **upd)
        return closed

    def close_manual(self, tid, state, now, reason: str = "closed by you"):
        for t in self.open_trades():
            if t["id"] == tid:
                inst = state.get(t["opt_key"])
                d = t.get("dir") or 1
                px = ((inst.bid if d > 0 else inst.ask) or inst.ltp) if inst and inst.ltp else (t["last"] or t["entry"])
                und = state.get(t["und_key"]) if t.get("und_key") else None
                return self._close(t, px, now, reason, und)
        return None

    def close_all(self, state, now) -> list:
        return [x for x in (self.close_manual(t["id"], state, now) for t in self.open_trades()) if x]

    def _close(self, t, px, now, reason, und=None):
        d = t.get("dir") or 1
        qty = t["lot"] * t["lots"]
        gross = round(d * (px - t["entry"]) * qty, 2)
        cst = costs(t["entry"], px, qty) if d > 0 else costs(px, t["entry"], qty)
        upd = dict(exit_ts=now.timestamp(), exit=px, reason=reason, status="closed", gross=gross, costs=cst,
                   net=round(gross - cst, 2), last=px)
        if und is not None and getattr(und, "ltp", None):
            upd.update(und_exit=und.ltp, und_last=und.ltp)
        self.store.update_trade(t["id"], **upd)
        self.log(f"Paper {'SELL' if d > 0 else 'BUY back'} {t['label']} @ {px:.2f} ({reason}) net ₹{gross - cst:,.0f}")
        return {**t, "exit": px, "reason": reason, "net": gross - cst}

    def margin(self, t) -> float:
        """Money a position blocks: premium paid for a buy; a rough exchange margin for a short."""
        qty = t["lot"] * t["lots"]
        if (t.get("dir") or 1) > 0:
            return t["entry"] * qty
        und = t.get("und_last") or t.get("und_entry")
        return (und * qty * float(self.cfg.get("short_margin_pct", 15)) / 100) if und else t["entry"] * qty * 8

    def account(self, trades: list, opened: list, now=None) -> dict:
        """The demo account: starting capital plus everything booked, what is blocked in open positions, what is free."""
        cap = float(self.cfg["capital"])
        realised = sum(t["net"] or 0 for t in trades if t["status"] == "closed")
        unreal = sum(t.get("unreal") or 0 for t in opened)
        used = sum(self.margin(t) for t in opened)
        day = (now or dt.datetime.now(IST)).date().isoformat()
        today_real = sum(t["net"] or 0 for t in trades if t["status"] == "closed" and t["day"] == day)
        bal = cap + realised
        return {"capital": round(cap, 0), "balance": round(bal, 2), "equity": round(bal + unreal, 2), "used": round(used, 0),
                "free": round(bal + unreal - used, 0), "realised": round(realised, 2), "unrealised": round(unreal, 2),
                "today": round(today_real + unreal, 2), "today_booked": round(today_real, 2),
                "return_pct": round((bal + unreal - cap) / cap * 100, 2) if cap else None}

    def summary(self, state=None) -> dict:
        trades = self.store.trades(source=self.source, limit=2000)
        closed = [t for t in trades if t["status"] == "closed"]
        opened = [t for t in trades if t["status"] == "open"]
        for t in opened:
            inst = state.get(t["opt_key"]) if state else None
            ltp = inst.ltp if inst and inst.ltp else t["last"] or t["entry"]
            if inst and inst.ltp:                       # 2.48: when this price is from (exchange trade time, else received)
                t["px_ts"] = (inst.ltt / 1000) if getattr(inst, "ltt", None) else getattr(inst, "t", None)
                t["px_rx"] = getattr(inst, "t", None)
            t["unreal"] = round((t.get("dir") or 1) * (ltp - t["entry"]) * t["lot"] * t["lots"], 2)
            t["last"] = ltp
            und = state.get(t["und_key"]) if state and t.get("und_key") else None
            if und and und.ltp:
                t["und_last"] = und.ltp
        by = {}
        for t in closed:
            b = by.setdefault(t["kind"], {"kind": t["kind"], "n": 0, "wins": 0, "net": 0.0})
            b["n"] += 1
            b["wins"] += t["net"] > 0
            b["net"] += t["net"]
        for b in by.values():
            b["win_rate"] = round(b["wins"] / b["n"] * 100) if b["n"] else None
            b["avg"] = round(b["net"] / b["n"], 2) if b["n"] else None
            b["net"] = round(b["net"], 2)
        eq, run = [], 0.0
        for t in sorted(closed, key=lambda x: x["exit_ts"] or 0):
            run += t["net"]
            eq.append({"ts": t["exit_ts"], "equity": round(run, 2)})
        n = len(closed)
        wins = sum(t["net"] > 0 for t in closed)
        return {"open": opened, "closed": closed[:200], "by_kind": sorted(by.values(), key=lambda b: -b["net"]),
                "equity": eq, "n": n, "win_rate": round(wins / n * 100) if n else None,
                "net": round(sum(t["net"] for t in closed), 2), "costs": round(sum(t["costs"] or 0 for t in closed), 2),
                "unreal": round(sum(t["unreal"] for t in opened), 2), "settings": self.cfg,
                "account": self.account(trades, opened), "skipped_today": dict(self.skipped),
                "guard": self.guard(dt.datetime.now(IST) if state is None else (state.clock.now() if getattr(state, "clock", None) else dt.datetime.now(IST)))}
