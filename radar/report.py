"""Builds output/report.html plus CSVs."""
from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

FEATURE_NAMES = {
    "vix": "India VIX level", "vix_chg1": "India VIX 1-day change", "vix_chg5": "India VIX 5-day change",
    "vix_rank": "India VIX rank vs past year", "bn_range": "Yesterday's range",
    "bn_atr14": "14-day average true range", "bn_range_vs_atr": "Yesterday's range vs ATR",
    "bn_rv20": "20-day realised volatility", "bn_rv_ratio_10_60": "Short vs long volatility",
    "bn_bbw": "Bollinger band width", "bn_bbw_rank": "Band width rank (squeeze)",
    "bn_implied_daily": "Implied daily move", "bn_iv_rv": "Implied vs realised vol",
    "bn_gap": "Yesterday's gap", "bn_oc": "Yesterday's open-to-close", "bn_ret1": "Yesterday's return",
    "bn_ret5": "5-day return", "bn_ret20": "20-day return", "bn_dd60": "Drawdown from 60-day high",
    "bn_rsi14": "RSI 14", "bn_big_yesterday": "Big move yesterday", "sp500_ret1": "S&P 500 last session",
    "nasdaq_ret1": "Nasdaq last session", "us_vix_lvl": "US VIX level", "us_vix_chg1": "US VIX change",
    "usdinr_ret1": "USD/INR change", "brent_ret1": "Brent crude change", "dxy_ret1": "Dollar index change",
    "us10y_chg1": "US 10Y yield change", "is_monthly_expiry": "Monthly expiry day",
    "is_weekly_expiry": "Weekly expiry day", "bdays_to_monthly_expiry": "Days to monthly expiry",
    "is_budget_day": "Budget day", "calendar_gap_days": "Days since last session", "dow": "Day of week",
    "is_day_after_budget": "Day after Budget", "is_month_end_week": "Month-end week",
    "bn_nr7": "Narrowest range in 7 days", "bn_inside_day": "Inside day yesterday",
    "bn_close_loc": "Where yesterday closed in its range", "bn_streak": "Up/down streak",
    "bn_rel_nifty5": "Bank Nifty vs Nifty, 5 days", "bn_dist_sma20": "Distance from 20-day average",
    "bn_dist_sma50": "Distance from 50-day average", "bn_dist_sma200": "Distance from 200-day average",
    "vix_rank": "India VIX rank vs past year", "us_vix_chg5": "US VIX 5-day change",
    "us_vix_lvl": "US VIX level", "us10y_lvl": "US 10Y yield", "us10y_chg5": "US 10Y yield 5-day change",
    "sp500_ret5": "S&P 500 5-day return", "nasdaq_ret5": "Nasdaq 5-day return",
    "usdinr_ret5": "USD/INR 5-day change", "brent_ret5": "Brent 5-day change", "dxy_ret5": "Dollar index 5-day change",
    "gold_ret1": "Gold change", "gold_ret5": "Gold 5-day change", "nikkei_ret1": "Nikkei last session",
    "nikkei_ret5": "Nikkei 5-day return", "hangseng_ret1": "Hang Seng last session", "hangseng_ret5": "Hang Seng 5-day return",
}

NEWS_NAMES = {"rbi": "RBI news", "indian_banks": "Indian bank news", "india_market": "Indian market news",
              "india_inflation": "Inflation and rates news", "fed": "Fed news", "geo_risk": "War and terror news"}


def nice(f: str) -> str:
    if f in FEATURE_NAMES:
        return FEATURE_NAMES[f]
    if f.startswith("gd_"):
        for k, v in NEWS_NAMES.items():
            if f.startswith(f"gd_{k}_"):
                rest = f[len(f"gd_{k}_"):]
                kind = {"tone_1d": "tone yesterday", "tone_3d": "tone, 3 days", "tone_chg7": "tone shift",
                        "vol_z3": "volume spike"}.get(rest, rest.replace("_", " "))
                return f"{v}: {kind}"
    if f.startswith("hl_"):
        return "Headlines: " + f[3:].replace("_", " ")
    if f.startswith("is_"):
        return f[3:].replace("_", " ").capitalize()
    return f.replace("_", " ")


def factor_table(X: pd.DataFrame, y: pd.DataFrame, importance: pd.Series, top: int = 15) -> pd.DataFrame:
    """For the most important features: how often was the next day a big move when the
    feature was in its top 20% vs bottom 20%?"""
    rows = []
    mask = y["big_move"].notna()
    base = y.loc[mask, "big_move"].mean()
    for f in importance.index[:top]:
        s = X.loc[mask, f]
        if s.nunique() < 3:
            hi = y.loc[mask & (X[f] == 1), "big_move"].mean() if (X.loc[mask, f] == 1).any() else np.nan
            lo = y.loc[mask & (X[f] == 0), "big_move"].mean()
            hi_lab, lo_lab = "= yes", "= no"
        else:
            q20, q80 = s.quantile([0.2, 0.8])
            hi = y.loc[mask & (X[f] >= q80), "big_move"].mean()
            lo = y.loc[mask & (X[f] <= q20), "big_move"].mean()
            hi_lab, lo_lab = "top 20%", "bottom 20%"
        rows.append({"Factor (known before open)": nice(f), "feature": f,
                     f"Big-move rate when high ({hi_lab})": hi,
                     f"Big-move rate when low ({lo_lab})": lo,
                     "Spread": (hi - lo) if pd.notna(hi) and pd.notna(lo) else np.nan})
    df = pd.DataFrame(rows)
    df.attrs["base"] = base
    return df


def missed_opportunities(y, oos, X, drivers_fn, gdelt, headlines, n=60) -> pd.DataFrame:
    lab = y.dropna(subset=["range_pct"]).copy()
    top = lab.nlargest(n, "range_pct")
    bm = oos[oos["target"] == "big_move"].set_index("date")
    drv = drivers_fn(X.loc[top.index])
    rows = []
    for (d, r), dv in zip(top.iterrows(), drv):
        row = {
            "date": d.date(), "range_pct": r["range_pct"], "open_to_close": r["oc_pct"], "gap": r["gap_pct"],
            "model_prob": bm["prob"].get(d, np.nan), "flagged": bm["flag"].get(d, np.nan),
            "top_drivers": "; ".join(f"{nice(f)} = {v:.4g}" for f, v, c in dv[:3]),
        }
        if gdelt is not None and not gdelt.empty:
            prev = gdelt[gdelt.index < d].tail(1)
            for col in [c for c in gdelt.columns if c.endswith("_tone")][:3]:
                row[col + "_prev_day"] = prev[col].iloc[0] if len(prev) else np.nan
        if headlines is not None and not headlines.empty:
            day = headlines[(headlines["date"] >= d - pd.Timedelta(days=1)) & (headlines["date"] <= d)]
            if len(day):
                pick = day.reindex(day["score"].abs().sort_values(ascending=False).index).head(3)
                row["headlines"] = " | ".join(pick["headline_text"])
        rows.append(row)
    return pd.DataFrame(rows)


def _table_html(df: pd.DataFrame, pct_cols=(), prob_cols=()) -> str:
    d = df.copy()
    for c in d.columns:
        if c in pct_cols:
            d[c] = d[c].map(lambda v: f"{v:.2%}" if pd.notna(v) else "-")
        elif c in prob_cols:
            d[c] = d[c].map(lambda v: f"{v:.0%}" if pd.notna(v) else "-")
    return d.to_html(index=False, escape=True, border=0, classes="tbl")


def build_report(cfg, mkt, X, y, oos, mets, bt, bt_summary, importance, factors, missed, out_path):
    bn = mkt["banknifty"]
    bm = oos[oos["target"] == "big_move"].set_index("date")
    figs = []

    # 1. Price with big days and flags
    f1 = go.Figure()
    f1.add_trace(go.Scatter(x=bn.index, y=bn["Close"], name="Bank Nifty", line=dict(width=1.2, color="#4a6fa5")))
    big = y[y["big_move"] == 1].index.intersection(bm.index)
    hit = big.intersection(bm[bm["flag"] == 1].index)
    miss = big.difference(hit)
    f1.add_trace(go.Scatter(x=hit, y=bn["Close"].reindex(hit), mode="markers", name="Big day, flagged",
                            marker=dict(color="#2e8b57", size=6)))
    f1.add_trace(go.Scatter(x=miss, y=bn["Close"].reindex(miss), mode="markers", name="Big day, missed",
                            marker=dict(color="#d9534f", size=6, symbol="x")))
    f1.update_layout(title="Bank Nifty: big-move days the model flagged vs missed (out-of-sample)", height=430)
    figs.append(f1)

    # 2. AUC by year
    yrs = mets["big_move"]["by_year_auc"]
    f2 = go.Figure(go.Bar(x=[str(k) for k in yrs], y=list(yrs.values()), marker_color="#4a6fa5"))
    f2.add_hline(y=0.5, line_dash="dash", annotation_text="coin flip = 0.5")
    f2.update_layout(title="Big-move prediction quality by test year (AUC)", height=330, yaxis_range=[0.4, 1])
    figs.append(f2)

    # 3. Proxy equity curves
    f3 = go.Figure()
    f3.add_trace(go.Scatter(x=bt.index, y=bt["pnl_pct"].cumsum() * 100, name="Buy straddle every day"))
    fl = bt[bt["flag"] == 1]
    f3.add_trace(go.Scatter(x=fl.index, y=fl["pnl_pct"].cumsum() * 100, name="Only on flagged days"))
    f3.update_layout(title="Option-buying proxy: cumulative P&L (% of spot, intraday ATM straddle)",
                     height=380, yaxis_title="% of spot")
    figs.append(f3)

    # 4. Importance
    imp = importance.head(20)[::-1]
    f4 = go.Figure(go.Bar(x=imp.values, y=[nice(i) for i in imp.index], orientation="h", marker_color="#4a6fa5"))
    f4.update_layout(title="What the model relies on most (top 20 factors)", height=560)
    figs.append(f4)

    charts = "".join(f.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False))
                     for i, f in enumerate(figs))

    m = mets["big_move"]
    d = mets["up_day"]
    fac_cols = [c for c in factors.columns if c.startswith("Big-move rate")]
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bank Nifty Radar</title><style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1d1d1f;background:#fff}}
h1{{font-size:26px}} h2{{font-size:19px;margin-top:36px;border-bottom:1px solid #ddd;padding-bottom:6px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.kpi{{border:1px solid #ddd;border-radius:8px;padding:12px}} .kpi b{{font-size:22px;display:block}}
.tbl{{border-collapse:collapse;width:100%;font-size:13px}} .tbl th,.tbl td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:top}}
.note{{color:#555;font-size:13px}} .scroll{{overflow-x:auto}}
</style></head><body>
<h1>Bank Nifty Opportunity Radar</h1>
<p class="note">Data {bn.index.min().date()} to {bn.index.max().date()} · out-of-sample test years {min(yrs) if yrs else '-'}–{max(yrs) if yrs else '-'} ·
"Big move" = day's high–low range in the top {1-cfg['labels']['big_move_quantile']:.0%} of the previous year. Not financial advice.</p>

<h2>Headline numbers (out-of-sample only)</h2>
<div class="kpis">
<div class="kpi"><b>{m['auc']:.2f}</b>Big-move AUC (0.5 = coin flip)</div>
<div class="kpi"><b>{m['precision_when_flagged']:.0%}</b>Flagged days that were big (base rate {m['base_rate']:.0%})</div>
<div class="kpi"><b>{m['lift_vs_random']:.1f}×</b>Lift vs picking days at random</div>
<div class="kpi"><b>{m['recall_of_actual_days']:.0%}</b>Big days caught (rest = missed)</div>
<div class="kpi"><b>{d['auc']:.2f}</b>Direction AUC (expect ~0.5)</div>
</div>
<p class="note">Read it like this: the model is useful if precision is clearly above the base rate and the yearly AUC stays above 0.55 in most years. Direction is close to random by design of markets — use it as a lean, never as the reason to trade.</p>

{charts}

<h2>Option-buying proxy: did flagged days beat what options priced in?</h2>
<div class="scroll">{bt_summary.to_html(index=False, border=0, classes="tbl")}</div>
<p class="note">Proxy only: ATM straddle bought at open, sold at close; cost = 0.8 × implied daily move from India VIX (scaled to Bank Nifty), plus fees. Positive expectancy on flagged days means realised moves beat implied on those days. Confirm with real option prices before trading.</p>

<h2>Crucial factors: what came before big days</h2>
<p class="note">Base rate of a big day: {factors.attrs.get('base', np.nan):.0%}. Each row compares the next-day big-move rate when the factor was high vs low.</p>
<div class="scroll">{_table_html(factors.drop(columns=['feature']), prob_cols=fac_cols + ['Spread'])}</div>

<h2>Biggest days in 10 years — were they flagged, and what preceded them?</h2>
<p class="note">Drivers = the factors that pushed the current model's probability most for that day (SHAP). Model probability and flag are out-of-sample where the day falls in a test year.</p>
<div class="scroll">{_table_html(missed, pct_cols=['range_pct','open_to_close','gap'], prob_cols=['model_prob'])}</div>
</body></html>"""
    Path(out_path).write_text(page, encoding="utf-8")
    return out_path
