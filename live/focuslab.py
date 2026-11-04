"""Shadow Focus (gun, 2.37): other ways of choosing Focus, run in shadow next to the current Focus.

The current Focus (live/engine.py _emit) is the champion. Every challenger version in rules/focus_shadow.json
(bullets) judges the same alerts at the same moment, and its choice is saved with the alert (data.focus_ch: pick and
why). Nothing else reads it: Telegram, the paper account, the risk check and every screen still follow the current
Focus. compare() puts the champion and the challengers side by side on the same alerts: how many picks, how much of
them is one alert type, and what the test bench's honest 1-lot trades on those picks made after costs.

The honest record of an alert type (honest_book) comes from the test bench only (ask in, bid out, every charge), from
the days before the day being judged: never today's trades, never later ones. Days recorded before a version existed
are replayed with the same code and marked "replayed". Read-only: nothing here talks to a broker.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import Counter
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "focus_shadow.json"
RANKS = ("honest", "none")
DEFAULT_VERSION = {"label": None, "rank": "honest", "grade_style": "buy", "min_n": 15, "days": 30, "type_cap": 0.30,
                   "cap_floor": 2, "gap_min": 60, "good_min_sev": 2, "skip_bad": True}
BUILTIN = {"main": "honest_cap30", "versions": {"honest_cap30": {"label": "Honest grade + 30% type cap"}}}
INDEX_SYMS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
VERDICTS = {"good": "winning", "bad": "losing", "unclear": "mixed", "new": "too few trades yet"}

_C: dict = {}
_C_LOCK = threading.Lock()


def _validate(name: str, spec) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("not an object")
    v = {**DEFAULT_VERSION, **spec}
    if v["rank"] not in RANKS:
        raise ValueError(f"rank must be one of {', '.join(RANKS)}")
    v["type_cap"] = float(v["type_cap"])
    if not 0 < v["type_cap"] <= 1:
        raise ValueError("type_cap must be above 0 and at most 1")
    v["cap_floor"], v["min_n"], v["days"] = int(v["cap_floor"]), int(v["min_n"]), int(v["days"])
    v["gap_min"], v["good_min_sev"] = float(v["gap_min"]), int(v["good_min_sev"])
    if v["cap_floor"] < 0 or v["min_n"] < 2 or not 1 <= v["days"] <= 365 or v["gap_min"] < 0 or not 1 <= v["good_min_sev"] <= 3:
        raise ValueError("cap_floor 0+, min_n 2+, days 1-365, gap_min 0+, good_min_sev 1-3")
    v["skip_bad"] = bool(v["skip_bad"])
    v["grade_style"] = str(v.get("grade_style") or "buy")
    v["label"] = str(v.get("label") or name)
    v["name"] = name
    return v


def load() -> dict:
    """{"versions": {name: version}, "errors": [...], "main": name, "error": None | str}. Re-read when the file changes;
    a broken version is skipped (listed in errors), a broken file falls back to the built-in version."""
    with _C_LOCK:
        try:
            mt = FILE.stat().st_mtime
        except Exception:
            mt = None
        if _C.get("mt") == mt and "versions" in _C:
            return _C["out"]
        error, raw = None, BUILTIN
        try:
            raw = json.loads(FILE.read_text())
        except Exception as e:
            error = f"rules/focus_shadow.json unreadable ({e}): using the built-in version"
        versions, errors = {}, []
        for name, spec in (raw.get("versions") or {}).items():
            try:
                versions[str(name)] = _validate(str(name), spec)
            except Exception as e:
                errors.append(f"{name}: {e}")
        if not versions and not error:
            error = "no valid version in rules/focus_shadow.json"
        main = raw.get("main") if raw.get("main") in versions else next(iter(versions), None)
        since = str(raw.get("champion_since") or "") or None
        _C.update(mt=mt, versions=versions, out={"versions": versions, "errors": errors, "main": main, "error": error,
                                                 "champion_since": since})
        return _C["out"]


# ------------------------------------------------------------------ the honest record
_BOOK: dict = {}
_BOOK_LOCK = threading.Lock()


def honest_book(store, source: str, today: str, style: str = "buy", days: int = 30, min_n: int = 15) -> dict:
    """(kind, is_index) -> {"n", "avg", "se", "win_pct", "days", "verdict"} from the test bench's closed 1-lot trades of
    plain alerts (no Lab rule, idea on trial or mover version) in `style`, on the `days` days before `today` only.
    verdict: good = average minus its uncertainty above 0; bad = average plus its uncertainty below 0; unclear = in
    between; new = fewer than min_n trades. Kept in memory for the day (it only uses finished days)."""
    key = (str(getattr(store, "path", "")), source, today, style, int(days), int(min_n))
    with _BOOK_LOCK:
        if key in _BOOK:
            return _BOOK[key]
    from .bench import day_groups, db_for
    agg: dict = {}
    try:
        groups = day_groups(db_for(store), source, int(days), today)
    except Exception:
        groups = []
    for g in groups:
        if g["day"] >= today or g["style"] != style or (g.get("variant") or ""):
            continue
        a = agg.setdefault((g["kind"], int(g.get("is_index") or 0)), {"n": 0, "s": 0.0, "s2": 0.0, "w": 0, "days": set()})
        a["n"] += g["n"]
        a["s"] += g["s"] or 0
        a["s2"] += g["s2"] or 0
        a["w"] += g["wins"] or 0
        a["days"].add(g["day"])
    out = {}
    for k, a in agg.items():
        n = a["n"]
        avg = a["s"] / n
        sd = math.sqrt(max(0.0, (a["s2"] - n * avg * avg) / (n - 1))) if n > 1 else abs(avg)
        se = sd / math.sqrt(n)
        verdict = "new" if n < min_n else "good" if avg - se > 0 else "bad" if avg + se < 0 else "unclear"
        out[k] = {"n": n, "avg": round(avg), "se": round(se), "win_pct": round(a["w"] / n * 100), "days": len(a["days"]),
                  "verdict": verdict}
    with _BOOK_LOCK:
        if len(_BOOK) > 200:
            _BOOK.clear()
        _BOOK[key] = out
    return out


def _rs(x) -> str:
    x = round(float(x or 0))
    return f"{'−' if x < 0 else ''}₹{abs(x):,}"


def _label(kind: str) -> str:
    try:
        from . import registry
        return registry.label(kind)
    except Exception:
        return kind


# ------------------------------------------------------------------ the chooser
class FocusLab:
    """Judges each alert for every challenger version (decide), keeping each version's picks of the day (counts per
    alert type for the cap, last pick per symbol for the gap). Thread-safe; a failing version is skipped and logged."""

    def __init__(self, store, source: str, log=print):
        self.store, self.source, self.log = store, source, log
        self.lock = threading.Lock()
        self.day = None
        self.st: dict = {}
        self.errors: dict = {}
        self.n_judged = 0

    def _state(self, name: str, day: str) -> dict:
        if day != self.day:
            self.day, self.st = day, {}
        return self.st.setdefault(name, {"by": Counter(), "n": 0, "last": {}, "seen": set()})

    def _count(self, name, day, kind, sym, ts):
        s = self._state(name, day)
        s["by"][kind] += 1
        s["n"] += 1
        s["last"][sym] = ts

    def warm(self, day: str) -> int:
        """After a restart: rebuild today's picks from the alerts already saved (so the cap and the gap carry on)."""
        n = 0
        try:
            with self.store.lock:
                rows = self.store.db.execute(
                    "SELECT ts, sym, kind, data FROM alerts WHERE day=? AND source=? AND data LIKE '%\"focus_ch\"%' ORDER BY ts",
                    (day, self.source)).fetchall()
        except Exception:
            rows = []
        with self.lock:
            self._state("", day)
            for ts, sym, kind, data in rows:
                try:
                    fch = (json.loads(data or "{}") or {}).get("focus_ch") or {}
                except Exception:
                    continue
                for name, r in fch.items():
                    r = r or {}
                    if r.get("pick") or str(r.get("why") or "").startswith(("type cap", f"{sym} already picked")):
                        self._state(name, day)["seen"].add(kind)
                    if r.get("pick"):
                        self._count(name, day, kind, sym, ts)
                        n += 1
        return n

    def decide(self, a: dict, *, sev0: int, muted: bool, strong: bool, ts: float, day: str) -> dict | None:
        """{version: {"pick": bool, "why": str}} for a directional alert (None for anything else). sev0 = the severity
        before any scorecard demotion; muted / strong = the current Focus's own demotion and strength test."""
        if a.get("direction") not in ("up", "down"):
            return None
        cfg = load()
        out = {}
        with self.lock:
            self.n_judged += 1
            for name, v in cfg["versions"].items():
                try:
                    pick, why = self._judge(v, a, sev0, muted, strong, ts, day)
                except Exception as e:
                    if name not in self.errors:
                        self.log(f"Shadow Focus version {name} skipped: {e}")
                    self.errors[name] = str(e)
                    continue
                out[name] = {"pick": pick, "why": why}
                if pick:
                    self._count(name, day, a["kind"], a["sym"], ts)
        return out or None

    def _judge(self, v: dict, a: dict, sev0: int, muted: bool, strong: bool, ts: float, day: str) -> tuple[bool, str]:
        kind, sym = a["kind"], a["sym"]
        verdict, h = "new", None
        if v["rank"] == "honest":
            h = honest_book(self.store, self.source, day, v["grade_style"], v["days"], v["min_n"]).get((kind, int(bool(a.get("is_index")))))
            verdict = h["verdict"] if h else "new"
        rec = f"{_rs(h['avg'])} a trade × {h['n']}" if h else ""
        if verdict == "bad" and v["skip_bad"]:
            return False, f"honest record losing ({rec})"
        if verdict == "good":
            if int(sev0 or 0) < v["good_min_sev"]:
                return False, f"honest record winning ({rec}) but the alert is too weak"
            why = f"honest record winning ({rec})"
        else:
            if muted:
                return False, "demoted on the scorecard"
            if not strong:
                return False, "not strong enough (same bar as Focus)"
            why = "same bar as Focus" + (f"; honest record {VERDICTS[verdict]}{f' ({rec})' if h else ''}" if v["rank"] == "honest" else "")
        s = self._state(v["name"], day)
        s["seen"].add(kind)                     # alert types good enough today (the cap can always be met by them)
        last = s["last"].get(sym)
        if last is not None and ts - last < v["gap_min"] * 60:
            return False, f"{sym} already picked in the last {int(v['gap_min'])} min"
        if v["type_cap"] < 1:
            k = len(s["seen"])                  # with only 2-3 types today an even split is allowed; with one, just the floor
            limit = v["cap_floor"] if k < 2 else max(v["cap_floor"], math.ceil(max(v["type_cap"], 1 / k) * (s["n"] + 1) - 1e-9))
            if s["by"][kind] + 1 > limit:
                return False, f"type cap: {_label(kind)} already {s['by'][kind]} of {s['n']} picks today"
        return True, why

    def status(self) -> dict:
        cfg = load()
        with self.lock:
            st = {k: {"picks": s["n"], "top": [{"kind": kk, "n": nn} for kk, nn in s["by"].most_common(3)]}
                  for k, s in self.st.items() if k}
        return {"day": self.day, "judged": self.n_judged, "versions": list(cfg["versions"]), "main": cfg["main"],
                "errors": {**{e.split(":")[0]: e for e in cfg["errors"]}, **self.errors}, "today": st}


# ------------------------------------------------------------------ champion vs challengers
_FIELDS = ("SELECT id, ts, sym, kind, direction, severity, json_extract(data, '$.focus'), json_extract(data, '$.muted'), "
           "json_extract(data, '$.conviction'), json_extract(data, '$.focus_ch') FROM alerts WHERE day=? AND source=? "
           "AND direction IN ('up', 'down') ORDER BY ts, id")


def _alerts_of_day(store, source: str, day: str) -> list:
    """The day's directional alerts with only what the selectors need (SQLite pulls the few fields out of each alert's
    saved data; without SQLite's JSON functions the data is read in Python)."""
    with store.lock:
        try:
            raw = [(i, ts, sym, kind, di, sev, f, m, c, json.loads(ch) if ch else None)
                   for i, ts, sym, kind, di, sev, f, m, c, ch in store.db.execute(_FIELDS, (day, source)).fetchall()]
        except Exception:
            raw = []
            for i, ts, sym, kind, di, sev, data in store.db.execute(
                    "SELECT id, ts, sym, kind, direction, severity, data FROM alerts WHERE day=? AND source=? "
                    "AND direction IN ('up', 'down') ORDER BY ts, id", (day, source)).fetchall():
                try:
                    d = json.loads(data or "{}") or {}
                except Exception:
                    d = {}
                raw.append((i, ts, sym, kind, di, sev, d.get("focus"), d.get("muted"), d.get("conviction"), d.get("focus_ch")))
        try:
            ix = dict(store.db.execute("SELECT alert_id, is_index FROM outcomes WHERE day=? AND source=?", (day, source)).fetchall())
        except Exception:
            ix = {}
    return [{"id": i, "ts": ts, "sym": sym, "kind": kind, "direction": di, "severity": int(sev or 0),
             "focus": None if f is None else bool(f),
             "muted": bool(m), "conviction": c, "focus_ch": ch or None,
             "is_index": bool(ix[i]) if i in ix and ix[i] is not None else sym in INDEX_SYMS}
            for i, ts, sym, kind, di, sev, f, m, c, ch in raw]


def _replay(store, source: str, day: str, rows: list, versions: dict, lc: dict) -> dict:
    """What each version would have picked on a recorded day, with the same code and only what was known then (the
    stored severity, demotion and conviction; the honest record of earlier days)."""
    lab = FocusLab(store, source, log=lambda m: None)
    min_conv, min_sev = int(lc.get("focus_min_conviction") or 85), int(lc.get("focus_min_severity") or 3)
    out = {n: set() for n in versions}
    for r in rows:
        trusted = r["conviction"] is not None
        sev = r["severity"]
        strong = (float(r["conviction"]) >= min_conv and sev >= 2) if trusted else sev >= min_sev
        sev0 = min(3, sev + 1) if r["muted"] else sev
        a = {"kind": r["kind"], "sym": r["sym"], "direction": r["direction"], "is_index": r["is_index"]}
        with lab.lock:
            for name, v in versions.items():
                try:
                    pick, _ = lab._judge(v, a, sev0, r["muted"], strong, r["ts"], day)
                except Exception:
                    continue
                if pick:
                    lab._count(name, day, r["kind"], r["sym"], r["ts"])
                    out[name].add(r["id"])
    return out


_DAY: dict = {}
_DAY_LOCK = threading.Lock()


def day_sets(store, source: str, day: str, lc: dict | None = None, finished: bool = True) -> dict:
    """The champion's and every version's picks on one day: {"rows", "sets": {id: set(alert ids)}, "mode": {version:
    live | replayed}, "from_ts"}. When the day has live choices, only alerts from the first one on are compared (all
    selectors judge the same alerts). Finished days are kept in memory."""
    cfg = load()
    key = (str(getattr(store, "path", "")), source, day, cfg_key(cfg))
    if finished:
        with _DAY_LOCK:
            if key in _DAY:
                return _DAY[key]
    rows = _alerts_of_day(store, source, day)
    flagged = [r for r in rows if r["focus_ch"]]
    from_ts = flagged[0]["ts"] if flagged else None
    if from_ts is not None:
        rows = [r for r in rows if r["ts"] >= from_ts]
    sets = {"focus": {r["id"] for r in rows if r["focus"]}, "all": {r["id"] for r in rows}}
    has_focus = any(r["focus"] is not None for r in rows)       # False on days recorded before Focus existed (2.13)
    mode, todo = {}, {}
    for name, v in cfg["versions"].items():
        if any(name in (r["focus_ch"] or {}) for r in flagged):
            sets[name] = {r["id"] for r in rows if ((r["focus_ch"] or {}).get(name) or {}).get("pick")}
            mode[name] = "live"
        else:
            todo[name] = v
    if todo:
        sets.update(_replay(store, source, day, rows, todo, lc or {}))
        mode.update({n: "replayed" for n in todo})
    out = {"rows": rows, "sets": sets, "mode": mode, "from_ts": from_ts, "has_focus": has_focus}
    if finished:
        with _DAY_LOCK:
            if len(_DAY) > 400:
                _DAY.clear()
            _DAY[key] = out
    return out


def cfg_key(cfg: dict) -> str:
    return json.dumps({k: v for k, v in sorted(cfg["versions"].items())}, sort_keys=True, default=str)


def _grades(store, source: str, days: list, style: str) -> tuple[dict, dict]:
    """alert id -> honest bench net (1 lot, `style`), and alert id -> (old grade result, option % after 60 min)."""
    from .bench import db_for
    bench, outs = {}, {}
    if not days:
        return bench, outs
    q = ",".join("?" * len(days))
    try:
        con = db_for(store).reader()
        try:
            for aid, net in con.execute(f"SELECT alert_id, net FROM bench_trades WHERE source=? AND day IN ({q}) AND style=? "
                                        "AND IFNULL(variant, '')='' AND net IS NOT NULL AND alert_id IS NOT NULL",
                                        (source, *days, style)).fetchall():
                bench[aid] = float(net)
        finally:
            con.close()
    except Exception:
        pass
    try:
        with store.lock:
            for aid, res, o60, o0 in store.db.execute(f"SELECT alert_id, result, o60, o0 FROM outcomes WHERE source=? AND day IN ({q})",
                                                      (source, *days)).fetchall():
                outs[aid] = (res, o60 if o0 else None)
    except Exception:
        pass
    return bench, outs


def _stats(ids: set, kinds: dict, bench: dict, outs: dict) -> dict:
    by = Counter(kinds[i] for i in ids if i in kinds)
    n = len(ids)
    b = [bench[i] for i in ids if i in bench]
    bn = len(b)
    bs = sum(b)
    sd = math.sqrt(max(0.0, (sum(x * x for x in b) - bn * (bs / bn) ** 2) / (bn - 1))) if bn > 1 else (abs(bs) if bn else 0)
    res = [outs[i][0] for i in ids if i in outs and outs[i][0]]
    hits, misses = res.count("hit"), res.count("miss")
    o60 = [outs[i][1] for i in ids if i in outs and outs[i][1] is not None]
    top = [{"kind": k, "label": _label(k), "n": c, "pct": round(c / n * 100)} for k, c in by.most_common(5)]
    return {"n": n, "top": top, "top_pct": top[0]["pct"] if top else None, "types": len(by),
            "bench": {"n": bn, "net": round(bs), "avg": round(bs / bn) if bn else None,
                      "win_pct": round(sum(x > 0 for x in b) / bn * 100) if bn else None,
                      "rank": round(bs / bn - sd / math.sqrt(bn)) if bn >= 5 else None},
            "worked_pct": round(hits / (hits + misses) * 100) if hits + misses else None,
            "o60": round(sum(o60) / len(o60), 1) if o60 else None}


def compare(store, source: str, days: int = 10, today: str | None = None, lc: dict | None = None) -> dict:
    """Current Focus vs every challenger version vs all directional alerts, on the same alerts of the last `days` days."""
    import datetime as _dt
    cfg = load()
    today = today or _dt.date.today().isoformat()
    with store.lock:
        dl = [r[0] for r in store.db.execute("SELECT DISTINCT day FROM alerts WHERE source=? ORDER BY day DESC LIMIT ?",
                                             (source, max(1, min(int(days), 60)))).fetchall()]
    dl = sorted(dl)
    main = cfg["main"]
    style = (cfg["versions"].get(main) or DEFAULT_VERSION)["grade_style"]
    bench, outs = _grades(store, source, dl, style)
    order = ["focus", *cfg["versions"], "all"]
    labels = {"focus": "Current Focus", "all": "All directional alerts", **{n: v["label"] for n, v in cfg["versions"].items()}}
    tot = {k: set() for k in order}
    kinds, per_day = {}, []
    skipped = []
    for d in dl:
        ds = day_sets(store, source, d, lc, finished=d < today)
        if (ds["rows"] and not ds["has_focus"]) or (cfg.get("champion_since") and d < cfg["champion_since"]):
            skipped.append(d)                # the current Focus rules were not running that day: nothing to compare with
            continue
        for r in ds["rows"]:
            kinds[r["id"]] = r["kind"]
        row = {"day": d, "mode": ds["mode"], "from": ds["from_ts"], "n_alerts": len(ds["rows"]), "sel": {}}
        for k in order:
            ids = ds["sets"].get(k) or set()
            tot[k] |= ids
            st = _stats(ids, kinds, bench, outs)
            row["sel"][k] = {"n": st["n"], "top_pct": st["top_pct"], "top": st["top"][:1], "net": st["bench"]["net"],
                             "bn": st["bench"]["n"]}
        per_day.append(row)
    dl = [d for d in dl if d not in skipped]
    champ = tot["focus"]
    sel = []
    for k in order:
        st = _stats(tot[k], kinds, bench, outs)
        st.update(id=k, label=labels[k], champion=k == "focus", baseline=k == "all", main=k == main,
                  per_day=round(st["n"] / len(dl), 1) if dl else 0,
                  both=len(tot[k] & champ) if k not in ("focus", "all") else None,
                  only=len(tot[k] - champ) if k not in ("focus", "all") else None,
                  live_days=sum(1 for r in per_day if r["mode"].get(k) == "live"),
                  replayed_days=sum(1 for r in per_day if r["mode"].get(k) == "replayed"),
                  rule=_rule(cfg["versions"].get(k)) if k in cfg["versions"] else None)
        sel.append(st)
    book = honest_book(store, source, today, style, (cfg["versions"].get(main) or DEFAULT_VERSION)["days"],
                       (cfg["versions"].get(main) or DEFAULT_VERSION)["min_n"])
    verdicts = sorted(({"kind": k[0], "label": _label(k[0]), "is_index": bool(k[1]), **v} for k, v in book.items()),
                      key=lambda x: (-x["n"]))[:16]
    return {"days": dl, "skipped_days": skipped, "champion_since": cfg.get("champion_since"), "selectors": sel, "per_day": per_day, "main": main, "style": style, "verdicts": verdicts,
            "bench_trades": len(bench), "errors": cfg["errors"] + ([cfg["error"]] if cfg["error"] else []),
            "note": "Shadow only: the challengers are recorded next to every alert and change nothing you see, get on "
                    "Telegram or paper-trade. Honest ₹ = the test bench's 1-lot trade on the same alert (ask in, bid out, "
                    "every charge, the paper account's exits). The honest record that a challenger uses comes from earlier "
                    "days only. Days before a version existed are replayed with the same code. A challenger replaces the "
                    "current Focus only on your say-so, once it beats it on the same days with enough trades."}


def _rule(v: dict | None) -> str | None:
    if not v:
        return None
    parts = []
    if v["rank"] == "honest":
        parts.append(f"types winning on the test bench ({v['min_n']}+ trades, earlier days) get in from severity "
                     f"{v['good_min_sev']}; losing ones never; the rest use the current Focus bar")
    else:
        parts.append("the current Focus bar")
    if v["type_cap"] < 1:
        parts.append(f"no alert type over {round(v['type_cap'] * 100)}% of the day's picks")
    parts.append(f"one pick per symbol in {int(v['gap_min'])} min")
    return "; ".join(parts)


def picks(store, source: str, day: str, version: str, lc: dict | None = None, today: str | None = None) -> set:
    """Alert ids a version picked (live) or would have picked (replayed) on a day: the Alerts page's Shadow Focus view."""
    import datetime as _dt
    today = today or _dt.date.today().isoformat()
    ds = day_sets(store, source, day, lc, finished=day < today)
    return ds["sets"].get(version) or set()
