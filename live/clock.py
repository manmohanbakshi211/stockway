"""Market clock in IST. The simulator runs its own clock (starts at 09:15, can run faster)."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
SESSION_MIN = 375


class Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(IST)

    def ts(self) -> float:
        return self.now().timestamp()


class SimClock(Clock):
    def __init__(self, speed: float = 1.0, start: dt.time = dt.time(9, 15, 5)):
        self.speed = speed
        self.real0 = time.time()
        today = dt.datetime.now(IST).date()
        self.sim0 = dt.datetime.combine(today, start, IST)

    def now(self) -> dt.datetime:
        return self.sim0 + dt.timedelta(seconds=(time.time() - self.real0) * self.speed)


def minutes_since_open(now: dt.datetime) -> float:
    start = dt.datetime.combine(now.date(), OPEN, now.tzinfo)
    return max(0.0, min(SESSION_MIN, (now - start).total_seconds() / 60))


def is_market_hours(now: dt.datetime) -> bool:
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE
