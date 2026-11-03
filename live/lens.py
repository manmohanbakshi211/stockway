"""Options lens: StockMojo-style views built from the app's own data (no scraping, no new paid source).

Per symbol, over today's session (from the minute-by-minute recording the scanner already keeps):
  - ATM straddle (call + put at the strike nearest the price, each minute) and its VWAP; call vs put premium (decay)
  - ATM implied volatility and put-call ratio through the day
  - Put OI minus call OI (change since the open) through the day: which side is adding positions
  - OI by strike now vs the open (the classic open-interest bars)
Market-wide:
  - Breadth through the day (F&O stocks up vs down, share above VWAP)
  - Futures buildup heatmap (long buildup / short covering / short buildup / long unwinding), IV grid of every stock,
    sectors, index contributors, FII / DII and participant positions (from the Market context data)

All of it is recorded every minute already (data/recordings), so each of these can be tested later in the Lab as a fact.
Market data only.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import numpy as np

from .clock import IST

IST_SHIFT = 19800


def _f(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if x != x or x in (float("inf"), float("-inf")) else round(x, n)


class Lens:
    def __init__(self, engine):
        self.e = engine

    # ------------------------------------------------------------ recording access
    def _frames(self, keys: list | None = None, sym: str | None = None, all_metrics=False):
        """(metrics rows, minutes rows) of today's recording, or of the last recorded session when the scanner is off."""
        import pandas as pd
        rec = getattr(self.e, "recorder", None)
        mq = "SELECT ts, sym, data FROM metrics" + ("" if all_metrics else " WHERE sym = ?")
        mp = () if all_metrics else (sym,)
        if rec is not None and getattr(rec, "db", None) is not None:
            with rec.lock:
                met = pd.read_sql(mq, rec.db, params=mp)
                mins = pd.read_sql(f"SELECT ts, key, ltp, oi, iv FROM minutes WHERE key IN ({','.join('?' * len(keys))})", rec.db,
                                   params=tuple(keys)) if keys else pd.DataFrame(columns=["ts", "key", "ltp", "oi", "iv"])
            return met, mins, "today"
        d = Path(self.e.data_dir) / "recordings"
        mf = sorted(d.glob("*_metrics.parquet"))
        if not mf:
            return pd.DataFrame(columns=["ts", "sym", "data"]), pd.DataFrame(columns=["ts", "key", "ltp", "oi", "iv"]), None
        last = mf[-1]
        met = pd.read_parquet(last)
        if not all_metrics:
            met = met[met["sym"] == sym]
        mins = pd.DataFrame(columns=["ts", "key", "ltp", "oi", "iv"])
        mm = Path(str(last).replace("_metrics.parquet", "_minutes.parquet"))
        if keys and mm.exists():
            mins = pd.read_parquet(mm, columns=["ts", "key", "ltp", "oi", "iv"], filters=[("key", "in", list(keys))])
        return met, mins, last.stem.split("_")[1]

    def _chain(self, sym):
        u = self.e.uni.underlyings.get(sym) if getattr(self.e, "uni", None) else None
        if not u:
            return None, None
        exp = u.near_expiry(self.e.clock.now().date())
        return (u.chains.get(exp) or {}), exp

    # ------------------------------------------------------------ one symbol
    def symbol(self, sym: str) -> dict:
        ch, exp = self._chain(sym)
        keys, kmap = [], {}
        for k, sides in (ch or {}).items():
            for side, o in sides.items():
                keys.append(o.key)
                kmap[o.key] = (float(k), side)
        met, mins, when = self._frames(keys, sym)
        out = {"sym": sym, "expiry": str(exp) if exp else None, "session": when, "series": {}, "strikes": []}
        if not len(met):
            out["empty"] = "No recording yet for this symbol (the scanner records every minute while it runs)."
            return out
        rows = [(int(t), json.loads(d)) for t, d in zip(met["ts"], met["data"])]
        rows.sort(key=lambda r: r[0])
        T = [t + IST_SHIFT for t, _ in rows]

        def line(key):
            return [{"time": t, "value": _f(r.get(key))} for t, (_, r) in zip(T, rows) if _f(r.get(key)) is not None]
        out["series"] = {"spot": line("spot"), "atm_iv": line("atm_iv"), "pcr": line("pcr"), "straddle_rec": line("straddle")}
        if len(mins):
            mins = mins[mins["key"].isin(kmap)]
            mins = mins.assign(strike=[kmap[k][0] for k in mins["key"]], side=[kmap[k][1] for k in mins["key"]])
            spot_at = {t: r.get("spot") for t, r in rows}
            ce, pe, strad, oi_diff = [], [], [], []
            base_oi = None
            for ts, g in mins.groupby("ts"):
                s = spot_at.get(int(ts))
                if not s:
                    continue
                ks = sorted(set(g["strike"]))
                if not ks:
                    continue
                atm = min(ks, key=lambda k: abs(k - s))
                c = g[(g["strike"] == atm) & (g["side"] == "CE")]["ltp"]
                p = g[(g["strike"] == atm) & (g["side"] == "PE")]["ltp"]
                t = int(ts) + IST_SHIFT
                if len(c) and len(p):
                    ce.append({"time": t, "value": _f(c.iloc[0])})
                    pe.append({"time": t, "value": _f(p.iloc[0])})
                    strad.append({"time": t, "value": _f(c.iloc[0] + p.iloc[0])})
                tot = g.groupby("side")["oi"].sum()
                if "CE" in tot and "PE" in tot:
                    if base_oi is None:
                        base_oi = (tot["CE"], tot["PE"])
                    oi_diff.append({"time": t, "value": _f(((tot["PE"] - base_oi[1]) - (tot["CE"] - base_oi[0])) / 1e5, 2),
                                    "ce": _f((tot["CE"] - base_oi[0]) / 1e5), "pe": _f((tot["PE"] - base_oi[1]) / 1e5)})
            out["series"].update(ce=ce, pe=pe, straddle=strad, pe_minus_ce_oi_lakh=oi_diff)
            if strad:                                       # straddle VWAP (each minute counts equally)
                vals = np.array([x["value"] for x in strad], dtype=float)
                vw = np.cumsum(vals) / np.arange(1, len(vals) + 1)
                out["series"]["straddle_vwap"] = [{"time": x["time"], "value": _f(v)} for x, v in zip(strad, vw)]
            # OI by strike: now vs the first recorded minute, around the price
            first = mins[mins["ts"] == mins["ts"].min()].set_index("key")["oi"].to_dict()
            last = mins[mins["ts"] == mins["ts"].max()].set_index("key")["oi"].to_dict()
            spot = rows[-1][1].get("spot") or 0
            by = {}
            for k, (strike, side) in kmap.items():
                if k in last:
                    b = by.setdefault(strike, {"strike": strike})
                    b[side.lower() + "_oi"] = _f((last[k] or 0) / 1e5)
                    b[side.lower() + "_chg"] = _f(((last[k] or 0) - (first.get(k) or last[k] or 0)) / 1e5)
            near = sorted(by.values(), key=lambda b: abs(b["strike"] - spot))[:21]
            out["strikes"] = sorted(near, key=lambda b: b["strike"])
        last_r = rows[-1][1]
        out["now"] = {k: last_r.get(k) for k in ("spot", "chg_pct", "atm_iv", "iv_chg_15m", "pcr", "max_pain", "support",
                                                   "resistance", "straddle", "iv_hv", "regime", "gex")}
        s = out["series"].get("straddle") or []
        if len(s) >= 2 and s[0]["value"]:
            out["now"]["straddle_decay_pct"] = _f((s[-1]["value"] / s[0]["value"] - 1) * 100)
        return out

    # ------------------------------------------------------------ the whole market
    def market(self) -> dict:
        met, _, when = self._frames(all_metrics=True)
        out = {"session": when, "breadth": [], "buildup": {}, "iv_grid": [], "sectors": [], "contributors": {}, "flows": {}}
        if len(met):
            per = {}
            for t, d in zip(met["ts"], met["data"]):
                r = json.loads(d)
                if r.get("is_index"):
                    continue
                b = per.setdefault(int(t), [0, 0, 0, 0])
                c = r.get("chg_pct") or 0
                b[0] += c > 0
                b[1] += c < 0
                b[2] += (r.get("vs_vwap_pct") or 0) > 0
                b[3] += 1
            out["breadth"] = [{"time": t + IST_SHIFT, "adv": a, "dec": dcl, "above_vwap_pct": _f(av / n * 100, 1) if n else None}
                              for t, (a, dcl, av, n) in sorted(per.items())]
        # now: from the live metrics (or the last recorded minute)
        cur = {}
        if self.e.metrics:
            cur = {s: self.e.slim(m) for s, m in self.e.metrics.items()}
        elif len(met):
            lt = met["ts"].max()
            cur = {s: json.loads(d) for s, d, t in zip(met["sym"], met["data"], met["ts"]) if t == lt}
        groups = {}
        for s, r in cur.items():
            if r.get("is_index"):
                continue
            lab = r.get("buildup") or "none"
            groups.setdefault(lab, []).append({"sym": s, "chg_pct": _f(r.get("chg_pct")), "fut_oi_chg": _f(r.get("fut_oi_chg"))})
        out["buildup"] = {k: sorted(v, key=lambda x: -abs(x["fut_oi_chg"] or 0))[:15] for k, v in groups.items()}
        out["iv_grid"] = sorted([{"sym": s, "atm_iv": _f(r.get("atm_iv")), "iv_chg_15m": _f(r.get("iv_chg_15m")), "iv_hv": _f(r.get("iv_hv")),
                                  "chg_pct": _f(r.get("chg_pct")), "pcr": _f(r.get("pcr"))}
                                 for s, r in cur.items() if r.get("atm_iv")], key=lambda x: -(x["atm_iv"] or 0))
        mk = getattr(self.e, "market", None) or {}
        out["sectors"] = mk.get("sectors") or []
        out["contributors"] = mk.get("contrib") or mk.get("contributors") or {}
        try:
            cv = self.e.context.view()
            out["flows"] = {"fii_dii": cv.get("fii_dii"), "participants": (cv.get("participants") or {}).get("rows")}
        except Exception:
            pass
        return out
