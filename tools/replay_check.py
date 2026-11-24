"""Replay check (3.08): replay recorded market days with the current code and score every paper account the same way,
so a change to Pro (or any account) is judged on the same days before release. Read-only for your data: each day runs
in its own temporary folder with a copy of live.db (recordings and instruments are linked, never written), the
"replay" source (no Telegram, no AI calls, never the real paper book) and no network jobs.

    python3 tools/replay_check.py --data data --days 2026-11-19,2026-11-23 [--jobs 2] [--speed 30]
                                  [--out /tmp/replay] [--save base.json] [--compare base.json] [--books my_books.json]

--books: replay with another rules/paper_books.json (the variant accounts of a test) without touching yours.
Prints, per account: trades, net after charges, won %, green / red days, worst day, net per lot per trade; --save keeps
that as a baseline and --compare lists every account / day whose result moved. Paper only: nothing here talks to a broker.
Never copies tokens or settings (upstox / dhan / kite tokens, user_settings.json).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = ("upstox_token", "dhan_", "kite_token", "user_settings")
COPY = ("ranker_upstox.joblib", "ranker2_upstox.joblib", "daytype.joblib", "daytype.json", "situation.json", "strict.json",
        "multiverse.json", "multiverse_brain.joblib", "multiverse_ideas.json")


def _prepare(data: Path, work: Path):
    """A day's own folder: a consistent copy of live.db, links to the recordings and instruments, the Lab's json files."""
    d = work / "data"
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "live.db").exists():
        src = sqlite3.connect(f"file:{data / 'live.db'}?mode=ro", uri=True)
        dst = sqlite3.connect(d / "live.db")
        src.backup(dst)
        dst.close()
        src.close()
    for x in ("recordings", "instruments"):
        if (data / x).exists() and not (d / x).exists():
            os.symlink((data / x).resolve(), d / x)
    (d / "lab").mkdir(exist_ok=True)
    for f in (data / "lab").glob("*.json"):
        shutil.copy(f, d / "lab" / f.name)
    if (data / "packs").exists() and not (d / "packs").exists():
        shutil.copytree(data / "packs", d / "packs")
    for f in COPY:
        if (data / f).exists() and not any(s in f for s in SECRET):
            shutil.copy(data / f, d / f)
    return d


def run_one(day: str, work: Path, speed: int, books: str | None) -> dict:
    """Replay one day in this process (called in a child process by main)."""
    d = work / "data"
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if books:
        from live import books as B
        B.FILE = Path(books)
    from live import context as CX, engine as EN, events as EV
    EN.LiveEngine._watch_loop = lambda self: None
    EN.LiveEngine._news_loop = lambda self: None
    CX.Context.run_forever = lambda self, *a, **k: None
    EV.EventEngine.start = lambda self: None
    from radar.config import load_config

    def cfg():
        c = load_config()
        c["live"] = {**(c.get("live") or {}), "auto_start": False, "news": False, "filings": False, "briefs": False}
        c["ai"] = {**(c.get("ai") or {}), "provider": "off"}
        c["lab"] = {**(c.get("lab") or {}), "nightly": False}
        return c

    e = EN.LiveEngine(cfg, d)
    e.telegram.send = lambda *a, **k: None
    e.replay_req = {"day": day, "speed": int(speed)}
    t0 = time.time()
    e.start("replay")
    while time.time() - t0 < 4 * 3600:
        time.sleep(5)
        if (e.status or {}).get("state") in ("closed", "error"):
            break
    state = (e.status or {}).get("state")
    try:
        e.stop()
    except Exception:
        pass
    db = sqlite3.connect(d / "live.db")
    db.row_factory = sqlite3.Row
    paper = [dict(r) for r in db.execute(
        "SELECT source, sym, label, side, kind, entry_ts, exit_ts, entry, exit, peak, lots, net, reason, mode, status "
        "FROM paper WHERE (source='replay' OR source LIKE 'replay#%') AND day=? ORDER BY entry_ts", (day,))]
    out = {"day": day, "seconds": int(time.time() - t0), "status": state, "paper": paper}
    (work / f"{day}.json").write_text(json.dumps(out, default=str))
    return out


def summary(results: list) -> dict:
    """Per account: trades, net, won, green / red days, worst day, net per lot per trade, net per day."""
    acc: dict = {}
    days = sorted({r["day"] for r in results})
    for r in results:
        for t in r["paper"]:
            if t.get("status") == "void":
                continue
            src = t["source"].split("#", 1)[1] if "#" in t["source"] else "main"
            a = acc.setdefault(src, {"trades": 0, "lots": 0, "net": 0.0, "won": 0, "by_day": {d: 0.0 for d in days}})
            a["trades"] += 1
            a["lots"] += int(t.get("lots") or 1)
            a["net"] += float(t.get("net") or 0)
            a["won"] += 1 if float(t.get("net") or 0) > 0 else 0
            a["by_day"][r["day"]] = a["by_day"].get(r["day"], 0.0) + float(t.get("net") or 0)
    out = {}
    for k, a in acc.items():
        traded = {d: v for d, v in a["by_day"].items() if v}
        out[k] = {"trades": a["trades"], "net": round(a["net"]), "won_pct": round(100 * a["won"] / a["trades"]) if a["trades"] else None,
                  "green": sum(1 for v in traded.values() if v > 0), "red": sum(1 for v in traded.values() if v < 0),
                  "worst_day": round(min(traded.values())) if traded else 0, "per_lot_trade": round(a["net"] / a["lots"]) if a["lots"] else 0,
                  "by_day": {d: round(v) for d, v in a["by_day"].items()}}
    return out


def table(s: dict) -> str:
    rows = [f"{'account':22} {'trades':>6} {'net':>10} {'won':>4} {'green/red':>9} {'worst day':>10} {'/lot/trade':>10}"]
    for k, v in sorted(s.items(), key=lambda x: -x[1]["net"]):
        rows.append(f"{k:22} {v['trades']:>6} {v['net']:>10,} {str(v['won_pct']) + '%':>4} {str(v['green']) + '/' + str(v['red']):>9} "
                    f"{v['worst_day']:>10,} {v['per_lot_trade']:>10,}")
    return "\n".join(rows)


def compare(base: dict, now: dict) -> list:
    moved = []
    for k in sorted(set(base) | set(now)):
        a, b = base.get(k), now.get(k)
        if a is None or b is None:
            moved.append(f"{k}: {'new' if a is None else 'gone'}")
            continue
        for d in sorted(set(a["by_day"]) | set(b["by_day"])):
            if a["by_day"].get(d) != b["by_day"].get(d):
                moved.append(f"{k} {d}: {a['by_day'].get(d)} -> {b['by_day'].get(d)}")
    return moved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--days", required=False, default="")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--speed", type=int, default=30)
    ap.add_argument("--out", default="/tmp/stockway_replay")
    ap.add_argument("--books", default=None)
    ap.add_argument("--save", default=None)
    ap.add_argument("--compare", default=None)
    ap.add_argument("--one", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.one:                                             # child: one day
        run_one(a.one, out / a.one, a.speed, a.books)
        os._exit(0)
    data = Path(a.data)
    days = [x.strip() for x in a.days.split(",") if x.strip()] or sorted(
        {p.name[7:17] for p in (data / "recordings").glob("upstox_*_minutes.parquet")})
    todo = []
    for day in days:
        if not (out / day / f"{day}.json").exists():
            _prepare(data, out / day)
            todo.append(day)
    procs: list = []
    while todo or procs:
        while todo and len(procs) < max(1, a.jobs):
            day = todo.pop(0)
            cmd = [sys.executable, str(Path(__file__).resolve()), "--one", day, "--out", str(out), "--speed", str(a.speed)]
            if a.books:
                cmd += ["--books", str(Path(a.books).resolve())]
            log = open(out / day / "replay.log", "w")
            procs.append((day, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))))
            print(f"replaying {day} ...", flush=True)
        time.sleep(5)
        for x in list(procs):
            if x[1].poll() is not None:
                procs.remove(x)
                print(f"{x[0]} done ({'ok' if (out / x[0] / f'{x[0]}.json').exists() else 'failed: see replay.log'})", flush=True)
    results = [json.loads((out / d / f"{d}.json").read_text()) for d in days if (out / d / f"{d}.json").exists()]
    s = summary(results)
    print(table(s))
    if a.save:
        Path(a.save).write_text(json.dumps(s, indent=1))
    if a.compare and Path(a.compare).exists():
        moved = compare(json.loads(Path(a.compare).read_text()), s)
        print("identical to the baseline" if not moved else "moved:\n  " + "\n  ".join(moved))
    return s


if __name__ == "__main__":
    main()
