"""Test bench: an honest paper trade for every signal, so every strategy gets a live record after costs.

Every directional alert (Focus or not, demoted or not), every idea on trial, every Strategy Lab library / book rule that
fires live and every mover test (2.31) gets one virtual trade per style, 1 lot, the same way the paper account fills:
buy at the ask, sell at the bid, every charge (STT 0.15%, brokerage, exchange, GST, stamp from rules/costs.json), the
paper account's target / stop / time limit unless the style says otherwise, closed by 15:15. No account limits apply
here (no daily cap, no risk check), so the record grows as fast as the market gives signals. The paper account itself
keeps its own rules and is not touched.

Styles are bullets in rules/bench_styles.json: any mix of bought and sold options ('with' = the side that gains if the
signal is right, 'against' = the other side; offset in strikes from at-the-money). Family: only buying, buying + selling,
only selling. "buy" and "spread" are the 2.30 styles and fill exactly as before. A broken style is skipped and listed
in style_errors; the rest keep running. A style whose legs have no live price (most stocks stream only the
at-the-money strike) is skipped for that signal and counted in `skips`.

Each trade records the legs, the day type, days to expiry, minute of the day, the money it blocks per lot (premium +
estimated margin, live/margin.py) and the facts at entry (trend, VIX, IV, PCR, news, results, events, world...), so the
Strategy board and the capital accounts (live/accounts.py) can say which strategy works on which kind of day for which
capital, and every trade can be re-tested later on the recorded minute prices. Market data only: nothing is ever sent
to a broker.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path

SQL = """
CREATE TABLE IF NOT EXISTS bench_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, source TEXT, sym TEXT, kind TEXT, variant TEXT, name TEXT,
  style TEXT, direction TEXT, regime TEXT, dte INTEGER, mins INTEGER, is_index INTEGER, alert_id INTEGER,
  key1 TEXT, key2 TEXT, lot INTEGER, entry REAL, cost_lot REAL, risk_lot REAL, target REAL, stop REAL, hold_min REAL,
  exit_ts REAL, exit REAL, reason TEXT, gross REAL, charges REAL, net REAL, r_mult REAL, peak_pct REAL, low_pct REAL,
  data TEXT);
CREATE INDEX IF NOT EXISTS bench_kind ON bench_trades(source, kind, variant, style);
CREATE INDEX IF NOT EXISTS bench_day ON bench_trades(source, day);
CREATE TABLE IF NOT EXISTS bench_signals (
  sig TEXT PRIMARY KEY, source TEXT, ts REAL, day TEXT, sym TEXT, kind TEXT, variant TEXT, direction TEXT, alert_id INTEGER,
  ctx TEXT, tag TEXT);
CREATE INDEX IF NOT EXISTS bench_sig_day ON bench_signals(source, day);
"""
# bench_signals (2.31): one row per signal with the facts at entry (trend, VIX, IV, PCR, news, results, events, world...)
# and how the app rated it (severity, score, conviction, Focus); the trades of that signal (one per style) share its sig.
NEW_COLS = {"family": "TEXT", "need_lot": "REAL", "max_loss": "REAL", "sig": "TEXT"}     # 2.31
STYLE_FILE = Path(__file__).resolve().parents[1] / "rules" / "bench_styles.json"
BUILTIN = {                                     # the 2.30 styles: used when the file is missing
    "buy": {"label": "Buy ATM", "legs": [{"side": "with", "offset": 0, "q": 1}], "exit": {}},
    "spread": {"label": "Debit spread", "legs": [{"side": "with", "offset": 0, "q": 1}, {"side": "with", "offset": 2, "q": -1}],
               "exit": {}},
}
FAMILIES = {"buy": "Only buying", "mixed": "Buying + selling", "sell": "Only selling"}
DEFAULT_STYLE = {"buy": "buy", "mixed": "spread", "sell": "sell"}      # one style per family when a signal is taken plainly
COOLDOWN_S = 300          # the same signal (symbol, kind, version, direction) once per 5 minutes
FIT = {"max_lot_share": 0.5,       # an account "fits" a bought option if one lot costs at most this share of capital
       "max_margin_share": 0.9}    # ... and a trade with sold options if the money it blocks (margin) fits in this share
                                   # (and, for both, the loss at the stop stays inside its risk per trade)


def family_of(spec: dict) -> str:
    qs = {int(l["q"]) for l in spec.get("legs") or []}
    return "buy" if qs == {1} else "sell" if qs == {-1} else "mixed"


def _validate(name: str, spec: dict) -> dict:
    legs = spec.get("legs")
    if not isinstance(legs, list) or not legs:
        raise ValueError("needs at least one leg")
    out = []
    for l in legs:
        side, off, q = l.get("side"), l.get("offset", 0), l.get("q")
        if side not in ("with", "against") or not isinstance(off, int) or q not in (1, -1):
            raise ValueError(f"bad leg {l}")
        out.append({"side": side, "offset": off, "q": q})
    ex = spec.get("exit") or {}
    for k in ("target_pct", "stop_pct", "hold_min"):
        if ex.get(k) is not None and not (isinstance(ex[k], (int, float)) and ex[k] > 0):
            raise ValueError(f"exit {k} must be a positive number or null")
    s = {"label": str(spec.get("label") or name), "legs": out, "exit": dict(ex), "since": spec.get("since")}
    s["family"] = family_of(s)
    return s


_LOADED: dict = {}


def load_styles() -> tuple[dict, list]:
    """(styles, errors). Re-read when the file changes; a broken entry is skipped, never the whole bench."""
    try:
        mt = STYLE_FILE.stat().st_mtime
    except Exception:
        mt = None
    if _LOADED.get("mt") == mt and "styles" in _LOADED:
        return _LOADED["styles"], _LOADED["errors"]
    errors, raw = [], BUILTIN
    if mt is not None:
        try:
            raw = json.loads(STYLE_FILE.read_text()).get("styles") or BUILTIN
        except Exception as e:
            errors.append(f"{STYLE_FILE.name}: {e}")
            raw = BUILTIN
    styles = {}
    for name, spec in raw.items():
        try:
            styles[name] = _validate(name, spec)
        except Exception as e:
            errors.append(f"style {name}: {e}")
    if not styles:
        styles = {n: _validate(n, s) for n, s in BUILTIN.items()}
    _LOADED.update(mt=mt, styles=styles, errors=errors)
    return styles, errors


STYLES = load_styles()[0]


# ---------------------------------------------------------------- the bench's own database file
class BenchDB:
    """data/bench.db (2.31): the test bench's trades live in their own SQLite file (WAL), with their own lock, so the
    tens of thousands of test trades a day, and the Strategy board / capital accounts reading them, can never slow
    down or lock the live engine's main database (alerts, paper account). Readers use their own read-only connection."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
            self.db.executescript(SQL)
            have = {r[1] for r in self.db.execute("PRAGMA table_info(bench_trades)").fetchall()}
            for c, t in NEW_COLS.items():
                if c not in have:
                    self.db.execute(f"ALTER TABLE bench_trades ADD COLUMN {c} {t}")
            self.db.execute("CREATE TABLE IF NOT EXISTS bench_kv (k TEXT PRIMARY KEY, v TEXT)")
            self.db.commit()

    def reader(self) -> sqlite3.Connection:
        """A separate read-only connection (close it when done): reads never wait for, or block, the bench's writes."""
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def migrate_from(self, store):
        """Once: copy the 2.30 bench trades that were kept in the main database (the originals stay where they are)."""
        with self.lock:
            if self.db.execute("SELECT v FROM bench_kv WHERE k='migrated'").fetchone():
                return 0
        try:
            with store.lock:
                cols = {r[1] for r in store.db.execute("PRAGMA table_info(bench_trades)").fetchall()}
                rows = [dict(r) for r in store.db.execute("SELECT * FROM bench_trades").fetchall()] if cols else []
        except Exception:
            rows = []
        n = 0
        with self.lock:
            mine = {r[1] for r in self.db.execute("PRAGMA table_info(bench_trades)").fetchall()}
            for r in rows:
                ks = [k for k in r if k in mine]
                self.db.execute(f"INSERT OR IGNORE INTO bench_trades({','.join(ks)}) VALUES ({','.join('?' * len(ks))})", [r[k] for k in ks])
                n += 1
            self.db.execute("INSERT OR REPLACE INTO bench_kv VALUES ('migrated', ?)", (json.dumps({"rows": n, "at": time.time()}),))
            self.db.commit()
        return n


_DBS: dict = {}
_DBS_LOCK = threading.Lock()


def db_for(store) -> BenchDB:
    """The bench database next to the main one (one writer per file in this process)."""
    base = Path(getattr(store, "path", None) or "data/live.db")
    p = (base.parent / "bench.db").resolve()
    with _DBS_LOCK:
        b = _DBS.get(str(p))
        if b is None:
            b = _DBS[str(p)] = BenchDB(p)
            try:
                b.migrate_from(store)
            except Exception:
                pass
        return b


def _charges(buy_value: float, sell_value: float) -> float:
    from lab.engine import charge_amount
    return float(charge_amount(buy_value, sell_value, 2))


class Bench:
    def __init__(self, store, source, log=print):
        self.store, self.source, self.log = store, source, log
        self.open: list[dict] = []
        self.cool: dict = {}
        self.skips: dict = {}            # style -> signals it could not price (no live price for a leg)
        self.opened: dict = {}           # style -> trades opened this session
        self.stale_skips: dict = {}      # style -> signals skipped because a leg's quote was old or suspect (2.33)
        self.bdb = db_for(store)
        with self.bdb.lock:
            if source == "sim":
                self.bdb.db.execute("DELETE FROM bench_trades WHERE source='sim'")
                self.bdb.db.execute("DELETE FROM bench_signals WHERE source='sim'")
            self.bdb.db.commit()
        styles, errs = load_styles()
        for e in errs:
            log(f"Test bench: {e} (skipped)")

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
        is_index = bool(u and u.is_index)
        sig = f"{int(ts)}|{a['sym']}|{a['kind']}|{variant or ''}|{d}"
        base = {"ts": ts, "day": now.date().isoformat(), "source": self.source, "sym": a["sym"], "kind": a["kind"],
                "variant": variant, "name": name, "direction": a["direction"], "regime": reg, "dte": dte, "mins": mins,
                "is_index": int(is_index), "alert_id": a.get("id"), "lot": int(opt.get("lot") or 1), "sig": sig}
        ctx = None
        facts = getattr(engine, "signal_facts", None)
        if callable(facts):
            try:
                ctx = facts(a["sym"], a["direction"], now)
            except Exception:
                ctx = None
        ad = a.get("data") or {}
        tag = {k: v for k, v in (("sev", a.get("severity")), ("score", ad.get("score")), ("conv", ad.get("conviction")),
                                 ("focus", ad.get("focus")), ("muted", bool(ad.get("muted")) if "muted" in ad else None),
                                 ("focus_ch", [n for n, r in (ad.get("focus_ch") or {}).items() if (r or {}).get("pick")] or None))
               if v is not None}
        styles, _ = load_styles()
        guard = getattr(engine, "dguard", None)
        rows = []
        for style, spec in styles.items():
            legs, info = self._legs(u, opt, spec)
            if not legs:
                self.skips[style] = self.skips.get(style, 0) + 1
                continue
            p_open = self._leg_prices(state, legs, "open")
            if len(p_open) != len(legs) or min(p_open) <= 0:
                self.skips[style] = self.skips.get(style, 0) + 1
                continue
            if guard is not None and not all(guard.fresh(k) for k, _ in legs):
                self.stale_skips[style] = self.stale_skips.get(style, 0) + 1   # an old or suspect quote is no fill
                continue
            px = sum(p * q for p, (_, q) in zip(p_open, legs))
            if abs(px) < 1e-9:
                continue
            lot = base["lot"]
            ex = spec.get("exit") or {}
            tp = float(ex.get("target_pct") or cfg.get("target_pct") or 30) / 100
            sp = float(ex.get("stop_pct") or cfg.get("stop_pct") or 20) / 100
            hold = 10 ** 6 if ex.get("to_close") else float(ex.get("hold_min") or cfg.get("max_hold_min") or 60)
            basis = abs(px)                    # premium paid (or received, for a credit trade): target / stop are % of it
            for l, p in zip(info, p_open):
                l["price"] = p
            try:
                from .margin import money_needed
                mon = money_needed(info, lot, is_index, now.date(), expiry_day=bool(exp and exp == now.date()))
            except Exception:
                mon = {"need": basis * lot if px > 0 else None, "max_loss": None}
            row = {**base, "style": style, "family": spec["family"], "key1": legs[0][0], "key2": legs[1][0] if len(legs) > 1 else None,
                   "entry": round(px, 2), "cost_lot": round(px * lot, 2),
                   "risk_lot": round(basis * lot * sp + _charges(basis * lot, basis * (1 - sp) * lot), 2),
                   "target": round(px + tp * basis, 2), "stop": round(px - sp * basis, 2), "hold_min": hold,
                   "need_lot": mon.get("need"), "max_loss": mon.get("max_loss"),
                   "data": json.dumps({"open_legs": p_open, "legs": [{k: l[k] for k in ("key", "side", "strike", "q")} for l in info],
                                       "money": mon}, default=str)}
            row["_legs"], row["_peak"], row["_low"], row["_basis"] = legs, px, px, basis
            rows.append(row)
        if not rows:
            return 0
        cols = [c for c in rows[0] if not c.startswith("_")]
        with self.bdb.lock:
            db = self.bdb.db
            db.execute("INSERT OR IGNORE INTO bench_signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (sig, self.source, ts, base["day"], a["sym"], a["kind"], variant, a["direction"], a.get("id"),
                        json.dumps(ctx, default=str) if ctx else None, json.dumps(tag) if tag else None))
            for row in rows:
                cur = db.execute(f"INSERT INTO bench_trades({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
                row["id"] = cur.lastrowid
            db.commit()
        for row in rows:
            self.opened[row["style"]] = self.opened.get(row["style"], 0) + 1
        self.open.extend(rows)
        return len(rows)

    def _legs(self, u, opt, spec) -> tuple[list, list]:
        """[(instrument key, q)] and [{key, side, strike, q}] for a style, or ([], []) when a strike isn't there."""
        legs, info = [], []
        for l in spec["legs"]:
            side = opt.get("side") if l["side"] == "with" else ("PE" if opt.get("side") == "CE" else "CE")
            if l["side"] == "with" and l["offset"] == 0:
                k = opt["key"]                     # the suggested option itself (as in 2.30)
            else:
                k = self._strike_key(u, opt, side, l["offset"])
            if not k:
                return [], []
            legs.append((k, l["q"]))
            info.append({"key": k, "side": side, "strike": self._strike_of(u, opt, l["offset"], side), "q": l["q"]})
        return legs, info

    @staticmethod
    def _chain(u, opt):
        if not u:
            return {}
        try:
            import datetime as _dt
            return u.chains.get(_dt.date.fromisoformat(opt["expiry"])) or {}
        except Exception:
            return {}

    @classmethod
    def _strike_of(cls, u, opt, offset, side):
        ch = cls._chain(u, opt)
        ks = sorted(ch)
        k = opt.get("strike")
        if not offset:
            return k
        if k not in ch:
            return None
        i = ks.index(k)
        j = i + offset if side == "CE" else i - offset          # out of the money: higher calls, lower puts
        return ks[j] if 0 <= j < len(ks) else None

    @classmethod
    def _strike_key(cls, u, opt, side, offset) -> str | None:
        ch = cls._chain(u, opt)
        k = cls._strike_of(u, opt, offset, side)
        if k is None or k not in ch:
            return None
        o = (ch.get(k) or {}).get(side)
        return o.key if o else None

    @staticmethod
    def _far_key(u, opt, width) -> str | None:
        """The option `width` strikes further out of the money than the bought one (same expiry, same side)."""
        return Bench._strike_key(u, opt, opt.get("side"), width)

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
    def update(self, state, now_ts: float, exit_time="15:15", force=False, bad=None):
        """bad: optional callable(key) -> True while a leg's price is suspect (live/dataguard.py): no exit on it."""
        if not self.open:
            return
        import datetime as _dt
        from .clock import IST
        hm = _dt.datetime.fromtimestamp(now_ts, IST).strftime("%H:%M")
        keep, done = [], []
        for r in self.open:
            if bad is not None and not force and any(bad(k) for k, _ in r["_legs"]):
                keep.append(r)                        # a suspect price never closes a test trade
                continue
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
            b = r.get("_basis") or abs(r["entry"]) or 1
            r.update(exit_ts=now_ts, exit=round(v, 2), reason=why, gross=round(gross, 2), charges=round(ch, 2),
                     net=round(gross - ch, 2), r_mult=round((gross - ch) / risk, 2),
                     peak_pct=round((r["_peak"] - r["entry"]) / b * 100, 1), low_pct=round((r["_low"] - r["entry"]) / b * 100, 1))
            done.append(r)
        self.open = keep
        if done:
            with self.bdb.lock:
                self.bdb.db.executemany(
                    "UPDATE bench_trades SET exit_ts=?, exit=?, reason=?, gross=?, charges=?, net=?, r_mult=?, peak_pct=?, low_pct=? WHERE id=?",
                    [(r["exit_ts"], r["exit"], r["reason"], r["gross"], r["charges"], r["net"], r["r_mult"], r["peak_pct"],
                      r["low_pct"], r["id"]) for r in done])
                self.bdb.db.commit()

    def status(self) -> dict:
        styles, errs = load_styles()
        return {"styles": {k: {"label": v["label"], "family": v["family"], "opened": self.opened.get(k, 0),
                               "skipped": self.skips.get(k, 0), "stale": self.stale_skips.get(k, 0)} for k, v in styles.items()},
                "errors": errs, "open": len(self.open)}


# ---------------------------------------------------------------- the Strategy board
def fits(row_cost: float, row_risk: float, capital: float, risk_pct: float, need: float | None = None,
         family: str = "buy") -> bool:
    """Can this account take one lot: the loss at the stop within its risk per trade, and the lot affordable (bought
    options: the premium; trades with sold options: the money they block, premium + margin)."""
    if row_risk > capital * risk_pct / 100:
        return False
    if family == "buy" or need is None:
        return row_cost <= capital * FIT["max_lot_share"]
    return need <= capital * FIT["max_margin_share"]


def style_family(style: str, family: str | None = None) -> str:
    if family:
        return family
    s = load_styles()[0].get(style)
    return s["family"] if s else ("mixed" if style == "spread" else "buy")


GROUP_SQL = ("SELECT day, kind, IFNULL(variant, '') AS variant, style, family, regime, COUNT(*) AS n, SUM(net) AS s, "
             "SUM(net * net) AS s2, SUM(net > 0) AS wins, SUM(CASE WHEN net > 0 THEN net ELSE 0 END) AS win_s, "
             "SUM(CASE WHEN net < 0 THEN -net ELSE 0 END) AS loss_s, MIN(net) AS worst, AVG(cost_lot) AS cost, "
             "AVG(risk_lot) AS risk, AVG(need_lot) AS need, MAX(name) AS name, IFNULL(is_index, 0) AS is_index "
             "FROM bench_trades WHERE source=? AND net IS NOT NULL AND {where} "
             "GROUP BY day, kind, IFNULL(variant, ''), style, family, regime, IFNULL(is_index, 0)")
_DAYS: dict = {}            # (db path, source, day) -> grouped rows of a finished day (they no longer change)
_DAYS_LOCK = threading.Lock()


def day_groups(bdb: BenchDB, source: str, days: int, today: str | None = None) -> list:
    """Closed bench trades grouped by day x strategy x style x day type, for the last `days` days. Finished days are
    computed once and kept in memory; only today is read again. Uses its own read-only connection (never blocks)."""
    import datetime as _dt
    today = today or _dt.date.today().isoformat()
    first = (_dt.date.fromisoformat(today) - _dt.timedelta(days=days)).isoformat()
    con = bdb.reader()
    try:
        have = [r[0] for r in con.execute("SELECT DISTINCT day FROM bench_trades WHERE source=? AND day >= ? AND day < ?",
                                          (source, first, today)).fetchall()]
        with _DAYS_LOCK:
            missing = [d for d in have if (str(bdb.path), source, d) not in _DAYS]
        if missing:
            got: dict = {d: [] for d in missing}
            for k in range(0, len(missing), 200):
                part = missing[k:k + 200]
                for r in con.execute(GROUP_SQL.format(where=f"day IN ({','.join('?' * len(part))})"), (source, *part)).fetchall():
                    got[r["day"]].append(dict(r))
            with _DAYS_LOCK:
                if len(_DAYS) > 5000:
                    _DAYS.clear()
                for d, rows in got.items():
                    _DAYS[(str(bdb.path), source, d)] = rows
        out = []
        with _DAYS_LOCK:
            for d in have:
                out += _DAYS.get((str(bdb.path), source, d)) or []
        out += [dict(r) for r in con.execute(GROUP_SQL.format(where="day >= ?"), (source, today)).fetchall()]
        return out
    finally:
        con.close()


def board(store, source, capital: float, risk_pct: float, regime: str | None = None, days: int = 60, today: str | None = None,
          min_n: int = 5, family: str | None = None) -> dict:
    """Every strategy (alert type / Lab rule / idea on trial / mover test) x style, ranked by money made per trade after costs.

    rank = average net per trade minus one standard error (a strategy needs both a good average and enough trades to
    be trusted). Columns for all days, the chosen day type, and today; 'fits' = the account can take one lot.
    family: only "buy" / "mixed" / "sell" styles (None = all). Reads data/bench.db with its own connection."""
    from . import registry
    import datetime as _dt
    bdb = db_for(store)
    try:
        groups_in = day_groups(bdb, source, days, today or _dt.date.today().isoformat())
        con = bdb.reader()
        try:
            n_open = con.execute("SELECT COUNT(*) FROM bench_trades WHERE source=? AND net IS NULL AND day >= ?",
                                 (source, today or _dt.date.today().isoformat())).fetchone()[0]
        finally:
            con.close()
    except Exception:
        groups_in, n_open = [], 0
    styles = load_styles()[0]
    groups = {}
    n_closed = 0

    def blank():
        return {"n": 0, "s": 0.0, "s2": 0.0, "wins": 0, "win_s": 0.0, "loss_s": 0.0, "worst": None}

    def merge(a, r):
        a["n"] += r["n"]
        a["s"] += r["s"] or 0
        a["s2"] += r["s2"] or 0
        a["wins"] += r["wins"] or 0
        a["win_s"] += r["win_s"] or 0
        a["loss_s"] += r["loss_s"] or 0
        a["worst"] = r["worst"] if a["worst"] is None else min(a["worst"], r["worst"])
    for r in groups_in:
        fam = style_family(r["style"], r.get("family"))
        if family and fam != family:
            continue
        n_closed += r["n"]
        g = groups.setdefault((r["kind"], r["variant"] or "", r["style"]), {"all": blank(), "reg": blank(), "today": blank(), "cost": 0.0,
                                                                          "risk": 0.0, "need": 0.0, "need_n": 0, "name": r["name"],
                                                                          "days": set(), "family": fam})
        merge(g["all"], r)
        g["cost"] += (r["cost"] or 0) * r["n"]
        g["risk"] += (r["risk"] or 0) * r["n"]
        if r["need"] is not None:
            g["need"] += r["need"] * r["n"]
            g["need_n"] += r["n"]
        g["days"].add(r["day"])
        g["name"] = g["name"] or r["name"]
        if regime and r["regime"] == regime:
            merge(g["reg"], r)
        if today and r["day"] == today:
            merge(g["today"], r)

    def stats(a):
        n = a["n"]
        if not n:
            return {"n": 0}
        avg = a["s"] / n
        sd = math.sqrt(max(0.0, (a["s2"] - n * avg * avg) / (n - 1))) if n > 1 else abs(avg)
        return {"n": n, "avg": round(avg), "total": round(a["s"]), "win_pct": round(a["wins"] / n * 100),
                "pf": round(a["win_s"] / a["loss_s"], 2) if a["loss_s"] else None, "worst": round(a["worst"]),
                "rank": round(avg - sd / math.sqrt(n)) if n >= min_n else None}
    out = []
    for (kind, variant, style), g in groups.items():
        n = g["all"]["n"] or 1
        cost, risk = g["cost"] / n, g["risk"] / n
        need = g["need"] / g["need_n"] if g["need_n"] else None
        label = g["name"] or registry.label(kind) + (f" {variant}" if variant else "")
        out.append({"kind": kind, "variant": variant or None, "style": style, "family": g["family"],
                    "style_label": (styles.get(style) or {}).get("label", style), "label": label, "all": stats(g["all"]),
                    "regime": stats(g["reg"]), "today": stats(g["today"]), "days": len(g["days"]), "cost_lot": round(cost),
                    "risk_lot": round(risk), "need_lot": round(need) if need is not None else None,
                    "fits": fits(cost, risk, capital, risk_pct, need, g["family"])})
    key = (lambda x: (x["regime"]["rank"] if x["regime"].get("rank") is not None else -1e12,
                      x["all"]["rank"] if x["all"].get("rank") is not None else -1e12)) if regime else \
        (lambda x: x["all"]["rank"] if x["all"].get("rank") is not None else -1e12)
    out.sort(key=key, reverse=True)
    best = [x for x in out if x["fits"] and ((x["regime"] if regime else x["all"]).get("rank") or -1) > 0][:5]
    fams = {}
    for x in out:
        f = fams.setdefault(x["family"], {"family": x["family"], "label": FAMILIES.get(x["family"], x["family"]), "n": 0, "net": 0})
        f["n"] += x["all"].get("n") or 0
        f["net"] += x["all"].get("total") or 0
    return {"rows": out, "best": best, "n_closed": n_closed, "n_open": n_open, "capital": capital, "risk_pct": risk_pct,
            "regime": regime, "days": days, "min_n": min_n, "family": family, "families": list(fams.values()),
            "family_labels": FAMILIES,
            "note": "Every signal paper-traded 1 lot per style like the paper account (ask in, bid out, every charge, the same "
                    "target / stop / time limit unless the style says otherwise), with no daily limits. Rank = average net "
                    "per trade minus its uncertainty: a strategy needs a good average and enough trades. Money needed for "
                    "sold options is an estimate (premium + margin). Not a promise: markets change."}
