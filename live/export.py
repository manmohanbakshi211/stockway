"""Everything the app tests, in one Excel file (gun, 2.49).

Mandeep, 10 Nov 2026 (#10): "ensure the shadow trades that test all the strategies and combinations on all the stock is
properly working and make its excel sheet also so i can also check what's going on. all the alerts should be tested,
which alert is working or not should be verified on the heavy data like daily validation and the historical also."

Sheets: Read me · Alert check (every alert type: working / not working / not enough data, from graded live alerts) ·
Alert check by day · Test bench strategies (every strategy x exit style, paper-traded 1 lot) · Test bench by day ·
Exit styles (shadow) · Strategy Lab 1 year / 5 years (historical: train, validation, holdout) · Paper accounts ·
Alerts (last days) · Test bench trades (last days). Read-only: it only reads the app's own databases.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import zipfile
from pathlib import Path

import pandas as pd

from .clock import IST

# when an alert type counts as working / not working (graded live alerts: option +20% first = worked, -20% first = failed)
VERDICT = {"min_decided": 20, "work_hit": 55.0, "fail_hit": 45.0}


def verdict(decided: int, hit_rate, avg_o60) -> str:
    if (decided or 0) < VERDICT["min_decided"] or hit_rate is None:
        return "Not enough data yet"
    if hit_rate >= VERDICT["work_hit"] and (avg_o60 is None or avg_o60 > 0):
        return "Working"
    if hit_rate < VERDICT["fail_hit"] or (avg_o60 is not None and avg_o60 < 0):
        return "Not working"
    return "Mixed"


def _alerts(store, source: str, days: int) -> pd.DataFrame:
    from .record import outcomes
    df = pd.DataFrame(outcomes(store, source, limit=200000))
    if df.empty:
        return df
    cut = (dt.datetime.now(IST).date() - dt.timedelta(days=days)).isoformat()
    return df[df["day"] >= cut] if days else df


def alert_check(df: pd.DataFrame, by_day: bool = False) -> pd.DataFrame:
    from . import registry
    if df.empty:
        return pd.DataFrame(columns=["Alert type"])
    keys = ["day", "kind"] if by_day else ["kind"]
    rows = []
    for k, g in df.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        hits, misses = int((g["result"] == "hit").sum()), int((g["result"] == "miss").sum())
        dec = hits + misses
        hr = round(hits / dec * 100, 1) if dec else None
        o60 = round(float(g["o60"].dropna().mean()), 2) if g["o60"].notna().any() else None
        r = {"Day": k[0]} if by_day else {}
        kind = k[-1]
        r.update({"Alert type": registry.label(kind), "code": kind, "Alerts": len(g), "Worked (+20% first)": hits,
                  "Failed (-20% first)": misses, "Flat": len(g) - dec, "Worked %": hr,
                  "Option after 15 min %": round(float(g["o15"].dropna().mean()), 2) if g["o15"].notna().any() else None,
                  "Option after 60 min %": o60,
                  "Verdict": verdict(dec, hr, o60) if not by_day else ("–" if dec < 3 else "Worked" if hr >= 55 else
                                                                      "Failed" if hr < 45 else "Mixed")})
        if not by_day:
            r.update({"Days": g["day"].nunique(), "First": g["day"].min(), "Last": g["day"].max()})
        rows.append(r)
    out = pd.DataFrame(rows)
    if by_day:
        return out.sort_values(["Day", "Alerts"], ascending=[False, False])
    order = {"Working": 0, "Mixed": 1, "Not working": 2, "Not enough data yet": 3}
    return out.assign(_o=out["Verdict"].map(order)).sort_values(["_o", "Alerts"], ascending=[True, False]).drop(columns="_o")


def bench_tables(store, source: str, days: int, capital: float, risk_pct: float, trades_days: int):
    from . import bench as BN
    b = BN.board(store, source, capital, risk_pct, days=days, min_n=5)
    rows = []
    for x in b["rows"]:
        a = x["all"]
        rows.append({"Strategy": x["label"], "code": x["kind"], "variant": x["variant"], "Exit style": x["style_label"],
                     "Way": x["family"], "Trades": a.get("n"), "Win %": a.get("win_pct"), "Avg net ₹": a.get("avg"),
                     "Total net ₹": a.get("total"), "Profit factor": a.get("pf"), "Worst ₹": a.get("worst"),
                     "Rank ₹ (avg minus doubt)": a.get("rank"), "Days": x["days"], "Cost / lot ₹": x["cost_lot"],
                     "Fits your capital": "yes" if x["fits"] else "no",
                     "Verdict": ("Not enough trades" if (a.get("n") or 0) < 20 else "Making money" if (a.get("rank") or -1) > 0
                                 else "Losing" if (a.get("avg") or 0) < 0 else "Unclear"),
                     "First": _d(x.get("first_ts")), "Last": _d(x.get("last_ts"))})
    strat = pd.DataFrame(rows)
    bdb = BN.db_for(store)
    con = bdb.reader()
    try:
        cut = (dt.datetime.now(IST).date() - dt.timedelta(days=days)).isoformat()
        daily = pd.read_sql("SELECT day, kind, variant, name, style, COUNT(*) AS trades, SUM(net > 0) AS wins, "
                            "ROUND(SUM(net), 0) AS net, ROUND(AVG(net), 0) AS avg_net FROM bench_trades "
                            "WHERE source=? AND day >= ? AND net IS NOT NULL GROUP BY day, kind, variant, style "
                            "ORDER BY day DESC, net DESC", con, params=(source, cut))
        cut2 = (dt.datetime.now(IST).date() - dt.timedelta(days=trades_days)).isoformat()
        tr = pd.read_sql("SELECT day, ts, sym, kind, variant, name, style, direction, regime, dte, lot, entry, cost_lot, "
                         "target, stop, exit_ts, exit, reason, gross, charges, net, peak_pct, low_pct FROM bench_trades "
                         "WHERE source=? AND day >= ? ORDER BY ts DESC LIMIT 300000", con, params=(source, cut2))
    finally:
        con.close()
    if not daily.empty:
        daily.insert(4, "win %", (daily["wins"] / daily["trades"] * 100).round(1))
    for c in ("ts", "exit_ts"):
        if c in tr:
            tr[c] = tr[c].map(_t)
    return strat, daily, tr


def lab_table(data_dir: Path, sub: str) -> pd.DataFrame:
    f = Path(data_dir) / sub / "lab" / "factory.json" if sub else Path(data_dir) / "lab" / "factory.json"
    if not f.exists():
        return pd.DataFrame(columns=["Rule"])
    d = json.loads(f.read_text())
    rows = []
    for r in (d.get("rules") or {}).values():
        row = {"id": r.get("id"), "Name": r.get("name") or "", "Rule": r.get("rule"), "Stage": r.get("stage"),
               "Evidence": r.get("evidence"), "From": r.get("pack") or r.get("source"), "Symbols": ", ".join(r.get("syms") or []),
               "Exit": r.get("exit"), "Found": r.get("found")}
        for part, lab in (("train", "Train"), ("valid", "Validation"), ("hold", "Holdout")):
            s = r.get(part) or {}
            row.update({f"{lab} trades": s.get("n"), f"{lab} avg ₹": s.get("avg"), f"{lab} total ₹": s.get("total"),
                        f"{lab} win %": s.get("win_pct"), f"{lab} PF": s.get("pf")})
        tr, va, ho = (r.get(p) or {} for p in ("train", "valid", "hold"))
        row["Positive in all three"] = "yes" if all((x.get("avg") or 0) > 0 and (x.get("n") or 0) > 0 for x in (tr, va, ho)) else "no"
        rows.append(row)
    out = pd.DataFrame(rows)
    per = d.get("period") or {}
    out.attrs["about"] = f"{len(rows)} saved rules; periods {per}; updated {d.get('updated')}"
    return out.sort_values(["Stage", "Holdout avg ₹"], ascending=[True, False], na_position="last") if len(out) else out


def _t(ts):
    try:
        return dt.datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d %H:%M:%S") if ts and not math.isnan(float(ts)) else None
    except Exception:
        return None


def _d(ts):
    s = _t(ts)
    return s[:10] if s else None


def build(engine, source: str | None = None, days: int = 60, trades_days: int = 5) -> dict:
    """All sheets as DataFrames (ordered)."""
    e = engine
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    pc = (e.paper.cfg if e.paper else (e.cfg()["live"].get("paper") or {})) or {}
    cap, risk = float(pc.get("capital") or 500000), float(pc.get("risk_pct") or 1.0)
    now = dt.datetime.now(IST)
    sheets: dict = {}
    al = _alerts(e.store, src, days)
    sheets["Alert check"] = alert_check(al)
    sheets["Alert check by day"] = alert_check(al, by_day=True)
    try:
        strat, daily, tr = bench_tables(e.store, src, days, cap, risk, trades_days)
    except Exception as ex:
        strat = daily = tr = pd.DataFrame({"error": [str(ex)]})
    sheets["Test bench strategies"] = strat
    sheets["Test bench by day"] = daily
    try:
        from . import shadow
        sb = shadow.board(e.store, src, days)
        sheets["Exit styles (shadow)"] = pd.DataFrame([{"Exit style": x["who"], "code": x["style"], "Alerts": x["n"],
                                                         "Days": x["days"], "Avg option %": x["avg_pct"],
                                                         "Win %": x["win_pct"], "Avg ₹ (1 lot)": x["avg_rs"]}
                                                        for x in sb["styles"]])
    except Exception as ex:
        sheets["Exit styles (shadow)"] = pd.DataFrame({"error": [str(ex)]})
    sheets["Strategy Lab 1 year"] = lab_table(e.data_dir, "")
    sheets["Strategy Lab 5 years"] = lab_table(e.data_dir, "lab5y")
    try:
        from . import books as BK
        bs = getattr(e, "books", None)
        if bs is None:
            bs = BK.Books(src, e.store, pc, e.cfg()["live"].get("books"), log=lambda m: None)
        sheets["Paper accounts"] = pd.DataFrame(BK.compare(e.store, src, pc, bs))
    except Exception as ex:
        sheets["Paper accounts"] = pd.DataFrame({"error": [str(ex)]})
    if not al.empty:
        a = al[["day", "ts", "sym", "kind", "direction", "severity", "result", "o5", "o15", "o60", "r15", "r60"]].copy()
        a["ts"] = a["ts"].map(_t)
        cut = (now.date() - dt.timedelta(days=trades_days)).isoformat()
        sheets["Alerts (last days)"] = a[a["day"] >= cut]
    else:
        sheets["Alerts (last days)"] = pd.DataFrame(columns=["day"])
    sheets["Test bench trades"] = tr
    readme = [
        ("Made", now.strftime("%d %b %Y %H:%M") + " IST"), ("Data", f"{src}: last {days} days (trade lists: last {trades_days} days)"),
        ("Paper only", "Every number here is a test: no order ever reaches a broker."),
        ("Alert check", f"Every alert type graded on live data: worked = its option rose 20% before falling 20% within an hour. "
                        f"Working = {VERDICT['work_hit']:.0f}%+ worked and the option up on average after 60 min, with "
                        f"{VERDICT['min_decided']}+ decided alerts; Not working = under {VERDICT['fail_hit']:.0f}% or down on average."),
        ("Alert check by day", "The same, day by day (the daily validation)."),
        ("Test bench strategies", "Every strategy x exit style paper-traded 1 lot on every signal (ask in, bid out, every charge). "
                                  "Rank = average minus its uncertainty: positive means it made money with enough trades to trust."),
        ("Test bench by day", "Each strategy x style, each day."),
        ("Exit styles (shadow)", "Every alert's option followed under each exit style (before charges)."),
        ("Strategy Lab 1 year / 5 years", "The historical test: each rule scored on train days, then judged on validation and holdout "
                                          "days it never saw. Promoted = passed the strict bar. 'Positive in all three' is a lead, not proof."),
        ("Paper accounts", "Main account vs Best only vs 3% a day, on their own money."),
    ]
    out = {"Read me": pd.DataFrame(readme, columns=["Sheet / item", "What it means"])}
    for k, v in sheets.items():
        if k.startswith("Strategy Lab") and getattr(v, "attrs", {}).get("about"):
            out["Read me"].loc[len(out["Read me"])] = [k, v.attrs["about"]]
    out.update(sheets)
    return out


def to_xlsx(sheets: dict) -> tuple[bytes, str, str]:
    """(bytes, file extension, media type). Excel when openpyxl is installed, else a zip of CSV files."""
    try:
        import openpyxl  # noqa: F401
        from openpyxl.utils import get_column_letter
    except Exception:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, df in sheets.items():
                z.writestr(f"{name}.csv", df.to_csv(index=False))
        return buf.getvalue(), "zip", "application/zip"
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            sn = name[:31]
            df.to_excel(w, sheet_name=sn, index=False)
            ws = w.sheets[sn]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, 1):
                vals = [str(col)] + [str(v) for v in df[col].head(300).tolist()]
                ws.column_dimensions[get_column_letter(i)].width = max(8, min(60, max(len(v) for v in vals) + 2))
            if len(df.columns):
                ws.auto_filter.ref = ws.dimensions
    return buf.getvalue(), "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
