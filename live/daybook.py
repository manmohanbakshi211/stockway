"""2.98 Day calendar and day review (gun).

Mandeep, 23 Nov 2026, 13:48: "ab ek chart calender bhi banao jisme daily day green ya red se mark ho or us par click
karke pata chale kya hua, konsi trade konsa segment ki trade or kis wajah se trade ne profit or loss kiya. ai bhi daale
taaki 1:30 tak ek report banaye ki system konsi trade le sakte hai kya miss hua jaise ki entry delay thi or kya trend miss
hua or kyu hua har trade ki summary saath mein system kya new seekh sakta hai or system ki training me kya better ho
sakta hai".

- calendar(): every day of a month: the net after charges of every paper account (main and the extra accounts), trades,
  won, the Nifty's day, same-day expiry days, AI reviews written. Green / red.
- build(): one day: the market's path (the indices' 1-minute candles), every trade of every account with its segment
  (index / stock, CE / PE, same-day expiry, alert type) and why it made or lost money, checked by rules (bullets in
  rules/day_review.json): entry delay after the alert, entered after the move had already happened (chase), same-day
  expiry, exit past the stop (late), profit given back, with / against the day's move, the move that came after a stop
  (trend missed), price right but option wrong (time decay), the same signal in several accounts. What was missed: big
  index moves with no trade that way, strong graded alerts not traded. Lessons with their rupee cost.
- review(): the AI (rules/ai_models.json task "day_review": deep) reads that day sheet at the set times (13:30 and after
  the close) or on request, and writes the market, what went right / wrong, what was missed and why, a note per trade,
  trades the system could have taken, what it can learn, what to change in its training. Its filter ideas go on trial
  in the multiverse (tested on days they never saw; nothing trades them by being proposed).
Paper and market data only: nothing here places an order or changes a setting.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from .clock import IST

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "day_review.json"
INDEX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
MARKET = ("NIFTY", "BANKNIFTY", "FINNIFTY")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
DEFAULTS = {"times": ["13:30", "15:45"], "ai": True, "telegram": False, "delay_s": 60, "chase_min": 20,
            "chase_pct": {"index": 0.6, "stock": 1.2}, "late_pct": 8, "giveback_peak_pct": 30,
            "after_stop_pct": {"index": 0.4, "stock": 1.0}, "miss_move_pct": 0.6, "miss_window_min": 30,
            "miss_alert_o60": 20, "max_trades_ai": 40}


def cfg() -> dict:
    c = json.loads(json.dumps(DEFAULTS))
    try:
        c.update(json.loads(RULES.read_text()))
    except Exception:
        pass
    return c


def book_names() -> dict:
    try:
        j = json.loads((ROOT / "rules" / "paper_books.json").read_text())
        return {k: (v.get("name") if isinstance(v, dict) else str(v)) for k, v in (j.get("books") or {}).items()}
    except Exception:
        return {}


def _acct(source: str, base: str) -> str:
    return "main" if source == base else source.split("#", 1)[1] if "#" in source else source


def _read(store, q: str, args=()) -> list:
    if getattr(store, "_read", None):
        return [dict(r) for r in store._read(q, args)]
    with store.lock:
        cur = store.db.execute(q, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def parse_label(label: str | None, day: str) -> dict:
    """'NIFTY 25650 PE 23 Nov' -> side PE, strike, expiry date (in the trade's year), same-day expiry."""
    out = {"side": None, "strike": None, "expiry": None, "same_day": False}
    s = str(label or "")
    m = re.search(r"\b(CE|PE)\b", s)
    if m:
        out["side"] = m.group(1)
    m = re.search(r"\s([\d.]+)\s+(?:CE|PE)\b", s)
    if m:
        out["strike"] = float(m.group(1))
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})(?:\s+(\d{4}))?\s*$", s)
    if m and m.group(2) in MONTHS:
        y = int(m.group(3)) if m.group(3) else int(day[:4])
        try:
            e = dt.date(y, MONTHS[m.group(2)], int(m.group(1)))
            if not m.group(3) and e < dt.date.fromisoformat(day) - dt.timedelta(days=180):
                e = dt.date(y + 1, e.month, e.day)
            out["expiry"] = e.isoformat()
            out["same_day"] = out["expiry"] == day
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- the month
def _daily_closes(data_dir, name: str) -> dict:
    f = Path(data_dir) / "cache" / f"mkt_{name}.parquet"
    try:
        import pandas as pd
        d = pd.read_parquet(f)
        col = next(c for c in d.columns if str(c).lower() in ("close", "adj close", "c"))
        idx = pd.to_datetime(d.index if not isinstance(d.index, pd.RangeIndex) else d.get("date"))
        return {str(i.date()): float(v) for i, v in zip(idx, d[col]) if v == v}
    except Exception:
        return {}


def calendar(store, data_dir, base: str, month: str, today: dict | None = None) -> dict:
    """month 'YYYY-MM'. today: {account: open P&L} for the session day (added to its net, marked open)."""
    y, m = int(month[:4]), int(month[5:7])
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    rows = _read(store, "SELECT day, source, sym, label, status, net, costs FROM paper WHERE day >= ? AND day < ? AND "
                        "(source = ? OR source LIKE ?)", (first.isoformat(), nxt.isoformat(), base, base + "#%"))
    names = {"main": "Main account", **book_names()}
    days: dict = {}
    for r in rows:
        d = days.setdefault(r["day"], {"day": r["day"], "net": 0.0, "n": 0, "won": 0, "open": 0, "accounts": {}, "expiry": False})
        a = d["accounts"].setdefault(_acct(r["source"], base), {"net": 0.0, "n": 0, "won": 0})
        if parse_label(r["label"], r["day"])["same_day"]:
            d["expiry"] = True
        if r["status"] != "closed":
            d["open"] += 1
            continue
        net = float(r["net"] or 0)
        sg = a.setdefault("seg", {}).setdefault("index" if r.get("sym") in INDEX else "stock", {"net": 0.0, "n": 0, "won": 0})
        for x in (d, a, sg):                             # 3.03: each account split into index / stock (Pro's calendar)
            x["net"] += net
            x["n"] += 1
            x["won"] += net > 0
    for day, extra in (today or {}).items():
        d = days.setdefault(day, {"day": day, "net": 0.0, "n": 0, "won": 0, "open": 0, "accounts": {}, "expiry": False})
        for acct, pnl in (extra or {}).items():
            if pnl:
                d["net"] += float(pnl)
                d["accounts"].setdefault(acct, {"net": 0.0, "n": 0, "won": 0})["net"] += float(pnl)
                d["open_pnl"] = round(d.get("open_pnl", 0) + float(pnl), 0)
    closes = _daily_closes(data_dir, "nifty")
    ks = sorted(closes)
    rev = Path(data_dir) / "dayreview"
    for d in days.values():
        d["net"] = round(d["net"], 0)
        for a in d["accounts"].values():
            a["net"] = round(a["net"], 0)
            for sg in (a.get("seg") or {}).values():
                sg["net"] = round(sg["net"], 0)
        i = bisect.bisect_left(ks, d["day"])
        if i < len(ks) and ks[i] == d["day"] and i > 0:
            d["nifty_chg"] = round((closes[ks[i]] / closes[ks[i - 1]] - 1) * 100, 2)
        d["reviews"] = sorted(p.stem.split("_", 1)[1] for p in rev.glob(f"{d['day']}_*.json")) if rev.exists() else []
    out = sorted(days.values(), key=lambda x: x["day"])
    tot = {"net": round(sum(d["net"] for d in out), 0), "days": len(out), "green": sum(d["net"] > 0 for d in out),
           "red": sum(d["net"] < 0 for d in out), "n": sum(d["n"] for d in out), "won": sum(d["won"] for d in out)}
    return {"month": month, "days": out, "total": tot, "names": names}


# ---------------------------------------------------------------- one day
def load_bars(data_dir, base: str, day: str, syms) -> dict:
    """The day's 1-minute candles {sym: [[t, o, h, l, c], ...]} from the recording (compressed file, else the open one)."""
    syms = set(syms)
    out: dict = {}
    rec = Path(data_dir) / "recordings"
    p = rec / f"{base}_{day}_bars.parquet"
    if p.exists():
        try:
            import pandas as pd
            df = pd.read_parquet(p, columns=["sym", "t", "o", "h", "l", "c"])
            df = df[df["sym"].isin(syms)].sort_values("t")
            for s, g in df.groupby("sym"):
                out[s] = g[["t", "o", "h", "l", "c"]].values.tolist()
        except Exception:
            pass
    s = rec / f"{base}_{day}.sqlite"
    if s.exists() and syms - set(out):
        try:
            con = sqlite3.connect(f"file:{s}?mode=ro", uri=True, timeout=5)
            qs = ",".join("?" * len(syms))
            for r in con.execute(f"SELECT sym, t, o, h, l, c FROM bars WHERE sym IN ({qs}) ORDER BY t", tuple(syms)):
                if r[0] not in out or out.get("_sqlite_" + r[0]):
                    out.setdefault(r[0], []).append(list(r[1:]))
                    out["_sqlite_" + r[0]] = True
            con.close()
        except Exception:
            pass
    return {k: v for k, v in out.items() if not k.startswith("_sqlite_")}


class _Px:
    def __init__(self, bars: list):
        self.t = [b[0] for b in bars]
        self.b = bars

    def at(self, ts):
        if not self.t or ts is None:
            return None
        i = bisect.bisect_right(self.t, ts) - 1
        return self.b[i][4] if i >= 0 else self.b[0][1]

    def first(self):
        return self.b[0][1] if self.b else None

    def last(self):
        return self.b[-1][4] if self.b else None

    def move(self, t0, t1):
        a, b = self.at(t0), self.at(t1)
        return (b / a - 1) * 100 if a and b else None


def _hm(ts) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M") if ts else "–"


def _rs(x) -> str:
    x = round(float(x or 0))
    return f"{'−' if x < 0 else ''}₹{abs(x):,}"


def market(bars: dict) -> list:
    out = []
    for s in MARKET:
        b = bars.get(s)
        if not b:
            continue
        hi = max(b, key=lambda x: x[2])
        lo = min(b, key=lambda x: x[3])
        o, c = b[0][1], b[-1][4]
        five = [round(x[4], 2) for x in b[::5]] + [round(c, 2)]
        out.append({"sym": s, "open": o, "high": hi[2], "high_at": _hm(hi[0]), "low": lo[3], "low_at": _hm(lo[0]),
                    "last": c, "last_at": _hm(b[-1][0]), "chg_from_open": round((c / o - 1) * 100, 2),
                    "range_pct": round((hi[2] / lo[3] - 1) * 100, 2), "path": five})
    return out


def _chip(code, text, tone):
    return {"code": code, "text": text, "tone": tone}


def diagnose(t: dict, alert: dict | None, px: _Px | None, day_end_ts, c: dict, gaps: list, dup: int) -> dict:
    """Why one trade made or lost money: chips (code, words, tone) and one sentence."""
    lab = parse_label(t.get("label"), t["day"])
    seg = "index" if t["sym"] in INDEX else "stock"
    side = lab["side"] or t.get("side")
    d_opt = 1 if (t.get("dir") or 1) >= 0 else -1                 # long (+1) or short (-1) the option
    d_und = (1 if side == "CE" else -1 if side == "PE" else 1) * d_opt
    entry, exitp = float(t.get("entry") or 0), t.get("exit")
    last = exitp if exitp is not None else t.get("last")
    pct = ((float(last) / entry - 1) * 100 * d_opt) if entry and last is not None else None
    peak_pct = ((float(t["peak"]) / entry - 1) * 100) if entry and t.get("peak") and d_opt > 0 else None
    chips = []
    reason = t.get("reason") or ("open" if t.get("status") != "closed" else "")
    held = round(((t.get("exit_ts") or time.time()) - t["entry_ts"]) / 60, 1) if t.get("entry_ts") else None
    net = float(t.get("net") or 0)
    if reason == "target":
        chips.append(_chip("target", f"Target hit in {held:g} min", "good"))
    elif reason == "stop loss":
        chips.append(_chip("stop", f"Stop hit after {held:g} min", "bad"))
    elif reason:
        chips.append(_chip("exit", f"Closed: {reason}" + (f" after {held:g} min" if held is not None else ""), "info"))
    a_ts = (alert or {}).get("ts")
    if a_ts and t.get("entry_ts") and t["entry_ts"] - a_ts > float(c["delay_s"]):
        chips.append(_chip("delay", f"Entered {round(t['entry_ts'] - a_ts):,} s after the alert", "bad"))
    if px is not None and t.get("entry_ts"):
        m0 = px.move(t["entry_ts"] - 60 * float(c["chase_min"]), t["entry_ts"])
        lim = float((c["chase_pct"] or {}).get(seg, 1.0))
        if m0 is not None and m0 * d_und >= lim:
            chips.append(_chip("chase", f"Entered after a {abs(m0):.2f}% move in {c['chase_min']} min (chasing)", "bad"))
        day_mv = px.move(px.t[0] if px.t else None, day_end_ts)
        if day_mv is not None and abs(day_mv) >= 0.3:
            with_day = day_mv * d_und > 0
            chips.append(_chip("trend", f"{'With' if with_day else 'Against'} the day's move ({day_mv:+.2f}% {t['sym']})",
                               "good" if with_day else "bad"))
        if t.get("exit_ts"):
            und = px.move(t["entry_ts"], t["exit_ts"])
            if und is not None and pct is not None and und * d_und > 0.05 and pct < 0:
                chips.append(_chip("decay", f"Price moved our way ({und * d_und:+.2f}%) but the option lost {pct:.0f}% "
                                            "(time decay / IV)", "bad"))
            after = px.move(t["exit_ts"], day_end_ts)
            lim2 = float((c["after_stop_pct"] or {}).get(seg, 1.0))
            if net < 0 and after is not None and after * d_und >= lim2:
                chips.append(_chip("after", f"After we were out, the price went {after * d_und:.2f}% our way: right idea, "
                                            "wrong timing", "bad"))
    if lab["same_day"]:
        chips.append(_chip("expiry", "Same-day expiry option: time decay is fastest", "bad" if net < 0 else "info"))
    if reason == "stop loss" and t.get("stop") and exitp is not None and d_opt > 0:
        past = (float(t["stop"]) - float(exitp)) / entry * 100 if entry else 0
        if past >= float(c["late_pct"]):
            gap = next((g for g in gaps if g[0] - 60 <= t["exit_ts"] and g[1] >= t["entry_ts"]), None)
            chips.append(_chip("late", f"Exit {past:.0f}% of the premium past the stop" +
                               (f" (the app was not running {_hm(gap[0])}–{_hm(gap[1])})" if gap else " (late exit)"), "bad"))
    if peak_pct is not None and peak_pct >= float(c["giveback_peak_pct"]) and net <= 0:
        chips.append(_chip("giveback", f"Was +{peak_pct:.0f}% at best, closed {pct:.0f}%: no profit lock", "bad"))
    if dup > 1:
        chips.append(_chip("dup", f"Same signal in {dup} accounts", "info"))
    bad = [x for x in chips if x["tone"] == "bad" and x["code"] not in ("stop",)]
    good = [x for x in chips if x["tone"] == "good"]
    if net > 0:
        why = good[0]["text"] if good else "Closed in profit"
        if len(good) > 1:
            why += "; " + good[1]["text"][0].lower() + good[1]["text"][1:]
    elif net < 0:
        order = ["late", "giveback", "chase", "decay", "after", "delay", "expiry", "trend"]
        bad.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)
        why = bad[0]["text"] if bad else ("Stop hit: the move went against us" if reason == "stop loss" else "Closed at a loss")
    else:
        why = "Open" if t.get("status") != "closed" else "Flat"
    return {"seg": seg, "side": side, "expiry": lab["expiry"], "same_day": lab["same_day"], "pct": None if pct is None else round(pct, 1),
            "peak_pct": None if peak_pct is None else round(peak_pct, 1), "held_min": held, "chips": chips, "why": why,
            "d_und": d_und}


def _cost_of(t: dict, code: str) -> float:
    """Rupees the chip cost: late exit = stop vs actual exit; give-back = best price vs exit."""
    try:
        q = float(t.get("qty") or 0)
        if code == "late":
            return max(0.0, (float(t["stop"]) - float(t["exit"])) * q)
        if code == "giveback":
            return max(0.0, (float(t["peak"]) - float(t["exit"])) * q)
    except Exception:
        pass
    return 0.0


def build(store, data_dir, base: str, day: str, live_bars: dict | None = None, gaps: list | None = None,
          now_ts: float | None = None, open_pnl: dict | None = None, accounts: list | None = None) -> dict:
    """accounts (3.03): only these accounts' trades (e.g. ["pro_index"] for Pro's calendar): their lessons and what they
    missed; the market and the AI reviews are the day's."""
    c = cfg()
    rows = _read(store, "SELECT * FROM paper WHERE day=? AND (source=? OR source LIKE ?) ORDER BY entry_ts",
                 (day, base, base + "#%"))
    if accounts:
        keep = set(accounts)
        rows = [r for r in rows if _acct(r["source"], base) in keep]
        open_pnl = {k: v for k, v in (open_pnl or {}).items() if k in keep} or None
    ids = sorted({r["alert_id"] for r in rows if r.get("alert_id")})
    alerts = {}
    if ids:
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for a in _read(store, f"SELECT id, ts, kind, title, detail, severity, data FROM alerts WHERE id IN "
                                  f"({','.join('?' * len(part))})", tuple(part)):
                alerts[a["id"]] = a
    syms = {r["sym"] for r in rows} | set(MARKET)
    bars = {k: v for k, v in (live_bars or {}).items() if v}
    rec = load_bars(data_dir, base, day, syms)            # the recording; after a restart the live candles may be partial
    for s_, b_ in rec.items():
        if len(b_) > len(bars.get(s_) or []):
            bars[s_] = b_
    pxs = {s: _Px(b) for s, b in bars.items() if b}
    end_ts = now_ts or max((b[-1][0] for b in bars.values() if b), default=None)
    dups = defaultdict(int)
    for r in rows:
        if r.get("alert_id"):
            dups[r["alert_id"]] += 1
    names = {"main": "Main account", **book_names()}
    from . import registry
    trades = []
    for r in rows:
        a = alerts.get(r.get("alert_id"))
        dg = diagnose(r, a, pxs.get(r["sym"]), end_ts, c, gaps or [], dups.get(r.get("alert_id"), 0))
        acct = _acct(r["source"], base)
        trades.append({"id": r["id"], "account": acct, "account_name": names.get(acct, acct), "sym": r["sym"],
                       "label": r["label"], "kind": r["kind"], "kind_name": registry.label(r["kind"]) if r["kind"] else "",
                       "alert": (a or {}).get("title"), "alert_at": _hm((a or {}).get("ts")), "status": r["status"],
                       "entry_at": _hm(r["entry_ts"]), "exit_at": _hm(r.get("exit_ts")), "entry": r["entry"],
                       "exit": r.get("exit"), "stop": r.get("stop"), "target": r.get("target"), "peak": r.get("peak"),
                       "qty": r.get("qty"), "net": round(float(r.get("net") or 0)), "costs": round(float(r.get("costs") or 0)),
                       "reason": r.get("reason"), "manual": bool(r.get("manual")), **dg})
    acc = {}
    for t in trades:
        x = acc.setdefault(t["account"], {"account": t["account"], "name": t["account_name"], "n": 0, "won": 0, "net": 0,
                                          "open": 0})
        if t["status"] == "closed":
            x["n"] += 1
            x["won"] += t["net"] > 0
            x["net"] += t["net"]
        else:
            x["open"] += 1
    for k, v in (open_pnl or {}).items():
        if k in acc and v:
            acc[k]["open_pnl"] = round(v)
    closed = [t for t in trades if t["status"] == "closed"]
    lessons = []
    for code, words in (("late", "Exits past the stop"), ("giveback", "Profit given back"), ("chase", "Entries after the move"),
                        ("decay", "Price right, option wrong (time decay)"), ("after", "Stopped out, then the move came"),
                        ("delay", "Late entries after the alert"), ("expiry", "Same-day expiry options"),
                        ("dup", "The same signal in several accounts")):
        xs = [t for t in closed if any(ch["code"] == code for ch in t["chips"])]
        if not xs:
            continue
        net = sum(t["net"] for t in xs)
        extra = sum(_cost_of(t, code) for t in xs)
        more = {"late": "lost past the stop", "giveback": "given back from the best price"}.get(code)
        lessons.append({"code": code, "what": words, "n": len(xs), "net": round(net),
                        "extra": round(extra) if extra else None,
                        "text": f"{words}: {len(xs)} trade(s), net {_rs(net)}" + (f"; about {_rs(extra)} {more}" if extra else "")})
    rep = {"day": day, "base": base, "made": dt.datetime.now(IST).isoformat(timespec="seconds"), "market": market(bars),
           "accounts": sorted(acc.values(), key=lambda x: x["net"]), "trades": trades,
           "net": round(sum(t["net"] for t in closed)), "n": len(closed), "won": sum(t["net"] > 0 for t in closed),
           "open": len(trades) - len(closed), "lessons": lessons, "gaps": [[_hm(a), _hm(b)] for a, b in (gaps or [])]}
    rep["missed"] = missed(store, base, day, pxs, trades, c)
    try:                                                 # 3.01: Pro's checks that day (graded Trend Rider signals)
        from . import pro as _pro
        pf = _pro.factors(store, base, 1, day)
        if pf.get("n"):
            rep["pro"] = pf
    except Exception:
        pass
    return rep


def missed(store, base: str, day: str, pxs: dict, trades: list, c: dict) -> list:
    """Big index moves with no trade that way, and strong graded alerts that were not traded."""
    out = []
    win = int(c["miss_window_min"]) * 60
    for s in MARKET:
        p = pxs.get(s)
        if not p or len(p.t) < 10:
            continue
        used = []
        cands = []
        for i, t0 in enumerate(p.t):
            j = bisect.bisect_right(p.t, t0 + win) - 1
            if j <= i:
                continue
            mv = (p.b[j][4] / p.b[i][4] - 1) * 100
            if abs(mv) >= float(c["miss_move_pct"]):
                cands.append((abs(mv), mv, t0, p.t[j]))
        for _a, mv, t0, t1 in sorted(cands, reverse=True):
            if any(not (t1 <= u0 or t0 >= u1) for u0, u1 in used):
                continue
            used.append((t0, t1))
            d = 1 if mv > 0 else -1
            had = [t for t in trades if t["sym"] in INDEX and t.get("d_und") == d and
                   _ts(day, t["entry_at"]) <= t1 and (t["exit_at"] == "–" or _ts(day, t["exit_at"]) >= t0)]
            if not had:
                out.append({"what": "move", "sym": s, "from": _hm(t0), "to": _hm(t1), "move_pct": round(mv, 2),
                            "text": f"{s} {'rose' if mv > 0 else 'fell'} {abs(mv):.2f}% {_hm(t0)}–{_hm(t1)}: no index trade "
                                    f"that way was open"})
            if len(used) >= 3:
                break
    merged: dict = {}                                    # the same window on several indices: one line
    for m in out:
        k = (m["from"], m["to"], m["move_pct"] > 0)
        if k in merged:
            merged[k]["syms"].append(m["sym"])
        else:
            merged[k] = {**m, "syms": [m["sym"]]}
    out = []
    for m in merged.values():
        mv = m["move_pct"]
        out.append({**m, "text": f"{', '.join(m['syms'])} {'rose' if mv > 0 else 'fell'} {abs(mv):.2f}% {m['from']}–{m['to']}: "
                                 f"no index trade that way was open"})
    try:
        rows = _read(store, "SELECT o.alert_id, o.kind, o.result, o.o60, a.sym, a.ts, a.title, a.severity, a.data FROM outcomes o "
                            "JOIN alerts a ON a.id = o.alert_id WHERE o.day=? AND o.source=? AND o.result='hit' AND o.o60 >= ? "
                            "AND a.severity >= 3 ORDER BY o.o60 DESC LIMIT 40", (day, base, float(c["miss_alert_o60"])))
    except Exception:
        rows = []
    paper_ids = {r["alert_id"] for r in _read(store, "SELECT alert_id FROM paper WHERE day=? AND (source=? OR source LIKE ?)",
                                              (day, base, base + "#%")) if r.get("alert_id")}
    n = 0
    for r in rows:
        if r["alert_id"] in paper_ids:
            continue
        try:
            data = json.loads(r.get("data") or "{}")
        except Exception:
            data = {}
        why = ((data.get("risk") or {}).get("verdict") or data.get("skip") or "")
        out.append({"what": "alert", "sym": r["sym"], "kind": r["kind"], "at": _hm(r["ts"]), "o60": round(r["o60"], 1),
                    "title": r.get("title"), "not_taken": why,
                    "text": f"{r['sym']} {r['kind'].replace('_', ' ')} at {_hm(r['ts'])}: the option was +{r['o60']:.0f}% an hour "
                            f"later, not traded" + (f" ({why})" if why else "")})
        n += 1
        if n >= 6:
            break
    return out


def _ts(day: str, hm: str) -> float:
    if not hm or hm == "–":
        return 0.0
    h, m = hm.split(":")
    return dt.datetime.fromisoformat(f"{day}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=IST).timestamp()


# ---------------------------------------------------------------- the AI review
def compact(b: dict, c: dict) -> dict:
    """The day sheet the AI reads (no keys, no personal data: market prices and paper trades)."""
    trades = sorted(b["trades"], key=lambda t: abs(t["net"]), reverse=True)[:int(c["max_trades_ai"])]
    return {"day": b["day"], "market": [{k: m[k] for k in ("sym", "open", "high", "high_at", "low", "low_at", "last", "last_at",
                                                          "chg_from_open")} for m in b["market"]],
            "net": b["net"], "closed": b["n"], "won": b["won"], "open": b["open"],
            "accounts": [{k: a.get(k) for k in ("name", "n", "won", "net", "open", "open_pnl")} for a in b["accounts"]],
            "trades": [{"id": t["id"], "account": t["account_name"], "sym": t["sym"], "seg": t["seg"], "side": t["side"],
                        "same_day_expiry": t["same_day"], "signal": t["kind_name"] or t["kind"], "alert": t["alert"],
                        "alert_at": t["alert_at"], "in": t["entry_at"], "out": t["exit_at"], "entry": t["entry"],
                        "exit": t["exit"], "pct": t["pct"], "best_pct": t["peak_pct"], "net": t["net"], "exit_reason": t["reason"],
                        "checks": [ch["text"] for ch in t["chips"]]} for t in trades],
            "missed": [m["text"] for m in b["missed"]], "lessons": [x["text"] for x in b["lessons"]],
            "app_not_running": b.get("gaps"),
            **({"pro_checks": {"tiers": (b.get("pro") or {}).get("tiers"),           # 3.01: which check lined up that day
                               "checks": [{k: r.get(k) for k in ("factor", "n_with", "avg_with", "n_against", "avg_against", "verdict")}
                                          for r in ((b.get("pro") or {}).get("factors") or [])[:12]]}} if b.get("pro") else {})}


def prompt(sheet: dict, names: list, slot: str) -> str:
    when = "at 13:30 (the market is still open: say what to watch for the rest of the day)" if slot < "1530" else "after the close"
    return ("You review one trading day of an Indian F&O alerts and paper-trading app (paper only, it never places real "
            f"orders), {when}. Use only the facts below. Answer in strict JSON only: "
            "{\"market\": \"2-3 sentences: what the market did and what it meant for our trades\", "
            "\"right\": [up to 4 short lines], \"wrong\": [up to 4 short lines, the biggest rupee causes first], "
            "\"missed\": [up to 5 lines: a move or signal we missed or entered late or left early, with times and why], "
            "\"could_take\": [up to 5 lines: trades the system could have taken, when, which way and why], "
            "\"trades\": [{\"id\": trade id, \"note\": one line why it made or lost money}], "
            "\"learn\": [up to 5 specific lessons the system can learn from today], "
            "\"training\": [up to 5 changes to test in its learning or training: inputs, labels, filters, exits; if "
            "pro_checks is given, say which Pro checks lined up and worked today and which did not], "
            "\"ideas\": [up to 3 objects {\"name\": short, \"fact\": one of the fact names below, \"op\": \">\" or \"<\", "
            "\"v\": number, \"why\": one line}] = filters worth testing in the multiverse on days it never saw, "
            "\"next\": one line: what to do differently next session}. "
            "Be specific with times, prices and rupees. One day proves little: say so where it matters.\n"
            f"Fact names: {', '.join(names[:120])}\n\nThe day:\n{json.dumps(sheet, default=str)[:16000]}")


def parse(text: str | None, names: set) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0) if m else text)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    clip = lambda xs, n: [str(s)[:300] for s in (xs or []) if s][:n]
    ideas = []
    for x in d.get("ideas") or []:
        try:
            if x["fact"] in names and x["op"] in (">", "<"):
                ideas.append({"name": str(x.get("name") or x["fact"])[:60], "fact": x["fact"], "op": x["op"],
                              "v": float(x["v"]), "why": str(x.get("why") or "")[:160]})
        except Exception:
            continue
    notes = {}
    for x in d.get("trades") or []:
        try:
            notes[int(x["id"])] = str(x.get("note") or "")[:240]
        except Exception:
            continue
    return {"market": str(d.get("market") or "")[:600], "right": clip(d.get("right"), 4), "wrong": clip(d.get("wrong"), 4),
            "missed": clip(d.get("missed"), 5), "could_take": clip(d.get("could_take"), 5), "trade_notes": notes,
            "learn": clip(d.get("learn"), 5), "training": clip(d.get("training"), 5), "ideas": ideas[:3],
            "next": str(d.get("next") or "")[:300]}


def review(engine, data_dir, day: str, slot: str, book: dict | None = None, log=print) -> dict:
    from . import analyst
    c = cfg()
    b = book or engine.day_book(day)
    sheet = compact(b, c)
    names = analyst.known_facts(data_dir)
    rep, note = None, None
    if c.get("ai", True) and engine.ai and engine.ai.enabled():
        rep = parse(engine.ai.ask(prompt(sheet, names, slot), 2500, auto=False, task="day_review"), set(names))
        if rep is None:
            note = "No readable AI answer (limit reached or reply not JSON): the day sheet and the rule checks are kept."
    else:
        note = "AI is off: the day sheet and the rule checks are kept."
    out = {"day": day, "slot": slot, "made": time.time(), "report": rep, "note": note, "net": b["net"], "n": b["n"]}
    if rep and rep["ideas"]:
        try:
            out["ideas_added"] = analyst.add_ideas(data_dir, rep["ideas"], day, source="AI day review")
        except Exception as e:
            out["ideas_error"] = str(e)[:120]
    d = Path(data_dir) / "dayreview"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{day}_{slot}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, default=str, indent=1))
    tmp.replace(f)
    log(f"Day review {day} {slot[:2]}:{slot[2:]}: {'written' if rep else 'no AI answer'}; "
        f"{out.get('ideas_added', 0)} idea(s) on trial")
    if rep and c.get("telegram"):
        try:
            import html
            e_ = lambda x: html.escape(str(x), quote=False)
            lines = [f"📅 <b>Day review {day} {slot[:2]}:{slot[2:]}</b>: paper {_rs(b['net'])}", e_(rep["market"])] + \
                    [f"❌ {e_(x)}" for x in rep["wrong"][:2]] + [f"👀 {e_(x)}" for x in rep["missed"][:2]] + \
                    ([f"➡️ {e_(rep['next'])}"] if rep["next"] else [])
            engine.telegram.send("\n".join(lines))
        except Exception:
            pass
    return out


def reviews(data_dir, day: str) -> list:
    d = Path(data_dir) / "dayreview"
    out = []
    for f in sorted(d.glob(f"{day}_*.json")) if d.exists() else []:
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out
