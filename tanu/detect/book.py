"""The alert book: every display-only alert of a day in <folder>/<day>.jsonl (detectors on the live day, and new
items from the Market page's sources). The same symbol + kind + direction stays quiet for its cooldown. Thread-safe
(the live loop and the intel reader both write). Read by the Market page's Alerts tab."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections import Counter
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SPEC = Path(__file__).with_name("detectors.json")


def cooldowns(spec: dict | None = None) -> tuple[dict, int]:
    spec = spec or json.loads(SPEC.read_text())
    c = {k: v.get("cooldown_min") for k, v in spec["detectors"].items() if v.get("cooldown_min") is not None}
    c.update(spec.get("intel_cooldown_min", {}))
    return c, spec.get("default_cooldown_min", 15)


class AlertBook:
    def __init__(self, folder: Path | str, now=time.time, spec: dict | None = None):
        self.dir = Path(folder)
        self.now = now
        self.cool, self.default_cool = cooldowns(spec)
        self.last: dict[tuple, float] = {}
        self.lock = threading.Lock()

    def add(self, a: dict, t: float | None = None) -> bool:
        """Records one alert {sym, kind, direction, severity, title, detail, price?, data?}; False if in cooldown."""
        t = float(self.now() if t is None else t)
        k = (a.get("sym"), a.get("kind"), a.get("direction"))
        cool = self.cool.get(a.get("kind"), self.default_cool) * 60
        with self.lock:
            if cool and k in self.last and t - self.last[k] < cool:
                return False
            self.last[k] = t
            when = dt.datetime.fromtimestamp(t, IST)
            rec = {"t": t, "at": when.isoformat(timespec="seconds"), "sym": a.get("sym"), "kind": a.get("kind"),
                   "direction": a.get("direction", "neutral"), "severity": int(a.get("severity", 1)),
                   "title": str(a.get("title", ""))[:200], "detail": str(a.get("detail", ""))[:400],
                   "price": a.get("price"), "data": a.get("data") or {}}
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / f"{when.date().isoformat()}.jsonl", "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return True

    def days(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.jsonl")) if self.dir.exists() else []

    def read(self, day: str) -> list[dict]:
        p = self.dir / f"{day}.jsonl"
        if not p.exists():
            return []
        out = []
        for ln in p.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

    def view(self, day: str | None = None, min_sev: int = 2, sym: str | None = None, kind: str | None = None,
             limit: int = 300) -> dict:
        days = self.days()
        day = day or (days[-1] if days else None)
        rows = self.read(day) if day else []
        counts = {"all": len(rows), "by_kind": dict(Counter(r["kind"] for r in rows)),
                  "by_severity": {str(k): v for k, v in sorted(Counter(r["severity"] for r in rows).items())},
                  "symbols": len({r["sym"] for r in rows})}
        sel = [r for r in rows if r["severity"] >= min_sev and (not sym or r["sym"] == sym.upper())
               and (not kind or r["kind"] == kind)]
        sel.sort(key=lambda r: -r["t"])
        return {"day": day, "days": days[-10:], "counts": counts, "shown": len(sel), "alerts": sel[:limit]}
