"""Paper trading: buys the suggested option on strong alerts and manages exits, with real costs."""
from __future__ import annotations

import datetime as dt

from .clock import IST

DEFAULTS = {
    "auto": True,
    "min_severity": 2,
    "kinds": ["sharp_move", "orb", "volume_surge", "oi_buildup", "unusual_options", "beyond_priced", "factory"],
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

# the small-account preset (Paper page): ₹40K, 1 lot, 2 trades a day, the day ends after one loss, lock in a good day
SMALL_ACCOUNT = {"capital": 40000, "risk_pct": 5.0, "max_lots": 1, "max_open": 1, "max_trades_day": 2,
                 "max_daily_loss_pct": 4.0, "profit_lock": 1200, "aggressive": False}

MODES = {0: "auto", 1: "you", 2: "override", 3: "aggressive", 4: "you (chain/chart)"}


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
            elif alert["severity"] < c["min_severity"] or alert["kind"] not in c["kinds"]:
                return None
            hm = now.strftime("%H:%M")
            if hm < "09:16" or hm >= (c["exit_time"] if aggressive else c["no_entry_after"]):
                return None                               # never on pre-open or after-close prices
        opens = self.open_trades()
        cap = int(c.get("aggressive_max_open") or 15) if aggressive else int(c["max_open"])
        if any(t["sym"] == alert["sym"] for t in opens) or (not manual and len(opens) >= cap):
            return None
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
        entry = inst.ask or inst.ltp
        if override:
            lots = 1
        else:
            lots = int(risk["lots"]) if risk and risk.get("lots") else self.size(entry, opt["lot"])
        manual_code = 3 if aggressive else 2 if override else int(manual)
        if int(c.get("max_lots") or 5) == 1:
            lots = 1
        ex = ((alert.get("data") or {}).get("factory") or {}).get("exit")   # a Strategy Lab rule brings its own exit
        return self._open(state, now, sym=alert["sym"], kind=alert["kind"], opt=opt, entry=entry, ltp=inst.ltp, lots=lots,
                          direction=1, manual=manual_code, alert_id=alert.get("id"), und_key=alert.get("und_key"),
                          why=alert.get("title", ""), exit=ex)

    def open_manual(self, state, now: dt.datetime, *, sym: str, opt: dict, direction: int, lots: int = 1,
                    und_key: str | None = None, kind="manual") -> dict:
        """Buy (direction 1) or sell (direction -1) an option by hand from the option chain or the chart. Selling an
        option you hold closes it; otherwise it opens a short. Market hours only, live price only. Paper only."""
        hm = now.strftime("%H:%M")
        if now.weekday() >= 5 or hm < "09:15" or hm >= "15:30":
            raise ValueError("Market closed: paper fills only on live prices (09:15–15:30).")
        inst = state.get(opt["key"])
        if not inst or not inst.ltp:
            raise ValueError("No live price for this option right now.")
        for t in self.open_trades():
            if t["opt_key"] == opt["key"] and (t.get("dir") or 1) == -direction:
                return {"closed": self.close_manual(t["id"], state, now)}
        if self.guard(now)["blocked"] and not self.cfg.get("aggressive"):
            raise ValueError(self.guard(now)["why"])
        entry = (inst.ask or inst.ltp) if direction > 0 else (inst.bid or inst.ltp)
        tid = self._open(state, now, sym=sym, kind=kind, opt=opt, entry=entry, ltp=inst.ltp, lots=max(1, int(lots)),
                         direction=direction, manual=4, und_key=und_key, why="by hand")
        return {"opened": tid}

    def _open(self, state, now, *, sym, kind, opt, entry, ltp, lots, direction, manual, alert_id=None, und_key=None, why="",
              exit=None):
        c = self.cfg
        und = state.get(und_key) if und_key else None
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
        tid = self.store.add_trade(t)
        self.log(f"Paper {'BUY' if direction > 0 else 'SELL'} {opt['label']} @ {entry:.2f} ({why})")
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
            if (d > 0 and px >= t["target"]) or (d < 0 and px <= t["target"]):
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
                if und and und.ltp:
                    upd["und_last"] = und.ltp
                self.store.update_trade(t["id"], **upd)
        return closed

    def close_manual(self, tid, state, now):
        for t in self.open_trades():
            if t["id"] == tid:
                inst = state.get(t["opt_key"])
                d = t.get("dir") or 1
                px = ((inst.bid if d > 0 else inst.ask) or inst.ltp) if inst and inst.ltp else (t["last"] or t["entry"])
                und = state.get(t["und_key"]) if t.get("und_key") else None
                return self._close(t, px, now, "closed by you", und)
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
                "account": self.account(trades, opened),
                "guard": self.guard(dt.datetime.now(IST) if state is None else (state.clock.now() if getattr(state, "clock", None) else dt.datetime.now(IST)))}
