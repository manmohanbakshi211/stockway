"""Chart data (candles, indicators, levels, markers) and the live setup scanner."""
from __future__ import annotations

import datetime as dt
import json
import math
import time
import urllib.parse
from pathlib import Path as Path_

import numpy as np

from . import patterns as PT, setups, ta
from .clock import IST

IST_SHIFT = 19800          # the chart library shows UTC; shift so the axis reads IST
from .registry import noisy_for_stocks as _noisy, shorts as _shorts  # noqa: E402

NOISY_FOR_STOCKS = _noisy()
SHORT = _shorts()


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


_RS = {"mtime": None, "cfg": {"paper": "off", "max_signals_per_day": 1, "regimes": ["range", "quiet"], "severity": 2}}


def range_sell_cfg() -> dict:
    """rules/range_sell.json (2.55), re-read when it changes; a broken file keeps the safe defaults (paper off)."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "rules" / "range_sell.json"
    try:
        mt = f.stat().st_mtime
        if _RS["mtime"] != mt:
            c = json.loads(f.read_text())
            if c.get("paper") not in ("off", "naked"):
                c["paper"] = "off"
            _RS.update(mtime=mt, cfg={**_RS["cfg"], **c})
    except Exception:
        pass
    return _RS["cfg"]


class ChartData:
    def __init__(self, engine):
        self.e = engine
        self.hist = {}            # (sym, date) -> [[t, o, h, l, c, v], ...] previous days
        self.today_fill = {}      # sym -> earlier minutes of today fetched from the broker
        self.last_slot = None
        self.active = {}          # sym -> list of live setup signals
        self._pat_cache = {}

    # ------------------------------------------------------------ bars
    def _session_start(self, day):
        return dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()

    def today_bars(self, sym):
        b = self.e.state.bars.get(sym) if self.e.state else None
        rows = [list(x) for x in b.bars] if b else []
        extra = self.today_fill.get(sym) or []
        if extra:
            have = {r[0] for r in rows}
            rows = sorted([r for r in extra if r[0] not in have] + rows, key=lambda r: r[0])
        return rows

    def history(self, sym):
        """Previous sessions' 1-minute bars (live mode only; the simulator has just today)."""
        if self.e.source != "upstox" or not self.e.rest or not self.e.uni:
            return []
        day = self.e.clock.now().date()
        k = (sym, day)
        if k in self.hist:
            return self.hist[k]
        u = self.e.uni.underlyings.get(sym)
        rows = []
        try:
            key = urllib.parse.quote(u.key, safe="")
            to, frm = (day - dt.timedelta(days=1)).isoformat(), (day - dt.timedelta(days=16)).isoformat()
            data = self.e.rest.get(f"/v3/historical-candle/{key}/minutes/1/{to}/{frm}") or {}
            for c in data.get("candles", []):
                t = dt.datetime.fromisoformat(c[0]).timestamp()
                rows.append([t, c[1], c[2], c[3], c[4], c[5] or 0])
            rows.sort(key=lambda r: r[0])
            if sym not in self.today_fill:
                data = self.e.rest.get(f"/v3/historical-candle/intraday/{key}/minutes/1") or {}
                self.today_fill[sym] = sorted(([dt.datetime.fromisoformat(c[0]).timestamp(), c[1], c[2], c[3], c[4], c[5] or 0]
                                               for c in data.get("candles", [])), key=lambda r: r[0])
        except PermissionError:
            raise
        except Exception as ex:
            self.e.log(f"Chart history for {sym} failed: {ex}")
        self.hist[k] = rows
        return rows

    # ------------------------------------------------------------ context for setups
    def context(self, sym, m=None) -> dict:
        m = m or self.e.metrics.get(sym) or {}
        d = self.e.daily.get(sym) or {}
        prev = d.get("prev") or {}
        o = m.get("opt") or {}
        g = self.e.gex.get(sym) or {}
        pdh, pdl, pdc = prev.get("h"), prev.get("l"), prev.get("c") or m.get("cp")
        return {"is_index": bool(m.get("is_index")), "pdh": pdh, "pdl": pdl, "pdc": pdc, "cpr": ta.cpr(pdh, pdl, pdc),
                "or_high": m.get("or_high"), "or_low": m.get("or_low"), "resistance": o.get("resistance"),
                "support": o.get("support"), "gamma": g.get("regime"), "flip": g.get("flip"),
                "call_wall": g.get("call_wall"), "put_wall": g.get("put_wall"), "max_pain": o.get("max_pain")}

    def _factory(self, now, slot, emit):
        """Promoted Strategy Lab rules on the bar that just closed (Nifty, Bank Nifty): same facts as the backtest."""
        from lab import factory
        rules = [dict(r, _kind="factory") for r in self.e.factory_rules()]
        try:                                   # 5-year Lab rules: on trial (recorded and graded only)
            rules += [dict(r, _kind="lab5y") for r in self.e.factory_rules_5y()]
        except Exception:
            pass
        rec = self.e.fact_bars
        syms = ["NIFTY", "BANKNIFTY"]
        if any(factory.STOCKS in r["syms"] or factory.EACH in r["syms"] for r in rules) and self.e.uni:   # stock rules: every F&O stock
            syms += [u.symbol for u in self.e.uni.stocks]
        in_play = set(getattr(self.e, "in_play", None) or ())    # 2.31: library rules also on the stocks moving today
        syms += sorted(in_play - set(syms))
        for sym in syms:
            m = self.e.metrics.get(sym)
            if not m:
                continue
            is_stock = sym not in factory.INDEX_SYMS
            o = m.get("opt") or {}
            # remember ATM IV and the priced move for each closed bar (the backtest has them per bar too)
            day_rec = rec.setdefault((sym, now.date()), {})
            st = o.get("straddle")
            day_rec[slot - 1] = (o.get("atm_iv"), st / m["spot"] * 100 if st and m.get("spot") else None)
            lib = self._bench_rules() if sym in factory.INDEX_SYMS else self._bench_rules_stocks() if sym in in_play else []
            if not rules and not lib:
                continue
            b = self.five_min(sym, now.date())
            if not b or len(b["c"]) < slot:
                continue
            o5, h5, l5, c5 = (np.asarray(b[k][:slot], float) for k in ("o", "h", "l", "c"))
            ctx0 = self.context(sym, m)
            pdc = ctx0.get("pdc")
            vix = self.e.state.get(self.e.VIX_KEY) if hasattr(self.e, "VIX_KEY") else None
            u = self.e.uni.underlyings.get(sym) if self.e.uni else None
            exp = u.near_expiry(now.date()) if u else None
            dte = int(np.busday_count(now.date(), exp)) if exp else None
            dly = (self.e.daily.get(sym) or {}).get("htf") or {}
            ctx = {"prev_close": pdc, "prev_high": ctx0.get("pdh"), "prev_low": ctx0.get("pdl"),
                   "gap": (o5[0] / pdc - 1) * 100 if pdc else None, "vix": getattr(vix, "cp", None) if vix else None,
                   "dte": dte, "dow": now.weekday(),
                   "d_trend": dly.get("trend"), "d_vs_ema20": dly.get("vs_ema20_pct"), "d_pos20": dly.get("pos20"),
                   "d_struct2": dly.get("struct2"), "d_struct3": dly.get("struct3")}            # 2.43
            try:
                ctx["hw"] = self._hw_live(sym, now)
            except Exception:
                ctx["hw"] = None
            ivs = [day_rec.get(i, (None, None))[0] for i in range(slot)]
            prem = [day_rec.get(i, (None, None))[1] for i in range(slot)]
            f = factory.bar_features(o5, h5, l5, c5, ctx, [np.nan if x is None else x for x in ivs],
                                     [np.nan if x is None else x for x in prem])
            self._add_new_facts(f, sym, now, slot, m, (o5, h5, l5, c5), prem, rules + lib)        # 2.47
            for r in rules:
                try:                               # one broken rule or model file is skipped, the rest still run
                    if r.get("ml"):
                        from lab import mlstrat
                        ok = factory.sym_ok(r["syms"], sym) and mlstrat.fires(self.e.data_dir, r, f)
                    else:
                        ok = factory.signal_now(r, f, sym)
                except Exception as ex:
                    if self._rate(f"rule_err:{r.get('id')}", 1800):
                        self.e.log(f"Strategy Lab rule {r.get('id')} skipped: {ex}")
                    ok = False
                if not ok:
                    continue
                fired = self.e.fact_fired.setdefault((r["id"], sym, now.date()), [])
                if len(fired) >= r["max_day"]:
                    continue
                fired.append(now.timestamp())
                d = r["dir"]
                h = r["hold"]
                if d == 0 and is_stock:
                    continue
                if r.get("_kind") == "lab5y":
                    if d == 0:
                        continue                             # option selling on trial: nothing to grade on the price yet
                    self._trial_rule(sym, m, r, d, h5, l5, c5, emit)
                    continue
                if d == 0:                                   # 2.55: one range-day sell signal per index per day (rules/range_sell.json)
                    self._range_sell(sym, m, r, h, now, emit)
                    continue
                if r["exit"] in factory.STYLES:              # chart-level exit: risk in points from today's 5-min ATR
                    from lab import exits as X
                    R = X.risk_pts(r["exit"], m["spot"], d, X.atr14(h5, l5, c5)[-1], l5[-1], h5[-1])
                    if not np.isfinite(R):
                        continue
                    fac = {"id": r["id"], "plan": {"style": r["exit"], "R": round(float(R), 2)}}
                else:
                    fac = {"id": r["id"], "exit": list(factory.EXITS[r["exit"]][:3]) +
                           (list(factory.EXITS[r["exit"]][3]) if factory.EXITS[r["exit"]][3] else [])}
                if r.get("strike"):                         # 2.57: the rule's own strike (live/engine.py _suggest)
                    fac["strike"] = r["strike"]
                untested = is_stock and (factory.STOCKS in r["syms"] or factory.EACH in r["syms"]) and sym not in self._tested_stocks()
                if untested and (self.e.cfg().get("lab") or {}).get("stock_rules_tested_only"):
                    continue                       # setting: "any F&O stock" rules only on stocks the Lab tested them on
                if untested:
                    fac["untested_stock"] = True
                emit({"sym": sym, "kind": "factory", "direction": "up" if d > 0 else "down", "severity": 3,
                      "title": f"{sym} Strategy Lab rule {r['id']}: {'BUY call' if d > 0 else 'BUY put'}",
                      "detail": f"{r['rule']} Holdout: {h.get('n', 0)} trades, avg ₹{h.get('avg', 0):,.0f}, green days {h.get('green_pct', 0)}%."
                                + (f" Note: tested on other F&O stocks, not on {sym} itself." if untested else ""),
                      "price": m.get("spot"), "data": {"factory": fac}})
            hits = []
            for r in lib:                                  # 2.46: which named strategies fire on this bar (all of them first)
                try:
                    if factory.signal_now(r, f, sym):
                        hits.append(r)
                except Exception as ex:
                    if self._rate(f"bench_rule:{r.get('_variant')}", 3600):
                        self.e.log(f"Test bench rule {r.get('_name')} skipped: {ex}")
            if hits and hasattr(self.e, "note_confluence"):
                for r in hits:
                    self.e.note_confluence(sym, r["dir"], r["_variant"], now.timestamp())
            for r in hits:                                 # test bench only: every library / book setup, live
                try:
                    fired = self.e.fact_fired.setdefault((r["_variant"], sym, now.date()), [])
                    if len(fired) >= int(r.get("max_day") or 1):
                        continue
                    fired.append(now.timestamp())
                    self.e.bench_signal(sym, "up" if r["dir"] > 0 else "down", now, r.get("_kind", "library"), r["_variant"], r["_name"])
                except Exception as ex:
                    if self._rate(f"bench_rule:{r.get('_variant')}", 3600):
                        self.e.log(f"Test bench rule {r.get('_name')} skipped: {ex}")

    def _bench_rules(self) -> list:
        """Every Strategy Lab library / book setup (one per entry rule, exits aside), for the test bench on the indices."""
        hit = getattr(self, "_lib_cache", None)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        out = []
        try:
            import hashlib
            import json as _json
            from lab import factory, library
            seen = set()
            for r in library.rules():
                if not r.get("dir"):
                    continue                                   # option selling: not part of the option-buying bench
                sig = _json.dumps([r["conds"], r["dir"], r.get("win")], sort_keys=True)
                if sig in seen:
                    continue
                seen.add(sig)
                v = factory.validate(dict(r))
                if not v:
                    continue
                src = str(r.get("source", ""))
                v["_variant"] = "L" + hashlib.sha1(sig.encode()).hexdigest()[:7]
                when = ", ".join(f"{c[0]} {c[1]} {c[2]:g}" if isinstance(c[2], (int, float)) else f"{c[0]} {c[1]} {c[2]}" for c in r["conds"])
                win = r.get("win") or []
                tw = f", {9 + (15 + int(win[0])) // 60:02d}:{(15 + int(win[0])) % 60:02d}-{9 + (15 + int(win[1])) // 60:02d}:{(15 + int(win[1])) % 60:02d}" if len(win) == 2 else ""
                v["_name"] = (f"{src.split(':', 1)[-1]} ({'long' if r['dir'] > 0 else 'short'}){' · book' if src.startswith('book:') else ''}"
                              f" — {when}{tw}")[:160]
                out.append(v)
        except Exception as ex:
            if self._rate("bench_rules", 3600):
                self.e.log(f"Test bench: library rules not loaded: {ex}")
        try:                                                   # 2.46: fusions the Lab promoted (bench until switched on)
            out += self._fusion_rules()
        except Exception as ex:
            if self._rate("bench_fusions", 3600):
                self.e.log(f"Test bench: fusions not loaded: {ex}")
        try:                                                   # 2.43: strategy files (lab/packs.py), switched-on ones
            out += self._pack_rules()
        except Exception as ex:
            if self._rate("bench_packs", 3600):
                self.e.log(f"Test bench: strategy files not loaded: {ex}")
        self._lib_cache = (time.time(), out)
        return out

    def _fusion_rules(self) -> list:
        """Promoted fusions from the 1-year and the 5-year Lab, one bench rule per entry (exits aside)."""
        import hashlib
        import json as _json
        from lab import dhan_hist, factory
        out, seen = [], set()
        for d in (self.e.data_dir, dhan_hist.root(self.e.data_dir)):
            try:
                rules = factory.load(d).get("rules", {}).values()
            except Exception:
                continue
            for r in rules:
                if not str(r.get("source", "")).startswith("fusion:") or r.get("stage") != "promoted" or not r.get("dir"):
                    continue
                sig = _json.dumps([r["conds"], r["dir"], r.get("win"), r["syms"]], sort_keys=True)
                if sig in seen:
                    continue
                seen.add(sig)
                v = factory.validate(dict(r))
                if not v:
                    continue
                v["_kind"] = "fusion"
                v["_variant"] = "F" + hashlib.sha1(sig.encode()).hexdigest()[:7]
                v["_name"] = (str(r["source"])[7:] + f" ({'long' if r['dir'] > 0 else 'short'})")[:160]
                out.append(v)
        return out

    # ------------------------------------------------------------ 2.47: bigger-timeframe / multi-day and option facts
    def _add_new_facts(self, f: dict, sym, now, slot: int, m: dict, bars: tuple, prem: list, rules: list):
        """Adds the 2.47 facts (lab/tfacts.py and the option momentum facts) to f, the same as the Lab's bar table.
        Computed only when a rule here uses one (history is fetched once a day per symbol); NaN otherwise."""
        from lab import factory, tfacts
        new = set(factory.TF_FACTS) | set(factory.OPT_FACTS)
        used = set()
        for r in rules:
            for c in list(r.get("conds") or []) + list((r.get("then") or {}).get("conds") or []):
                if c and c[0] in new:
                    used.add(c[0])
        try:
            if used & set(factory.TF_FACTS):
                hist = self._tf_hist(sym, now.date())
                if hist:
                    f.update(tfacts.live(hist, *bars))
            if used & set(factory.OPT_FACTS):
                f.update(self._opt_live(sym, now, slot, m, prem))
        except Exception as ex:
            if self._rate(f"newfacts:{sym}", 1800):
                self.e.log(f"Timeframe / option facts for {sym} not ready: {ex}")
        for k in new:
            if k not in f:
                f[k] = np.full(slot, np.nan)

    def _tf_hist(self, sym, day) -> list:
        """Earlier days' 5-min bars [(o, h, l, c) per day, oldest first], cached for the day; retried every 30 min
        when empty."""
        from lab import tfacts
        cache = self.__dict__.setdefault("_tfh", {})
        hit = cache.get((sym, day))
        if hit and (hit[1] or time.time() - hit[0] < 1800):
            return hit[1]
        rows = []
        try:
            mtf = getattr(self.e, "mtf", None)
            if mtf is None:
                from .mtf import MTF
                mtf = self.e.mtf = MTF(self.e)
            rows, _ = mtf.long_rows(sym, 5, 25)
        except Exception:
            rows = []
        if not rows:
            from .mtf import resample
            rows = resample(sorted(self.history(sym) or [], key=lambda r: r[0]), 5)
        by = {}
        for r in rows or []:
            d = dt.datetime.fromtimestamp(r[0], IST).date()
            if d < day:
                by.setdefault(d, []).append(r)
        out = []
        for d in sorted(by):
            a = np.asarray(sorted(by[d], key=lambda r: r[0]), float)
            if len(a) >= 10:
                out.append((a[:, 1], a[:, 2], a[:, 3], a[:, 4]))
        out = out[-int(tfacts.config().get("history_days") or 12):]
        cache[(sym, day)] = (time.time(), out)
        return out

    def _opt_live(self, sym, now, slot: int, m: dict, prem: list) -> dict:
        """The at-the-money call / put chosen at each reference bar and its premium change since, per closed bar of
        today (kept per bar, so two-step rules see earlier bars too); the straddle vs its average so far."""
        from lab import factory
        st = self.__dict__.setdefault("_optref", {}).setdefault((sym, now.date()), {"refs": {}, "bars": {}})
        o = m.get("opt") or {}
        rows = {r["strike"]: r for r in (o.get("rows") or []) if r.get("strike") is not None}
        spot, i = m.get("spot"), slot - 1
        cur = {}
        for key, tod in factory.OPT_REFS.items():
            if i == tod // 5 - 1 and key not in st["refs"] and spot and rows:
                k = min(rows, key=lambda s_: abs(s_ - spot))
                st["refs"][key] = (k, (rows[k].get("CE") or {}).get("ltp"), (rows[k].get("PE") or {}).get("ltp"))
            if key in st["refs"]:
                k, c0, p0 = st["refs"][key]
                row = rows.get(k) or {}
                c1, p1 = (row.get("CE") or {}).get("ltp"), (row.get("PE") or {}).get("ltp")
                cur[f"ce_m{key}"] = (c1 / c0 - 1) * 100 if c1 and c0 and c0 > 0.5 else np.nan
                cur[f"pe_m{key}"] = (p1 / p0 - 1) * 100 if p1 and p0 and p0 > 0.5 else np.nan
        st["bars"][i] = cur
        out = {}
        for key in factory.OPT_REFS:
            for side in ("ce", "pe"):
                name = f"{side}_m{key}"
                out[name] = np.array([st["bars"].get(j, {}).get(name, np.nan) for j in range(slot)], float)
        p = np.array([np.nan if x is None else x for x in prem], float)
        with np.errstate(divide="ignore", invalid="ignore"):
            import pandas as pd
            out["strd_avg"] = (p / pd.Series(p).expanding(min_periods=1).mean().to_numpy() - 1) * 100
        return out

    def _pack_rules(self) -> list:
        """One bench rule per strategy of each switched-on strategy file (exits aside: the bench uses its own styles)."""
        import hashlib
        import json as _json
        from lab import packs
        out, seen = [], set()
        for pid, e, res in packs.current(self.e.data_dir):
            if res.get("fatal"):
                continue
            for r in res["rules"]:
                if not r.get("dir"):
                    continue
                sig = _json.dumps([pid, r["conds"], r["dir"], r.get("win"), r["syms"]], sort_keys=True)
                if sig in seen:
                    continue
                seen.add(sig)
                v = dict(r)
                v["_kind"] = "pack"
                v["_variant"] = "P" + hashlib.sha1(sig.encode()).hexdigest()[:7]
                v["_name"] = f"{e.get('name') or pid}: {r.get('name') or ''} ({'long' if r['dir'] > 0 else 'short'})"[:160]
                out.append(v)
        return out

    def _hw_live(self, sym, now) -> dict | None:
        """2.43: today's heavyweights of an index (rules/heavyweights.json) for the hw_open / hw_day facts."""
        from lab.data import heavyweights
        w = heavyweights(sym, now.date())
        stocks = {}
        for s in w:
            fm = self.five_min(s, now.date())
            ms = self.e.metrics.get(s) or {}
            pc = ms.get("cp")
            if not fm or not len(fm["c"]) or not pc:
                continue
            stocks[s] = (float(fm["o"][0]), float(pc), [float(x) for x in fm["c"]])
        return {"weights": w, "stocks": stocks} if stocks else None

    def _bench_rules_stocks(self) -> list:
        """2.31: the same library / book setups on the stocks in play today (test bench only), kept apart on the board
        (their own version id and name) because a stock may behave differently from an index."""
        from lab import factory
        base = self._bench_rules()
        hit = getattr(self, "_lib_stock_cache", None)
        if hit and hit[0] is base:
            return hit[1]
        out = [dict(r, syms=[factory.STOCKS], _variant=r["_variant"] + "s", _name=(r["_name"] + " · stocks in play")[:180]) for r in base
               if r.get("_kind") not in ("pack", "fusion") or factory.STOCKS in r.get("syms", [])]   # only where they say
        self._lib_stock_cache = (base, out)
        return out

    def _trial_rule(self, sym, m, r, d, h5, l5, c5, emit):
        """A 5-year Lab rule on trial: chart-level exits use the rule's own stop; % exits are graded with a 1 ATR stop
        and a 2R target on the price (the same way the chart overlays show them)."""
        from lab import exits as X, factory
        atr = float(X.atr14(h5, l5, c5)[-1])
        if r["exit"] in factory.STYLES:
            R = float(X.risk_pts(r["exit"], m["spot"], d, atr, l5[-1], h5[-1]))
            tr = X.STYLES[r["exit"]].get("target_r") or 2.0
        else:
            R, tr = atr, 2.0
        if not np.isfinite(R) or R <= 0 or not m.get("spot"):
            return
        e, h = float(m["spot"]), r.get("hold") or {}
        emit({"sym": sym, "kind": "lab5y", "direction": "up" if d > 0 else "down", "severity": 2,
              "title": f"{sym} 5-year Lab rule {r['id']}: {'BUY call' if d > 0 else 'BUY put'} (on trial)",
              "detail": f"{r['rule']} 5-year holdout: {h.get('n', 0)} trades, avg ₹{h.get('avg', 0):,.0f}. On trial: recorded and graded only.",
              "price": e, "data": {"factory": {"id": r["id"], "lab": "5y"}, "trial_key": r["id"],
                                   "setup_levels": {"entry": round(e, 2), "stop": round(e - d * R, 2), "target": round(e + d * tr * R, 2), "rr": tr}}})

    def _tested_stocks(self) -> set:
        """Stocks with option history in the current Lab (the ones its 'any F&O stock' rules were tested on)."""
        t = getattr(self, "_tested", None)
        if t is None or time.time() - t[0] > 3600:
            from pathlib import Path
            hd = Path(self.e.data_dir) / "history"
            self._tested = t = (time.time(), {p.parent.name for p in hd.glob("*/meta.json")})
        return t[1]

    def _range_sell(self, sym, m, r, h, now, emit):
        """2.55 (Mandeep, 11 Nov: sell only sideways and risk-controlled; "not hedging"): the promoted straddle rules are
        near-copies of one idea, so they raise ONE signal per index per day (the rest count as agreeing), marked with the
        day's regime, below Focus (not on Telegram). Paper: off by default (naked selling stopped); 'naked' only on request."""
        cfg = range_sell_cfg()
        key = (sym, now.date())
        day = self.__dict__.setdefault("_rs_day", {})
        if len(day) > 50:
            for k in [k for k in day if k[1] != now.date()]:
                day.pop(k, None)
        rec = day.setdefault(key, {"ids": set(), "signals": 0})
        rec["ids"].add(r["id"])
        if rec["signals"] >= int(cfg.get("max_signals_per_day") or 1):
            return
        rec["signals"] += 1
        reg = (self.e.regimes or {}).get(sym) or {}
        fits = reg.get("code") in (cfg.get("regimes") or ["range", "quiet"])
        took = None
        if cfg.get("paper") == "naked" and fits:
            took = self._paper_straddle(sym, m, r, now)
        emit({"sym": sym, "kind": "range_sell", "direction": "neutral", "severity": int(cfg.get("severity") or 2),
              "title": f"{sym} range-day sell setup (Strategy Lab straddle rules) · on trial",
              "detail": (f"Market now: {reg.get('label') or 'not read yet'}. "
                         + ("Range day: fits the sell playbook. " if fits else "Not a range day: the playbook says skip. ")
                         + f"First rule: {r['rule']} Holdout {h.get('n', 0)} trades, avg ₹{h.get('avg', 0):,.0f}. "
                         + (f"Paper-sold both legs ({took}). " if took else
                            "No paper trade: naked selling is off; hedged versions are being tested first. ")
                         + "Recorded only, not in Focus or Telegram."),
              "price": m.get("spot"), "data": {"factory": {"id": r["id"], "sell": True}, "playbook": "range_sell",
                                               "regime": reg.get("code"), "fits": bool(fits)}})

    def _paper_straddle(self, sym, m, r, now) -> str | None:
        """Paper-sell the ATM call and put of the nearest expiry, each with the rule's stop, held to 15:15."""
        from lab import factory
        pb = self.e.paper
        if not pb or r["exit"] not in factory.SELL_EXITS or r["exit"].startswith("fly"):
            return None
        if float(pb.cfg.get("capital") or 0) < 150000 or (pb.guard(now)["blocked"] and not pb.cfg.get("aggressive")):
            return None
        u = self.e.uni.underlyings.get(sym) if self.e.uni else None
        exp = u.near_expiry(now.date()) if u else None
        ch = u.chains.get(exp, {}) if exp else {}
        if not ch or not m.get("spot"):
            return None
        k = min(ch, key=lambda x: abs(x - m["spot"]))
        und_key = u.key if self.e.state.get(u.key) and self.e.state.get(u.key).ltp else u.fut_key
        done = []
        for side in ("CE", "PE"):
            o = ch[k].get(side)
            inst = self.e.state.get(o.key) if o else None
            if not inst or not inst.ltp or any(t["opt_key"] == o.key for t in pb.open_trades()):
                continue
            opt = {"key": o.key, "label": f"{sym} {k:g} {side} {exp:%d %b}", "side": side, "lot": o.lot}
            pb._open(self.e.state, now, sym=sym, kind="factory", opt=opt, entry=inst.bid or inst.ltp, ltp=inst.ltp, lots=1,
                     direction=-1, manual=0, und_key=und_key, why=f"Strategy Lab {r['id']}",
                     exit=[None, factory.SELL_EXITS[r["exit"]], 400])
            done.append(opt["label"])
        return " + ".join(done) or None

    def five_min(self, sym, day):
        rows = self.today_bars(sym)
        if len(rows) < 2:
            return None
        a = np.asarray(rows, dtype=float)
        t0 = self._session_start(day)
        a = a[a[:, 0] >= t0]
        if len(a) < 2:
            return None
        t, o, h, l, c, v = ta.resample(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], 5, t0)
        return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

    # ------------------------------------------------------------ live setup scanner (every closed 5-min bar)
    def _rate(self, key, seconds) -> bool:
        """True at most once per `seconds` for a key (keeps repeated errors out of the log)."""
        t = getattr(self, "_rate_t", None)
        if t is None:
            t = self._rate_t = {}
        if time.time() - t.get(key, 0) >= seconds:
            t[key] = time.time()
            return True
        return False

    def scan(self, now, emit):
        t0 = self._session_start(now.date())
        slot = int((now.timestamp() - t0) // 300)
        if slot < 3 or slot == self.last_slot or now.timestamp() < t0:
            return
        self.last_slot = slot
        try:
            self._factory(now, slot, emit)
        except Exception as ex:
            self.e.log(f"Strategy Lab rules check failed: {ex}")
        for sym, m in list(self.e.metrics.items()):
            try:                                   # one stock's bad data or a setup bug never stops the others
                self._scan_one(sym, m, now, slot, emit)
            except Exception as ex:
                if self._rate(f"scan_err:{sym}", 600):
                    self.e.log(f"Setup scan {sym} skipped: {ex}")

    def _scan_one(self, sym, m, now, slot, emit):
        b = self.five_min(sym, now.date())
        if not b or len(b["c"]) < 4:
            return
        closed = {k: v[:slot] for k, v in b.items()} if len(b["c"]) > slot else b
        if len(closed["c"]) < 4:
            return
        ind = setups.indicators(closed)
        ctx = self.context(sym, m)
        v = closed["v"]
        ctx["rel_vol"] = float(v[-1] / v[:-1].mean()) if len(v) > 1 and v[:-1].mean() > 0 else None
        if len(closed["c"]) >= 8:
            try:
                self._pattern_alerts(sym, m, closed, ctx, emit)
            except Exception as ex:
                self.e.log(f"Pattern check {sym} failed: {ex}")
        try:
            self._candle_trials(sym, m, closed, ctx, emit)
        except Exception as ex:
            if self._rate(f"candle_err:{sym}", 600):
                self.e.log(f"Candle trial check {sym} skipped: {ex}")
        for s in setups.evaluate(closed, ind, ctx):
            d = 1 if s["direction"] == "up" else -1
            s["ts"] = now.timestamp()
            self.active.setdefault(sym, []).append(s)
            e = s["entry"]
            hist = {"line": None, "grade": "unknown", "p": None}
            edge = getattr(self.e, "edge", None)
            if edge is not None and sym in ("NIFTY", "BANKNIFTY"):
                try:
                    edge.load()
                    hist = edge.verdict(sym, s["setup"], s["direction"], self._edge_row(sym, m, closed, ind, ctx, s, slot))
                except Exception:
                    pass
            detail = f"Stop {s['stop']:,.1f}, target {s['target']:,.1f} (reward:risk {s['rr']}). " + "; ".join(s["why"]) + "."
            if hist["line"]:
                detail += " " + hist["line"]
            emit({"sym": sym, "kind": "setup_" + s["setup"], "direction": s["direction"],
                  "severity": 1 if hist["grade"] == "losing" else 2,
                  "title": f"{sym} {setups.NAMES[s['setup']]} {'long' if d > 0 else 'short'} at {e:,.1f}",
                  "detail": detail,
                  "price": m.get("spot"),
                  "data": {"setup": s["setup"], "history": hist, "setup_levels": {
                      "stop": round(s["stop"], 2), "target": round(s["target"], 2), "rr": float(s["rr"]),
                      "stop_pct": round(abs(e - s["stop"]) / e * 100, 2), "target_pct": round(abs(s["target"] - e) / e * 100, 2)}}})

    @staticmethod
    def _levels(ctx) -> list:
        cp = ctx.get("cpr") or {}
        lv = [("yesterday's high", ctx.get("pdh")), ("yesterday's low", ctx.get("pdl")), ("yesterday's close", ctx.get("pdc")),
              ("CPR top", cp.get("TC")), ("CPR bottom", cp.get("BC")), ("opening-range high", ctx.get("or_high")),
              ("opening-range low", ctx.get("or_low")), ("call OI wall", ctx.get("resistance")), ("put OI wall", ctx.get("support"))]
        return [(n, float(v)) for n, v in lv if _num(v)]

    def _pattern_alerts(self, sym, m, closed, ctx, emit):
        book = getattr(self.e, "patterns", None)
        for p in PT.detect(closed, self._levels(ctx), recent=1):
            if not m.get("is_index") and p["pattern"] in NOISY_FOR_STOCKS:
                continue                      # common, small patterns: drawn on the chart, not alerted for 200 stocks
            d = 1 if p["direction"] == "up" else -1
            line, grade = book.line(sym, p["pattern"], p["direction"]) if book else (None, "unknown")
            e = p["entry"]
            detail = f"{p['why'][0].upper() + p['why'][1:]}. Stop {p['stop']:,.1f}, target {p['target']:,.1f} (reward:risk {p['rr']})."
            if line:
                detail += " " + line
            emit({"sym": sym, "kind": "pattern_" + p["pattern"], "direction": p["direction"],
                  "severity": 1 if grade in ("losing", "unknown") and not m.get("is_index") else 1 if grade == "losing" else 2,
                  "title": f"{sym} {p['name']} {'(bullish)' if d > 0 else '(bearish)'} at {e:,.1f}", "detail": detail, "price": m.get("spot"),
                  "data": {"pattern": p["pattern"], "history_grade": grade, "setup_levels": {
                      "stop": round(p["stop"], 2), "target": round(p["target"], 2), "rr": float(p["rr"]),
                      "stop_pct": round(abs(e - p["stop"]) / e * 100, 2), "target_pct": round(abs(p["target"] - e) / e * 100, 2)}}})

    def _candle_trials(self, sym, m, closed, ctx, emit):
        """Candle bullets (live/candles.py, listed in the registry) on the 5-minute bar that just closed. While a kind is
        on trial the engine only records and grades it; nothing you see changes. Indices and the Lab's first stocks."""
        from . import candles as CDL, registry
        if not m.get("is_index"):
            from lab import rules
            if sym not in rules.stock_list("lab_first"):
                return
        o, h, l, c = (np.asarray(closed[k], dtype=float) for k in "ohlc")
        if len(c) < 8:
            return
        # the same levels the Lab uses for these ideas (lab/factory.py lvl_touch), so live and backtest agree
        named = [(n, float(v)) for n, v in (("yesterday's high", ctx.get("pdh")), ("yesterday's low", ctx.get("pdl")),
                                             ("yesterday's close", ctx.get("pdc")), ("opening-range high", ctx.get("or_high")),
                                             ("opening-range low", ctx.get("or_low")), ("VWAP", m.get("vwap"))) if _num(v)]
        ideas = CDL.trade_ideas(o, h, l, c, [v for _, v in named], registry.candle_spec(), last_only=True)
        names = registry.names("candle")
        for x in ideas:
            at_lv = registry.get("candle_" + x["name"]).get("at_level")
            lv_name = min(named, key=lambda nv: abs(nv[1] - x["entry"]))[0] if named and at_lv else None
            d, e = x["d"], x["entry"]
            emit({"sym": sym, "kind": "candle_" + x["name"], "direction": "up" if d > 0 else "down", "severity": 1,
                  "title": f"{sym} {names.get(x['name'], x['name'])} {'(bullish)' if d > 0 else '(bearish)'}"
                           f"{' at ' + lv_name if lv_name else ''} at {e:,.1f}",
                  "detail": f"Stop {x['stop']:,.1f} beyond the pattern, target {x['target']:,.1f} (2R). On trial: recorded and graded only.",
                  "price": m.get("spot"),
                  "data": {"candle": x["name"], "level": lv_name, "level_atr": round(x["lvl_atr"], 2) if x["lvl_atr"] is not None else None,
                           "setup_levels": {"entry": round(e, 2), "stop": round(x["stop"], 2), "target": round(x["target"], 2), "rr": 2.0}}})

    def _chart_patterns(self, sym, T, o, h, l, c, ctx):
        """Patterns found anywhere on the shown bars (for drawing), plus swing labels and market structure."""
        n = len(c)
        key = (sym, int(T[-1]) if n else 0, n)
        if self._pat_cache.get("key") == key:
            return self._pat_cache["val"]
        b = {"o": o, "h": h, "l": l, "c": c}
        lv = self._levels(ctx)
        found, seen = [], set()
        for i in range(8, n):
            bi = {k: v[:i + 1] for k, v in b.items()}
            for p in PT.detect(bi, lv, recent=1):
                k = (p["pattern"], p["direction"], round(p["level"] or 0, 1))
                if k in seen:
                    continue
                seen.add(k)
                found.append(p)
        found = found[-10:]
        book = getattr(self.e, "patterns", None)
        out = []
        for p in found:
            line, grade = book.line(sym, p["pattern"], p["direction"]) if book else (None, "unknown")
            out.append({**{k: v for k, v in p.items() if k not in ("lines", "i0", "i1")}, "time": int(T[p["i1"]]), "start": int(T[p["i0"]]),
                        "lines": [[{"time": int(T[min(max(int(x), 0), n - 1)]), "value": round(float(y), 2)} for x, y in ln] for ln in p["lines"]],
                        "history": line, "grade": grade})
        sw = PT.swings_for_chart(b)
        swings = [{"time": int(T[s_["i"]]), "price": s_["price"], "type": s_["type"], "label": s_["label"]} for s_ in sw if s_["label"]]
        val = {"patterns": out, "swings": swings[-16:], "structure": PT.structure(sw)}
        self._pat_cache = {"key": key, "val": val}
        return val

    def _smart_lines(self, sym, m, today, rows, dates, offline) -> dict | None:
        """2.54: the lines that matter for this symbol now (live/linesmith.py), cached per symbol and minute.
        Display only; a failure leaves the chart as it was."""
        try:
            from . import linesmith
            if offline or not today:                       # market closed: the last session in the chart
                last = dates[-1] if dates else None
                today = [r for r in rows if last and linesmith.ist_day(r[0]) == last]
            if not today:
                return None
            key = (sym, int(today[-1][0] // 60), round(float((m or {}).get("spot") or 0), 1))
            c = getattr(self, "_smart_cache", None)
            if c is None:
                c = self._smart_cache = {}
            if key in c:
                return c[key]
            multi = rows if len({linesmith.ist_day(r[0]) for r in rows[-1200:]}) > 1 else (
                (self.history(sym) if not offline else []) + today)
            out = linesmith.lines_from_engine(self.e, sym, m, today, multi[-3 * 375:])
            if len(c) > 200:
                c.clear()
            c[key] = out
            return out
        except Exception as ex:
            if self._rate(f"smart_err:{sym}", 600):
                self.e.log(f"Smart lines for {sym} skipped: {ex}")
            return {"error": str(ex)[:160]}

    def _scenario(self, sym, day, ctx, tf, last_bar_time):
        """Three projected paths (up / sideways / down) and the 80% range, as chart series in the future."""
        sc = getattr(self.e, "scenarios", None)
        if sc is None or sym not in ("NIFTY", "BANKNIFTY"):
            return None
        today = self.today_bars(sym)
        if not today:
            return {"ready": False, "why": "Starts 15 minutes after the open."}
        try:
            vix = self.e._vix() if self.e.state else None
            f = sc.live(sym, today, day, ctx.get("pdc"), vix and vix.get("ltp"))
        except Exception as ex:
            return {"ready": False, "why": f"Scenario engine error: {ex}"}
        if not f:
            st = sc.status()["symbols"].get(sym) or {}
            why = ("Needs 1-minute history: log in to Upstox; it downloads by itself (a few minutes)." if not st.get("have")
                   else "Starts 15 minutes after the open.")
            return {"ready": False, "why": why}
        t0, t = f["t0"], f["t"]
        def series(vals):
            pts = [{"time": int(t0 + t * 60 + IST_SHIFT) // (60 * tf) * (60 * tf), "value": f["spot"]}]
            for j, v in enumerate(vals):
                minute = t + 1 + j
                if minute % tf == 0 or j == len(vals) - 1:
                    tm = int(t0 + minute * 60 + IST_SHIFT)
                    if tm > pts[-1]["time"]:
                        pts.append({"time": tm, "value": v})
            return pts
        out = {k: v for k, v in f.items() if k not in ("paths", "band")}
        out["ready"] = True
        out["paths"] = {k: {**{x: y for x, y in v.items() if x != "path"}, "line": series(v["path"])} for k, v in f["paths"].items()}
        out["band"] = {"lo": series(f["band"]["lo"]), "hi": series(f["band"]["hi"])}
        return out

    def _edge_row(self, sym, m, b, ind, ctx, s, slot) -> dict:
        """The facts the signal filter was trained on, as far as they are known live (missing ones stay empty)."""
        d = 1 if s["direction"] == "up" else -1
        c = float(b["c"][-1])
        o = m.get("opt") or {}
        vix = self.e._vix() if self.e.state else None
        o0 = float(b["o"][0])
        cp = ctx.get("cpr") or {}
        atr = float(ind["atr"][-1]) if len(ind["atr"]) else math.nan
        wall = ctx.get("resistance") if d > 0 else ctx.get("support")
        r = float(ind["rsi"][-1])
        return {"dir": d, "minute": 5 * slot - 1, "rr": s["rr"], "dte": o.get("days_to_expiry"), "iv": o.get("atm_iv"),
                "vix": vix and vix.get("ltp"), "vix_chg": vix and vix.get("chg_pct"),
                "gap_pct": (o0 / ctx["pdc"] - 1) * 100 if ctx.get("pdc") else math.nan,
                "move_pct": d * (c / o0 - 1) * 100, "dist_vwap_pct": d * (c / float(ind["vw"][-1]) - 1) * 100,
                "rsi": r if d > 0 else 100 - r, "ema_spread_pct": d * (float(ind["e9"][-1]) / float(ind["e21"][-1]) - 1) * 100,
                "st_with": int(ind["sd"][-1] == d), "cpr_width": cp.get("width_pct"),
                "risk_atr": abs(c - s["stop"]) / atr if atr and atr == atr else math.nan,
                "wall_room": ((wall - c) if d > 0 else (c - wall)) / c * 100 if wall else math.nan}

    # ------------------------------------------------------------ chart payload
    def offline_rows(self, sym) -> list:
        """Past sessions without the live feed (before the open, after hours, scanner off, simulator).

        Indices: the scenario engine's saved 1-minute history. Anything else: Yahoo Finance 5-minute bars (last
        60 days), cached for the day with the Time Machine's copy.
        """
        import pandas as pd
        from pathlib import Path
        day = self.e.clock.now().date()
        k = (sym, day, "offline")
        if k in self.hist:
            return self.hist[k]
        rows = []
        base = Path(self.e.data_dir) / "history"
        try:
            f = base / sym / "minutes.parquet"
            df = None
            if f.exists():
                df = pd.read_parquet(f)
                df = df[df["ts"] >= df["ts"].max() - 18 * 86400]
            else:
                tf_ = base / "tm" / f"{sym}.parquet"
                if tf_.exists() and dt.datetime.fromtimestamp(tf_.stat().st_mtime, IST).date() == day:
                    df = pd.read_parquet(tf_)
                else:
                    from .timemachine import TimeMachine
                    df = TimeMachine._yahoo(sym)
                    if df is not None and len(df):
                        tf_.parent.mkdir(parents=True, exist_ok=True)
                        df.to_parquet(tf_, index=False)
            if df is not None and len(df):
                df = df[df["ts"] >= df["ts"].max() - 18 * 86400]
                vol = df["volume"] if "volume" in df else 0
                rows = [[float(a), float(b), float(c), float(d), float(e_), float(v or 0)] for a, b, c, d, e_, v in
                        zip(df["ts"], df["open"], df["high"], df["low"], df["close"], (vol if hasattr(vol, "__iter__") else [0] * len(df)))]
        except Exception as ex:
            self.e.log(f"Chart history (offline) for {sym} failed: {ex}")
        self.hist[k] = rows
        return rows

    def _payload_offline(self, sym, tf, days):
        return self.payload(sym, tf, days)

    def payload(self, sym, tf=5, days=3) -> dict | None:
        e = self.e
        day = e.clock.now().date()
        live_m = e.metrics.get(sym)
        t_open = self._session_start(day)
        # only real session bars count as "today": before 9:15 the feed sends a few pre-open prices, and a lone
        # 7:44 tick used to blank the whole chart
        today = [r for r in (self.today_bars(sym) if live_m else []) if t_open <= r[0] < t_open + 6.5 * 3600]
        hist = self.history(sym) if (days > 1 or not today) and live_m else []
        offline = False
        if (not today and not hist) or self.hist.pop((sym, day, "force_offline"), False):
            today, hist = [], self.offline_rows(sym)
            offline = bool(hist)
        rows = hist + today
        if not rows:
            return None
        m = live_m
        if not m:                                  # no live feed for this symbol: describe it from the bars
            last = max(dt.datetime.fromtimestamp(r[0], IST).date() for r in rows)
            prev_rows = [r for r in rows if dt.datetime.fromtimestamp(r[0], IST).date() < last]
            last_rows = [r for r in rows if dt.datetime.fromtimestamp(r[0], IST).date() == last]
            spot = last_rows[-1][4]
            pc = prev_rows[-1][4] if prev_rows else None
            m = {"sym": sym, "is_index": sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"), "spot": spot,
                 "chg_pct": round((spot / pc - 1) * 100, 2) if pc else None, "cp": pc}
        prev_bars = []
        if offline:
            ds = sorted({dt.datetime.fromtimestamp(r[0], IST).date() for r in rows})
            if len(ds) >= 2:
                prev_bars = sorted((r for r in rows if dt.datetime.fromtimestamp(r[0], IST).date() == ds[-2]), key=lambda r: r[0])
        if not today and days == 1:              # market closed / before the open: show the last session
            last = max(dt.datetime.fromtimestamp(r[0], IST).date() for r in rows)
            rows = [r for r in rows if dt.datetime.fromtimestamp(r[0], IST).date() == last]
        a = np.asarray(sorted(rows, key=lambda r: r[0]), dtype=float)
        # keep the last `days` sessions
        dates = sorted({dt.datetime.fromtimestamp(x, IST).date() for x in a[:, 0]})[-days:]
        keep = np.array([dt.datetime.fromtimestamp(x, IST).date() in dates for x in a[:, 0]])
        a = a[keep]
        segs = []
        for d in dates:
            t0 = self._session_start(d)
            s = a[(a[:, 0] >= t0) & (a[:, 0] < t0 + 6.5 * 3600)]
            if len(s):
                segs.append(ta.resample(s[:, 0], s[:, 1], s[:, 2], s[:, 3], s[:, 4], s[:, 5], tf, t0))
        if not segs:
            if not offline and not getattr(self, "_retry", False):    # whatever happened, fall back to saved history
                self._retry = True
                try:
                    self.hist[(sym, day, "force_offline")] = True
                    return self._payload_offline(sym, tf, days)
                finally:
                    self._retry = False
            return None
        t, o, h, l, c, v = (np.concatenate([sg[k] for sg in segs]) for k in range(6))
        vw = np.concatenate([ta.vwap_bands(sg[2], sg[3], sg[4], sg[5])[0] for sg in segs])
        bands = [np.concatenate([ta.vwap_bands(sg[2], sg[3], sg[4], sg[5])[j] for sg in segs]) for j in range(1, 5)]
        st, sd = ta.supertrend(h, l, c, 10, 3.0)
        r = ta.rsi(c, 14)
        mline, msig, mhist = ta.macd(c)
        T = (t + IST_SHIFT).astype(int)

        def line(arr):
            return [{"time": int(tt), "value": round(float(x), 2)} for tt, x in zip(T, arr) if not math.isnan(x)]
        ctx = self.context(sym, m)
        if offline and not ctx.get("pdh") and prev_bars:          # previous-day levels from the bars themselves
            ph, pl, pc_ = max(r[2] for r in prev_bars), min(r[3] for r in prev_bars), prev_bars[-1][4]
            ctx.update(pdh=ph, pdl=pl, pdc=pc_, cpr=ta.cpr(ph, pl, pc_))
        g = e.gex.get(sym) or {}
        o_ = m.get("opt") or {}
        # expected move for today from the ATM straddle (from the day's open)
        em = None
        if m.get("day_o") and o_.get("daily_move_pct"):
            em = {"up": m["day_o"] * (1 + o_["daily_move_pct"] / 100), "down": m["day_o"] * (1 - o_["daily_move_pct"] / 100)}
        lv = []
        def add(name, price, color, style=2, group="levels"):
            if _num(price):
                lv.append({"name": name, "price": round(float(price), 2), "color": color, "style": style, "group": group})
        cp = ctx.get("cpr") or {}
        add("PDH", ctx.get("pdh"), "#1E8A5A", 0, "prev")
        add("PDL", ctx.get("pdl"), "#C23B32", 0, "prev")
        add("PDC", ctx.get("pdc"), "#5E6B78", 2, "prev")
        for k, col in (("TC", "#3D2FA8"), ("P", "#3D2FA8"), ("BC", "#3D2FA8"), ("R1", "#9A6A12"), ("S1", "#9A6A12"), ("R2", "#9A6A12"), ("S2", "#9A6A12")):
            add(k if k not in ("P",) else "Pivot", cp.get(k), col, 1 if k in ("TC", "BC", "P") else 3, "cpr")
        add("OR high", m.get("or_high"), "#16212C", 3, "or")
        add("OR low", m.get("or_low"), "#16212C", 3, "or")
        add("Call OI wall", o_.get("resistance"), "#C23B32", 0, "oi")
        add("Put OI wall", o_.get("support"), "#1E8A5A", 0, "oi")
        add("Max pain", o_.get("max_pain"), "#9A6A12", 2, "oi")
        add("Gamma flip", g.get("flip"), "#E07B00", 1, "gamma")
        add("Gamma call wall", g.get("call_wall"), "#B04ACF", 2, "gamma")
        add("Gamma put wall", g.get("put_wall"), "#B04ACF", 2, "gamma")
        if em:
            add("Expected high", em["up"], "#5E6B78", 3, "em")
            add("Expected low", em["down"], "#5E6B78", 3, "em")
        # markers: alerts, setups, paper and desk trades
        mk = []
        t_first = float(t[0]) if len(t) else 0
        def tt(ts):
            return int((ts // (60 * tf)) * 60 * tf + IST_SHIFT)
        for al in e.alerts_mem:
            if al["sym"] != sym or al["ts"] < t_first:
                continue
            setup = al["kind"].startswith("setup_")
            mk.append({"time": tt(al["ts"]), "position": "belowBar" if al["direction"] == "up" else "aboveBar" if al["direction"] == "down" else "inBar",
                       "color": "#1E8A5A" if al["direction"] == "up" else "#C23B32" if al["direction"] == "down" else "#9A6A12",
                       "shape": "arrowUp" if al["direction"] == "up" else "arrowDown" if al["direction"] == "down" else "circle",
                       "text": ("BUY" if al["direction"] == "up" else "SELL") if (al.get("data") or {}).get("focus") and al["direction"] in ("up", "down")
                               else (SHORT.get(al["kind"][6:], "") if setup else "") if al["severity"] >= 2 else "",
                       "id": al["id"], "title": al["title"], "kind": al["kind"]})
        trades = []
        if e.paper:
            for tr in e.store.trades(source=e.source, limit=300):
                if tr["sym"] == sym and tr["entry_ts"] >= t_first:
                    side = "Sell" if (tr.get("dir") or 1) < 0 else "Buy"
                    trades.append({"time": tt(tr["entry_ts"]), "label": side, "title": f"Paper {side.lower()} {tr['label']}", "kind": "paper"})
                    if tr.get("exit_ts"):
                        trades.append({"time": tt(tr["exit_ts"]), "label": f"₹{tr['net']:,.0f}", "title": f"Paper exit ({tr['reason']}) ₹{tr['net']:,.0f}", "kind": "paper"})
        try:
            for tr in e.desk.trades(source=e.source):
                if tr["sym"] == sym and tr["entry_ts"] >= t_first:
                    trades.append({"time": tt(tr["entry_ts"]), "label": "Desk", "title": f"Strategy desk: {tr['family']}", "kind": "desk"})
                    if tr.get("exit_ts"):
                        trades.append({"time": tt(tr["exit_ts"]), "label": f"₹{(tr['net'] or 0):,.0f}", "title": f"Desk exit ₹{(tr['net'] or 0):,.0f}", "kind": "desk"})
        except Exception:
            pass
        for x in trades:
            mk.append({"time": x["time"], "position": "aboveBar", "color": "#3D2FA8", "shape": "square", "text": x["label"][:12],
                       "title": x["title"], "kind": x["kind"]})
        mk.sort(key=lambda x: x["time"])
        seen = set()                         # one label per bar and side, the rest stay as plain arrows
        for m_ in mk:
            k_ = (m_["time"], m_["position"])
            if k_ in seen:
                m_["text"] = ""
            seen.add(k_)
        # chart read and active setups (today, still unresolved)
        b5 = self.five_min(sym, day) if live_m else None
        if b5 is None and offline and len(segs):
            sg = segs[-1] if tf == 5 else None
            if sg is None:
                d_last = dates[-1]
                t0_ = self._session_start(d_last)
                s_ = a[(a[:, 0] >= t0_) & (a[:, 0] < t0_ + 6.5 * 3600)]
                sg = ta.resample(s_[:, 0], s_[:, 1], s_[:, 2], s_[:, 3], s_[:, 4], s_[:, 5], 5, t0_) if len(s_) else None
            if sg is not None:
                b5 = {"t": sg[0], "o": sg[1], "h": sg[2], "l": sg[3], "c": sg[4], "v": sg[5]}
        read, live_setups = [], []
        if b5 is not None and len(b5["c"]) >= 3:
            ind = setups.indicators(b5)
            read = setups.chart_read(b5, ind, {**ctx, "flip": g.get("flip")})
            px = m.get("spot") or float(b5["c"][-1])
            for s in self.active.get(sym, [])[-8:]:
                d = 1 if s["direction"] == "up" else -1
                hit_t = (px - s["target"]) * d >= 0
                hit_s = (px - s["stop"]) * d <= 0
                live_setups.append({**s, "name": setups.NAMES[s["setup"]], "state": "target hit" if hit_t else "stopped" if hit_s else "active",
                                    "r_now": round((px - s["entry"]) * d / abs(s["entry"] - s["stop"]), 2)})
        smart = self._smart_lines(sym, m, today, rows, dates, offline)      # 2.54: lines drawn by the app, with their record
        scen = self._scenario(sym, day, ctx, tf, int(T[-1]) if len(T) else None)
        try:
            pats = self._chart_patterns(sym, T, o, h, l, c, ctx)
        except Exception as ex:
            pats = {"patterns": [], "swings": [], "structure": None, "error": str(ex)[:120]}
        return {"sym": sym, "name": m.get("name"), "tf": tf, "spot": m.get("spot"), "chg_pct": m.get("chg_pct"), "scenario": scen, **pats,
                "offline": ({"as_of": str(dates[-1]), "source": "saved 1-minute history" if (Path_(e.data_dir) / "history" / sym / "minutes.parquet").exists()
                             else "Yahoo Finance 5-minute bars"} if offline else None),
                "candles": [{"time": int(a_), "open": round(float(b_), 2), "high": round(float(c_), 2), "low": round(float(d_), 2), "close": round(float(e_), 2)}
                            for a_, b_, c_, d_, e_ in zip(T, o, h, l, c)],
                "volume": [{"time": int(a_), "value": float(x), "color": "rgba(30,138,90,.45)" if cc >= oo else "rgba(194,59,50,.45)"}
                           for a_, x, cc, oo in zip(T, v, c, o)],
                "has_volume": bool(np.nansum(v) > 0),
                "series": {"vwap": line(vw), "u1": line(bands[0]), "d1": line(bands[1]), "u2": line(bands[2]), "d2": line(bands[3]),
                           "ema9": line(ta.ema(c, 9)), "ema21": line(ta.ema(c, 21)), "ema50": line(ta.ema(c, 50)),
                           "st_up": line(np.where(sd > 0, st, np.nan)), "st_dn": line(np.where(sd < 0, st, np.nan)),
                           "rsi": line(r), "macd": line(mline), "macd_sig": line(msig),
                           "macd_hist": [{"time": int(a_), "value": round(float(x), 2), "color": "rgba(30,138,90,.6)" if x >= 0 else "rgba(194,59,50,.6)"}
                                         for a_, x in zip(T, mhist) if not math.isnan(x)]},
                "levels": lv, "smart": smart, "markers": mk, "read": read, "setups": live_setups, "ctx": {k: v for k, v in ctx.items() if k != "cpr"},
                "cpr": ctx.get("cpr"), "regime": e.regimes.get(sym), "updated": time.time()}
