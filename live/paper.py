"""Paper trading: buys the suggested option on strong alerts and manages exits, with real costs."""
from __future__ import annotations

import datetime as dt

from .clock import IST

DEFAULTS = {
    "auto": True,
    "min_severity": 2,
    "kinds": ["sharp_move", "orb", "volume_surge", "oi_buildup", "unusual_options", "beyond_priced"],
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
}


def costs(entry: float, exit: float, qty: int) -> float:
    """Indian option-buying charges for one round trip (discount broker)."""
    buy, sell = entry * qty, exit * qty
    brokerage = 20 + 20
    stt = 0.0015 * sell                      # 0.15% on sell-side premium (from 1 Apr 2026)
    exch = 0.0003503 * (buy + sell)          # NSE transaction charges on premium
    sebi = 10 / 1e7 * (buy + sell)
    gst = 0.18 * (brokerage + exch + sebi)
    stamp = 0.00003 * buy
    return round(brokerage + stt + exch + sebi + gst + stamp, 2)


class PaperBook:
    def __init__(self, store, source: str, cfg: dict | None = None, log=print):
        self.store = store
        self.source = source
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.log = log

    def open_trades(self):
        return self.store.trades(status="open", source=self.source)

    def try_open(self, alert: dict, state, now: dt.datetime, manual=False) -> int | None:
        c = self.cfg
        opt = alert.get("option")
        if not opt:
            return None
        if not manual:
            if not c["auto"] or alert["severity"] < c["min_severity"] or alert["kind"] not in c["kinds"]:
                return None
            if now.strftime("%H:%M") >= c["no_entry_after"]:
                return None
        opens = self.open_trades()
        if any(t["sym"] == alert["sym"] for t in opens) or (not manual and len(opens) >= c["max_open"]):
            return None
        g = self.guard(now)
        if not manual and g["blocked"]:
            return None
        risk = (alert.get("data") or {}).get("risk")
        if not manual and risk and risk.get("verdict") != "PASS":
            return None                                   # the risk gate said no: no paper trade
        inst = state.get(opt["key"])
        if not inst or not inst.ltp:
            return None
        entry = inst.ask or inst.ltp
        lots = int(risk["lots"]) if risk and risk.get("lots") else self.size(entry, opt["lot"])
        t = {"alert_id": alert.get("id"), "source": self.source, "day": now.date().isoformat(), "sym": alert["sym"],
             "kind": alert["kind"], "opt_key": opt["key"], "label": opt["label"], "side": opt["side"],
             "lot": opt["lot"], "lots": lots, "entry_ts": now.timestamp(), "entry": entry,
             "target": round(entry * (1 + c["target_pct"] / 100), 2), "stop": round(entry * (1 - c["stop_pct"] / 100), 2),
             "peak": entry, "last": inst.ltp, "status": "open", "manual": int(manual)}
        tid = self.store.add_trade(t)
        self.log(f"Paper BUY {opt['label']} @ {entry:.2f} ({alert['title']})")
        return tid

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
        if booked <= limit:
            blocked, why = True, f"Daily loss limit hit (₹{booked:,.0f}). No new trades today."
        elif len(today) >= int(c["max_trades_day"]):
            blocked, why = True, f"{len(today)} trades today: over-trading guard reached."
        return {"blocked": blocked, "why": why, "booked_today": round(booked, 2), "trades_today": len(today),
                "loss_limit": round(limit, 0), "risk_per_trade": round(float(c["capital"]) * float(c["risk_pct"]) / 100, 0)}

    def update(self, state, now: dt.datetime, force_close=False):
        c = self.cfg
        closed = []
        for t in self.open_trades():
            inst = state.get(t["opt_key"])
            if not inst or not inst.ltp:
                if force_close:
                    self._close(t, t["last"] or t["entry"], now, "market closed")
                continue
            bid = inst.bid or inst.ltp
            peak = max(t["peak"] or 0, inst.ltp)
            held = (now.timestamp() - t["entry_ts"]) / 60
            reason = None
            if bid >= t["target"]:
                reason = "target"
            elif bid <= t["stop"]:
                reason = "stop loss"
            elif force_close or now.strftime("%H:%M") >= c["exit_time"]:
                reason = "end of day"
            elif held >= c["max_hold_min"]:
                reason = "time limit"
            if reason:
                closed.append(self._close(t, bid, now, reason))
            else:
                self.store.update_trade(t["id"], peak=peak, last=inst.ltp)
        return closed

    def close_manual(self, tid, state, now):
        for t in self.open_trades():
            if t["id"] == tid:
                inst = state.get(t["opt_key"])
                px = (inst.bid or inst.ltp) if inst and inst.ltp else (t["last"] or t["entry"])
                return self._close(t, px, now, "closed by you")
        return None

    def _close(self, t, px, now, reason):
        qty = t["lot"] * t["lots"]
        gross = round((px - t["entry"]) * qty, 2)
        cst = costs(t["entry"], px, qty)
        self.store.update_trade(t["id"], exit_ts=now.timestamp(), exit=px, reason=reason, status="closed",
                                gross=gross, costs=cst, net=round(gross - cst, 2), last=px)
        self.log(f"Paper SELL {t['label']} @ {px:.2f} ({reason}) net ₹{gross - cst:,.0f}")
        return {**t, "exit": px, "reason": reason, "net": gross - cst}

    def summary(self, state=None) -> dict:
        trades = self.store.trades(source=self.source, limit=2000)
        closed = [t for t in trades if t["status"] == "closed"]
        opened = [t for t in trades if t["status"] == "open"]
        for t in opened:
            inst = state.get(t["opt_key"]) if state else None
            ltp = inst.ltp if inst and inst.ltp else t["last"] or t["entry"]
            t["unreal"] = round((ltp - t["entry"]) * t["lot"] * t["lots"], 2)
            t["last"] = ltp
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
                "guard": self.guard(dt.datetime.now(IST) if state is None else (state.clock.now() if getattr(state, "clock", None) else dt.datetime.now(IST)))}
