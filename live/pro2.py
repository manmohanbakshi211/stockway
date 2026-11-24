"""Stockway Pro v2 (3.08, Mandeep 25 Nov 2026): Pro's entries checked harder, exits from the chart (gun).

"Humein pro mein ye chahiye ki day type kaisa bhi ho ... agar kisi bhi index mein bilkul bhi jagah na bane trade ki toh
stocks me trades dhunde ... entry exit koi percentage se nahi chart, index/stock price levels, indicators, Resistance
support ... Strong signal, valuable trades, high profit low loss and max green days ... ab system itna strict bhi na ho
ki opportunities hi na le." Then (02:44): "okay apne suggestions ke saath V2 banao pura ek sath complete".

v1 stays as it is (the control). v2 = its own paper accounts (rules/paper_books.json "pro2_*", pick.v2):
- index: Pro's strong index signals (as v1);
- stocks: strong + strict (strength with, flow not already with) + CONFIRMED: the main account's risk check passed an
  alert on the same stock and side in the last 15 minutes (7 replayed days: +0.50R a signal, 71% won); and NOT already
  beyond VWAP +/- 2 sigma the trade's way (the Vote Lab's as_vwap2_stretch; stretched ones -0.02R vs +0.28R);
- ranking: when several signals come on the same 5-minute bar, the best score first (a pause at the level before the
  break, Ghanshyam's point, adds a bonus); money is the only limit (no trades-a-day count, no day stop, no cool-off);
- size: stocks 2 lots when the free money covers both, else 1 (all ride); index 1 lot;
- exit: the chart-level exit (lab/chart_exit.py), the 10 EMA runner; a challenger account keeps Trend Rider's exit;
- on trial, switchable per account: from 09:45 to 11:00 a stock needs a fresh break (no retest); after 3 losses on one
  side in a day the next stock trade on that side needs a fresh break too;
- early window (live/early.py): its own account, 1 lot; a stock bought there is not bought again the same day.
Every number is in rules/pro_v2.json (bullets). Paper and market data only: nothing here talks to a broker.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .clock import IST

RULES = Path(__file__).resolve().parents[1] / "rules" / "pro_v2.json"
DEFAULTS = {"on": True, "confirm_main": {"stock": True, "index": False, "minutes": 15},
            "no_stretch": {"stock": True, "index": False, "vote": "x_as_vwap2_stretch"},
            "pause_bonus": {"vote": "x_gh_pause", "points": 5},
            "midmorning": {"on": False, "from": "09:45", "to": "11:00"},
            "same_side": {"on": False, "losses": 3},
            "rank": True, "lots": {"want": 2}, "exit": {}, "early": {}}
_C: dict = {"m": None, "v": None}


def cfg() -> dict:
    try:
        m = RULES.stat().st_mtime
        if _C["m"] != m:
            _C.update(m=m, v={**DEFAULTS, **json.loads(RULES.read_text())})
        return _C["v"]
    except Exception:
        return dict(DEFAULTS)


def _opt(pick: dict, c: dict, k: str) -> dict:
    """A rule's settings: rules/pro_v2.json, the account's own pick on top (the variant accounts of a test)."""
    return {**(c.get(k) or {}), **((pick or {}).get(k) or {})}


def main_pass(store, source: str, sym: str, direction: str, ts: float, minutes: float = 15) -> bool:
    """The main account's risk check passed an alert on this symbol and side in the last `minutes` (or at this bar)."""
    if store is None:
        return False
    from .store import reading
    try:
        with reading(store) as db:
            r = db.execute("SELECT 1 FROM alerts WHERE source=? AND sym=? AND direction=? AND ts>=? AND ts<=? "
                           "AND instr(data, '\"verdict\": \"PASS\"') > 0 LIMIT 1",
                           (source, sym, direction, float(ts) - float(minutes) * 60, float(ts) + 1)).fetchone()
        return bool(r)
    except Exception:
        return False


def _today(store, source: str, day: str) -> list:
    try:
        return [t for t in store.trades(source=source, limit=400) if t.get("day") == day]
    except Exception:
        return []


def side_losses(store, source: str, day: str, side: str) -> int:
    """Losing trades on one side (CE / PE) today in an account (the two parts of a 2-lot trade count once)."""
    seen = {}
    for t in _today(store, source, day):
        if t.get("side") == side and t.get("status") == "closed":
            k = (t.get("sym"), round(float(t.get("entry_ts") or 0)))
            seen[k] = seen.get(k, 0.0) + float(t.get("net") or 0)
    return sum(1 for v in seen.values() if v < 0)


def check(pick: dict, a: dict, now: dt.datetime, store, source: str, main_source: str, c: dict | None = None) -> str | None:
    """Why a Pro v2 account does NOT buy this Trend Rider signal (None = it may)."""
    from . import pro
    c = c or cfg()
    if not c.get("on", True):
        return "Pro v2 is switched off (rules/pro_v2.json)"
    why = pro.block(pick, a, now, store, source)
    if why:
        return why
    d = a.get("data") or {}
    pr, rd = d.get("pro") or {}, d.get("rider") or {}
    votes = pr.get("votes") or {}
    seg = pr.get("seg") or ("index" if pro.is_index(a.get("sym", ""), a) else "stock")
    cm = _opt(pick, c, "confirm_main")
    if cm.get(seg) and not main_pass(store, main_source, a.get("sym"), a.get("direction"), now.timestamp(),
                                     float(cm.get("minutes") or 15)):
        return f"not confirmed: no main-account PASS on {a.get('sym')} {a.get('direction')} in the last {int(cm.get('minutes') or 15)} min"
    ns = _opt(pick, c, "no_stretch")
    if ns.get(seg) and votes.get(ns.get("vote") or "x_as_vwap2_stretch") == -1:
        return "already beyond VWAP ± 2σ the trade's way (late: no chase)"
    retest = str(rd.get("trigger") or "").startswith("retest")
    hm = now.strftime("%H:%M")
    mm = _opt(pick, c, "midmorning")
    if mm.get("on") and seg == "stock" and str(mm.get("from", "09:45")) <= hm < str(mm.get("to", "11:00")) and retest:
        return f"{mm.get('from', '09:45')}–{mm.get('to', '11:00')}: a stock needs a fresh break, this is a retest"
    ss = _opt(pick, c, "same_side")
    if ss.get("on") and seg == "stock" and retest:
        side = "CE" if a.get("direction") == "up" else "PE"
        n = side_losses(store, source, now.date().isoformat(), side)
        if n >= int(ss.get("losses") or 3):
            return f"{n} {side} losses today: the next one needs a fresh break, this is a retest"
    eb = pick.get("early_book")
    if eb and store is not None:
        side = "CE" if a.get("direction") == "up" else "PE"
        for t in _today(store, f"{main_source}#{eb}", now.date().isoformat()):
            if t.get("sym") == a.get("sym") and t.get("side") == side:
                return "already bought in the early window today"
    return None


def rank(a: dict, c: dict | None = None) -> float:
    """Who goes first when several signals come on one bar: Pro's score, plus the pause bonus."""
    c = c or cfg()
    pr = (a.get("data") or {}).get("pro") or {}
    pb = c.get("pause_bonus") or {}
    s = float(pr.get("score") or 0)
    if (pr.get("votes") or {}).get(pb.get("vote") or "x_gh_pause") == 1:
        s += float(pb.get("points") or 0)
    return s


def words(c: dict | None = None) -> dict:
    """The rules in a few lines for the Pro page."""
    c = c or cfg()
    from lab.chart_exit import WORDS, cfg as ecfg
    ex = ecfg()
    return {"stocks": "strong + strength with + flow not already with + main-account PASS on the same stock and side "
                      f"in the last {int((c.get('confirm_main') or {}).get('minutes') or 15)} min + not beyond VWAP ± 2σ",
            "index": "strong (as Pro v1); 1 lot; chart exit with a profit lock (+20% -> keep +5%)",
            "rank": "several on one bar: the best score first (+ a bonus for a pause at the level)",
            "size": f"stocks {int((c.get('lots') or {}).get('want') or 2)} lots when the free money covers them, else 1 "
                    "(all lots ride: selling one at T1 added ~nothing on the replay)",
            "exit": "chart stop on a 5-minute close; T1 / T2 = the next support / resistance zones; before T1 out on a failed "
                    f"breakout or no move in {int(ex.get('nomove_bars') or 6) * 5} min; " + WORDS.get(ex.get("runner") or "bar5", "")
                    + f"; emergency −{ex.get('emergency_premium_pct')}%; out by {ex.get('exit_hhmm')}",
            "midmorning": bool((c.get("midmorning") or {}).get("on")), "same_side": bool((c.get("same_side") or {}).get("on"))}


# ------------------------------------------------------------------ the Pro page (3.08: v2 segments)
def signals(store, source: str, day: str, seg: str, pick: dict) -> dict:
    """One day's Trend Rider signals for a v2 account: Pro's tiers, and a strong signal the v2 checks would not buy
    moved to the watch list with the reason (as 3.07 does for the must-have checks)."""
    from . import pro
    out = pro.signals(store, source, day, seg, None)
    from .store import reading
    keep, moved = [], []
    rows = {}
    with reading(store) as db:
        for i, ts, sym, d, data in db.execute("SELECT id, ts, sym, direction, data FROM trial_alerts WHERE source=? AND "
                                              "kind='trend_rider' AND day=?", (source, day)).fetchall():
            rows[i] = (ts, sym, d, data)
    for s in out["strong"]:
        r = rows.get(s["id"])
        why = None
        if r:
            ts, sym, d, data = r
            try:
                dd = json.loads(data or "{}") or {}
            except Exception:
                dd = {}
            a = {"sym": sym, "direction": d, "data": dd}
            why = check(pick, a, dt.datetime.fromtimestamp(ts, IST), store, "", source)
        (moved if why else keep).append({**s, **({"gate": why} if why else {})})
    out["strong"], out["medium"] = keep, moved + out["medium"]
    return out


def early_signals(store, source: str, day: str) -> dict:
    """One day's early-breakout signals (live/early.py): strong (the account may buy) / weak, newest first."""
    from .store import reading
    with reading(store) as db:
        rows = db.execute("SELECT id, ts, sym, direction, result, r_exit, data FROM trial_alerts WHERE source=? AND "
                          "kind='early_breakout' AND day=? ORDER BY ts DESC", (source, day)).fetchall()
    out = {"strong": [], "medium": [], "low": []}
    for i, ts, sym, d, res, r, data in rows:
        try:
            e = (json.loads(data or "{}") or {}).get("early") or {}
        except Exception:
            e = {}
        if not e:
            continue
        ck = e.get("checks") or {}
        x = {"id": i, "at": dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M"), "sym": sym, "dir": d, "score": e.get("score"),
             "with": [k for k, v in ck.items() if v], "against": [k for k, v in ck.items() if not v],
             "trigger": f"{e.get('tf')} close beyond the first candle, through {e.get('through')}", "stop": e.get("stop"),
             "result": res, "r": r, **({"gate": "missing: " + ", ".join(e["missing"])} if e.get("missing") else {})}
        out["strong" if e.get("label") == "strong" else "low"].append(x)
    out["low_n"] = len(out["low"])
    return out


def compare(store, source: str, accounts: list) -> list:
    """All days, side by side: trades, net, won, green / red days, worst day (the Pro page's v2 Trades tab)."""
    out = []
    for acct in accounts:
        src = source if acct == "main" else f"{source}#{acct}"
        try:
            rows = [t for t in store.trades(source=src, limit=5000) if t.get("status") == "closed"]
        except Exception:
            rows = []
        by: dict = {}
        for t in rows:
            by[t.get("day")] = by.get(t.get("day"), 0.0) + float(t.get("net") or 0)
        net = sum(float(t.get("net") or 0) for t in rows)
        out.append({"acct": acct, "n": len(rows), "net": round(net), "won": sum(1 for t in rows if float(t.get("net") or 0) > 0),
                    "green": sum(1 for v in by.values() if v > 0), "red": sum(1 for v in by.values() if v < 0),
                    "worst": round(min(by.values())) if by else 0, "days": len(by)})
    return out
