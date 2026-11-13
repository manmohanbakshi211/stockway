"""Higher-timeframe candles inside the 1-minute engine (gun piece, 0.11.0).

EmaAlerts(tf, n): candles of `tf` minutes aligned to 09:15, an EMA(n) of their closes (carried across days like the
chart does), and the "alert candle" rule of the 5 EMA method:
- a candle that closes above the EMA without its low touching it (low > EMA) is an up-alert; one that closes below
  without its high touching it (high < EMA) is a down-alert;
- during the following candle, price breaking the up-alert's low gives -1 (stop: the alert's high), breaking the
  down-alert's high gives +1 (stop: the alert's low); the alert is then used up;
- an alert lasts one candle: when the next candle closes it becomes the new alert or the alert lapses.
Setups use it by feature name only: `ealert_<tf>m_<n>` (+1 / -1 / 0 in this minute) and `ealert_<tf>m_<n>_stop`
(the stop level of that break), so a new timeframe or period needs no engine edit (ADR-0003).

Also the trend "pullback trigger candle" of the 10 EMA method (0.12.0), on the same candles and EMA:
- above the EMA, a RED candle whose low does not touch the EMA is a long trigger; below it, a GREEN candle whose high
  does not touch it is a short trigger;
- during the following candle, a break of the long trigger's high gives +1, of the short trigger's low -1; the stop
  is the EMA (last closed value) at that moment; a trigger lasts one candle.
Features: `epull_<tf>m_<n>` and `epull_<tf>m_<n>_stop`."""
from __future__ import annotations

import re

NAME = re.compile(r"^e(?:alert|pull)_(\d+)m_(\d+)(?:_stop)?$")


def needed(names) -> set[tuple[int, int]]:
    """(tf, n) pairs a list of feature names asks for (prefixes prev: and d_ are ignored)."""
    out = set()
    for raw in names:
        if not isinstance(raw, str):
            continue
        name = raw[5:] if raw.startswith("prev:") else raw
        name = name[2:] if name.startswith("d_") else name
        m = NAME.match(name)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return out


class EmaAlerts:
    def __init__(self, tf: int, n: int, ema: float | None = None):
        self.tf, self.n, self.ema = tf, n, ema
        self.alpha = 2 / (n + 1)
        self.cur: list | None = None             # forming candle [o, h, l, c, bucket]
        self.up: tuple[float, float] | None = None   # (high, low) of the active up-alert
        self.dn: tuple[float, float] | None = None   # (high, low) of the active down-alert
        self.pl: tuple[float, float] | None = None   # (high, low) of the active long pullback trigger
        self.ps: tuple[float, float] | None = None   # (high, low) of the active short pullback trigger
        self.pull: tuple[int, float | None] = (0, None)

    @property
    def key(self) -> str:
        return f"ealert_{self.tf}m_{self.n}"

    @property
    def pkey(self) -> str:
        return f"epull_{self.tf}m_{self.n}"

    def on_bar(self, m: int, o: float, h: float, l: float, c: float) -> tuple[int, float | None]:
        """Feeds minute m (0 = 09:15); returns (signal, stop) for this minute."""
        sig, stop = 0, None
        if self.up is not None and l < self.up[1]:
            sig, stop = -1, self.up[0]
            self.up = None
        if self.dn is not None and h > self.dn[0]:
            if sig == 0:
                sig, stop = 1, self.dn[1]
            self.dn = None
        psig, pstop = 0, None
        if self.pl is not None and h > self.pl[0]:
            psig, pstop = 1, self.ema
            self.pl = None
        if self.ps is not None and l < self.ps[1]:
            if psig == 0:
                psig, pstop = -1, self.ema
            self.ps = None
        self.pull = (psig, pstop)
        b = m // self.tf
        if self.cur is None or self.cur[4] != b:
            self.cur = [o, h, l, c, b]
        else:
            self.cur[1], self.cur[2], self.cur[3] = max(self.cur[1], h), min(self.cur[2], l), c
        if (m + 1) % self.tf == 0:                   # the candle closes with this minute
            co, ch, cl, cc, _b = self.cur
            self.ema = cc if self.ema is None else self.ema + self.alpha * (cc - self.ema)
            self.up = (ch, cl) if (cl > self.ema and cc > self.ema) else None
            self.dn = (ch, cl) if (ch < self.ema and cc < self.ema) else None
            self.pl = (ch, cl) if (cc < co and cl > self.ema) else None
            self.ps = (ch, cl) if (cc > co and ch < self.ema) else None
            self.cur = None
        return sig, stop
