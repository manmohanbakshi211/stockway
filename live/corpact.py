"""3.06: corporate actions of the F&O stocks (Mandeep, 24 Nov 2026: "haan ye bhi add karo but ek relevant page mein or
system chain mein best jagah place karke connect karo").

Splits, bonus issues, dividends, rights, buybacks and demergers with their ex-dates:
- source 1: NSE's website corporate-actions list (the same browser-like NSE session live/context.py already uses), past
  past_days to ahead_days, every refresh_hours;
- source 2 (when the website does not answer): NSE's official MCP server (mcp.nseindia.in, public, no login,
  read-only), get_corporate_actions per F&O stock, past dates only, paced.
Kept in data/corp_actions.json. Where it goes in the chain (information and warnings only, never a block):
- event risk on every alert of that stock on its ex-date and the trading day before (live/events.py), with a fact for
  the models' records (ev_corp_d, not used by any model);
- the Market context page (upcoming and recent ex-dates), the stock's bigger-picture line on alerts;
- the Day calendar: a chip on a trade made on its stock's ex-date (the open gap was the corporate action, not a move).
Numbers are bullets in rules/corp_actions.json. Market data only: nothing here talks to a broker.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules" / "corp_actions.json"
DEFAULTS = {"on": True, "past_days": 370, "ahead_days": 60, "refresh_hours": 6, "dividend_warn_pct": 1.0, "warn_days": 1,
            "mcp": {"on": True, "url": "https://mcp.nseindia.in/bhavcopy/cm/mcp", "pace_s": 1.5}}
FILE = "corp_actions.json"
KINDS = {"split": "Split", "bonus": "Bonus", "dividend": "Dividend", "rights": "Rights", "buyback": "Buyback",
         "demerger": "Demerger", "other": "Other"}
_C: dict = {}
_MEM: dict = {}
_LOCK = threading.Lock()


def cfg() -> dict:
    try:
        mt = RULES.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(RULES.read_text())
            _C.update(mt=mt, v={**DEFAULTS, **raw, "mcp": {**DEFAULTS["mcp"], **(raw.get("mcp") or {})}}, error=None)
    except Exception as e:
        _C.update(mt=None, v=dict(DEFAULTS), error=str(e))
    return _C["v"]


# ---------------------------------------------------------------- reading the words NSE uses
_RS = r"(?:rs\.?|re\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)"


def classify(purpose: str) -> dict:
    """{"kind", "ratio" (a:b for bonus, old:new face value for a split), "amount" (₹ a share, dividends summed), "factor"
    (price after = price before x factor, splits and bonuses)} from NSE's purpose text."""
    p = (purpose or "").strip()
    low = p.lower()
    out = dict(kind="other", ratio=None, amount=None, factor=None)   # (not an alert kind: kept out of the registry scan)
    if "split" in low or "sub-division" in low or "sub division" in low:
        out["kind"] = "split"
        fv = re.findall(_RS, low)
        if len(fv) >= 2 and float(fv[0]) > 0 and float(fv[1]) > 0:
            a, b = float(fv[0]), float(fv[1])
            out["ratio"] = f"{a:g}:{b:g}"
            out["factor"] = round(b / a, 6)
        return out
    if "bonus" in low:
        out["kind"] = "bonus"
        m = re.search(r"(\d+)\s*:\s*(\d+)", low)
        if m and int(m.group(2)) > 0:
            a, b = int(m.group(1)), int(m.group(2))
            out["ratio"] = f"{a}:{b}"
            out["factor"] = round(b / (a + b), 6)
        return out
    if "dividend" in low or "distribution" in low:
        out["kind"] = "dividend"
        amts = [float(x) for x in re.findall(_RS, low)]
        out["amount"] = round(sum(amts), 2) if amts else None
        return out
    for k, words in (("rights", ("rights",)), ("buyback", ("buy back", "buyback")), ("demerger", ("demerger", "spin off", "scheme of arrangement"))):
        if any(w in low for w in words):
            out["kind"] = k
            if k == "rights":
                m = re.search(r"(\d+)\s*:\s*(\d+)", low)
                out["ratio"] = f"{m.group(1)}:{m.group(2)}" if m else None
            return out
    return out


def _date(v) -> str | None:
    if not v or str(v).strip() in ("-", "--", "NA"):
        return None
    s = str(v).strip()[:11]
    for f in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s if f != "%Y-%m-%d" else s[:10], f).date().isoformat()
        except ValueError:
            continue
    return None


def parse_nse(data, symbols: set | None = None) -> list:
    """NSE website rows ({symbol, subject, exDate, recDate, ...}) -> [{sym, ex_date, purpose, kind, ...}]."""
    rows = data.get("data") if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or r.get("sym") or "").strip().upper()
        ex = _date(r.get("exDate") or r.get("ex_date") or r.get("exdate"))
        purpose = str(r.get("subject") or r.get("purpose") or "").strip()
        if not sym or not ex or not purpose or (symbols and sym not in symbols):
            continue
        out.append({"sym": sym, "ex_date": ex, "purpose": purpose, "record_date": _date(r.get("recDate") or r.get("record_date")),
                    **classify(purpose)})
    return out


def parse_mcp(sym: str, data: dict) -> list:
    """NSE MCP get_corporate_actions answer -> the same rows (its adjustmentFactor kept when given)."""
    out = []
    for a in (data or {}).get("actions") or []:
        ex = _date(a.get("exDate"))
        purpose = str(a.get("purpose") or "").strip()
        if not ex or not purpose:
            continue
        c = classify(purpose)
        f = a.get("adjustmentFactor")
        if f not in (None, 1, 1.0) and c["factor"] is None:
            c["factor"] = float(f)
        out.append({"sym": sym.upper(), "ex_date": ex, "purpose": purpose, "record_date": None, **c})
    return out


# ---------------------------------------------------------------- NSE's official MCP server (backup source)
def mcp_actions(sym: str, frm: str, to: str, url: str | None = None, post=None) -> list:
    """One stock's corporate actions from NSE's MCP server (JSON-RPC over HTTP). post: a test stub."""
    import requests
    url = url or cfg()["mcp"]["url"]
    post = post or (lambda body, headers: requests.post(url, json=body, headers=headers, timeout=30))
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def body_of(r):
        t = r.text if hasattr(r, "text") else str(r)
        if t.lstrip().startswith(("event:", "data:")):
            t = "\n".join(line[5:] for line in t.splitlines() if line.startswith("data:"))
        return json.loads(t) if t.strip() else {}
    r = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "stockway", "version": "1"}}}, h)
    sid = (getattr(r, "headers", None) or {}).get("mcp-session-id") or (getattr(r, "headers", None) or {}).get("Mcp-Session-Id")
    if sid:
        h = {**h, "Mcp-Session-Id": sid}
    post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, h)
    res = body_of(post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "get_corporate_actions", "arguments": {"symbol": sym, "fromDate": frm, "toDate": to}}}, h))
    txt = "".join(c.get("text", "") for c in ((res.get("result") or {}).get("content") or []))
    data = json.loads(txt) if txt.strip().startswith("{") else {}
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:120])
    return parse_mcp(sym, data)


# ---------------------------------------------------------------- the store
def load(data_dir) -> dict:
    f = Path(data_dir) / FILE
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {"rows": [], "updated": None, "source": None}
    with _LOCK:
        m = _MEM.get(str(f))
        if m and m[0] == mt:
            return m[1]
    try:
        j = json.loads(f.read_text())
    except Exception:
        j = {"rows": [], "updated": None, "source": None}
    with _LOCK:
        _MEM[str(f)] = (mt, j)
    return j


def save(data_dir, j: dict):
    f = Path(data_dir) / FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, separators=(",", ":")))
    tmp.replace(f)


def merge(old: list, new: list) -> list:
    seen, out = set(), []
    for r in sorted(new + old, key=lambda x: (x["ex_date"], x["sym"])):
        k = (r["sym"], r["ex_date"], r["purpose"].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def refresh(data_dir, symbols: set, site=None, today: dt.date | None = None, log=print, mcp=None, sleep=time.sleep) -> dict:
    """NSE website first; if it gives nothing, NSE's MCP server per F&O stock (past dates). Keeps what it had on a failure.
    site(frm, to) -> raw NSE rows; mcp(sym, frm, to) -> rows (test stubs)."""
    c = cfg()
    today = today or dt.date.today()
    frm = today - dt.timedelta(days=int(c["past_days"]))
    to = today + dt.timedelta(days=int(c["ahead_days"]))
    old = load(data_dir)
    rows, src, err = [], None, None
    if site is not None:
        try:
            rows = parse_nse(site(frm, to), symbols or None)
            src = "nse"
        except Exception as e:
            err = f"NSE website: {str(e)[:100]}"
    if not rows and c["mcp"].get("on", True) and symbols:
        mcp = mcp or (lambda s, a, b: mcp_actions(s, a, b))
        got, fails = [], 0
        for s in sorted(symbols):
            try:
                got += mcp(s, frm.isoformat(), min(today, to).isoformat())
            except Exception as e:
                fails += 1
                err = f"NSE MCP: {str(e)[:100]}"
                if fails >= 5 and not got:
                    break
            sleep(float(c["mcp"].get("pace_s") or 0))
        if got or fails < len(symbols):
            rows, src = got, "nse_mcp"
    if not rows and src is None:
        j = {**old, "error": err or "no answer", "tried": time.time()}
        save(data_dir, j)
        log(f"Corporate actions not refreshed: {j['error']}")
        return j
    rows = [r for r in rows if frm.isoformat() <= r["ex_date"] <= to.isoformat()]
    keep = [r for r in old.get("rows") or [] if r["ex_date"] >= frm.isoformat()]   # the MCP has past dates only: the
    j = {"rows": merge(keep, rows), "updated": time.time(), "source": src, "error": None if rows else err,
         "window": [frm.isoformat(), to.isoformat()]}
    save(data_dir, j)
    return j


# ---------------------------------------------------------------- questions the system asks
def _busdays(a: dt.date, b: dt.date) -> int:
    import numpy as np
    return int(np.busday_count(a, b)) if b >= a else -int(np.busday_count(b, a))


def near(data_dir, sym: str, today: dt.date, days: int | None = None) -> list:
    """The stock's actions with an ex-date from today up to `days` trading days ahead (default warn_days)."""
    days = int(cfg()["warn_days"] if days is None else days)
    out = []
    for r in load(data_dir).get("rows") or []:
        if r["sym"] != sym or r["ex_date"] < today.isoformat():
            continue
        d = _busdays(today, dt.date.fromisoformat(r["ex_date"]))
        if d <= days:
            out.append({**r, "days": d})
    return sorted(out, key=lambda r: r["ex_date"])


def on_day(data_dir, day: str) -> dict:
    """{sym: [actions]} with this ex-date."""
    out: dict = {}
    for r in load(data_dir).get("rows") or []:
        if r["ex_date"] == day:
            out.setdefault(r["sym"], []).append(r)
    return out


def words(r: dict, price: float | None = None) -> dict:
    """{"what", "warn", "level"} for one action, in plain words."""
    k = r.get("kind")
    when = "today" if r.get("days") == 0 else "tomorrow" if r.get("days") == 1 else f"on {r['ex_date'][5:]}"
    if k in ("split", "bonus"):
        what = f"{KINDS[k]} {r.get('ratio') or ''} ex-date {when}".replace("  ", " ")
        return {"level": "high", "what": what,
                "warn": f"{what}: NSE adjusts the stock's option strikes and lot size, and the chart shows a price jump that "
                        f"is not a real move (the old candles are not adjusted)."}
    if k == "dividend":
        amt = r.get("amount")
        pct = (amt / price * 100) if amt and price else None
        what = f"Ex-dividend {when}" + (f" ₹{amt:g}" if amt else "") + (f" (~{pct:.1f}%)" if pct is not None else "")
        big = pct is not None and pct >= float(cfg()["dividend_warn_pct"])
        return {"level": "medium" if big else "low", "what": what,
                "warn": (f"{what}: the stock opens about ₹{amt:g} lower on the ex-date (not a real fall): calls lose, puts "
                         f"gain.") if big else None}
    what = f"{KINDS.get(k, 'Corporate action')} ex-date {when}: {r.get('purpose', '')[:60]}"
    return {"level": "medium" if k in ("rights", "demerger") else "low", "what": what,
            "warn": f"{what}: the price can jump on the ex-date." if k in ("rights", "demerger") else None}


def view(data_dir, today: dt.date, symbols: set | None = None, ahead: int = 30, back: int = 10) -> dict:
    j = load(data_dir)
    rows = [r for r in j.get("rows") or [] if not symbols or r["sym"] in symbols]
    a, b = (today + dt.timedelta(days=ahead)).isoformat(), (today - dt.timedelta(days=back)).isoformat()
    up = [{**r, "days": _busdays(today, dt.date.fromisoformat(r["ex_date"]))} for r in rows if today.isoformat() <= r["ex_date"] <= a]
    recent = [r for r in rows if b <= r["ex_date"] < today.isoformat()]
    return {"upcoming": sorted(up, key=lambda r: r["ex_date"]), "recent": sorted(recent, key=lambda r: r["ex_date"], reverse=True),
            "updated": j.get("updated"), "source": j.get("source"), "error": j.get("error"), "n": len(rows),
            "kinds": KINDS}
