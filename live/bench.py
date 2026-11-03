"""Test bench: an honest paper trade for every signal, so every strategy gets a live record after costs.

Every directional alert (Focus or not, demoted or not), every idea on trial, and every Strategy Lab library / book rule
that fires live gets one virtual trade per style, 1 lot, the same way the paper account fills: buy at the ask, sell at
the bid, every charge (STT 0.15%, brokerage, exchange, GST, stamp from rules/costs.json), the paper account's target /
stop / time limit, closed by 15:15. No account limits apply here (no daily cap, no risk check), so the record grows as
fast as the market gives signals. The paper account itself keeps its own rules and is not touched.

Styles (bullets in STYLES below): "buy" = the suggested at-the-money option; "spread" = the same option bought and
the one `width` strikes further out sold (a defined-risk debit spread: less theta, less vega, smaller cost).

Each trade records the day type (trend / range / quiet / volatile), days to expiry, minute of the day and the money it
needs per lot, so the Strategy board can say which strategy works on which kind of day for which capital.
Market data only: nothing is ever sent to a broker.
"""
from __future__ import annotations

import json
import math
import time

SQL = """
CREATE TABLE IF NOT EXISTS bench_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, source TEXT, sym TEXT, kind TEXT, variant TEXT, name TEXT,
  style TEXT, direction TEXT, regime TEXT, dte INTEGER, mins INTEGER, is_index INTEGER, alert_id INTEGER,
  key1 TEXT, key2 TEXT, lot INTEGER, entry REAL, cost_lot REAL, risk_lot REAL, target REAL, stop REAL, hold_min REAL,
  exit_ts REAL, exit REAL, reason TEXT, gross REAL, charges REAL, net REAL, r_mult REAL, peak_pct REAL, low_pct REAL,
  data TEXT);
CREATE INDEX IF NOT EXISTS bench_kind ON bench_trades(source, kind, variant, style);
CREATE INDEX IF NOT EXISTS bench_day ON bench_trades(source, day);
"""
STYLES = {
    "buy": {"label": "Option buy", "legs": 1},
    "spread": {"label": "Debit spread", "legs": 2, "width": 2},
}
COOLDOWN_S = 300          # the same signal (symbol, kind, version, direction) once per 5 minutes
FIT = {"max_lot_share": 0.5}   # an account "fits" a strategy if one lot costs at most this share of capital (and the stop
                               # loss stays inside its risk per trade)


def _charges(buy_value: float, sell_value: float) -> float:
    from lab.engine import charge_amount
    return float(charge_amount(buy_value, sell_value, 2))


class Bench:
    def __init__(self, store, source, log=print):
        self.store, self.source, self.log = store, source, log
        self.open: list[dict] = []
        self.cool: dict = {}
        with store.lock:
            store.db.executescript(SQL)
            if source == "sim":
                store.db.execute("DELETE FROM bench_trades WHERE source='sim'")
            store.db.commit()

    # ------------------------------------------------------------ opening
    def add(self, a: dict, engine, now, cfg: dict, name: str | None = None, variant: str | None = None) -> int:
        """One virtual trade per style for a directional signal with a suggested option. Returns trades opened."""
        d = {"up": 1, "down": -1}.get(a.get("direction"))
        opt = a.get("option") or {}
        if not d or not opt.get("key"):
            return 0
        variant = variant or ((a.get("data") or {}).get("factory") or {}).get("id") or (a.get("data") or {}).get("trial_key")
        key = (a["sym"], a["kind"], variant, d)
        ts = now.timestamp()
        if ts - self.cool.get(key, -1e18) < COOLDOWN_S:
            return 0
        self.cool[key] = ts
        hm = now.strftime("%H:%M")
        if hm < "09:16" or hm >= (cfg.get("no_entry_after") or "14:45"):
            return 0
        state = engine.state
        i1 = state.get(opt["key"]) if state is not None else None
        if not i1 or not (i1.ask or i1.ltp):
            return 0
        u = engine.uni.underlyings.get(a["sym"]) if engine.uni else None
        reg = (engine.regimes.get(a["sym"]) or engine.regimes.get("NIFTY") or {}).get("code")
        exp = None
        try:
            import datetime as _dt
            exp = _dt.date.fromisoformat(opt["expiry"]) if opt.get("expiry") else None
        except Exception:
            exp = None
        import numpy as np
        dte = int(np.busday_count(now.date(), exp)) if exp else None
        mins = int((ts - now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()) // 60)
        base = {"ts": ts, "day": now.date().isoformat(), "source": self.source, "sym": a["sym"], "kind": a["kind"],
                "variant": variant, "name": name, "direction": a["direction"], "regime": reg, "dte": dte, "mins": mins,
                "is_index": int(bool(u and u.is_index)), "alert_id": a.get("id"), "lot": int(opt.get("lot") or 1)}
        tp, sp = float(cfg.get("target_pct") or 30) / 100, float(cfg.get("stop_pct") or 20) / 100
        hold = float(cfg.get("max_hold_min") or 60)
        n = 0
        for style, spec in STYLES.items():
            legs = [(opt["key"], 1)]
            if spec["legs"] == 2:
                k2 = self._far_key(u, opt, int(spec.get("width") or 2))
                if not k2:
                    continue
                legs.append((k2, -1))
            px = self._value(state, legs, side="open")
            if px is None or px <= 0:
                continue
            lot = base["lot"]
            row = {**base, "style": style, "key1": legs[0][0], "key2": legs[1][0] if len(legs) > 1 else None, "entry": round(px, 2),
                   "cost_lot": round(px * lot, 2), "risk_lot": round(px * lot * sp + _charges(px * lot, px * (1 - sp) * lot), 2),
                   "target": round(px * (1 + tp), 2), "stop": round(px * (1 - sp), 2), "hold_min": hold,
                   "data": json.dumps({"open_legs": self._leg_prices(state, legs, "open")})}
            cols = [c for c in row if c != "id"]
            with self.store.lock:
                cur = self.store.db.execute(f"INSERT INTO bench_trades({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                            [row[c] for c in cols])
                self.store.db.commit()
                row["id"] = cur.lastrowid
            row["_legs"], row["_peak"], row["_low"] = legs, px, px
            self.open.append(row)
            n += 1
        return n

    @staticmethod
    def _far_key(u, opt, width) -> str | None:
        """The option `width` strikes further out of the money than the bought one (same expiry, same side)."""
        if not u:
            return None
        try:
            import datetime as _dt
            ch = u.chains.get(_dt.date.fromisoformat(opt["expiry"])) or {}
        except Exception:
            return None
        ks = sorted(ch)
        k = opt.get("strike")
        if k not in ch:
            return None
        i = ks.index(k)
        j = i + width if opt.get("side") == "CE" else i - width
        if not 0 <= j < len(ks):
            return None
        o = (ch.get(ks[j]) or {}).get(opt["side"])
        return o.key if o else None

    @staticmethod
    def _leg_prices(state, legs, side) -> list:
        out = []
        for k, q in legs:
            i = state.get(k) if state is not None else None
            if not i or not i.ltp:
                return []
            buy_now = (q > 0) == (side == "open")             # paying the ask, or receiving the bid
            out.append(float((i.ask or i.ltp) if buy_now else (i.bid or i.ltp)))
        return out

    @classmethod
    def _value(cls, state, legs, side) -> float | None:
        """Net price per unit: opening = what you pay, closing = what you get (long legs at bid, short legs at ask)."""
        p = cls._leg_prices(state, legs, side)
        if len(p) != len(legs):
            return None
        return sum(px * q for px, (_, q) in zip(p, legs))

    # ------------------------------------------------------------ following
    def update(self, state, now_ts: float, exit_time="15:15", force=False):
        if not self.open:
            return
        import datetime as _dt
        from .clock import IST
        hm = _dt.datetime.fromtimestamp(now_ts, IST).strftime("%H:%M")
        keep, done = [], []
        for r in self.open:
            v = self._value(state, r["_legs"], side="close")
            if v is None:
                if force:
                    v = r.get("_last") or r["entry"]
                else:
                    keep.append(r)
                    continue
            r["_last"] = v
            r["_peak"], r["_low"] = max(r["_peak"], v), min(r["_low"], v)
            held = (now_ts - r["ts"]) / 60
            why = ("target" if v >= r["target"] else "stop loss" if v <= r["stop"] else
                   "end of day" if force or hm >= exit_time else "time limit" if held >= r["hold_min"] else None)
            if not why:
                keep.append(r)
                continue
            lot = r["lot"]
            p_open = json.loads(r["data"]).get("open_legs") or []
            p_close = self._leg_prices(state, r["_legs"], "close") or []
            if len(p_open) == len(r["_legs"]) and len(p_close) == len(r["_legs"]):
                ch = 0.0
                for (k, q), po, pc in zip(r["_legs"], p_open, p_close):
                    ch += _charges(po * lot, pc * lot) if q > 0 else _charges(pc * lot, po * lot)
            else:
                ch = _charges(r["entry"] * lot, v * lot)
            gross = (v - r["entry"]) * lot
            risk = r["risk_lot"] or 1
            r.update(exit_ts=now_ts, exit=round(v, 2), reason=why, gross=round(gross, 2), charges=round(ch, 2),
                     net=round(gross - ch, 2), r_mult=round((gross - ch) / risk, 2),
                     peak_pct=round((r["_peak"] / r["entry"] - 1) * 100, 1), low_pct=round((r["_low"] / r["entry"] - 1) * 100, 1))
            done.append(r)
        self.open = keep
        if done:
            with self.store.lock:
                self.store.db.executemany(
                    "UPDATE bench_trades SET exit_ts=?, exit=?, reason=?, gross=?, charges=?, net=?, r_mult=?, peak_pct=?, low_pct=? WHERE id=?",
                    [(r["exit_ts"], r["exit"], r["reason"], r["gross"], r["charges"], r["net"], r["r_mult"], r["peak_pct"],
                      r["low_pct"], r["id"]) for r in done])
                self.store.db.commit()


# ---------------------------------------------------------------- the Strategy board
def fits(row_cost: float, row_risk: float, capital: float, risk_pct: float) -> bool:
    """Can this account take one lot: the loss at the stop within its risk per trade, and the lot affordable."""
    return row_risk <= capital * risk_pct / 100 and row_cost <= capital * FIT["max_lot_share"]


def board(store, source, capital: float, risk_pct: float, regime: str | None = None, days: int = 60, today: str | None = None,
          min_n: int = 5) -> dict:
    """Every strategy (alert type / Lab rule / idea on trial) x style, ranked by money made per trade after costs.

    rank = average net per trade minus one standard error (a strategy needs both a good average and enough trades to
    be trusted). Columns for all days, the chosen day type, and today; 'fits' = the account can take one lot."""
    from . import registry
    try:
        with store.lock:
            rows = [dict(r) for r in store.db.execute(
                "SELECT kind, variant, name, style, regime, day, net, r_mult, cost_lot, risk_lot, is_index FROM bench_trades "
                "WHERE source=? AND net IS NOT NULL AND day >= date('now', ?)", (source, f"-{days} day")).fetchall()]
            n_open = store.db.execute("SELECT COUNT(*) FROM bench_trades WHERE source=? AND net IS NULL", (source,)).fetchone()[0]
    except Exception:
        rows, n_open = [], 0
    groups = {}
    for r in rows:
        g = groups.setdefault((r["kind"], r["variant"] or "", r["style"]), {"all": [], "reg": [], "today": [], "cost": [], "risk": [],
                                                                          "name": r["name"], "days": set()})
        g["all"].append(r["net"])
        g["cost"].append(r["cost_lot"] or 0)
        g["risk"].append(r["risk_lot"] or 0)
        g["days"].add(r["day"])
        if regime and r["regime"] == regime:
            g["reg"].append(r["net"])
        if today and r["day"] == today:
            g["today"].append(r["net"])

    def stats(xs):
        if not xs:
            return {"n": 0}
        n = len(xs)
        avg = sum(xs) / n
        sd = math.sqrt(sum((x - avg) ** 2 for x in xs) / (n - 1)) if n > 1 else abs(avg)
        wins = [x for x in xs if x > 0]
        losses = [-x for x in xs if x < 0]
        return {"n": n, "avg": round(avg), "total": round(sum(xs)), "win_pct": round(len(wins) / n * 100),
                "pf": round(sum(wins) / sum(losses), 2) if losses else None, "worst": round(min(xs)),
                "rank": round(avg - sd / math.sqrt(n)) if n >= min_n else None}
    out = []
    for (kind, variant, style), g in groups.items():
        cost = sorted(g["cost"])[len(g["cost"]) // 2] if g["cost"] else 0
        risk = sorted(g["risk"])[len(g["risk"]) // 2] if g["risk"] else 0
        label = g["name"] or registry.label(kind) + (f" {variant}" if variant else "")
        out.append({"kind": kind, "variant": variant or None, "style": style, "style_label": STYLES.get(style, {}).get("label", style),
                    "label": label, "all": stats(g["all"]), "regime": stats(g["reg"]), "today": stats(g["today"]),
                    "days": len(g["days"]), "cost_lot": round(cost), "risk_lot": round(risk), "fits": fits(cost, risk, capital, risk_pct)})
    key = (lambda x: (x["regime"]["rank"] if x["regime"].get("rank") is not None else -1e12,
                      x["all"]["rank"] if x["all"].get("rank") is not None else -1e12)) if regime else \
        (lambda x: x["all"]["rank"] if x["all"].get("rank") is not None else -1e12)
    out.sort(key=key, reverse=True)
    best = [x for x in out if x["fits"] and ((x["regime"] if regime else x["all"]).get("rank") or -1) > 0][:5]
    return {"rows": out, "best": best, "n_closed": len(rows), "n_open": n_open, "capital": capital, "risk_pct": risk_pct,
            "regime": regime, "days": days, "min_n": min_n,
            "note": "Every signal paper-traded 1 lot like the paper account (ask in, bid out, every charge, the same target / "
                    "stop / time limit), with no daily limits. Rank = average net per trade minus its uncertainty: a strategy "
                    "needs a good average and enough trades. Not a promise: markets change."}
