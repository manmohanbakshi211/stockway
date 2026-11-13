"""The day's measured costs and tradeable list from its option-chain snapshots (Tanu's own, or imported legacy ones)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..data import instruments
from . import measure, tradeable


def run(day: str, base: Path | str, legacy_data: Path | str | None = None) -> tuple[Path | None, str]:
    base = Path(base)
    legacy = Path(legacy_data) if legacy_data else None
    chains = next((p for p in (base / "chains" / f"{day}.parquet", base / "live" / "chains" / f"{day}.parquet",
                               base / "imports" / "legacy" / "chains" / f"{day}.parquet") if p.exists()), None)
    if chains is None:
        return None, f"No option-chain snapshots for {day}"
    inst = sorted((base / "instruments").glob("*NSE*.json.gz"))
    if not inst and legacy is not None:
        inst = sorted((legacy / "instruments").glob("NSE_*.json.gz"))
    lots = {s: u["lot"] for s, u in instruments.underlyings(instruments.load(inst[-1])).items()} if inst else {}
    lab_path = legacy / "lab5y" / "lab" / "factory_bars.parquet" if legacy is not None else None
    lab = measure.lab_drag(lab_path) if lab_path is not None and lab_path.exists() else None
    items = tradeable.build(day, measure.day_spreads(pd.read_parquet(chains), day), lots, lab=lab)
    out = tradeable.write(day, items, base / "costs")
    return out, f"{sum(i['tradeable'] for i in items)} of {len(items)} tradeable -> {out}"
