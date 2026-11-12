"""The daily tradeable list: for each symbol, the estimated round-trip cost of buying its ATM option today (measured
spread + slippage + charges + theta for the rules' holding time) and whether it is cheap enough to buy.

On trial (rules.json): computed and shown, does not block anything yet. Output: <out_dir>/tradeable_<day>.json
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from ..core import log
from ..data.facts import Facts
from . import model

_log = log.get("costs")


def build(day: str, spreads: pd.DataFrame, lots: dict, *, lab: pd.DataFrame | None = None,
          facts: Facts | None = None, rules_path: Path = model.RULES) -> list[dict]:
    """spreads: day_spreads() rows; lots: {sym: lot size} (live: the broker's instrument list)."""
    f = facts or Facts()
    r = model.rules(day, rules_path)
    labi = lab.set_index("sym") if lab is not None and len(lab) else None
    d0 = dt.date.fromisoformat(day)
    out = []
    for row in spreads.itertuples(index=False):
        lot = lots.get(row.sym)
        item = {"sym": row.sym, "mid": round(float(row.mid), 2), "spread_pct": round(float(row.spread_pct), 2),
                "snapshots": int(row.snapshots), "lot": lot}
        if labi is not None and row.sym in labi.index:
            item["lab_drag_12m_pct"] = round(float(labi.loc[row.sym, "lab_drag_12m_pct"]), 2)
        reasons = []
        if not lot:
            reasons.append("lot size unknown")
        else:
            exp = dt.date.fromisoformat(str(row.expiry)[:10])
            dte = sum(1 for k in range(1, (exp - d0).days + 1) if f.trading_day(d0 + dt.timedelta(days=k)))
            c = model.round_trip(float(row.mid), float(row.spread), int(lot), day, hold_min=r["hold_min"],
                                 mins_to_expiry=model.minutes_to_expiry(dte), extra_slip_ticks=r["extra_slip_ticks"],
                                 tick=r["tick"], facts=f)
            item.update(dte=dte, cost=c)
            if c["total_pct"] > r["max_round_trip_pct"]:
                reasons.append(f"round trip {c['total_pct']:.1f}% > {r['max_round_trip_pct']}%")
        if row.spread_pct > r["max_spread_pct"]:
            reasons.append(f"spread {row.spread_pct:.1f}% > {r['max_spread_pct']}%")
        if row.snapshots < r["min_snapshots"]:
            reasons.append(f"only {int(row.snapshots)} quotes")
        item["tradeable"] = not reasons
        item["why_not"] = reasons
        out.append(item)
    out.sort(key=lambda x: (not x["tradeable"], x.get("cost", {}).get("total_pct", 999)))
    return out


def write(day: str, items: list[dict], out_dir: Path | str) -> Path:
    p = Path(out_dir) / f"tradeable_{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"day": day, "made": dt.datetime.now().isoformat(timespec="seconds"),
                             "tradeable": sum(i["tradeable"] for i in items), "items": items}, indent=1))
    _log.info("tradeable_list", day=day, symbols=len(items), tradeable=sum(i["tradeable"] for i in items))
    return p
