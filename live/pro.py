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
- Account rules on top: no same-day-expiry buy after 11:00, a 15-minute cool-off after an exit on the same symbol and
  side, and a profit lock (+50% -> keep +25%) on top of the rider exit.
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
            "expiry_cutoff": "11:00", "cooloff_min": 15, "exit": {},
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
    cut = str(pick.get("expiry_cutoff") or c.get("expiry_cutoff") or "")
    exp = str((a.get("option") or {}).get("expiry") or "")[:10]
    if cut and exp == now.date().isoformat() and now.strftime("%H:%M") >= cut:
        return f"same-day expiry after {cut}"
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
        out.append({"factor": f, "n_with": len(g["with"]), "avg_with": aw, "win_with": _avg([1.0 if r > 0 else 0.0 for r in g["with"]]),
                    "n_against": len(g["against"]), "avg_against": aa, "n_none": len(g["none"]),
                    "edge": round(aw - aa, 3) if ok and aw is not None and aa is not None else None,
                    "verdict": ("lined up and worked" if ok and aw > aa else "did not help" if ok else "too few yet")})
    out.sort(key=lambda r: (r["edge"] is None, -(r["edge"] or 0)))
    return {"days": days, "since": since, "n": len(rows),
            "tiers": {k: {"n": len(v), "avg_r": _avg(v), "win": _avg([1.0 if r > 0 else 0.0 for r in v])} for k, v in tiers.items()},
            "factors": out}


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
    st["at"] = time.time()
    try:
        f.write_text(json.dumps(st, indent=1))
    except Exception:
        pass
    return st


# ------------------------------------------------------------------ the Pro page
def signals(store, source: str, day: str, seg: str | None = None) -> dict:
    """Today's Trend Rider signals by Pro tier (strong / medium / low count), for one segment."""
    from .store import reading
    with reading(store) as db:
        rows = db.execute("SELECT id, ts, sym, direction, result, r_exit, title, data FROM trial_alerts WHERE source=? "
                          "AND kind='trend_rider' AND day=? ORDER BY ts DESC", (source, day)).fetchall()
    out = {"strong": [], "medium": [], "low": 0}
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
        lab = pr.get("label") or "low"
        if lab == "low":
            out["low"] += 1
            continue
        rd = dd.get("rider") or {}
        out[lab].append({"id": i, "at": dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M"), "sym": sym, "dir": d,
                         "score": pr.get("score"), "groups": pr.get("groups"), "with": pr.get("with"),
                         "against": pr.get("against"), "why": pr.get("why"), "trigger": rd.get("trigger"),
                         "stop": rd.get("stop"), "result": res, "r": r})
    return out
