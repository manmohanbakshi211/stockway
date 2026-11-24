"""Stockway Pro v1 (3.01, Mandeep 24 Nov 2026): one option-buying setup where every input votes together (gun).

"Isme koi alag se ghanshyam ya pattern alag se nahi. Balki sab ek saath milkar kaam karein. Strong check ke sath. Or
baad mein ... after market note kare ki konsa check sahi mein align hoke kaam kar rha hai or konsa nahi ... Light
weight model. Back testing nahi chahiye new days par hi train ho." Option buying only (the Strategy Lab's live rules are
113 straddle sells and 2 weak buys).

How it works
- The setup is Trend Rider's (live/rider.py, 2.84-2.90): at each closed 5-minute bar, every index and F&O stock; when
  its setup passes, every factor has already voted with / against / no view (breakout, trend day, trend, momentum,
  structure / lines, VWAP, flow / OI, option, the EMA videos, chase, move vs decay, tradability, strength vs Nifty,
  Ghanshyam's points, the other strategies, patterns and setups that fired in the last 15 minutes).
- Pro adds two votes: pro_mind (only the MindGame behaviours that beat a coin flip, taken_back turned round) and
  daytype (the 09:45 day-type forecast); then scores 0-100 with its own weights (rules/pro.json; forecast and the
  all-bullet MindGame lean at 0). Price-made votes count as one group: "strong" needs the score AND 3+ groups.
- Tiers: strong -> the Pro paper accounts (Index / Stocks) may buy it; medium -> the Pro page's watch list; low ->
  recorded only. Every signal keeps its votes (data.pro) so the after-market report can say which check lined up
  and worked.
- Account rules on top (3.04, Mandeep: no lock on good signals): no trades-a-day count, day loss stop or cool-off; after
  11:00 on a same-day expiry a strong signal is bought unless the move-vs-decay check votes against it; a profit lock
  (+50% -> keep +25%) on top of the rider exit. The cool-off code stays (cooloff_min 0 = off).
- Learning from new days only (learn): a factor's weight moves only with 100+ graded signals, by 25% at most, as a
  challenger; it is used only if it beats the current weights on the next 5 market days it never saw.
Paper and market data only: nothing here talks to a broker.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

from .clock import IST

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "pro.json"
STATE = "pro_weights.json"
INDEXES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
DEFAULTS = {"on": True, "strong": 85, "medium": 75, "min_groups": 3, "weights": {}, "groups": {},
            "mind": {"tfs": ["5m"], "use": {}, "invert": {}}, "daytype": {"trend_lift": 5, "quiet_lift": 10, "index_only": True},
            "expiry_cutoff": "11:00", "expiry_needs": "move_vs_decay", "cooloff_min": 0, "exit": {},
            "learn": {"min_n": 100, "forward_days": 5, "min_strong": 20, "max_step": 0.25, "days": 60, "at": "16:05"}}
_CFG: dict = {"m": None, "v": None}


def cfg() -> dict:
    try:
        m = RULES.stat().st_mtime
        if _CFG["m"] != m:
            _CFG.update(m=m, v={**DEFAULTS, **json.loads(RULES.read_text())})
        return _CFG["v"]
    except Exception:
        return dict(DEFAULTS)


def _state(data_dir) -> dict:
    try:
        return json.loads((Path(data_dir) / STATE).read_text())
    except Exception:
        return {}


def weights(data_dir=None) -> dict:
    """The weights in use: rules/pro.json, overlaid by the learned champion (data/pro_weights.json) when there is one."""
    w = dict(cfg().get("weights") or {})
    if data_dir is not None:
        ch = _state(data_dir).get("champion")
        if isinstance(ch, dict):
            w.update({k: float(v) for k, v in ch.items() if k in w})
    return w


def is_index(sym: str, a: dict | None = None) -> bool:
    if a is not None and a.get("is_index") is not None:
        return bool(a["is_index"])
    return str(sym).upper() in INDEXES


# ------------------------------------------------------------------ the two Pro votes
def mind_vote(e, sym: str, d: int, ts: float, c: dict | None = None):
    """Only the MindGame behaviours that beat a coin flip vote (their side vs the trade's); taken_back votes turned
    round. None = no view (MindGame not running, or none of them active)."""
    c = c or cfg()
    mind = getattr(e, "mind", None)
    if mind is None:
        return None, ""
    m = c.get("mind") or {}
    tfs = tuple(m.get("tfs") or ("5m",))
    reads, _ = mind.small(sym, tfs, ts)
    s, why = 0, []
    for tf in tfs:
        use, inv = set((m.get("use") or {}).get(tf) or []), set((m.get("invert") or {}).get(tf) or [])
        for it in (reads.get(tf) or {}).get("items") or []:
            side = int(it.get("side") or 0)
            if not side:
                continue
            if it.get("id") in use:
                s += side * d
                why.append(f"{tf} {it['id']} {'with' if side * d > 0 else 'against'}")
            elif it.get("id") in inv:
                s -= side * d
                why.append(f"{tf} {it['id']} (turned round) {'with' if -side * d > 0 else 'against'}")
    if not why:
        return None, ""
    return (1 if s > 0 else -1 if s < 0 else 0), "; ".join(why)


def daytype_vote(e, sym: str, day: str, c: dict | None = None):
    """The 09:45 day-type forecast for this index: a trend day likely -> with (a trend setup), a quiet day likely ->
    against, else no lean. None = no forecast (a stock, or before 09:45)."""
    c = c or cfg()
    dc = c.get("daytype") or {}
    if dc.get("index_only", True) and not is_index(sym):
        return None, ""
    rows = getattr(getattr(e, "charts", None), "_dt_rows", None) or []
    row = next((r for r in rows if r.get("day") == day and r.get("sym") == sym), None)
    if not row or not row.get("probs"):
        return None, ""
    pr, us = row["probs"], row.get("usual") or {}
    if pr.get("trend", 0) - us.get("trend", 0) >= float(dc.get("trend_lift", 5)):
        return 1, f"trend day likely {pr['trend']:.0f}% (usual {us.get('trend', 0):.0f}%)"
    if pr.get("quiet", 0) - us.get("quiet", 0) >= float(dc.get("quiet_lift", 10)):
        return -1, f"quiet day likely {pr['quiet']:.0f}% (usual {us.get('quiet', 0):.0f}%)"
    return 0, f"trend {pr.get('trend', 0):.0f}% (usual {us.get('trend', 0):.0f}%)"


# ------------------------------------------------------------------ the score
def votes_from(conf: dict) -> dict:
    v = {f: 0 for f in conf.get("no_view") or []}
    v.update({f: -1 for f in conf.get("against") or []})
    v.update({f: 1 for f in conf.get("with") or []})
    return v


def score(votes: dict, w: dict, c: dict | None = None) -> dict:
    """50 + 50 x (weighted votes with - against) / (weights of the factors with a view). Strong needs the score and
    min_groups different groups voting with (price-made votes are one group)."""
    c = c or cfg()
    view = {f: int(v) for f, v in votes.items() if v and float(w.get(f, 0) or 0) > 0}
    tot = sum(float(w[f]) for f in view)
    s = sum(float(w[f]) * v for f, v in view.items())
    sc = 50.0 + 50.0 * s / tot if tot else 50.0
    grp = {}
    for g, fs in (c.get("groups") or {}).items():
        for f in fs:
            grp[f] = g
    groups = sorted({grp.get(f, f) for f, v in view.items() if v > 0})
    label = "strong" if sc >= float(c.get("strong", 85)) and len(groups) >= int(c.get("min_groups", 3)) else \
        "medium" if sc >= float(c.get("medium", 75)) else "low"
    return {"score": round(sc, 1), "label": label, "groups": groups,
            "with": sorted(f for f, v in view.items() if v > 0), "against": sorted(f for f, v in view.items() if v < 0)}


def judge(e, sym: str, d: int, now: dt.datetime, conf: dict) -> dict:
    """Pro's reading of a Trend Rider signal (stored on the signal: data.pro)."""
    c = cfg()
    if not c.get("on", True) or not conf:
        return {}
    v = votes_from(conf)
    why = {}
    try:
        mv, mw = mind_vote(e, sym, d, now.timestamp(), c)
        if mv is not None:
            v["pro_mind"], why["pro_mind"] = mv, mw
    except Exception:
        pass
    try:
        dv, dw = daytype_vote(e, sym, now.date().isoformat(), c)
        if dv is not None:
            v["daytype"], why["daytype"] = dv, dw
    except Exception:
        pass
    u = (getattr(getattr(e, "uni", None), "underlyings", None) or {}).get(sym)
    idx = bool(getattr(u, "is_index", False)) if u is not None else is_index(sym)
    out = score(v, weights(getattr(e, "data_dir", None)), c)
    out.update(votes=v, why=why, seg="index" if idx else "stock", v="pro1")
    return out


# ------------------------------------------------------------------ the Pro paper accounts' rules
def gate(pick: dict, pr: dict) -> str | None:
    """3.07: an account's must-have (need_with) and must-not (not_with) checks on a signal's Pro reading; None = passes.
    Pro · Stocks strict (6 replayed days, 453 stock signals): relative strength with us AND order flow not already with
    us: +0.40R a signal, 60% won, 5 of 5 days up, against +0.18R / 54% / 3 of 5 for every strong one. When the flow is
    already with us the move has mostly happened (late entry): -0.01R, 48%."""
    w = set(pr.get("with") or [])
    for f in pick.get("need_with") or []:
        if f not in w:
            return f"needs {f.replace('_', ' ')} with it"
    for f in pick.get("not_with") or []:
        if f in w:
            return f"{f.replace('_', ' ')} already with it (late)"
    return None


def block(pick: dict, a: dict, now: dt.datetime, store, source: str, c: dict | None = None) -> str | None:
    """Why a Pro account does NOT buy this signal (None = it may). pick = the account's rules/paper_books.json pick."""
    c = c or cfg()
    pr = (a.get("data") or {}).get("pro") or {}
    if not pr:
        return "no Pro reading on this signal"
    idx = pr.get("seg") == "index" if pr.get("seg") else is_index(a.get("sym", ""), a)
    seg = pick.get("segment")
    if seg == "index" and not idx:
        return "a stock (this account buys indices)"
    if seg == "stock" and idx:
        return "an index (this account buys stocks)"
    if pr.get("label") != "strong":
        return f"Pro score {pr.get('score')} ({pr.get('label')}): not strong"
    g = gate(pick, pr)                                   # 3.07: the account's must-have / must-not checks
    if g:
        return g
    cut = str(pick.get("expiry_cutoff") or c.get("expiry_cutoff") or "")
    exp = str((a.get("option") or {}).get("expiry") or "")[:10]
    if cut and exp == now.date().isoformat() and now.strftime("%H:%M") >= cut:
        need = pick.get("expiry_needs", c.get("expiry_needs"))
        if not need:
            return f"same-day expiry after {cut}"
        if need in (pr.get("against") or []):            # 3.04: not a time lock; the decay check decides
            return f"same-day expiry after {cut} and the {need.replace('_', ' ')} check votes against (time decay beats the move)"
    cool = float(pick.get("cooloff_min") or c.get("cooloff_min") or 0)
    if cool > 0 and store is not None:
        want = "CE" if a.get("direction") == "up" else "PE"
        ts = now.timestamp()
        for t in store.trades(source=source, limit=50):
            if t.get("sym") == a.get("sym") and t.get("side") == want and t.get("status") == "closed" \
                    and t.get("exit_ts") and 0 <= ts - float(t["exit_ts"]) < cool * 60:
                return f"cool-off: {a.get('sym')} {want} closed {int((ts - float(t['exit_ts'])) // 60)} min ago"
    return None


# ------------------------------------------------------------------ after the close: which check lined up and worked
def _graded(store, source: str, since: str, until: str | None = None) -> list:
    from .store import reading
    q = ("SELECT day, sym, direction, result, r_exit, data FROM trial_alerts WHERE source=? AND kind='trend_rider' "
         "AND day>=? AND result IS NOT NULL" + (" AND day<=?" if until else "") + " ORDER BY ts")
    with reading(store) as db:
        rows = db.execute(q, (source, since) + ((until,) if until else ())).fetchall()
    out = []
    for day, sym, d, res, r, data in rows:
        try:
            pr = (json.loads(data or "{}") or {}).get("pro") or {}
        except Exception:
            pr = {}
        if pr.get("votes") and r is not None and math.isfinite(float(r)):
            out.append({"day": day, "sym": sym, "r": float(r), "result": res, "pro": pr})
    return out


def _avg(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def factors(store, source: str, days: int = 20, today: str | None = None) -> dict:
    """Per factor: the graded Trend Rider signals when it voted with, against and had no view (average R, wins); per
    tier (strong / medium / low). 'Lined up and worked' = with beats against on 5+ signals each."""
    today = today or dt.datetime.now(IST).date().isoformat()
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=int(days) * 7 // 5 + 2)).isoformat()
    rows = _graded(store, source, since, today)
    fac: dict = {}
    tiers: dict = {}
    for x in rows:
        t = tiers.setdefault(x["pro"].get("label") or "low", [])
        t.append(x["r"])
        for f, v in (x["pro"].get("votes") or {}).items():
            k = "with" if v > 0 else "against" if v < 0 else "none"
            fac.setdefault(f, {"with": [], "against": [], "none": []})[k].append(x["r"])
    out = []
    for f, g in fac.items():
        aw, aa = _avg(g["with"]), _avg(g["against"])
        ok = len(g["with"]) >= 5 and len(g["against"]) >= 5
        rest = g["against"] + g["none"]                  # 3.07: "with" against every signal it was not with
        an = _avg(rest)
        ok_rest = len(g["with"]) >= 5 and len(rest) >= 5
        edge_rest = round(aw - an, 3) if ok_rest and aw is not None and an is not None else None
        d_ = (aw - aa) if ok else edge_rest
        out.append({"factor": f, "n_with": len(g["with"]), "avg_with": aw, "win_with": _avg([1.0 if r > 0 else 0.0 for r in g["with"]]),
                    "n_against": len(g["against"]), "avg_against": aa, "n_none": len(g["none"]),
                    "n_not": len(rest), "avg_not": an, "edge_rest": edge_rest,
                    "edge": round(aw - aa, 3) if ok and aw is not None and aa is not None else None,
                    "verdict": ("too few yet" if d_ is None else "lined up and worked" if d_ > 0 else "did not help")})
    out.sort(key=lambda r: (r["edge"] is None and r["edge_rest"] is None, -(r["edge"] if r["edge"] is not None else r["edge_rest"] or 0)))
    return {"days": days, "since": since, "n": len(rows),
            "tiers": {k: {"n": len(v), "avg_r": _avg(v), "win": _avg([1.0 if r > 0 else 0.0 for r in v])} for k, v in tiers.items()},
            "factors": out}


# ------------------------------------------------------------------ 3.07: must-have / must-not checks, learned
def gate_stats(rows: list, seg: str | None = None) -> dict:
    """Per factor, for one segment: graded signals when it voted with vs when it did not (against or no view)."""
    out: dict = {}
    for x in rows:
        if seg and (x["pro"].get("seg") or ("index" if is_index(x["sym"]) else "stock")) != seg:
            continue
        for f, v in (x["pro"].get("votes") or {}).items():
            g = out.setdefault(f, {"with": [], "not": []})
            g["with" if v > 0 else "not"].append(x["r"])
    return {f: {"n_with": len(g["with"]), "avg_with": _avg(g["with"]), "n_not": len(g["not"]), "avg_not": _avg(g["not"])}
            for f, g in out.items()}


def suggest_gates(rows: list, lc: dict) -> dict:
    """{seg: {"need_with": [...], "not_with": [...]}}: a check whose "with" signals beat the rest by gate_min_edge R (both
    sides gate_min_n+) is a must-have; one whose "with" signals did that much worse is a must-not (the move had already
    happened). Proposed only: judged on the market days after, never used on their own."""
    n_min, e_min = int(lc.get("gate_min_n", 50)), float(lc.get("gate_min_edge", 0.25))
    out = {}
    for seg in ("index", "stock"):
        need, avoid = [], []
        for f, s in gate_stats(rows, seg).items():
            if s["n_with"] < n_min or s["n_not"] < n_min or s["avg_with"] is None or s["avg_not"] is None:
                continue
            d = s["avg_with"] - s["avg_not"]
            if d >= e_min:
                need.append(f)
            elif d <= -e_min:
                avoid.append(f)
        if need or avoid:
            out[seg] = {"need_with": sorted(need), "not_with": sorted(avoid)}
    return out


def _gates_check(rows: list, gates: dict, c: dict) -> dict:
    """Strong signals of each segment on the days checked: all of them vs the ones that pass the proposed checks."""
    out = {}
    for seg, g in gates.items():
        al = [x["r"] for x in rows if (x["pro"].get("seg") or ("index" if is_index(x["sym"]) else "stock")) == seg
              and x["pro"].get("label") == "strong"]
        ps = [x["r"] for x in rows if (x["pro"].get("seg") or ("index" if is_index(x["sym"]) else "stock")) == seg
              and x["pro"].get("label") == "strong" and gate(g, x["pro"]) is None]
        out[seg] = {"all": {"n": len(al), "avg_r": _avg(al)}, "pass": {"n": len(ps), "avg_r": _avg(ps)}}
    return out


def learn_gates(rows_all: list, st: dict, lc: dict, today: str, c: dict, log=print) -> dict:
    """Nightly, with the weights: an open gate proposal is judged on the days after it was made; a won one becomes the
    suggestion on the Pro page (adopting it in an account is Mandeep's call); with none open, a new one is proposed."""
    gc = st.get("gates_challenger")
    if isinstance(gc, dict) and gc.get("since"):
        rows = [x for x in rows_all if x["day"] > gc["since"]]
        days = sorted({x["day"] for x in rows})
        chk = _gates_check(rows, gc["gates"], c)
        st["gates_check"] = {"days": len(days), **chk}
        n_pass = sum(v["pass"]["n"] for v in chk.values())
        if len(days) >= int(lc.get("forward_days", 5)) and n_pass >= int(lc.get("min_strong", 20)):
            won = {seg: (v["pass"]["avg_r"] or -9) > (v["all"]["avg_r"] or -9) for seg, v in chk.items() if v["pass"]["n"]}
            st.setdefault("gates_history", []).append({"day": today, "since": gc["since"], "gates": gc["gates"], "won": won,
                                                       "days": len(days), "check": chk})
            ok = {seg: gc["gates"][seg] for seg, w in won.items() if w}
            if ok:
                st["gates_suggested"] = {"day": today, "gates": ok, "check": chk}
                log(f"Pro: checks that did better on {len(days)} new days, suggested on the Pro page: {ok}")
            st["gates_challenger"] = None
    if not st.get("gates_challenger"):
        since = (dt.date.fromisoformat(today) - dt.timedelta(days=int(lc.get("days", 60)) * 7 // 5 + 2)).isoformat()
        g = suggest_gates([x for x in rows_all if x["day"] >= since], lc)
        if g:
            st["gates_challenger"] = {"since": today, "gates": g, "stats": {seg: gate_stats(rows_all, seg) for seg in g}}
            log(f"Pro: must-have / must-not checks to judge on the next new days: {g}")
    return st


# ------------------------------------------------------------------ learning from new days only
def learn(store, source: str, data_dir, today: str | None = None, log=print) -> dict:
    """Nightly. 1) An open challenger is judged on the market days after it was made (never on the days it learned
    from): used only if its strong signals did better than the champion's on 5+ days and 20+ strong signals. 2) With
    no challenger open: each factor with 100+ graded signals gets a weight step (with-vs-against edge in R, capped at
    25%); that set becomes the challenger. Nothing trades on a challenger."""
    c = cfg()
    lc = c.get("learn") or {}
    today = today or dt.datetime.now(IST).date().isoformat()
    f = Path(data_dir) / STATE
    st = _state(data_dir)
    champ = weights(data_dir)
    ch = st.get("challenger")
    if isinstance(ch, dict) and st.get("since"):
        rows = [x for x in _graded(store, source, st["since"]) if x["day"] > st["since"]]
        days = sorted({x["day"] for x in rows})
        a = [x["r"] for x in rows if score(x["pro"]["votes"], champ, c)["label"] == "strong"]
        b = [x["r"] for x in rows if score(x["pro"]["votes"], {**champ, **ch}, c)["label"] == "strong"]
        st["check"] = {"days": len(days), "champion": {"n": len(a), "avg_r": _avg(a)}, "challenger": {"n": len(b), "avg_r": _avg(b)}}
        if len(days) >= int(lc.get("forward_days", 5)) and len(b) >= int(lc.get("min_strong", 20)):
            won = (_avg(b) or -9) > (_avg(a) or -9)
            st.setdefault("history", []).append({"day": today, "since": st["since"], "weights": ch, "won": won, **st["check"]})
            if won:
                st["champion"] = {**(st.get("champion") or {}), **ch}
                log(f"Pro: new weights in use (beat the old ones on {len(days)} new days: {_avg(b)}R vs {_avg(a)}R a strong signal)")
            else:
                log(f"Pro: proposed weights did not beat the old ones on {len(days)} new days ({_avg(b)}R vs {_avg(a)}R); dropped")
            st["challenger"], st["since"] = None, None
    if not st.get("challenger"):
        rep = factors(store, source, int(lc.get("days", 60)), today)
        step_max = float(lc.get("max_step", 0.25))
        new = {}
        for r in rep["factors"]:
            fname = r["factor"]
            if fname not in champ or float(champ.get(fname) or 0) <= 0:
                continue
            if r["n_with"] + r["n_against"] < int(lc.get("min_n", 100)) or r["edge"] is None:
                continue
            step = max(-step_max, min(step_max, float(r["edge"])))
            nw = round(float(champ[fname]) * (1 + step), 3)
            if abs(nw - float(champ[fname])) >= 0.01:
                new[fname] = nw
        if new:
            st["challenger"], st["since"] = new, today
            log(f"Pro: weights to test on the next new days: {new}")
    try:                                                 # 3.07: must-have / must-not checks (the score alone saturates)
        learn_gates(_graded(store, source, "2000-01-01", today), st, lc, today, c, log)
    except Exception as e:
        log(f"Pro: check suggestions skipped: {e}")
    st["at"] = time.time()
    try:
        f.write_text(json.dumps(st, indent=1))
    except Exception:
        pass
    return st


# ------------------------------------------------------------------ the Pro page
def signals(store, source: str, day: str, seg: str | None = None, pick: dict | None = None) -> dict:
    """One day's Trend Rider signals by Pro tier (strong / medium / low), for one segment. 3.02: the low ones are
    listed too (the Pro page's "Recorded" tab: everything Pro looked at that day). 3.07: pick = an account's
    must-have / must-not checks: a strong signal that fails them goes to the watch list with the reason."""
    from .store import reading
    with reading(store) as db:
        rows = db.execute("SELECT id, ts, sym, direction, result, r_exit, title, data FROM trial_alerts WHERE source=? "
                          "AND kind='trend_rider' AND day=? ORDER BY ts DESC", (source, day)).fetchall()
    out = {"strong": [], "medium": [], "low": []}
    for i, ts, sym, d, res, r, title, data in rows:
        try:
            dd = json.loads(data or "{}") or {}
        except Exception:
            dd = {}
        pr = dd.get("pro") or {}
        if not pr:
            continue
        s_ = pr.get("seg") or ("index" if is_index(sym) else "stock")
        if seg and s_ != seg:
            continue
        lab = pr.get("label") if pr.get("label") in ("strong", "medium") else "low"
        g = gate(pick, pr) if pick and lab == "strong" else None
        if g:
            lab = "medium"
        rd = dd.get("rider") or {}
        out[lab].append({"id": i, "at": dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M"), "sym": sym, "dir": d,
                         "score": pr.get("score"), "groups": pr.get("groups"), "with": pr.get("with"),
                         "against": pr.get("against"), "why": pr.get("why"), "trigger": rd.get("trigger"),
                         "stop": rd.get("stop"), "result": res, "r": r, **({"gate": g} if g else {})})
    out["low_n"] = len(out["low"])
    return out


def days(store, source: str, n: int = 20) -> list:
    """3.02: the latest days with Trend Rider signals (the Pro page's day list)."""
    from .store import reading
    with reading(store) as db:
        return [r[0] for r in db.execute("SELECT DISTINCT day FROM trial_alerts WHERE source=? AND kind='trend_rider' "
                                         "ORDER BY day DESC LIMIT ?", (source, int(n))).fetchall()]


def book_history(store, source: str, limit: int = 300) -> dict:
    """3.02: a Pro paper account's trades (closed and open), with its day-by-day result (the Pro page's Trades tab)."""
    rows = store.trades(source=source, limit=limit)
    closed = [t for t in rows if t.get("status") == "closed"]
    by: dict = {}
    for t in closed:
        d = by.setdefault(t.get("day"), {"day": t.get("day"), "n": 0, "net": 0.0, "won": 0})
        d["n"] += 1
        d["net"] += float(t.get("net") or 0)
        d["won"] += 1 if float(t.get("net") or 0) > 0 else 0
    keep = ("id", "day", "sym", "label", "side", "entry", "exit", "last", "peak", "net", "reason", "status", "entry_ts",
            "exit_ts", "opt_key", "qty", "dir")
    net = sum(float(t.get("net") or 0) for t in closed)
    return {"n": len(closed), "net": round(net), "won": sum(1 for t in closed if float(t.get("net") or 0) > 0),
            "open": [{k: t.get(k) for k in keep} for t in rows if t.get("status") == "open"],
            "days": [{**v, "net": round(v["net"])} for v in sorted(by.values(), key=lambda x: x["day"] or "", reverse=True)],
            "rows": [{k: t.get(k) for k in keep} for t in closed[:100]]}
