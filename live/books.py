"""Extra paper accounts next to the main one (gun, 2.49).

Mandeep, 10 Nov 2026:
  14. "add one more section in the paper trade that will take only and only most precise and accurate trade as per the
      most high rated trades ... after a month system can check the difference between regular trades and the high
      quality selected ones."
  15. "one more thing in paper trade ... to earn 3% daily profit on the total capital. system should take the trade on
      the top alerts only. the purpose is to compound 3% capital everyday. capital can be set manual."

Gun: any number of named paper accounts, each a normal PaperBook (same fills, costs, exits, free-money check, NSE
calendar) with its own source "<feed>#<id>", so its trades never mix with the main account, the scorecard or the test
bench. Optional day target / day stop (close everything when the day's money reaches it) and compounding (each day
starts from yesterday's closing money). Bullets: rules/paper_books.json (which alerts each account takes, its rules,
its capital); your own capital for an account is saved in your settings (live.books.<id>.capital).

A bad or failing account is skipped, logged and marked failed; the main account and everything else keep running.
Paper only: nothing here can place a real order.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

from .clock import IST
from .paper import PaperBook

FILE = Path(__file__).resolve().parents[1] / "rules" / "paper_books.json"
_C: dict = {}


def _paper_exit() -> dict:
    """3.09: rules/paper_exit.json (the base exit of every paper trade)."""
    try:
        from .engine import paper_exit
        return paper_exit()
    except Exception:
        return {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, cfg=json.loads(FILE.read_text()), error=None)
    except Exception as e:
        _C.update(error=str(e), cfg=_C.get("cfg") or {"books": {}})
    return _C["cfg"]


def _unreal(t: dict, state) -> float:
    i = state.get(t["opt_key"]) if state is not None else None
    px = i.ltp if i is not None and getattr(i, "ltp", None) else (t.get("last") or t["entry"])
    return (t.get("dir") or 1) * (px - t["entry"]) * t["lot"] * t["lots"]


class Books:
    def __init__(self, source: str, store, main_cfg: dict, user_books: dict | None = None, log=print, on_open=None,
                 risk_for=None, data_dir=None):
        self.source, self.store, self.log = source, store, log
        self.data_dir = Path(data_dir) if data_dir else None      # 2.58: where data/multiverse.json lives
        self._mv: dict = {}
        self.on_open, self.risk_for = on_open, risk_for
        self.books: dict[str, PaperBook] = {}
        self.specs: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.today: dict[str, dict] = {}
        self._pack_seen: dict = {}                                # 2.69: strategy-file signals taken today
        self.rank_ok = False          # 3.08: the engine flushes ranked signals after each scan (Pro v2); else at once
        self.pending: dict = {}       # 3.08: account -> [(rank, order, signal)] waiting for this bar's flush
        self.load(main_cfg, user_books)

    # ------------------------------------------------------------------ loading (each account on its own)
    def load(self, main_cfg: dict, user_books: dict | None = None):
        books, specs = {}, {}
        ret = retired(self.data_dir).get("retired") or {} if self.data_dir else {}
        self.retired = {}
        for bid, sp in (cfg().get("books") or {}).items():
            try:
                if not sp.get("on", True):
                    continue
                if bid in ret:                            # 3.05: switched off by the scorecard (its record is kept)
                    self.retired[bid] = ret[bid]
                    continue
                mine = (user_books or {}).get(bid) or {}
                cap = float(mine.get("capital") or sp.get("capital") or main_cfg.get("capital") or 500000)
                pc = {**main_cfg, **(sp.get("paper") or {}), "capital": cap, "aggressive": False, "kinds": "all",
                      "min_severity": int((sp.get("pick") or {}).get("min_severity") or 1)}
                if (sp.get("pick") or {}).get("same_as_main"):    # 2.56: the main account's alerts, only the exit differs
                    pc.update(kinds=main_cfg.get("kinds") or "all", min_severity=int(main_cfg.get("min_severity") or 2))
                if (sp.get("pick") or {}).get("multiverse") or (sp.get("pick") or {}).get("pack_prefix") \
                        or (sp.get("pick") or {}).get("rider") or (sp.get("pick") or {}).get("early"):
                    pc.update(kinds="all", min_severity=1)        # 2.58 / 2.69 / 2.85 / 3.08: the picks / file / setup decide
                if (sp.get("pick") or {}).get("mirror_main"):     # 2.56.2: the main account's own trades decide; its
                    pc.update(kinds="all", min_severity=1, max_open=99, max_trades_day=99,    # limits already applied
                              max_daily_loss_pct=100, cooloff_min=0)
                if sp.get("exit_style") == "premium":            # 3.09: the old premium % exit, kept on one account to compare
                    pc["exit_style"] = "premium"
                elif sp.get("exit_style"):
                    from lab.exits import STYLES
                    from lab.rider_exit import STYLE as RIDER
                    from lab.chart_exit import STYLE as CHART, RUNNERS
                    if sp["exit_style"] not in STYLES and sp["exit_style"] not in (RIDER, CHART):
                        raise ValueError(f"unknown exit_style {sp['exit_style']!r}")
                    if sp["exit_style"] == RIDER and not (sp.get("pick") or {}).get("rider"):
                        raise ValueError("exit_style 'rider' needs pick.rider (Trend Rider signals carry its stop)")
                    if sp["exit_style"] == CHART:             # 3.08: Pro v2's chart exit needs a setup's stop and level
                        if not ((sp.get("pick") or {}).get("rider") or (sp.get("pick") or {}).get("early")):
                            raise ValueError("exit_style 'chart' needs pick.rider or pick.early")
                        if (sp.get("exit") or {}).get("runner", "bar5") not in RUNNERS:
                            raise ValueError(f"unknown runner {(sp.get('exit') or {}).get('runner')!r}")
                old = self.books.get(bid)
                if old is not None:                       # keep today's skip counts across a settings reload
                    old.cfg = pc
                    books[bid] = old
                else:
                    books[bid] = PaperBook(self.store, f"{self.source}#{bid}", pc, self.log, on_open=self.on_open)
                specs[bid] = sp
                self.errors.pop(bid, None)
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} not loaded: {e}")
        self.books, self.specs = books, specs

    def open_keys(self) -> set:
        out = set()
        for bk in self.books.values():
            out |= {t["opt_key"] for t in bk.open_trades()}
        return out

    # ------------------------------------------------------------------ the day's money
    def day_state(self, bid: str, state, now: dt.datetime) -> dict:
        bk, sp = self.books[bid], self.specs[bid]
        day = now.astimezone(IST).date().isoformat() if now.tzinfo else now.date().isoformat()
        trades = self.store.trades(source=bk.source, limit=5000)
        before = sum(t["net"] or 0 for t in trades if t["status"] == "closed" and t["day"] < day)
        booked = sum(t["net"] or 0 for t in trades if t["status"] == "closed" and t["day"] == day)
        opn = sum(_unreal(t, state) for t in trades if t["status"] == "open")
        cap = float(bk.cfg.get("capital") or 0)
        start = cap + before if sp.get("compound") else cap
        tp, sp_ = float(sp.get("day_target_pct") or 0), float(sp.get("day_stop_pct") or 0)
        return {"day": day, "start": round(start, 2), "booked": round(booked, 2), "open": round(opn, 2),
                "now": round(booked + opn, 2), "target": round(start * tp / 100, 2) if tp else None,
                "stop": round(start * sp_ / 100, 2) if sp_ else None, "target_pct": tp or None, "stop_pct": sp_ or None,
                "pct": round((booked + opn) / start * 100, 2) if start else None}

    # ------------------------------------------------------------------ which alerts an account takes
    def pick(self, bid: str, a: dict, ds: dict | None = None) -> str | None:
        """None = take it (subject to the risk check and the account's own rules), else why not."""
        p = self.specs[bid].get("pick") or {}
        d = a.get("data") or {}
        if a.get("direction") not in ("up", "down") or not a.get("option"):
            return "no option idea"
        if d.get("muted"):
            return "demoted by its live record"
        if p.get("situation"):                          # 2.73: Lab-rule signals the situation picker says fit today
            if a.get("kind") != "factory":
                return "not a Strategy Lab rule signal"
            v = d.get("situation") or {}
            if v.get("take") is None:
                return "the situation picker has not learned this rule yet"
            if not v.get("take"):
                return "the situation picker skips it today"
        if p.get("strict"):                             # 2.77: only alert types that passed the strict test (live/strict.py)
            st = d.get("strict") or {}
            if not st:
                return "its alert type has not passed the strict test"
            if not st.get("buy"):
                return "passed the strict test only in a style this account cannot trade (" + ", ".join(st.get("styles") or []) + ")"
        if p.get("focus") and not d.get("focus"):
            return "not a Focus alert"
        if a.get("severity", 0) < int(p.get("min_severity") or 1):
            return f"strength below {p.get('min_severity')}"
        if (d.get("score") or 0) < float(p.get("min_score") or 0):
            return f"score below {p.get('min_score')}"
        rec = p.get("record")
        if rec and a.get("kind") not in (p.get("proven_kinds") or []):
            st = d.get("track") or {}
            if (st.get("decided") or 0) < int(rec.get("min_decided") or 0):
                return "alert type not graded enough yet"
            if (st.get("hit_rate") or 0) < float(rec.get("min_hit_rate") or 0):
                return "alert type's live record too weak"
            if st.get("avg_o60") is None or st["avg_o60"] < float(rec.get("min_avg_o60") or 0):
                return "alert type loses on average"
        if ds and ds.get("target") is not None and ds["now"] >= ds["target"]:
            return "day target reached"
        if ds and ds.get("stop") is not None and ds["now"] <= -ds["stop"]:
            return "day stop reached"
        return None

    def consider(self, a: dict, state, now: dt.datetime, main: dict | None = None) -> dict:
        """Every account looks at a new alert. Returns {account: "taken" | reason}.
        main: the trade the main account just opened for this alert ({"lots": n}), None if it took none (2.56.2)."""
        out = {}
        if a.get("direction") not in ("up", "down") or not a.get("option"):
            return out
        for bid, bk in list(self.books.items()):
            try:
                sp = self.specs[bid]
                if (sp.get("pick") or {}).get("multiverse") or (sp.get("pick") or {}).get("pack_prefix") \
                        or (sp.get("pick") or {}).get("rider") or (sp.get("pick") or {}).get("early"):
                    continue                                  # 2.58 / 2.69 / 2.85 / 3.08: their own signals, not alerts
                mirror = bool((sp.get("pick") or {}).get("mirror_main"))
                ds = self.day_state(bid, state, now) if (sp.get("day_target_pct") or sp.get("day_stop_pct")
                                                          or sp.get("compound")) else None
                why = ("the main account did not take it" if not main else None) if mirror else self.pick(bid, a, ds)
                if why is None:
                    risk = self.risk_for(a, now, book=bk, capital=ds["start"] if ds else None) if self.risk_for and not mirror \
                        else ((a.get("data") or {}).get("risk") or {})
                    if mirror:                                # 2.56.2: exactly the main account's trade, same lots
                        risk = {**risk, "verdict": "PASS", "lots": int(main.get("lots") or 1)}
                    if (sp.get("pick") or {}).get("risk_pass") and risk.get("verdict") != "PASS":
                        why = "risk check: " + "; ".join((risk.get("reasons") or ["not PASS"])[:1])
                    elif sp.get("exit_style") and sp["exit_style"] != "premium" \
                            and not ((a.get("data") or {}).get("plan_R") or {}).get(sp["exit_style"]):
                        why = "no chart risk for this alert (trailing exit needs 5-minute bars)"
                    elif sp.get("split") and sp.get("exit_style"):   # 3.05: part booked with the main exit, the rest trailed
                        why = self._split_open(bk, sp, a, risk, state, now)
                    else:
                        a2 = {**a, "data": {**(a.get("data") or {}), "risk": risk}}
                        if sp.get("exit_style") and sp["exit_style"] != "premium":   # 2.56: its own exit, every alert
                            st = sp["exit_style"]
                            a2["data"]["factory"] = {"plan": {"style": st, "R": float(a["data"]["plan_R"][st])}}
                        before = dict(bk.skipped)
                        tid = bk.try_open(a2, state, now)
                        new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
                        why = None if tid else (new[0] if new else "account rules (open trades, trades a day, time)")
                self._count(bid, now, why)
                out[bid] = "taken" if why is None else why
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} skipped an alert: {e}")
        return out

    # ------------------------------------------------------------------ 2.58: the "Multiverse pick" account
    def has_multiverse(self) -> bool:
        return any((sp.get("pick") or {}).get("multiverse") for sp in self.specs.values())

    def mv_picks(self) -> list:
        """The multiverse's live picks (data/multiverse.json), re-read when the file changes."""
        if not self.data_dir:
            return []
        f = self.data_dir / "multiverse.json"
        try:
            mt = f.stat().st_mtime
        except OSError:
            return []
        if self._mv.get("mt") != mt:
            try:
                self._mv = {"mt": mt, "picks": json.loads(f.read_text()).get("live_picks") or []}
            except Exception as e:
                self._mv = {"mt": mt, "picks": [], "error": str(e)}
        return self._mv.get("picks") or []

    def offer_multiverse(self, a: dict, state, now: dt.datetime, facts: dict, suggest) -> dict:
        """A test-bench signal: each multiverse account takes it when it matches one of the live picks, with that pick's
        strike and exit (1 lot, its own account rules). suggest(strike) -> the option to buy. Paper only."""
        from lab.multiverse import matches
        out = {}
        picks = None
        for bid, bk in list(self.books.items()):
            sp = self.specs[bid]
            if not (sp.get("pick") or {}).get("multiverse"):
                continue
            try:
                picks = self.mv_picks() if picks is None else picks
                kinds = facts.get("_kinds") or [a.get("kind")]
                p = next((x for x in picks if matches(x.get("filter") or {}, kinds, facts)), None)
                if p is None:
                    why = "no live pick matches" if picks else "no live pick yet (multiverse not proven)"
                else:
                    opt = suggest(p.get("strike") or "atm")
                    if not opt:
                        why = "no live price for the pick's strike"
                    else:
                        kind, t, b, st, h = p["exit"]
                        ex = [t if kind == "tsh" else None, st if st is not None else 95, h if h is not None else 400,
                              t if kind == "trail" else None, b if kind == "trail" else None]
                        d = dict(a.get("data") or {})
                        d.update(risk={"verdict": "PASS", "lots": 1}, factory={"exit": ex}, multiverse=p.get("words"))
                        a2 = {**a, "option": opt, "severity": max(1, int(a.get("severity") or 1)), "data": d,
                              "title": f"Multiverse pick: {p.get('words', '')}"[:200]}
                        before = dict(bk.skipped)
                        tid = bk.try_open(a2, state, now)
                        new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
                        why = None if tid else (new[0] if new else "account rules (open trades, trades a day, time)")
                self._count(bid, now, why)
                out[bid] = "taken" if why is None else why
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} skipped a signal: {e}")
        return out

    # ------------------------------------------------------------------ 2.69: strategy-file accounts (e.g. Ghanshyam)
    def has_pack(self) -> bool:
        return any((sp.get("pick") or {}).get("pack_prefix") for sp in self.specs.values())

    def offer_pack(self, a: dict, state, now: dt.datetime, r: dict) -> dict:
        """A strategy-file signal (r: the live rule with _pack, _strategy, _variant): each account whose pack_prefix
        matches the file takes it on its symbols, 1 lot, once per strategy, symbol and day. Paper only."""
        out = {}
        pid, strat = str(r.get("_pack") or ""), str(r.get("_strategy") or "")
        day = now.date().isoformat()
        if self._pack_seen.get("day") != day:
            self._pack_seen = {"day": day, "keys": set()}
        for bid, bk in list(self.books.items()):
            p = self.specs[bid].get("pick") or {}
            pre = p.get("pack_prefix")
            if not pre or not pid.startswith(str(pre)):
                continue
            if p.get("syms") and a["sym"] not in p["syms"]:
                continue                                   # the account never looks at other symbols
            try:
                key = (bid, strat, a["sym"])
                if key in self._pack_seen["keys"]:
                    why = "this strategy already traded this symbol today"
                elif not a.get("option"):
                    why = "no option price for this signal"
                else:
                    d = dict(a.get("data") or {})
                    d.update(risk={"verdict": "PASS", "lots": 1},
                             pack={"pack": pid, "strategy": strat, "variant": r.get("_variant")})
                    a2 = {**a, "data": d, "title": strat[:200]}
                    before = dict(bk.skipped)
                    tid = bk.try_open(a2, state, now)
                    new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
                    why = None if tid else (new[0] if new else "account rules (open trades, trades a day, time)")
                    if tid:
                        self._pack_seen["keys"].add(key)
                self._count(bid, now, why)
                out[bid] = "taken" if why is None else why
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} skipped a strategy-file signal: {e}")
        return out

    # ------------------------------------------------------------------ 2.85: the "Trend Rider" account
    def has_rider(self) -> bool:
        return any((sp.get("pick") or {}).get("rider") for sp in self.specs.values())

    def offer_rider(self, a: dict, state, now: dt.datetime) -> dict:
        """A Trend Rider signal (live/rider.py, on trial): each account with pick.rider buys its option, 1 lot, with the
        setup's own stop and the rider exit (lab/rider_exit.py). a["data"]["rider"] carries stop and ATR on the
        underlying. Paper only; the main account never sees it."""
        out = {}
        r = (a.get("data") or {}).get("rider") or {}
        for bid, bk in list(self.books.items()):
            p = self.specs[bid].get("pick") or {}
            if not p.get("rider"):
                continue
            if p.get("syms") and a["sym"] not in p["syms"]:
                continue
            try:
                if p.get("v2"):                              # 3.08: Pro v2: its own checks, ranking, lots and chart exit
                    from . import pro2 as _p2
                    why = _p2.check(p, a, now, self.store, bk.source, self.source)
                    if why is None and not a.get("option"):
                        why = "no option price for this signal"
                    elif why is None and (r.get("stop") is None or not r.get("atr")):
                        why = "no setup stop / ATR on the signal"
                    elif why is None and not a.get("und_key"):
                        why = "no live price for the underlying"
                    if why is None and self.rank_ok and p.get("rank", _p2.cfg().get("rank", True)):
                        q = self.pending.setdefault(bid, [])
                        q.append((_p2.rank(a), len(q), a))
                        out[bid] = "queued"
                        continue
                    if why is None:
                        why = self._open_chart(bid, bk, a, state, now, r)
                    self._count(bid, now, why)
                    out[bid] = "taken" if why is None else why
                    continue
                pro_why = None
                if p.get("pro"):                             # 3.01: Pro accounts: strong only, segment, expiry, cool-off
                    from . import pro as _pro
                    pro_why = _pro.block(p, a, now, self.store, bk.source)
                if pro_why:
                    why = pro_why
                elif not a.get("option"):
                    why = "no option price for this signal"
                elif r.get("stop") is None or not r.get("atr"):
                    why = "no setup stop / ATR on the signal"
                elif not a.get("und_key"):
                    why = "no live price for the underlying"
                else:
                    d = dict(a.get("data") or {})
                    ex_ = p.get("exit")
                    if ex_ is None and p.get("pro"):                 # 3.01: the profit lock of rules/pro.json
                        from . import pro as _pro
                        ex_ = _pro.cfg().get("exit") or None
                    d.update(risk={"verdict": "PASS", "lots": 1},
                             factory={"plan": {"style": "rider", "R": abs(float(a.get("price") or 0) - float(r["stop"])),
                                               "stop": float(r["stop"]), "atr": float(r["atr"]),
                                               **({"ema": float(r["ema10"])} if r.get("ema10") is not None else {}),
                                               **({"exit": dict(ex_)} if ex_ else {})}})
                    a2 = {**a, "data": d, "severity": max(1, int(a.get("severity") or 1)),
                          "title": (a.get("title") or "Trend Rider")[:200]}
                    before = dict(bk.skipped)
                    tid = bk.try_open(a2, state, now)
                    new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
                    why = None if tid else (new[0] if new else "account rules (open trades, trades a day, time)")
                self._count(bid, now, why)
                out[bid] = "taken" if why is None else why
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} skipped a Trend Rider signal: {e}")
        return out

    # ------------------------------------------------------------------ 3.08: Stockway Pro v2 (chart exit, ranking, lots)
    def has_chart(self) -> bool:
        return any(sp.get("exit_style") == "chart" for sp in self.specs.values())

    def flush_rider(self, state, now: dt.datetime) -> dict:
        """The signals of one bar that waited for the ranking: best first, each opened while the money lasts."""
        out = {}
        pend, self.pending = self.pending, {}
        for bid, q in pend.items():
            bk = self.books.get(bid)
            if bk is None:
                continue
            for _, _, a in sorted(q, key=lambda x: (-x[0], x[1])):
                try:
                    why = self._open_chart(bid, bk, a, state, now, (a.get("data") or {}).get("rider") or {})
                except Exception as e:
                    why = f"error: {e}"
                    self.log(f"Paper account {bid} skipped a ranked signal: {e}")
                self._count(bid, now, why)
                out.setdefault(bid, []).append((a.get("sym"), "taken" if why is None else why))
        return out

    def _open_chart(self, bid: str, bk, a: dict, state, now: dt.datetime, r: dict, want: int | None = None) -> str | None:
        """Open a Pro v2 trade with the lots the free money covers (want, else 1). Chart exit: one trade whose lots all
        ride (split_at_t1 true: two parts, one sold at T1); exit_style "rider" (Trend Rider's exit, v1's): the lots on
        one trade. None when opened, else why not."""
        sp = self.specs[bid]
        from . import pro2 as _p2
        want = int(want or (sp.get("lots") or {}).get("want") or (_p2.cfg().get("lots") or {}).get("want") or 1)
        opt = a.get("option") or {}
        inst = state.get(opt.get("key")) if state is not None and opt.get("key") else None
        px = (inst.ask or inst.ltp) if inst is not None and getattr(inst, "ltp", None) else None
        if px and want >= 2:
            per = bk.need(float(px), int(opt.get("lot") or 1), 1, None)
            if per <= 0 or bk.free_cash(state) < 2 * per:
                want = 1
        if sp.get("exit_style") == "rider":               # Trend Rider's own exit (v1's), the lots on one trade
            plan = {"style": "rider", "R": abs(float(a.get("price") or 0) - float(r["stop"])), "stop": float(r["stop"]),
                    "atr": float(r["atr"]), **({"ema": float(r["ema10"])} if r.get("ema10") is not None else {}),
                    **({"exit": dict(sp["exit"])} if sp.get("exit") else {})}
            d = dict(a.get("data") or {})
            d.update(risk={"verdict": "PASS", "lots": max(1, want)}, factory={"plan": plan})
            before = dict(bk.skipped)
            tid = bk.try_open({**a, "data": d, "severity": max(1, int(a.get("severity") or 1)),
                               "title": (a.get("title") or "Pro v2")[:200]}, state, now)
            new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
            return None if tid else (new[0] if new else "account rules (open trades, time, money)")
        plan = {"style": "chart", "stop": float(r["stop"]), "atr": float(r.get("atr") or 0), "level": r.get("level"),
                "named": a.get("chart_named") or [], "exit": dict(sp.get("exit") or {}),
                **({"ema": float(r["ema10"])} if r.get("ema10") is not None else {})}
        before = dict(bk.skipped)
        from lab.chart_exit import cfg as _ccfg
        split = bool(_ccfg(plan["exit"]).get("split_at_t1", False))
        if want >= 2 and split:                           # two parts: one sold at T1, one rides
            legs = [("t1", "t1", 1), ("run", "run", 1)]
        else:                                             # 3.08 replay: selling a part at T1 added ~nothing; all ride
            legs = [(None, "solo", max(1, want))]
        opened = 0
        for leg, role, n in legs:
            d = dict(a.get("data") or {})
            d.update(risk={"verdict": "PASS", "lots": n}, factory={"plan": {**plan, "role": role}})
            a2 = {**a, "data": d, "severity": max(1, int(a.get("severity") or 1)), "title": (a.get("title") or "Pro v2")[:200]}
            if bk.try_open(a2, state, now, leg=leg):
                opened += 1
            elif leg == "t1":
                break                                     # the first part did not open: no second part
        new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
        return None if opened else (new[0] if new else "account rules (open trades, time, money)")

    # ------------------------------------------------------------------ 3.08: the 09:20-09:30 early-breakout account
    def has_early(self) -> bool:
        return any((sp.get("pick") or {}).get("early") for sp in self.specs.values())

    def offer_early(self, a: dict, state, now: dt.datetime) -> dict:
        """An early-breakout signal (live/early.py, on trial): accounts with pick.early buy it, 1 lot, chart exit (the
        account's exit settings: stop on 1- or 5-minute closes). pick.trigger_tf: which trigger the account takes."""
        out = {}
        e_ = (a.get("data") or {}).get("early") or {}
        for bid, bk in list(self.books.items()):
            p = self.specs[bid].get("pick") or {}
            if not p.get("early"):
                continue
            try:
                seg = "index" if a.get("is_index") else "stock"
                if p.get("segment") and p["segment"] != seg:
                    why = f"a{'n index' if seg == 'index' else ' stock'} (this account buys {p['segment']})"
                elif str(p.get("trigger_tf") or "1m") != str(e_.get("tf") or "1m"):
                    why = f"a {e_.get('tf')} trigger (this account takes {p.get('trigger_tf') or '1m'})"
                elif e_.get("label") != "strong":
                    why = f"early score {e_.get('score')}: not strong"
                elif not a.get("option"):
                    why = "no option price for this signal"
                elif not a.get("und_key"):
                    why = "no live price for the underlying"
                else:
                    r = {"stop": e_.get("stop"), "atr": e_.get("atr"), "level": e_.get("level")}
                    why = self._open_chart(bid, bk, a, state, now, r, want=int((self.specs[bid].get("lots") or {}).get("want") or 1))
                self._count(bid, now, why)
                out[bid] = "taken" if why is None else why
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} skipped an early-breakout signal: {e}")
        return out

    @staticmethod
    def split_lots(lots: int, sp: dict) -> list:
        """3.05: [(leg, lots)] of a split account: book_frac of the lots (rounded down, at least 1 when there are 2+) with
        the main exit, the rest with the account's trailing exit; one lot goes to one_lot ("trail" or "book")."""
        lots = max(1, int(lots or 1))
        s = sp.get("split") or {}
        if lots == 1:
            return [(str(s.get("one_lot") or "trail"), 1)]
        book = max(1, int(lots * float(s.get("book_frac", 0.5))))
        book = min(book, lots - 1)
        return [("book", book), ("trail", lots - book)]

    def _split_open(self, bk, sp: dict, a: dict, risk: dict, state, now) -> str | None:
        """Open the legs of a split trade; None when at least one leg opened, else why not."""
        st = sp["exit_style"]
        before = dict(bk.skipped)
        opened = 0
        for leg, n in self.split_lots(int(risk.get("lots") or 1), sp):
            d = {**(a.get("data") or {}), "risk": {**risk, "verdict": "PASS", "lots": n}}
            if leg == "trail":
                d["factory"] = {"plan": {"style": st, "R": float(a["data"]["plan_R"][st])}}
            else:                                     # 3.09: the booked part is sold at the next support / resistance
                ch = (a.get("data") or {}).get("chart") or {}            # (T1) with a swing stop, not at +30% of premium
                bl = _paper_exit().get("book_leg") or {}
                if ch.get("stop") is not None and ch.get("atr") and bl.get("style", "chart") == "chart":
                    d["factory"] = {"plan": {"style": "chart", "stop": float(ch["stop"]), "atr": float(ch["atr"]), "level": None,
                                             "named": ch.get("named") or [], "role": "t1", "exit": dict(bl.get("exit") or {})}}
            # without chart levels the booked part takes the account's base exit (rules/paper_exit.json)
            if bk.try_open({**a, "data": d}, state, now, leg=leg):
                opened += 1
        new = [k for k, v in bk.skipped.items() if v > before.get(k, 0)]
        return None if opened else (new[0] if new else "account rules (open trades, trades a day, time)")

    def _count(self, bid, now, why):
        day = now.date().isoformat()
        t = self.today.get(bid)
        if not t or t["day"] != day:
            t = self.today[bid] = {"day": day, "seen": 0, "taken": 0, "why": Counter()}
        t["seen"] += 1
        if why is None:
            t["taken"] += 1
        else:
            t["why"][why] += 1

    # ------------------------------------------------------------------ every second: exits, day target / stop
    def update_fast(self, state, now: dt.datetime):
        """2.71: every account's open trades checked against their exits on every fresh price (the day target / stop of
        an account stays with the main loop's update)."""
        for bid, bk in list(self.books.items()):
            try:
                bk.update(state, now, fast=True)
            except Exception as e:
                self.log(f"{self.specs.get(bid, {}).get('name', bid)}: fast exit check skipped: {e}")

    def update(self, state, now: dt.datetime, force_close: bool = False):
        for bid, bk in list(self.books.items()):
            try:
                bk.update(state, now, force_close=force_close)
                sp = self.specs[bid]
                if force_close or not (sp.get("day_target_pct") or sp.get("day_stop_pct")) or not bk.open_trades():
                    continue
                ds = self.day_state(bid, state, now)
                why = "day target reached" if ds["target"] is not None and ds["now"] >= ds["target"] else \
                    "day stop reached" if ds["stop"] is not None and ds["now"] <= -ds["stop"] else None
                if why:
                    for t in bk.open_trades():
                        bk.close_manual(t["id"], state, now, reason=why)
                    self.log(f"Paper account {sp.get('name') or bid}: {why} ({ds['now']:+,.0f} on ₹{ds['start']:,.0f}); "
                             f"everything closed, no new trades today")
            except Exception as e:
                self.errors[bid] = str(e)
                self.log(f"Paper account {bid} update failed: {e}")

    # ------------------------------------------------------------------ views
    def summary(self, bid: str, state=None, now: dt.datetime | None = None) -> dict:
        bk, sp = self.books[bid], self.specs[bid]
        now = now or dt.datetime.now(IST)
        out = bk.summary(state)
        t = self.today.get(bid) or {}
        out.update(book=bid, name=sp.get("name") or bid, short=sp.get("short"), explain=sp.get("explain"),
                   pick=sp.get("pick"), rules={k: bk.cfg.get(k) for k in ("max_open", "max_trades_day", "risk_pct",
                                                                         "max_daily_loss_pct", "target_pct", "stop_pct")},
                   compound=bool(sp.get("compound")), day=self.day_state(bid, state, now),
                   today_seen=t.get("seen", 0) if t.get("day") == now.date().isoformat() else 0,
                   today_taken=t.get("taken", 0) if t.get("day") == now.date().isoformat() else 0,
                   today_why=dict((t.get("why") or Counter()).most_common(6)) if t.get("day") == now.date().isoformat() else {},
                   error=self.errors.get(bid))
        return out

    def list(self) -> list:
        return [{"id": b, "name": s.get("name") or b, "short": s.get("short"), "capital": self.books[b].cfg.get("capital"),
                 "compound": bool(s.get("compound")), "error": self.errors.get(b)} for b, s in self.specs.items()] + \
               [{"id": b, "name": b, "error": e} for b, e in self.errors.items() if b not in self.specs]


def stats(trades: list, capital: float, target_pct: float | None = None, compound: bool = False) -> dict:
    """One account's record: trades, win rate, money, worst drop, green days, days the day target was reached."""
    closed = sorted((t for t in trades if t["status"] == "closed"), key=lambda t: (t.get("exit_ts") or 0, t["id"]))
    n = len(closed)
    net = sum(t["net"] or 0 for t in closed)
    won = sum(1 for t in closed if (t["net"] or 0) > 0)
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in closed:
        eq += t["net"] or 0
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    days: dict = {}
    for t in closed:
        days[t["day"]] = days.get(t["day"], 0) + (t["net"] or 0)
    run, hit = float(capital), 0
    for d in sorted(days):
        if target_pct and days[d] >= run * target_pct / 100:
            hit += 1
        if compound:
            run += days[d]
    return {"trades": n, "won_pct": round(won / n * 100, 1) if n else None, "net": round(net, 2),
            "avg": round(net / n, 2) if n else None, "max_drop": round(dd, 2), "days": len(days),
            "green_days": sum(1 for v in days.values() if v > 0), "best_day": round(max(days.values()), 2) if days else None,
            "worst_day": round(min(days.values()), 2) if days else None, "target_days": hit if target_pct else None,
            "since": min(days) if days else None, "return_pct": round(net / capital * 100, 2) if capital else None,
            "open": sum(1 for t in trades if t["status"] == "open")}


# ------------------------------------------------------------------ 3.05: the scorecard against the main account
REVIEW = {"min_days": 20, "retire": True, "at": "16:10", "file": "accounts_review.json"}


def review_cfg() -> dict:
    return {**REVIEW, **(cfg().get("review") or {})}


def retired(data_dir) -> dict:
    """{"retired": {account: {"since", "why"}}, "kept": [accounts you turned back on]} (data/accounts_review.json)."""
    try:
        j = json.loads((Path(data_dir) / review_cfg()["file"]).read_text())
        return {"retired": dict(j.get("retired") or {}), "kept": list(j.get("kept") or []), "day": j.get("day")}
    except Exception:
        return {"retired": {}, "kept": [], "day": None}


def _save_review(data_dir, j: dict):
    f = Path(data_dir) / review_cfg()["file"]
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, indent=1))
    tmp.replace(f)


def scorecard(store, source: str, today: str | None = None, c: dict | None = None) -> list:
    """Every extra account against the main account on the same days (Mandeep, 24 Nov 2026: "okay sab karo"): from the
    account's first trade day, every day the app traded anything; the account's net, the main account's net on those
    days, days ahead / behind. After min_days such days: ahead or behind; behind AND losing money = retire (unless the
    account's review is "show": then only the verdict, the decision is yours)."""
    from .store import reading
    c = c or review_cfg()
    today = today or dt.datetime.now(IST).date().isoformat()
    with reading(store) as db:
        rows = db.execute("SELECT source, day, SUM(net), COUNT(*) FROM paper WHERE status='closed' AND day <= ? AND "
                          "(source=? OR source LIKE ?) GROUP BY source, day", (today, source, source + "#%")).fetchall()
    per: dict = {}
    days_all = set()
    for src, day, net, n in rows:
        per.setdefault(src, {})[day] = (float(net or 0), int(n))
        days_all.add(day)
    days_all = sorted(days_all)
    main = per.get(source, {})
    out = []
    for bid, sp in (cfg().get("books") or {}).items():
        if not sp.get("on", True):
            continue
        mine = per.get(f"{source}#{bid}", {})
        if not mine:
            out.append({"id": bid, "name": sp.get("name") or bid, "days": 0, "verdict": "no trades yet", "retire": False})
            continue
        start = min(mine)
        ds = [d for d in days_all if d >= start]
        net = sum(mine.get(d, (0.0, 0))[0] for d in ds)
        mnet = sum(main.get(d, (0.0, 0))[0] for d in ds)
        ahead = sum(1 for d in ds if mine.get(d, (0.0, 0))[0] > main.get(d, (0.0, 0))[0])
        behind = sum(1 for d in ds if mine.get(d, (0.0, 0))[0] < main.get(d, (0.0, 0))[0])
        full = len(ds) >= int(c["min_days"])
        verdict = ("ahead" if net > mnet else "behind" if net < mnet else "level") if full else "learning"
        mode = sp.get("review", "auto")
        out.append({"id": bid, "name": sp.get("name") or bid, "since": start, "days": len(ds), "need": int(c["min_days"]),
                    "trades": sum(mine.get(d, (0.0, 0))[1] for d in ds), "net": round(net), "main_net": round(mnet),
                    "diff": round(net - mnet), "ahead_days": ahead, "behind_days": behind, "verdict": verdict,
                    "mode": mode, "retire": bool(c.get("retire", True)) and mode == "auto" and verdict == "behind" and net < 0})
    return out


def apply_review(data_dir, rows: list, today: str, log=print) -> list:
    """Switch off the accounts the scorecard says to retire (not the ones you turned back on). Their trades stay."""
    j = retired(data_dir)
    new = []
    for r in rows:
        if r.get("retire") and r["id"] not in j["retired"] and r["id"] not in j["kept"]:
            j["retired"][r["id"]] = {"since": today, "why": f"{r['days']} market days: {r['net']:+,} against the main account's "
                                                           f"{r['main_net']:+,} on the same days ({r['ahead_days']} days ahead, "
                                                           f"{r['behind_days']} behind)"}
            new.append(r["id"])
    j["day"] = today
    _save_review(data_dir, j)
    for bid in new:
        log(f"Paper account {bid} switched off by the scorecard: {j['retired'][bid]['why']}. Its record is kept; turn it "
            f"back on from the Paper page.")
    return new


def keep_on(data_dir, bid: str) -> dict:
    """You turned a retired account back on: it is never switched off by the scorecard again."""
    j = retired(data_dir)
    j["retired"].pop(bid, None)
    if bid not in j["kept"]:
        j["kept"].append(bid)
    _save_review(data_dir, j)
    return j


def compare(store, source: str, main_cfg: dict, books: "Books | None", days: int | None = None) -> list:
    """Main account vs every extra account, on the same days (Mandeep: "after a month check the difference")."""
    rows = []

    def _since(tr):
        if not days:
            return tr
        cut = (dt.datetime.now(IST).date() - dt.timedelta(days=days)).isoformat()
        return [t for t in tr if t["day"] >= cut]
    main = _since(store.trades(source=source, limit=20000))
    rows.append({"id": "main", "name": "Main account", **stats(main, float(main_cfg.get("capital") or 500000))})
    for bid, sp in ((books.specs.items()) if books else []):
        bk = books.books[bid]
        tr = _since(store.trades(source=bk.source, limit=20000))
        rows.append({"id": bid, "name": sp.get("name") or bid,
                     **stats(tr, float(bk.cfg.get("capital") or 0), sp.get("day_target_pct"), bool(sp.get("compound")))})
    return rows
