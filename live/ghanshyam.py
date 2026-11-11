"""The Ghanshyam section (2.69).

Mandeep, 12 Nov 2026: "ghanshyam ke naam se ek different section banao... jo ghanshyam ki strategy wali trades and vo
usme rahegi nifty or banknifty mein bas. usme strategy accountability ka bhi monitor hona chahiye. or jo alert hai uski
list bhi waha show karna expand karne wale button ke saath."

One view of everything Ghanshyam: his strategy files (ids starting "ghanshyam"), each strategy's record in three places
(the Lab on past data it never saw, the test bench live at 1 lot, and the Ghanshyam paper account), and every signal on
Nifty and Bank Nifty with what happened to it. Reads only: the paper account itself lives in live/books.py.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .clock import IST

PREFIX = "ghanshyam"
BOOK = "ghanshyam"
SYMS = ("NIFTY", "BANKNIFTY")
ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "rules" / "strategy_files" / "ghanshyam_course.json"


def strategies(data_dir) -> dict:
    """{strategy name: {pack, dir, syms, rules in words, note, variants, lab}} for every Ghanshyam strategy file."""
    from lab import factory as F
    from lab import packs
    out = {}
    try:
        view = {p["id"]: p for p in packs.view(Path(data_dir))["packs"]}
    except Exception:
        view = {}
    for pid, e, res in packs.current(Path(data_dir), only_on=False):
        if not pid.startswith(PREFIX) or res.get("fatal"):
            continue
        notes = {}
        try:
            raw = json.loads((packs.root(Path(data_dir)) / "files" / str(e.get("current"))).read_text())
            notes = {s.get("name"): s.get("note") for s in raw.get("strategies") or []}
        except Exception:
            pass
        labs = {s["name"]: s.get("labs") for s in (view.get(pid) or {}).get("strategies") or []}
        for r in res.get("rules") or []:
            if not r.get("dir"):
                continue
            name = r.get("name") or "?"
            s = out.setdefault(name, {"name": name, "pack": pid, "pack_on": bool(e.get("on", True)), "dir": r["dir"],
                                      "syms": [], "words": None, "note": notes.get(name), "variants": [],
                                      "lab": labs.get(name) or {}})
            v = packs.bench_variant(pid, r)
            if v not in s["variants"]:
                s["variants"].append(v)
            for x in r["syms"]:
                if x not in s["syms"]:
                    s["syms"].append(x)
            if s["words"] is None:
                try:
                    s["words"] = F.words(r)
                except Exception:
                    s["words"] = None
    return out


def _bench_rows(store, variants: set, since: str) -> list:
    if not variants:
        return []
    from .bench import db_for
    c = db_for(store).reader()
    try:
        q = ",".join("?" * len(variants))
        rows = c.execute(f"SELECT id, ts, day, sym, variant, name, direction, entry, exit, exit_ts, net, reason, key1, r_mult "
                         f"FROM bench_trades WHERE style='buy' AND day>=? AND sym IN ('NIFTY','BANKNIFTY') "
                         f"AND variant IN ({q}) ORDER BY ts DESC LIMIT 2000", [since, *variants]).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def _rec(rows: list, key: str = "net") -> dict:
    done = [r for r in rows if r.get(key) is not None and (r.get("exit_ts") or r.get("status") == "closed")]
    n = len(done)
    net = sum(r[key] or 0 for r in done)
    won = sum(1 for r in done if (r[key] or 0) > 0)
    return {"n": n, "won": won, "won_pct": round(won / n * 100) if n else None, "net": round(net, 2),
            "avg": round(net / n, 2) if n else None, "open": sum(1 for r in rows if r not in done)}


def view(e, books, source: str, live: bool, days: int = 30) -> dict:
    data_dir = Path(getattr(e, "data_dir", None) or ROOT / "data")
    st = strategies(data_dir)
    by_var = {v: s["name"] for s in st.values() for v in s["variants"]}
    since = (dt.datetime.now(IST).date() - dt.timedelta(days=max(1, days))).isoformat()
    try:
        bench = _bench_rows(e.store, set(by_var), since)
    except Exception:
        bench = []
    acct, trades = None, []
    if books is not None and BOOK in books.books:
        try:
            acct = books.summary(BOOK, e.state if live else None)
        except Exception as ex:
            acct = {"error": str(ex)[:160]}
        trades = [t for t in e.store.trades(source=f"{source}#{BOOK}", limit=5000) if t["day"] >= since]
    for t in (acct or {}).get("open", []) + (acct or {}).get("closed", []):
        m = str(t.get("mode") or "")
        t["strategy"] = by_var.get(m[5:], "") if m.startswith("pack:") else ""
    t_by = {}
    for t in trades:
        m = str(t.get("mode") or "")
        if m.startswith("pack:"):
            t_by.setdefault(by_var.get(m[5:], m[5:]), []).append(t)
    rows = []
    for name, s in st.items():
        b = [r for r in bench if r["variant"] in s["variants"]]
        tr = t_by.get(name, [])
        lab = s.get("lab") or {}
        rows.append({"name": name, "pack": s["pack"], "dir": s["dir"], "syms": s["syms"], "note": s["note"],
                     "words": s["words"], "bench": _rec(b), "account": _rec(tr), "lab": lab,
                     "last": max((r["ts"] for r in b), default=None),
                     "verdict": verdict(lab, _rec(b), _rec(tr))})
    rows.sort(key=lambda r: (-(r["bench"]["n"] + r["account"]["n"]), r["name"]))
    sig = []
    for r in bench[:300]:
        took = next((t for t in trades if t["sym"] == r["sym"] and str(t.get("mode") or "") == f"pack:{r['variant']}"
                     and abs((t["entry_ts"] or 0) - (r["ts"] or 0)) < 180), None)
        sig.append({"ts": r["ts"], "day": r["day"], "sym": r["sym"], "strategy": by_var.get(r["variant"], r["name"]),
                    "direction": r["direction"], "entry": r["entry"], "exit": r["exit"], "exit_ts": r["exit_ts"],
                    "reason": r["reason"], "net": r["net"], "option": r.get("key1"),
                    "account": {"label": took["label"], "net": took.get("net"), "status": took["status"],
                                "reason": took.get("reason")} if took else None,
                    "words": (st.get(by_var.get(r["variant"], "")) or {}).get("words")})
    have = bool(st)
    return {"have_file": have, "file_in_repo": FILE.exists(), "days": days, "since": since, "account": acct,
            "strategies": rows, "signals": sig, "symbols": list(SYMS),
            "totals": {"bench": _rec(bench), "account": _rec(trades)}}


def verdict(lab: dict, bench: dict, acct: dict) -> str:
    """One honest line: what the record says so far."""
    y5, y1 = lab.get("5y") or {}, lab.get("1y") or {}
    best = y5 if y5.get("tested") else y1
    if best.get("passed"):
        return "passed the Lab on unseen data: can be switched on (your call)"
    if best.get("tested"):
        st = best.get("stage") or "tested"
        live = f"; live {bench['n']} signals, {bench['won_pct']}% won" if bench["n"] else ""
        return f"Lab: {st}{live}"
    if bench["n"] or acct["n"]:
        return f"not tested in the Lab yet; live {bench['n']} signals" + (f", {bench['won_pct']}% won" if bench["n"] else "")
    return "waiting: no Lab result and no live signal yet"


def install(data_dir) -> dict:
    """Load the repo's Ghanshyam strategy file into the Strategy Lab (the same check as an upload)."""
    from lab import packs
    if not FILE.exists():
        raise ValueError("The Ghanshyam strategy file is missing from this copy of the app.")
    return packs.store(Path(data_dir), FILE.read_bytes(), FILE.name)
