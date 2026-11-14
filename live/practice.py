"""2.76: the practice sources. The simulator (a made-up market) and the replay of a recorded day (live/replay.py) are
never the real market: no Telegram, no real-day reviews, no NSE-calendar or broker checks, their alerts and paper
trades kept apart under their own source name."""

PRACTICE = ("sim", "replay")


def is_practice(source) -> bool:
    return source in PRACTICE
