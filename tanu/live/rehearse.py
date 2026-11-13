"""Rehearsal (ADR-0009): play a past market day through the whole live path (fake broker websocket -> feed process ->
tick tape -> minute bars -> engine -> paper accounts -> journal) and check it against a plain history replay of the
same day. Passing means: the same trades (entry / exit minute, strike, prices, reason, ₹), and the tape rebuilds the
same index bars. Writes only under <base>/rehearsal/<day>/ (its own journal: the real journal is never touched)."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pandas as pd

from ..core import log
from ..data import bars as B
from ..data import tape as T
from ..engine import replay
from ..engine.core import Engine
from ..engine.options import OptionHistory
from ..evaluate.run import SPREAD_PCT
from ..setups import registry
from . import context
from .fakebroker import INDEX_KEYS, FakeBroker
from .session import Session
from .universe import load_spec

_log = log.get("rehearsal")
KEYS = ["setup", "sym", "d", "side", "strike", "entry_t", "exit_t", "entry", "exit", "reason", "pnl_rs"]


def _norm(rows: list[dict], day: str) -> pd.DataFrame:
    df = pd.DataFrame([r for r in rows if r.get("day") == day])
    if df.empty:
        return pd.DataFrame(columns=KEYS)
    df = df[[c for c in KEYS if c in df.columns]].copy()
    for c in ("entry", "exit", "pnl_rs"):
        df[c] = df[c].astype(float).round(4)
    return df.sort_values(["setup", "sym", "d", "entry_t"], na_position="last").reset_index(drop=True)


def replay_day(base: Path, legacy_history: Path, day: str, syms, warm_days: int = 2) -> list[dict]:
    good, _bad = registry.load()
    eng = Engine(good, OptionHistory(legacy_history), spread_pct=SPREAD_PCT)
    for s in syms:
        mins = context.minutes(base, s, INDEX_KEYS.get(s))
        days = replay.day_arrays(mins)[0]
        before = [d for d in days if d < day][-warm_days:] if warm_days else []
        replay.run(eng, s, mins, only_days=set(before) | {day})
    return eng.trades()


def rehearse(day: str, base: Path | str, legacy_history: Path | str, *, syms=("NIFTY", "BANKNIFTY"),
             minute_s: float = 0.02, timeout: float = 900) -> dict:
    base, legacy_history = Path(base), Path(legacy_history)
    out = base / "rehearsal" / day
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    t0 = time.time()
    fake = FakeBroker(day, base, legacy_history, syms, minute_s=minute_s)
    if not fake.syms:
        raise ValueError(f"No index minutes or option prices for {day}")
    spec = load_spec()
    spec.update(indices=list(fake.syms), index_strikes_each_side=999)        # every strike the day has, like replay
    sess = Session(base, day, fake.rows(), url=fake.url, token="rehearsal", out_dir=out, journal_path=out / "journal.db",
                   tape_dir=out / "tape", clock="exchange", spec=spec, lots="facts", min_free_gb=0.5)
    sess.start()
    end = time.time() + timeout
    while time.time() < end and not fake.finished.is_set() and not sess.feed.gave_up:
        time.sleep(0.2)
    ok_done = fake.finished.is_set() and sess.wait_done(60)
    time.sleep(0.5)
    sess.stop()
    live = _norm(sess.engine.trades(), day)
    ref = _norm(replay_day(base, legacy_history, day, fake.syms), day)
    same = len(live) == len(ref) and (live.reset_index(drop=True).equals(ref.reset_index(drop=True)) or
                                      live.astype(str).equals(ref.astype(str)))
    diffs = []
    if not same:
        m = live.merge(ref, how="outer", on=["setup", "sym", "d", "entry_t"], suffixes=("_live", "_replay"), indicator=True)
        diffs = json.loads(m[m["_merge"] != "both"].head(20).to_json(orient="records"))
        both = m[m["_merge"] == "both"]
        for c in ("exit_t", "entry", "exit", "reason", "strike"):
            bad = both[both[f"{c}_live"].astype(str) != both[f"{c}_replay"].astype(str)]
            diffs += json.loads(bad.head(10).to_json(orient="records"))
    # the tape rebuilds the index bars the fake played
    folders = sorted(p.name for p in (out / "tape" / "upstox").iterdir()) if (out / "tape" / "upstox").exists() else []
    recs = [r for f in folders for r in T.read_day(out / "tape", "upstox", f)]   # tape folders are by receive date
    tape_bars = B.from_records(recs)
    tape_ok = {}
    a = pd.Timestamp(day + " 09:15", tz="Asia/Kolkata").timestamp()
    for s in fake.syms:
        k = INDEX_KEYS[s]
        mine = tape_bars[(tape_bars["key"] == k) & (tape_bars["t"] >= a) & (tape_bars["t"] < a + 22500)]
        mins = context.minutes(base, s, k)
        ref_b = mins[(mins["t"] >= a) & (mins["t"] < a + 22500)]
        j = mine.merge(ref_b, on="t", suffixes=("", "_h"))
        tape_ok[s] = bool(len(j) == len(ref_b) and all((j[c] - j[c + "_h"]).abs().max() < 1e-6 for c in "ohlc"))
    rep = {"day": day, "syms": fake.syms, "finished": bool(ok_done), "seconds": round(time.time() - t0, 1),
           "messages": fake.sent, "live": sess.loop.counts, "trades_live": len(live), "trades_replay": len(ref),
           "same_trades": bool(same), "tape_rebuilds_index_bars": tape_ok, "differences": diffs,
           "subscribed": sess.feed.counts(), "notes": sess.notes,
           "passed": bool(ok_done and same and all(tape_ok.values()))}
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    live.to_csv(out / "trades_live.csv", index=False)
    ref.to_csv(out / "trades_replay.csv", index=False)
    _log.info("rehearsal_done", day=day, passed=rep["passed"], trades=len(live), seconds=rep["seconds"])
    return rep
