"""Minute bars built while ticks arrive: the streaming twin of tanu.data.bars.from_records (same bucketing by the
exchange trade time, same fields), so a live day and the bars rebuilt from its tape agree for every tick that arrived
before its minute was closed. A tick for an already-closed minute is counted as late (the tape still has it)."""
from __future__ import annotations

import collections


class MinuteBars:
    def __init__(self):
        self.open: dict[tuple[str, int], list] = {}       # (key, minute) -> [o, h, l, c, cum_vol, oi, bid, ask, ticks]
        self.last_cum: dict[str, float | None] = {}        # key -> cum_vol of its last closed bar
        self.closed_upto = 0                              # minutes < this are closed
        self.late = 0
        self.last: dict[str, dict] = {}                    # key -> last tick fields (ltp, bid, ask, oi, iv, ...)

    def add(self, ticks: list[dict], t_recv: float) -> float:
        """Adds one message's ticks; returns the latest exchange time seen in them (0 if none)."""
        latest = 0.0
        for tk in ticks:
            key = tk["key"]
            prev = self.last.get(key)
            self.last[key] = {**prev, **tk} if prev else dict(tk)
            p = tk.get("ltp")
            if not p:
                continue
            ts = tk["ltt"] / 1000 if tk.get("ltt") else t_recv
            latest = max(latest, ts)
            m = int(ts // 60) * 60
            if m < self.closed_upto:
                self.late += 1
                continue
            b = self.open.get((key, m))
            if b is None:
                self.open[(key, m)] = b = [p, p, p, p, None, None, None, None, 0]
            else:
                b[1], b[2], b[3] = max(b[1], p), min(b[2], p), p
            if tk.get("vol") is not None:
                b[4] = tk["vol"] if b[4] is None else max(b[4], tk["vol"])
            if tk.get("oi") is not None:
                b[5] = tk["oi"]
            if tk.get("bid") is not None:
                b[6], b[7] = tk["bid"], tk.get("ask")
            b[8] += 1
        return latest

    def close(self, upto: int) -> list[dict]:
        """Closes every bar of a minute before `upto` (epoch seconds, minute start) and returns them in key / time
        order: {key, t, o, h, l, c, vol, cum_vol, oi, bid, ask, ticks}."""
        done = sorted((k for k in self.open if k[1] < upto), key=lambda k: (k[0], k[1]))
        out = []
        by_key = collections.defaultdict(list)
        for k in done:
            by_key[k[0]].append(k)
        for key, ks in by_key.items():
            for k in ks:
                o, h, l, c, cum, oi, bid, ask, n = self.open.pop(k)
                prev = self.last_cum.get(key) or 0
                out.append({"key": key, "t": k[1], "o": o, "h": h, "l": l, "c": c,
                            "vol": None if cum is None else cum - prev, "cum_vol": cum, "oi": oi, "bid": bid,
                            "ask": ask, "ticks": n})
                self.last_cum[key] = cum
        self.closed_upto = max(self.closed_upto, upto)
        return out
