"""Research in steps gives exactly the trades of one long run (carried ATR / EMAs saved between chunks)."""
import pandas as pd

from tanu.engine import replay
from tanu.engine.core import Engine
from tanu.engine.options import OptionHistory
from tanu.evaluate import steps
from tanu.evaluate.run import SPREAD_PCT
from tanu.setups import registry


def test_steps_equal_one_run(market, tmp_path):
    base, legacy = market
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "NIFTY.parquet").write_bytes((base / "history" / "NIFTY.parquet").read_bytes())
    p = steps.plan(tmp_path, legacy, syms=("NIFTY",), days_per_step=1)
    assert p["chunks"] == 3
    while not steps.step(tmp_path, journal_path=tmp_path / "j.db").get("done"):
        pass
    rep = steps.finish(tmp_path)
    got = pd.read_parquet(rep["out"] + "/trades.parquet")
    good, _ = registry.load()
    eng = Engine(good, OptionHistory(legacy), spread_pct=SPREAD_PCT)
    oh = OptionHistory(legacy)
    replay.run(eng, "NIFTY", replay.load_minutes(tmp_path / "history" / "NIFTY.parquet"), only_days=oh.days("NIFTY"))
    want = pd.DataFrame(eng.trades())
    k = ["setup", "day", "d", "entry_t"]
    a = got.sort_values(k).reset_index(drop=True)
    b = want.sort_values(k).reset_index(drop=True)[a.columns]
    pd.testing.assert_frame_equal(a, b)
    assert (got["kind"] == "sell").any() and (got["kind"] == "buy").any()
