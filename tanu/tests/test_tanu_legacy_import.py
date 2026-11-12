"""Legacy import: every part copied with the right columns and counts, legacy files untouched, re-run idempotent, a
broken part never stops the rest."""
import hashlib
import json
import sqlite3

import pandas as pd

from tanu.data import legacy as L
from tanu.data.bars import COLS


def _legacy(tmp_path):
    lg = tmp_path / "legacy"
    (lg / "history" / "NIFTY").mkdir(parents=True)
    pd.DataFrame({"ts": [60, 0, 60], "open": [2, 1, 2], "high": [2, 1, 2], "low": [2, 1, 2], "close": [2, 1, 2],
                  "volume": [0, 0, 0]}).to_parquet(lg / "history" / "NIFTY" / "minutes.parquet")
    pd.DataFrame({"date": ["2026-11-11", "2026-11-12"], "open": [1, 2], "high": [1, 2], "low": [1, 2],
                  "close": [1, 2]}).to_parquet(lg / "history" / "NIFTY" / "daily.parquet")
    pd.DataFrame({"x": [1]}).to_parquet(lg / "history" / "NIFTY" / "raw_2026-11-17.parquet")
    rec = lg / "recordings"
    rec.mkdir()
    pd.DataFrame({"sym": ["NIFTY", "SBIN"], "t": [1.0, 1.0], "o": [1, 2], "h": [1, 2], "l": [1, 2], "c": [1, 2],
                  "v": [5, 6]}).to_parquet(rec / "upstox_2026-11-12_bars.parquet")
    pd.DataFrame({"ts": [1], "key": ["K"], "ltp": [1.0]}).to_parquet(rec / "upstox_2026-11-12_minutes.parquet")
    pd.DataFrame({"ts": [1], "sym": ["NIFTY"], "strike": [25000.0]}).to_parquet(rec / "upstox_2026-11-12_chains.parquet")
    pd.DataFrame({"ts": [1]}).to_parquet(rec / "sim_2026-11-12_bars.parquet")              # simulator: skipped
    con = sqlite3.connect(lg / "live.db")
    con.execute("CREATE TABLE alerts (id INTEGER, kind TEXT)")
    con.executemany("INSERT INTO alerts VALUES (?,?)", [(1, "orb"), (2, "sharp_move")])
    con.execute("CREATE TABLE paper (id INTEGER, pnl REAL)")
    con.commit()
    con.close()
    return lg


def _digest(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_import_copies_every_part_and_never_touches_legacy(tmp_path):
    lg = _legacy(tmp_path)
    before = _digest(lg)
    out = tmp_path / "tanu"
    rep = L.run(lg, out)
    assert _digest(lg) == before                                       # read-only
    assert rep["totals"] == {"history": 4, "recordings": 4, "live_db": 2, "lab_tables": 0}
    assert rep["parts"]["raw_history"][0]["files"] == 1
    assert list(pd.read_parquet(out / "history" / "daily" / "NIFTY.parquet").date) == ["2026-11-11", "2026-11-12"]
    h = pd.read_parquet(out / "history" / "NIFTY.parquet")
    assert list(h.t) == [0, 60] and set(h.src) == {"legacy_history"}
    b = pd.read_parquet(out / "bars" / "legacy" / "2026-11-12.parquet")
    assert list(b.columns) == COLS and list(b.key) == ["NIFTY", "SBIN"] and set(b.src) == {"legacy"}
    assert len(pd.read_parquet(out / "imports" / "legacy" / "live" / "alerts.parquet")) == 2
    assert (out / "imports" / "legacy" / "chains" / "2026-11-12.parquet").exists()
    assert json.loads((out / "imports" / "legacy" / "report.json").read_text())["totals"]["live_db"] == 2
    assert L.run(lg, out)["totals"] == rep["totals"]                   # re-run: same result


def test_a_broken_part_is_reported_and_the_rest_imports(tmp_path):
    lg = _legacy(tmp_path)
    (lg / "history" / "NIFTY" / "minutes.parquet").write_bytes(b"broken")   # one bad file fails its part
    rep = L.run(lg, tmp_path / "tanu")
    assert "error" in rep["parts"]["history"][0] and rep["totals"]["recordings"] == 4 and rep["totals"]["live_db"] == 2
