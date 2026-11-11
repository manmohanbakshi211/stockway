"""2.56.2: bugs found in live QA on 11 Nov 2026 (market open), each with its check.
- Nifty Midcap Select's "52-week range" was just today's range: Yahoo has one monthly bar for it. Less than a year of
  Yahoo history now falls back to the chart's saved daily bars (Dhan); no all-time high is claimed from under 3 years.
- A chart's older history that failed once (Dhan busy) was never asked again that session; now again after 10 minutes.
- A screen's Dhan client gives up after 2 tries (a busy Dhan made a chart wait 45-90 s); the download keeps 5."""
import datetime as dt
import json
import time

from live.clock import IST
from live.world import extremes_from_chart, extremes_from_daily, short_history


def _daily(n, start_hi=100.0, now=None):
    now = now or time.time()
    return [[now - (n - i) * 86400, 1, start_hi + i, 50 + i * 0.1, 1, 0] for i in range(n)]


def test_short_yahoo_history_is_not_a_52_week_range():
    ts = [int(dt.datetime(2026, 11, 1, tzinfo=IST).timestamp())]
    js = {"chart": {"result": [{"timestamp": ts, "meta": {"fiftyTwoWeekHigh": 14234.5, "fiftyTwoWeekLow": 14117.5},
                                "indicators": {"quote": [{"high": [14234.5], "low": [14117.5]}]}}]}}
    e = extremes_from_chart(js)
    assert e["months"] == 1 and short_history(e)
    assert short_history({"since": "2026", "hi52": 1}, dt.date(2026, 11, 11))       # saved before 2.56.2 (no "months")
    assert not short_history({"since": "2007", "hi52": 1}, dt.date(2026, 11, 11))
    assert short_history(None)


def test_daily_bars_give_the_52_week_range_and_no_fake_all_time_high():
    now = time.time()
    rows = _daily(300, now=now)
    e = extremes_from_daily(rows, day_h=None, day_l=None, now=now)
    yr = [r for r in rows if r[0] >= now - 365 * 86400]
    assert e["hi52"] == round(max(r[2] for r in yr), 2) and e["lo52"] == round(min(r[3] for r in yr), 2)
    assert e["ath"] is None                                  # under 3 years of bars: no all-time claim
    assert extremes_from_daily(rows, day_h=10_000, now=now)["hi52"] == 10_000     # today's live high counts
    assert extremes_from_daily(_daily(50, now=now), now=now) is None              # too few bars: no range at all
    long = _daily(1200, now=now)
    assert extremes_from_daily(long, now=now)["ath"] == round(max(r[2] for r in long), 2)


def test_index_card_uses_daily_bars_when_yahoo_is_short(tmp_path):
    from live.engine import LiveEngine
    e = LiveEngine(lambda: {"live": {}}, tmp_path)
    (tmp_path / "charts").mkdir()
    rows = _daily(260)
    (tmp_path / "charts" / "MIDCPNIFTY_D.json").write_text(json.dumps({"rows": rows}))
    e.world.extremes = {"NIFTY_MID_SELECT.NS": {"ath": 14234.5, "atl": 14117.5, "since": "2026", "months": 1,
                                                "hi52": 14234.5, "lo52": 14117.5}}
    x = e.card_extras("MIDCPNIFTY", 300.0)
    assert x["ext"]["hi52"] == round(max(r[2] for r in rows if r[0] >= time.time() - 365 * 86400), 2)
    assert x["ext"]["lo52"] < 100 and x["ext"]["ath"] is None
    e.world.extremes["^NSEI"] = {"ath": 26373.2, "since": "2007", "months": 230, "hi52": 26373.2, "lo52": 22179.9}
    assert e.card_extras("NIFTY", 25000.0)["ext"]["hi52"] == 26373.2                # a long Yahoo history is kept as is


def test_chart_older_history_is_retried_after_ten_minutes(tmp_path, monkeypatch):
    from live.mtf import MTF

    class E:
        data_dir = tmp_path
        metrics = {}

        def cfg(self):
            return {}
    m = MTF(E())
    asked = []

    class D:
        def post(self, *a, **k):
            asked.append(1)
            raise RuntimeError("Dhan /charts/intraday: too many retries (last answer HTTP 429)")
    monkeypatch.setattr(m, "base_rows", lambda *a: [[time.time() - 86400, 1, 2, 0.5, 1, 0]])
    monkeypatch.setattr(m, "_dhan", lambda: D())
    monkeypatch.setattr(m, "_sec", lambda s: (1, "NSE_EQ", "EQUITY"))
    monkeypatch.setattr(m, "_yahoo", lambda *a: [])
    m.long_rows("X", 5, 30)
    m.long_rows("X", 5, 30)
    assert len(asked) == 1                                   # resting after the failure
    m.retry_at[("long", "X", 5)] = time.time() - 1
    m.long_rows("X", 5, 30)
    assert len(asked) == 2                                   # asked again after the rest


def test_screen_dhan_client_tries_twice_download_keeps_five():
    from lab.dhan import Dhan
    from live.mtf import MTF
    assert Dhan("1", "t").tries == 5

    class E:
        data_dir = "."

        def cfg(self):
            return {"lab": {"dhan_client_id": "1"}}
    import lab.dhan_token as T
    orig = (T.latest, T.is_dead)
    try:
        T.latest, T.is_dead = (lambda: "tok"), (lambda t: False)
        assert MTF(E())._dhan().tries == 2
    finally:
        T.latest, T.is_dead = orig


def test_holiday_rows_never_teach_or_judge_the_model():
    """10 Nov 2026 was an NSE holiday; the scanner of that day (before 2.48's calendar) graded 224 frozen-price alerts
    and they became the conviction model's whole holdout (base rate 0, AUC 0.5). Session days only now."""
    from live.learn import _session_day
    assert not _session_day("2026-11-10") and not _session_day("2026-11-07")       # holiday, Saturday
    assert _session_day("2026-11-09") and _session_day("2026-11-11") and _session_day("2026-11-08")   # 8 Nov: Muhurat
    assert _session_day("not a day")                                                  # unreadable: kept as before
