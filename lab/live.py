"""Run the backtested strategy plan live, as paper trades on real (or simulator) prices.

Same entry rules and the same exit rules as the backtest (lab.engine.Position), checked every second.
Portfolio guard: one daily loss limit across all strategies.
"""
from __future__ import annotations

import datetime as dt
import json
import math

import numpy as np

from .data import N_MIN
from .engine import Position, charges, fill_entry, slip
from .strategies import FAMILIES, NICE
from .walk import DEFAULTS as LAB_DEFAULTS, lab_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS strat_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, source TEXT, sym TEXT, family TEXT, pid TEXT, describe TEXT,
  lots INTEGER, lot INTEGER, legs TEXT, entry_ts REAL, exit_ts REAL, status TEXT, reason TEXT, premium REAL,
  max_loss REAL, margin REAL, gross REAL, costs REAL, net REAL, mtm REAL);
CREATE INDEX IF NOT EXISTS strat_day ON strat_trades(source, day);
"""


class LiveDay:
    """Looks like lab.data.Day to the strategy functions, filled from live state up to the current minute."""

    def __init__(self, sym, step, spot_hist, price_fn, date):
        self.sym, self.step, self.date = sym, step, date
        self.spot = spot_hist
        self._price = price_fn
        self.px = {}

    def atm(self, m):
        return round(self.spot[m] / self.step) * self.step

    def price(self, strike, side, m, kind="c"):
        return self._price(strike, side)


class StrategyDesk:
    def __init__(self, engine):
        self.e = engine
        self.store = engine.store
        with self.store.lock:
            self.store.db.executescript(SCHEMA)
            self.store.db.commit()
        self.plan = []
        self.inactive = []
        self.cfg = dict(LAB_DEFAULTS)
        self.plan_info = {}
        self.done_today = set()      # plan items that already entered (or were skipped) today
        self.status = {}             # pid -> text
        self.open = {}               # trade id -> (Position, record)
        self.day = None
        self.halted = None
        self.load_plan()

    # ------------------------------------------------------------ plan
    def load_plan(self):
        f = lab_dir(self.e.data_dir) / "plan.json"
        if f.exists():
            try:
                d = json.loads(f.read_text())
                self.plan = [p for p in d.get("plan", []) if p.get("active")]
                self.inactive = [p for p in d.get("plan", []) if not p.get("active")]
                self.plan_info = {"generated": d.get("generated"), "n": len(self.plan)}
                self.cfg.update(d.get("config") or {})
            except Exception as e:
                self.e.log(f"Strategy plan unreadable: {e}")
        lc = (self.e.cfg().get("lab") or {})
        for k in ("capital", "risk_pct", "alloc_pct"):
            if lc.get(k):
                self.cfg[k] = float(lc[k])
        self.cfg["daily_stop_pct"] = float(lc.get("daily_stop_pct") or 2.0)
        self.cfg["enabled"] = lc.get("auto_trade", True) is not False

    # ------------------------------------------------------------ helpers
    def _spot_hist(self, u, now_m):
        st = self.e.state
        bars = st.bars.get(u.symbol)
        arr = np.full(N_MIN, np.nan)
        if bars:
            day0 = dt.datetime.combine(self.e.clock.now().date(), dt.time(9, 15), self.e.clock.now().tzinfo).timestamp()
            for b in bars.bars:
                m = int((b[0] - day0) // 60)
                if 0 <= m < N_MIN:
                    arr[m] = b[4]
        i = st.get(u.key) or st.get(u.fut_key)
        if i and i.ltp and 0 <= now_m < N_MIN:
            arr[now_m] = i.ltp
        # forward fill up to now
        last = math.nan
        for m in range(0, min(now_m + 1, N_MIN)):
            if math.isnan(arr[m]):
                arr[m] = last
            else:
                last = arr[m]
        return arr

    def _option(self, u, exp, strike, side):
        return u.chains.get(exp, {}).get(float(strike), {}).get(side)

    def _ltp(self, key):
        i = self.e.state.get(key) if key else None
        return i.ltp if i and i.ltp else None

    def _features(self, u, m):
        st = self.e.state
        und = st.get(u.key)
        vix = st.get("NSE_INDEX|India VIX")
        exp = u.near_expiry(self.e.clock.now().date())
        f = {"date": self.e.clock.now().date(), "is_expiry": exp == self.e.clock.now().date()}
        if und and und.day_o and und.cp:
            f["gap_pct"] = (und.day_o / und.cp - 1) * 100
        if vix and vix.day_o and vix.cp:
            f["vix_open"], f["vix_prev"] = vix.day_o, vix.cp
        mm = self.e.metrics.get(u.symbol) or {}
        f["iv_open"] = (self.e.open_marks.get(u.symbol) or {}).get("iv_open") or (mm.get("opt") or {}).get("atm_iv")
        f["rv20_prev"] = (self.e.daily.get(u.symbol) or {}).get("hv20")
        return f

    def needed_keys(self) -> set:
        """Option keys the desk needs streamed (open legs)."""
        keys = set()
        for pos, rec in self.open.values():
            keys.update(l.key for l in pos.legs if l.key)
        return keys

    # ------------------------------------------------------------ main tick
    def tick(self, now: dt.datetime):
        if not self.plan or not self.e.uni:
            return
        day = now.date().isoformat()
        if self.day != day:
            self.day, self.done_today, self.status, self.halted = day, set(), {}, None
        m = int((now - dt.datetime.combine(now.date(), dt.time(9, 15), now.tzinfo)).total_seconds() // 60)
        if m < 0:
            return
        self._manage(now, m)
        self._guard(now, m)
        if self.halted or not self.cfg.get("enabled"):
            return
        for p in self.plan:
            key = p["pid"] + "|" + p["sym"]
            if key in self.done_today:
                continue
            u = self.e.uni.underlyings.get(p["sym"])
            if not u:
                self.status[key] = "Not available in this data feed"
                self.done_today.add(key)
                continue
            fam, params = p["family"], p["params"]
            last_entry = params.get("until", params.get("entry", 0))
            if m > max(last_entry, params.get("entry", 0)) + 1:
                self.done_today.add(key)
                self.status.setdefault(key, "No entry today: conditions not met")
                continue
            if fam == "orb_spread" and m < 15:
                self.status[key] = f"Watching for a breakout of the 9:15–9:30 range until {(dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=params['until'])):%H:%M}"
                continue
            if "entry" in params and m < params["entry"]:
                self.status[key] = f"Waiting for {(dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=params['entry'])):%H:%M}"
                continue
            exp = u.near_expiry(now.date())
            if not exp:
                continue
            strikes = u.strikes(exp)
            step = min(b - a for a, b in zip(strikes, strikes[1:])) if len(strikes) > 1 else 50

            def price(k, side, u=u, exp=exp):
                o = self._option(u, exp, k, side)
                return self._ltp(o.key) if o else None
            ld = LiveDay(p["sym"], step, self._spot_hist(u, m), price, now.date())
            if math.isnan(ld.spot[min(m, N_MIN - 1)]):
                continue
            f = self._features(u, m)
            fn = FAMILIES[fam][0]
            call = dict(params)
            if fam == "chart_setup":
                call["after"] = m - 6           # only act on a signal from the bar that just closed
            try:
                pos = fn(ld, f, call)
            except Exception as e:
                self.status[key] = f"Error: {e}"
                self.done_today.add(key)
                continue
            if pos is None:
                if fam == "orb_spread":
                    self.status[key] = f"Watching for a breakout until {(dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=params['until'])):%H:%M}"
                if "entry" in params and m >= params["entry"]:
                    from .strategies import blocked
                    why = blocked(f, params.get("flt", "loose"), fam in ("iron_fly", "iron_condor", "expiry_condor"))
                    self.status[key] = f"Skipped today: {why}" if why else "No entry today: conditions not met"
                    self.done_today.add(key)
                continue
            self._enter(p, u, exp, pos, now, key)

    def _enter(self, p, u, exp, pos: Position, now, key):
        for l in pos.legs:
            o = self._option(u, exp, l.strike, l.side)
            if not o:
                self.status[key] = f"Strike {l.strike:g} {l.side} not listed"
                self.done_today.add(key)
                return
            l.key = o.key
        lot = self._option(u, exp, pos.legs[0].strike, pos.legs[0].side).lot
        if not fill_entry(pos, lambda k, s: self._ltp(self._option(u, exp, k, s).key)):
            self.status[key] = "Waiting for option prices"
            return
        spot = pos.legs and self.e.state.get(u.key).ltp if self.e.state.get(u.key) else None
        max_loss = pos.max_loss_pts() * lot
        plan_loss = pos.planned_loss_pts() * lot
        margin = pos.margin_est(spot or pos.legs[0].strike, lot)
        cap = float(self.cfg["capital"])
        lots = int(max(0, min(cap * float(self.cfg["risk_pct"]) / 100 // max(1, plan_loss),
                              cap * float(self.cfg.get("worst_pct", 5)) / 100 // max(1, max_loss),
                              cap * float(self.cfg["alloc_pct"]) / 100 // max(1, margin), int(self.cfg.get("max_lots", 20)))))
        self.done_today.add(key)
        if lots < 1:
            self.status[key] = "Skipped: one lot is above your risk limit"
            return
        rec = {"day": now.date().isoformat(), "source": self.e.source, "sym": pos.sym, "family": pos.family, "pid": p["pid"],
               "describe": p.get("describe"), "lots": lots, "lot": lot, "legs": self._legs_json(pos), "entry_ts": now.timestamp(),
               "status": "open", "premium": pos.net_premium, "max_loss": max_loss * lots, "margin": margin * lots, "mtm": 0.0}
        cols = list(rec)
        with self.store.lock:
            cur = self.store.db.execute(f"INSERT INTO strat_trades({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                        [rec[c] for c in cols])
            self.store.db.commit()
            tid = cur.lastrowid
        rec["id"] = tid
        pos.meta.update(lot=lot, lots=lots, key=key, exp=exp.isoformat())
        self.open[tid] = (pos, rec)
        self.status[key] = f"Entered {lots} lot{'s' if lots > 1 else ''}"
        what = "credit" if pos.net_premium > 0 else "debit"
        self.e.log(f"Strategy {NICE[pos.family]} {pos.sym}: entered {lots} lots, {what} {abs(pos.net_premium):.1f}")
        self.e.telegram.send(f"🧭 <b>{NICE[pos.family]}</b> {pos.sym}: entered {lots} lot(s), {what} ₹{abs(pos.net_premium):.1f}/unit. "
                             f"Max loss ₹{max_loss * lots:,.0f}.")

    def _legs_json(self, pos):
        return json.dumps([{"strike": l.strike, "side": l.side, "q": l.q, "entry": l.entry, "exit": None if math.isnan(l.exit) else l.exit,
                            "sl": l.sl, "key": l.key} for l in pos.legs])

    def _manage(self, now, m):
        for tid, (pos, rec) in list(self.open.items()):
            lot, lots = pos.meta["lot"], pos.meta["lots"]
            prices = [self._ltp(l.key) for l in pos.legs]
            alive = [math.isnan(l.exit) for l in pos.legs]
            # per-leg stops (close the group)
            if pos.leg_sl_pct:
                groups = pos.groups or [[i] for i in range(len(pos.legs))]
                for g in groups:
                    si = next((i for i in g if pos.legs[i].q < 0), None)
                    if si is None or not alive[si] or prices[si] is None or pos.legs[si].sl is None:
                        continue
                    if prices[si] >= pos.legs[si].sl:
                        for i in g:
                            if alive[i] and prices[i] is not None:
                                self._exit_leg(pos.legs[i], prices[i], pos.sym, m)
                        if pos.trail_to_cost:
                            for l in pos.legs:
                                if math.isnan(l.exit) and l.q < 0:
                                    l.sl = min(l.sl or 1e18, l.entry)
                        rec["reason"] = "leg stop"
                alive = [math.isnan(l.exit) for l in pos.legs]
            mtm_pts = sum(l.q * (((prices[i] if prices[i] is not None else l.entry) if alive[i] else l.exit) - l.entry)
                          for i, l in enumerate(pos.legs))
            prem = abs(pos.net_premium) or 1.0
            reason = None
            if not any(alive):
                reason = rec.get("reason") or "leg stops"
            elif pos.stop_x and mtm_pts <= -pos.stop_x * prem:
                reason = "stop"
            elif pos.target_x and mtm_pts >= pos.target_x * prem:
                reason = "target"
            elif pos.spot_stop or pos.spot_target:
                u = self.e.uni.underlyings[pos.sym]
                i = self.e.state.get(u.key)
                px = i.ltp if i and i.ltp else None
                if px and pos.spot_stop:
                    lvl, d = pos.spot_stop
                    if (d == "below" and px < lvl) or (d == "above" and px > lvl):
                        reason = "spot stop"
                if px and not reason and pos.spot_target:
                    lvl, d = pos.spot_target
                    if (d == "above" and px >= lvl) or (d == "below" and px <= lvl):
                        reason = "spot target"
            if not reason and m >= pos.exit_m:
                reason = "time exit"
            if self.halted and not reason:
                reason = "daily loss limit"
            rec["mtm"] = mtm_pts * lot * lots
            if reason:
                for i, l in enumerate(pos.legs):
                    if math.isnan(l.exit):
                        self._exit_leg(l, prices[i] if prices[i] is not None else l.entry, pos.sym, m)
                self._close(tid, pos, rec, reason, now)
            else:
                with self.store.lock:
                    self.store.db.execute("UPDATE strat_trades SET mtm=?, legs=? WHERE id=?", (rec["mtm"], self._legs_json(pos), tid))
                    self.store.db.commit()

    def _exit_leg(self, l, px, sym, m):
        s = slip(sym, px)
        l.exit = px - s if l.q > 0 else px + s
        l.exit_m = m

    def _close(self, tid, pos, rec, reason, now):
        lot, lots = pos.meta["lot"], pos.meta["lots"]
        gross_pts = sum(l.q * (l.exit - l.entry) for l in pos.legs)
        fills = [(l.q, l.entry) for l in pos.legs] + [(-l.q, l.exit) for l in pos.legs]
        cost = charges(fills, lot) * lots
        gross = gross_pts * lot * lots
        net = gross - cost
        with self.store.lock:
            self.store.db.execute("UPDATE strat_trades SET status='closed', exit_ts=?, reason=?, gross=?, costs=?, net=?, mtm=?, legs=? WHERE id=?",
                                  (now.timestamp(), reason, gross, cost, net, net, self._legs_json(pos), tid))
            self.store.db.commit()
        self.open.pop(tid, None)
        self.status[pos.meta["key"]] = f"Closed ({reason}): ₹{net:,.0f}"
        self.e.log(f"Strategy {NICE[pos.family]} {pos.sym}: closed ({reason}) net ₹{net:,.0f}")
        self.e.telegram.send(f"{'✅' if net >= 0 else '🔻'} <b>{NICE[pos.family]}</b> {pos.sym} closed ({reason}): ₹{net:,.0f} after charges.")

    def _guard(self, now, m):
        if self.halted:
            return
        today = self.trades(day=now.date().isoformat())
        pnl = sum((t["net"] if t["status"] == "closed" else t["mtm"]) or 0 for t in today)
        limit = -float(self.cfg["capital"]) * float(self.cfg["daily_stop_pct"]) / 100
        if pnl <= limit:
            self.halted = f"Daily loss limit ₹{abs(limit):,.0f} reached (₹{pnl:,.0f}). All strategy positions closed for today."
            self.e.log(self.halted)
            self.e.telegram.send("⛔ " + self.halted)

    def end_of_day(self, now):
        for tid, (pos, rec) in list(self.open.items()):
            for l in pos.legs:
                if math.isnan(l.exit):
                    self._exit_leg(l, self._ltp(l.key) or l.entry, pos.sym, N_MIN - 1)
            self._close(tid, pos, rec, "end of day", now)

    # ------------------------------------------------------------ views
    def trades(self, day=None, source=None, limit=2000):
        q, a = "SELECT * FROM strat_trades WHERE source=?", [source or self.e.source or "sim"]
        if day:
            q += " AND day=?"
            a.append(day)
        q += " ORDER BY id DESC LIMIT ?"
        a.append(limit)
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute(q, a).fetchall()]
        for r in rows:
            r["legs"] = json.loads(r["legs"]) if r.get("legs") else []
        return rows

    def view(self, source=None) -> dict:
        src = source or self.e.source or (self.e.cfg().get("live") or {}).get("source") or "sim"
        from live.clock import IST
        day = (self.e.clock.now() if self.e.running else dt.datetime.now(IST)).date().isoformat()
        today = self.trades(day=day, source=src)
        allt = self.trades(source=src)
        closed = [t for t in allt if t["status"] == "closed"]
        days = {}
        for t in closed:
            days[t["day"]] = days.get(t["day"], 0.0) + (t["net"] or 0)
        dl = sorted(days.items())
        vals = [v for _, v in dl]
        plan = []
        for p in self.plan:
            k = p["pid"] + "|" + p["sym"]
            plan.append({**p, "status": self.status.get(k, "Waits for market hours")})
        return {"source": src, "plan": plan, "inactive": self.inactive, "plan_info": self.plan_info, "halted": self.halted, "config": self.cfg,
                "today": today, "today_pnl": sum((t["net"] if t["status"] == "closed" else t["mtm"]) or 0 for t in today),
                "days": [{"day": d, "pnl": v} for d, v in dl[-120:]],
                "stats": {"days": len(vals), "avg_day": sum(vals) / len(vals) if vals else None,
                          "green_pct": sum(v > 0 for v in vals) / len(vals) * 100 if vals else None,
                          "total": sum(vals), "worst": min(vals) if vals else None, "best": max(vals) if vals else None},
                "recent": closed[:60]}
