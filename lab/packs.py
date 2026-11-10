"""Strategy files: new strategies arrive as a file you upload, not as a new app version.

A strategy file (".json") holds strategies written in the Strategy Lab's rule format: direction, up to 4 conditions on
the facts the app already computes (lab/factory.py FEATURES), a time window, exits, symbols. It may also define its own
exits from a fixed vocabulary (lab/exits.py spec_check: stop, target, breakeven, trail, momentum exit, time limit).
Upload it on the Strategy Lab page. The app checks it, keeps it (every upload is kept; nothing is overwritten), and:

  - queues every strategy (one Lab rule per strategy × exit) for the next research run in the 1-year and the 5-year
    Lab: the same train / validation / holdout test as every other rule (factory.run calls queue() first);
  - runs each strategy live on the test bench (paper-tested with the bench styles, live/chartdata.py), kind "pack";
  - never puts them on live paper or in the alert feed by itself (factory.is_live / set_live): that is your call.

A strategy that needs a fact or exit the app doesn't have yet is listed as "can't test yet" with what it needs; the
rest of the file still loads. That missing piece is a gun piece (a new app version, once); after that, every file that
uses it is just an upload.

Safety: the file is data only. It is parsed as JSON, checked against a fixed vocabulary and size limits, and nothing in
it is ever run as code. There is no field for orders, brokers or accounts: strategy files can only produce alerts on
the test bench and Lab results. Market data only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path

FORMAT = 1
MAX_BYTES = 256_000
MAX_STRATEGIES = 100
MAX_RULES = 600
MAX_EXITS = 20
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")
TOP_KEYS = {"stockway_pack", "id", "name", "version", "made", "source", "about", "exits", "strategies", "cannot_test"}
STRAT_KEYS = {"name", "dir", "conds", "win", "exit", "syms", "max_day", "note", "then"}     # 2.47: then
_LOCK = threading.Lock()
_CACHE: dict = {}


def root(data_dir) -> Path:
    """data/packs for the 1-year Lab; the 5-year Lab (data/lab5y) uses the same files."""
    d = Path(data_dir)
    return (d.parent if d.name == "lab5y" else d) / "packs"


def _index_file(data_dir) -> Path:
    return root(data_dir) / "index.json"


def load_index(data_dir) -> dict:
    f = _index_file(data_dir)
    try:
        x = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        x = {}
    x.setdefault("packs", {})
    x.setdefault("exits", {})
    return x


def _save_index(data_dir, x: dict):
    f = _index_file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(x, indent=1, default=str))
    os.replace(tmp, f)


def _txt(v, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else ""))[:n].strip()


# ------------------------------------------------------------------ check (no side effects)
def check(raw: bytes) -> dict:
    """Read and check a strategy file. Returns {"fatal": str | None, "pack": {...}, "rules": [...], "exits": {key: spec},
    "problems": [...], "cannot_test": [...]}. Rules are factory.validate()d Lab rules, one per strategy × exit."""
    from . import exits as X
    from . import factory as F
    out = {"fatal": None, "pack": {}, "rules": [], "exits": {}, "problems": [], "cannot_test": [], "strategies": []}
    if len(raw) > MAX_BYTES:
        out["fatal"] = f"file too large ({len(raw) // 1000} KB; {MAX_BYTES // 1000} KB max)"
        return out
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        out["fatal"] = f"not a readable JSON file ({str(e)[:120]})"
        return out
    if not isinstance(d, dict) or d.get("stockway_pack") != FORMAT:
        out["fatal"] = 'not a Stockway strategy file (needs "stockway_pack": 1)'
        return out
    pid = str(d.get("id") or "")
    if not ID_RE.match(pid):
        out["fatal"] = "id must be 1-40 lowercase letters, digits, _ or -, starting with a letter or digit"
        return out
    try:
        ver = int(d.get("version") or 1)
        if not 1 <= ver <= 9999:
            raise ValueError
    except (TypeError, ValueError):
        out["fatal"] = "version must be a whole number 1-9999"
        return out
    for k in sorted(set(d) - TOP_KEYS):
        out["problems"].append(f"unknown field '{_txt(k, 30)}' ignored")
    pack = {"id": pid, "name": _txt(d.get("name") or pid, 100), "version": ver, "made": _txt(d.get("made"), 20),
            "source": _txt(d.get("source"), 200), "about": _txt(d.get("about"), 2000)}
    out["pack"] = pack
    # the file's own exits
    local = {}
    ex_in = d.get("exits") or {}
    if not isinstance(ex_in, dict):
        out["problems"].append("exits must be an object: ignored")
        ex_in = {}
    for name, spec in list(ex_in.items())[:MAX_EXITS]:
        if not NAME_RE.match(str(name)):
            out["problems"].append(f"exit name '{_txt(name, 30)}': use 1-24 lowercase letters, digits or _")
            continue
        sp, why = X.spec_check(spec)
        if sp is None:
            out["problems"].append(f"exit '{name}' not usable: {why}")
            continue
        key = X.spec_key(sp)
        local[str(name)] = key
        out["exits"][key] = {**sp, "name": str(name), "pack": pid}
    if len(ex_in) > MAX_EXITS:
        out["problems"].append(f"only the first {MAX_EXITS} exits are used")
    builtin = set(F.EXITS) | set(F.STYLES)
    strategies = d.get("strategies") or []
    if not isinstance(strategies, list) or not strategies:
        out["fatal"] = "no strategies in the file"
        return out
    if len(strategies) > MAX_STRATEGIES:
        out["problems"].append(f"only the first {MAX_STRATEGIES} strategies are read")
    seen_names, seen_ids = set(), set()
    for n_, st in enumerate(strategies[:MAX_STRATEGIES]):
        if not isinstance(st, dict):
            out["problems"].append(f"strategy {n_ + 1} is not an object: skipped")
            continue
        name = _txt(st.get("name") or f"strategy {n_ + 1}", 60)
        if name in seen_names:
            out["problems"].append(f"'{name}': the name is used twice: skipped")
            continue
        seen_names.add(name)
        rec = {"name": name, "rules": [], "status": "ok", "why": ""}
        out["strategies"].append(rec)
        for k in sorted(set(st) - STRAT_KEYS):
            out["problems"].append(f"'{name}': unknown field '{_txt(k, 30)}' ignored")
        try:
            dr = int(st.get("dir"))
            if dr not in (1, -1, 0):
                raise ValueError
        except (TypeError, ValueError):
            rec.update(status="bad", why="dir must be 1 (buy call), -1 (buy put) or 0 (sell straddle / fly)")
            continue
        conds, needs = [], []
        bad = None
        for c in (st.get("conds") or []):
            if not (isinstance(c, list) and len(c) == 3):
                bad = "each condition is [fact, '>' or '<', number]"
                break
            f, op, v = c
            try:
                v = float(v)
            except (TypeError, ValueError):
                bad = f"condition on {_txt(f, 30)}: the value must be a number"
                break
            if op not in (">", "<") or not math.isfinite(v):
                bad = f"condition on {_txt(f, 30)}: use '>' or '<' and a finite number"
                break
            if f not in F.FEATURES:
                needs.append(_txt(f, 40))
            conds.append([str(f), op, v])
        if bad:
            rec.update(status="bad", why=bad)
            continue
        if not conds or len(conds) > 4:
            rec.update(status="bad", why="1 to 4 conditions")
            continue
        then = st.get("then")                              # 2.47: setup bar, then a trigger bar within N bars
        if then is not None:
            if not (isinstance(then, dict) and isinstance(then.get("conds"), list) and 1 <= len(then["conds"]) <= 3):
                rec.update(status="bad", why="then must be {\"conds\": [1-3 conditions], \"within\": bars}")
                continue
            for c in then["conds"]:
                if isinstance(c, list) and len(c) == 3 and c[0] not in F.FEATURES:
                    needs.append(_txt(c[0], 40))
        if needs:
            rec.update(status="cannot", why="needs facts the app doesn't have yet: " + ", ".join(needs))
            out["cannot_test"].append({"idea": f"{pack['name']}: {name}", "needs": ", ".join(needs)})
            continue
        try:
            w = [int(st.get("win", [15, 330])[0]), int(st.get("win", [15, 330])[1])]
        except (TypeError, ValueError, IndexError):
            rec.update(status="bad", why="win must be [from, to] in minutes since 09:15 (bar close times, e.g. [10, 75])")
            continue
        exs = st.get("exit", "std")
        if exs == "all":
            exs = (list(F.SELL_EXITS) if dr == 0 else list(F.BUY_EXITS) + list(local))
        elif isinstance(exs, str):
            exs = [exs]
        if not isinstance(exs, list) or not exs:
            rec.update(status="bad", why="exit must be a name, a list of names or \"all\"")
            continue
        syms = st.get("syms") or ["BANKNIFTY", "NIFTY"]
        if not isinstance(syms, list):
            syms = [syms]
        known_syms = set(F.INDEX_SYMS) | {F.STOCKS} | set(F.UNDERLYINGS)
        unknown = [str(x) for x in syms if str(x) not in known_syms]
        if unknown:
            out["problems"].append(f"'{name}': unknown symbols ignored: {', '.join(unknown)[:60]}")
        for e in exs[:12]:
            e = str(e)
            key = local.get(e) or (e if (e in builtin if dr != 0 else e in F.SELL_EXITS) else None)
            if key is None:
                out["problems"].append(f"'{name}': exit '{_txt(e, 30)}' unknown here: skipped")
                continue
            r = {"dir": dr, "conds": conds, "win": w, "exit": key, "max_day": 2 if int(st.get("max_day") or 1) > 1 else 1,
                 "syms": [str(x) for x in syms], "note": _txt(st.get("note"), 300) or pack["about"][:300],
                 "source": f"pack:{pid}:{name}"[:80], "pack": pid, "pack_v": ver, "name": name, "early": w[0] < F.FIRST_ENTRY_TOD}
            if then is not None:
                r["then"] = then
            v = _validate(r, out["exits"])
            if not v:
                out["problems"].append(f"'{name}' with exit '{e}': not a testable rule")
                continue
            if v["id"] in seen_ids:
                continue
            seen_ids.add(v["id"])
            if w[0] < F.EARLY_ENTRY_TOD or w[1] > F.LAST_ENTRY_TOD:
                out["problems"].append(f"'{name}': window kept inside 09:25-14:45")
            rec["rules"].append(v["id"])
            out["rules"].append(v)
            if len(out["rules"]) >= MAX_RULES:
                break
        if not rec["rules"] and rec["status"] == "ok":
            rec.update(status="bad", why="no usable exit")
        if len(out["rules"]) >= MAX_RULES:
            out["problems"].append(f"only the first {MAX_RULES} rules are used")
            break
    for c in (d.get("cannot_test") or [])[:50] if isinstance(d.get("cannot_test"), list) else []:
        if isinstance(c, dict):
            out["cannot_test"].append({"idea": _txt(c.get("idea"), 200), "needs": _txt(c.get("needs"), 200)})
    if not out["rules"] and not out["cannot_test"]:
        out["fatal"] = "no strategy in the file can be tested: " + "; ".join(
            f"{s['name']}: {s['why']}" for s in out["strategies"] if s["why"])[:400]
    return out


def _validate(r: dict, file_exits: dict) -> dict | None:
    """factory.validate with this file's own exits known."""
    from . import factory as F
    return F.validate(r, extra_exits=set(file_exits))


# ------------------------------------------------------------------ store, list, switch
def store(data_dir, raw: bytes, filename: str = "") -> dict:
    """Check and keep an uploaded file. Raises ValueError with the reason when it can't be used at all."""
    res = check(raw)
    if res["fatal"]:
        raise ValueError(res["fatal"] + (f" (also: {'; '.join(res['problems'][:4])})" if res["problems"] else ""))
    p = res["pack"]
    sha = hashlib.sha256(raw).hexdigest()
    with _LOCK:
        idx = load_index(data_dir)
        cur = idx["packs"].get(p["id"]) or {"versions": [], "on": True}
        same = next((v for v in cur["versions"] if v.get("sha") == sha), None)
        if same:
            res["already"] = True
        else:
            fdir = root(data_dir) / "files"
            fdir.mkdir(parents=True, exist_ok=True)
            fname = f"{p['id']}__v{p['version']}__{sha[:8]}.json"
            (fdir / fname).write_bytes(raw)
            cur["versions"].append({"version": p["version"], "file": fname, "sha": sha,
                                    "at": dt.datetime.now().isoformat(timespec="seconds"), "uploaded_as": _txt(Path(filename).name, 80),
                                    "rules": len(res["rules"]), "problems": res["problems"][:30], "cannot_test": res["cannot_test"][:30]})
            cur["current"] = fname
        cur.update(name=p["name"], source=p["source"], about=p["about"], made=p["made"], version=p["version"])
        if same:
            cur["current"] = same["file"]
        idx["packs"][p["id"]] = cur
        for k, sp in res["exits"].items():                # exits are kept forever: saved rules can always be rescored
            idx["exits"].setdefault(k, sp)
        _save_index(data_dir, idx)
    activate(data_dir)
    _CACHE.clear()
    return {"id": p["id"], "name": p["name"], "version": p["version"], "rules": len(res["rules"]),
            "strategies": res["strategies"], "problems": res["problems"], "cannot_test": res["cannot_test"],
            "already": bool(res.get("already"))}


def set_on(data_dir, pack_id: str, on: bool) -> dict:
    with _LOCK:
        idx = load_index(data_dir)
        if pack_id not in idx["packs"]:
            raise KeyError(pack_id)
        idx["packs"][pack_id]["on"] = bool(on)
        _save_index(data_dir, idx)
    _CACHE.clear()
    return {"id": pack_id, "on": bool(on)}


def activate(data_dir) -> int:
    """Register every exit any strategy file ever defined (on or off: saved rules must stay rescorable)."""
    from . import exits as X
    n = 0
    for k, sp in load_index(data_dir)["exits"].items():
        chk, _ = X.spec_check({kk: sp.get(kk) for kk in X.SPEC_KEYS if kk in sp and kk != "words"})
        if chk is not None and X.spec_key(chk) == k:
            X.PACK_STYLES.setdefault(k, {**chk, "name": sp.get("name"), "pack": sp.get("pack")})
            n += 1
    return n


def current(data_dir, only_on: bool = True) -> list:
    """[(pack id, index entry, check result)] of each pack's current file."""
    idx = load_index(data_dir)
    out = []
    for pid, e in sorted(idx["packs"].items()):
        if only_on and not e.get("on", True):
            continue
        f = root(data_dir) / "files" / str(e.get("current") or "")
        try:
            key = (str(f), f.stat().st_mtime)
        except OSError:
            continue
        hit = _CACHE.get(key)
        if hit is None:
            try:
                hit = check(f.read_bytes())
            except Exception as ex:
                hit = {"fatal": str(ex)[:200], "rules": [], "strategies": [], "problems": [], "cannot_test": []}
            _CACHE[key] = hit
        out.append((pid, e, hit))
    return out


def rules(data_dir, only_on: bool = True) -> list:
    """Every Lab rule of the switched-on strategy files (current version of each)."""
    activate(data_dir)
    return [dict(r) for _pid, _e, res in current(data_dir, only_on) if not res.get("fatal") for r in res["rules"]]


def queue(data_dir) -> dict:
    """Queue the rules this Lab has not tested yet (called at the start of every Lab run, 1-year and 5-year)."""
    from . import factory as F
    rs = rules(data_dir)
    if not rs:
        return {"queued": 0}
    return F.queue_untested(data_dir, rs, source="pack")


def view(data_dir) -> dict:
    """For the Strategy Lab page: each file, its strategies and their Lab results (1-year and 5-year)."""
    from . import factory as F
    labs = {"1y": F.load(data_dir).get("rules", {})}
    r5 = Path(data_dir) / "lab5y"
    if (r5 / "lab" / "factory.json").exists():
        labs["5y"] = F.load(r5).get("rules", {})
    stage_rank = {"promoted": 0, "failed holdout": 1, "failed validation": 2, "failed train": 3, "duplicate": 4}
    idx = load_index(data_dir)
    out = []
    for pid, e, res in current(data_dir, only_on=False):
        strats = []
        for s in res.get("strategies", []):
            item = {"name": s["name"], "status": s["status"], "why": s["why"], "rules": len(s["rules"]), "labs": {},
                    "ids": list(s["rules"])}                     # 2.49: the Lab page opens each version, stock by stock
            for lab, known in labs.items():
                got = [known[i] for i in s["rules"] if i in known]
                if not got:
                    item["labs"][lab] = {"tested": 0}
                    continue
                # the version to show is chosen on train (holdout only among promoted ones): never pick by holdout luck
                best = min(got, key=lambda r: (stage_rank.get(r.get("stage"), 9),
                                               -((r.get("hold") or {}).get("avg") or -1e9) if r.get("stage") == "promoted"
                                               else -((r.get("train") or {}).get("t") or -9)))
                item["labs"][lab] = {"tested": len(got), "of": len(s["rules"]), "stage": best.get("stage"), "id": best.get("id"),
                                     "exit": F.exit_words(best.get("exit", "")), "train": best.get("train"),
                                     "valid": best.get("valid"), "hold": best.get("hold"),
                                     "passed": sum(1 for r in got if r.get("stage") == "promoted")}
            strats.append(item)
        out.append({"id": pid, "name": e.get("name"), "on": e.get("on", True), "version": e.get("version"),
                    "source": e.get("source"), "about": e.get("about"), "made": e.get("made"),
                    "versions": [{k: v.get(k) for k in ("version", "at", "rules", "uploaded_as")} for v in e.get("versions", [])][-10:],
                    "fatal": res.get("fatal"), "problems": res.get("problems", [])[:20],
                    "cannot_test": res.get("cannot_test", [])[:20], "strategies": strats, "n_rules": len(res.get("rules", []))})
    return {"packs": out, "exits": len(idx["exits"]), "format": FORMAT}


def vocabulary() -> dict:
    """What a strategy file can use: facts, exits, exit fields, symbols (for whoever writes the file)."""
    from . import exits as X
    from . import factory as F
    return {"format": FORMAT, "facts": {k: v[0] + (f" ({v[1]})" if v[1] else "") for k, v in F.FEATURES.items()},
            "exits": {k: F.exit_words(k) for k in F.BUY_EXITS}, "sell_exits": {k: F.EXIT_WORDS.get(k, k) for k in F.SELL_EXITS},
            "exit_fields": {"stop": [list(X.STOP_KINDS), "atr k | bar | fc (other side of the first 5-min candle) | pct k"],
                            "target_r": "number or null", "be_at": "R or null", "trail_atr": "ATRs or null (atr stop only)",
                            "momentum": list(X.MOMENTUM), "max": "minutes (375 = out by 15:15)",
                            "exit_if": "2.47: 1-3 [fact, '>' or '<', number]: out at the 5-min close where any holds",
                            "opt_stop_pct": "2.47: stop on the option's own price, % against"},
            "then": f"2.47: {{\"conds\": [1-3 conditions], \"within\": 1-{F.THEN_MAX}}}: the strategy's conds mark a setup bar; "
                    "the entry is the first later bar (within N bars) where these hold",
            "windows": f"minutes since 09:15 at the bar's close: {F.EARLY_ENTRY_TOD} (09:25) to {F.LAST_ENTRY_TOD} (14:45)",
            "symbols": sorted(set(F.INDEX_SYMS) | {F.STOCKS})}
