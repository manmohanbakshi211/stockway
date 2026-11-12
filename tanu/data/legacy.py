"""Import the legacy app's data into Tanu (ADR-0002, ADR-0007). Read-only on the legacy side: files are only read
(SQLite opened with mode=ro), never moved or changed. Re-running replaces Tanu's copies (idempotent).

  legacy data/history/<SYM>/minutes.parquet        -> <tanu>/history/<SYM>.parquet        (t, o, h, l, c, vol, src)
  legacy data/history/<SYM>/daily.parquet          -> <tanu>/history/daily/<SYM>.parquet  (date, o, h, l, c, src)
  legacy data/recordings/<src>_<day>_bars.parquet  -> <tanu>/bars/legacy/<day>.parquet    (Tanu bar columns, key = symbol)
  legacy data/recordings/<src>_<day>_minutes|chains.parquet -> <tanu>/imports/legacy/<part>/<day>.parquet (as recorded)
  legacy data/live.db tables                       -> <tanu>/imports/legacy/live/<table>.parquet
  legacy Lab bar tables, expired-option and F&O end-of-day files (large) -> listed in the report only (read in place)
A report (<tanu>/imports/legacy/report.json) lists every source, its rows and where it went.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from ..core import log
from .bars import COLS

LIVE_TABLES = ("alerts", "outcomes", "paper", "plan_outcomes", "trial_alerts", "mind_reads")
LAB_TABLES = ("lab/factory_bars.parquet", "lab5y/lab/factory_bars.parquet")
_DAY = re.compile(r"^(?P<src>[a-z]+)_(?P<day>\d{4}-\d{2}-\d{2})_(?P<part>[a-z]+)\.parquet$")
_log = log.get("legacy_import")


def _write(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(path)
    return len(df)


def history(legacy: Path, out: Path) -> list[dict]:
    rows = []
    for f in sorted((legacy / "history").glob("*/minutes.parquet")):
        df = pd.read_parquet(f)
        t = pd.DataFrame({"t": df["ts"].astype("int64"), "o": df["open"], "h": df["high"], "l": df["low"],
                          "c": df["close"], "vol": df.get("volume"), "src": "legacy_history"})
        n = _write(t.sort_values("t").drop_duplicates("t"), out / "history" / f"{f.parent.name}.parquet")
        rows.append({"source": str(f.relative_to(legacy)), "rows": n})
    for f in sorted((legacy / "history").glob("*/daily.parquet")):
        df = pd.read_parquet(f)
        d = pd.DataFrame({"date": df["date"].astype(str), "o": df["open"], "h": df["high"], "l": df["low"],
                          "c": df["close"], "src": "legacy_history"})
        n = _write(d.sort_values("date").drop_duplicates("date"), out / "history" / "daily" / f"{f.parent.name}.parquet")
        rows.append({"source": str(f.relative_to(legacy)), "rows": n})
    return rows


def raw_manifest(legacy: Path) -> list[dict]:
    """Large legacy history kept in place and only listed: expired-option 1-minute files (raw_<expiry>.parquet per
    symbol), F&O end-of-day files (eod/fo_<year>.parquet)."""
    rows = []
    h = legacy / "history"
    for d in sorted(p for p in h.glob("*") if p.is_dir()):
        fs = sorted(d.glob("raw_*.parquet")) + sorted(d.glob("fo_*.parquet"))
        if fs:
            rows.append({"source": str(d.relative_to(legacy)), "files": len(fs), "bytes": sum(f.stat().st_size for f in fs),
                         "first": fs[0].stem, "last": fs[-1].stem, "note": "read in place (read-only), not copied"})
    return rows


def recordings(legacy: Path, out: Path) -> list[dict]:
    rows = []
    for f in sorted((legacy / "recordings").glob("*.parquet")):
        m = _DAY.match(f.name)
        if not m or m["src"] != "upstox":                     # simulator days are not market data
            continue
        day, part = m["day"], m["part"]
        if part == "bars":
            b = pd.read_parquet(f)
            df = pd.DataFrame({"key": b["sym"], "t": b["t"].astype("int64"), "o": b["o"], "h": b["h"], "l": b["l"],
                               "c": b["c"], "vol": b["v"], "cum_vol": None, "oi": None, "bid": None, "ask": None,
                               "ticks": 0, "src": "legacy"})[COLS]
            n = _write(df.sort_values(["key", "t"]), out / "bars" / "legacy" / f"{day}.parquet")
        elif part in ("minutes", "chains"):
            n = _write(pd.read_parquet(f), out / "imports" / "legacy" / part / f"{day}.parquet")
        else:
            continue
        rows.append({"source": str(f.relative_to(legacy)), "rows": n})
    return rows


def live_db(legacy: Path, out: Path) -> list[dict]:
    db = legacy / "live.db"
    if not db.exists():
        return []
    rows = []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in LIVE_TABLES:
            if t in have:
                n = _write(pd.read_sql(f"SELECT * FROM {t}", con), out / "imports" / "legacy" / "live" / f"{t}.parquet")
                rows.append({"source": f"live.db:{t}", "rows": n})
    finally:
        con.close()
    return rows


def lab_manifest(legacy: Path) -> list[dict]:
    import pyarrow.parquet as pq
    rows = []
    for rel in LAB_TABLES:
        f = legacy / rel
        if f.exists():
            md = pq.ParquetFile(f).metadata
            rows.append({"source": rel, "rows": md.num_rows, "columns": md.num_columns, "bytes": f.stat().st_size,
                         "note": "read in place (read-only), not copied"})
    return rows


def run(legacy: Path | str, out: Path | str) -> dict:
    legacy, out = Path(legacy), Path(out)
    rep = {"at": dt.datetime.now().isoformat(timespec="seconds"), "legacy": str(legacy), "parts": {}}
    for name, fn in (("history", history), ("recordings", recordings), ("live_db", live_db)):
        try:
            rep["parts"][name] = fn(legacy, out)
        except Exception as e:                                   # one bad part never stops the others
            rep["parts"][name] = [{"error": str(e)[:300]}]
            _log.error("import_part_failed", part=name, error=str(e)[:300])
    rep["parts"]["lab_tables"] = lab_manifest(legacy)
    rep["parts"]["raw_history"] = raw_manifest(legacy)
    rep["totals"] = {k: sum(r.get("rows", 0) for r in v) for k, v in rep["parts"].items() if k != "raw_history"}
    p = out / "imports" / "legacy" / "report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=1))
    _log.info("legacy_imported", **rep["totals"])
    return rep
