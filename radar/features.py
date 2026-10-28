"""Feature + label construction.

Row index = target trading day T. Every feature uses only information available
before Bank Nifty opens on T:
  - Indian market features: values up to the close of T-1 (shifted by one row)
  - Global markets / news: last observation dated strictly before T
  - Calendar events: known in advance, computed for T itself
Labels use T's own Bank Nifty candle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dates import ns, ns_col, ns_frame
from .events import event_features
from .news import news_features

GLOBAL_LEVEL = {"us_vix", "us10y"}  # also keep level / absolute change


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _rank(s: pd.Series, n: int = 250) -> pd.Series:
    return s.rolling(n, min_periods=60).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)


def _streak(ret: pd.Series) -> pd.Series:
    sign = np.sign(ret.fillna(0)).to_numpy()
    out = np.zeros(len(sign))
    for i in range(1, len(sign)):
        out[i] = out[i - 1] + sign[i] if sign[i] == np.sign(out[i - 1]) or out[i - 1] == 0 else sign[i]
    return pd.Series(out, index=ret.index)


def next_session(last: pd.Timestamp) -> pd.Timestamp:
    d = last + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def india_features(mkt: dict) -> pd.DataFrame:
    """Features computed at the close of day t (to be shifted to t+1)."""
    mkt = {k: ns_frame(v) for k, v in mkt.items()}
    bn = mkt["banknifty"]
    idx = bn.index
    o, h, l, c = bn["Open"], bn["High"], bn["Low"], bn["Close"]
    nf = mkt["nifty"]["Close"].reindex(idx).ffill()
    vix = mkt["india_vix"]["Close"].reindex(idx).ffill()

    f = pd.DataFrame(index=idx)
    ret = c.pct_change()
    f["bn_ret1"] = ret
    f["bn_ret5"] = c.pct_change(5)
    f["bn_ret20"] = c.pct_change(20)
    f["bn_range"] = (h - l) / c.shift(1)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    f["bn_atr14"] = tr.rolling(14).mean() / c
    f["bn_range_vs_atr"] = f["bn_range"] / f["bn_atr14"]
    f["bn_nr7"] = (f["bn_range"] <= f["bn_range"].rolling(7).min()).astype(int)
    f["bn_inside_day"] = ((h < h.shift(1)) & (l > l.shift(1))).astype(int)
    f["bn_gap"] = o / c.shift(1) - 1
    f["bn_oc"] = c / o - 1
    f["bn_close_loc"] = (c - l) / (h - l).replace(0, np.nan)  # where it closed in the day's range
    rv10 = ret.rolling(10).std() * np.sqrt(252)
    rv20 = ret.rolling(20).std() * np.sqrt(252)
    rv60 = ret.rolling(60).std() * np.sqrt(252)
    f["bn_rv20"] = rv20
    f["bn_rv_ratio_10_60"] = rv10 / rv60
    bbw = 4 * c.rolling(20).std() / c.rolling(20).mean()
    f["bn_bbw"] = bbw
    f["bn_bbw_rank"] = _rank(bbw)
    f["bn_rsi14"] = _rsi(c)
    for n in (20, 50, 200):
        f[f"bn_dist_sma{n}"] = c / c.rolling(n).mean() - 1
    f["bn_dd60"] = c / c.rolling(60).max() - 1
    f["bn_streak"] = _streak(ret)
    nf_ret = nf.pct_change()
    f["bn_rel_nifty5"] = f["bn_ret5"] - nf.pct_change(5)
    bn_nf_ratio = (ret.rolling(60).std() / nf_ret.rolling(60).std()).clip(0.8, 2.5)
    f["vix"] = vix
    f["vix_chg1"] = vix.pct_change()
    f["vix_chg5"] = vix.pct_change(5)
    f["vix_rank"] = _rank(vix)
    implied_annual = vix / 100 * bn_nf_ratio
    f["bn_implied_daily"] = implied_annual / np.sqrt(252)   # expected 1-sigma daily move
    f["bn_iv_rv"] = implied_annual / rv20
    return f


def global_features(mkt: dict, target_days: pd.DatetimeIndex) -> pd.DataFrame:
    target_days = ns(target_days)
    out = pd.DataFrame(index=target_days)
    left = pd.DataFrame({"date": target_days}).sort_values("date")
    for name, df in mkt.items():
        if name in ("banknifty", "nifty", "india_vix") or df is None or df.empty:
            continue
        c = df["Close"]
        g = pd.DataFrame(index=df.index)
        if name in GLOBAL_LEVEL:
            g[f"{name}_lvl"] = c
            g[f"{name}_chg1"] = c.diff()
            g[f"{name}_chg5"] = c.diff(5)
        else:
            g[f"{name}_ret1"] = c.pct_change()
            g[f"{name}_ret5"] = c.pct_change(5)
        g.index = ns(g.index)
        g.index.name = "src_date"
        right = g.reset_index()
        right["src_date"] = ns_col(right["src_date"])
        m = pd.merge_asof(left, right.sort_values("src_date"), left_on="date",
                          right_on="src_date", allow_exact_matches=False, direction="backward",
                          tolerance=pd.Timedelta(days=6))
        out = out.join(m.drop(columns=["src_date"]).set_index("date"))
    return out


def labels(mkt: dict, cfg: dict) -> pd.DataFrame:
    bn = ns_frame(mkt["banknifty"])
    o, h, l, c = bn["Open"], bn["High"], bn["Low"], bn["Close"]
    lab = pd.DataFrame(index=bn.index)
    lab["open"], lab["close"] = o, c
    lab["range_pct"] = (h - l) / c.shift(1)
    lab["oc_pct"] = c / o - 1
    lab["gap_pct"] = o / c.shift(1) - 1
    lab["ret_pct"] = c.pct_change()
    q, n = cfg["labels"]["big_move_quantile"], cfg["labels"]["lookback_days"]
    lab["big_threshold"] = lab["range_pct"].shift(1).rolling(n, min_periods=120).quantile(q)
    lab["big_move"] = (lab["range_pct"] > lab["big_threshold"]).astype(float)
    lab.loc[lab["big_threshold"].isna(), "big_move"] = np.nan
    lab["up_day"] = (lab["ret_pct"] > 0).astype(float)
    return lab


def build_dataset(mkt: dict, news_daily: pd.DataFrame, cfg: dict, include_next: bool = False):
    """Returns X (features), y (labels), meta. If include_next, X has one extra row for the next session."""
    mkt = {k: ns_frame(v) for k, v in mkt.items()}
    news_daily = ns_frame(news_daily)
    bn_idx = mkt["banknifty"].index
    target = bn_idx[1:]
    if include_next:
        target = target.append(pd.DatetimeIndex([next_session(bn_idx.max())]))
    ind = india_features(mkt).reindex(bn_idx.append(target).unique().sort_values()).shift(1)
    X = ind.reindex(target)
    X = X.join(global_features(mkt, target))
    X = X.join(event_features(target, bn_idx, cfg))
    X = X.join(news_features(news_daily, target))
    X = X.replace([np.inf, -np.inf], np.nan)
    y = labels(mkt, cfg).reindex(target)
    # yesterday's realised label is a legitimate feature (known at T-1 close)
    lab_all = labels(mkt, cfg)
    X["bn_big_yesterday"] = lab_all["big_move"].reindex(bn_idx.append(target).unique().sort_values()).shift(1).reindex(target)
    X.index.name = "date"
    y.index.name = "date"
    return X, y
