"""One date precision everywhere.

pandas 3 keeps whatever precision a source gives (s, ms, us, ns). Joining two sources with
different precisions fails ("incompatible merge keys"), so every date index is forced to ns."""
from __future__ import annotations

import pandas as pd


def ns(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.as_unit("ns")


def ns_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    df = df.copy()
    name = df.index.name
    df.index = ns(df.index)
    df.index.name = name
    return df


def ns_col(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).astype("datetime64[ns]")
