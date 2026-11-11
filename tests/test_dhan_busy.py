"""2.56.2: live QA on 11 Nov 2026 (market open). The Live page said "Dhan /data/newsheadline: too many retries" and
"/data/marketmovers: too many retries" with no reason. Now the error says Dhan's answer (HTTP 429 / 5xx), a live call
tries twice instead of five times (it stalled the news loop up to 45 s), and a busy endpoint rests 2 min (doubling to
15 min) instead of being asked again every minute. The 5-year download keeps its own retries and its "too many
retries" check unchanged."""
import time

import pytest

from lab import dhan as D
from live import dhanintel as I


class _R:
    def __init__(self, code, text="busy"):
        self.status_code, self.text = code, text

    def json(self):
        return {}


def _client(codes, monkeypatch):
    c = D.Dhan("1", "tok", per_sec=1000)
    seq = list(codes)
    c.s.post = lambda *a, **k: _R(seq.pop(0)) if seq else _R(200)
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    monkeypatch.setattr(c, "_wait", lambda: None)
    return c


def test_retry_error_names_the_http_answer_and_keeps_the_old_words(monkeypatch):
    c = _client([429, 429, 429, 429, 429], monkeypatch)
    with pytest.raises(D.DhanError) as e:
        c.post("/data/newsheadline", {})
    assert e.value.status == 429 and "HTTP 429" in str(e.value)
    assert "too many retries" in str(e.value)               # lab/dhan_hist.py still treats it as "network down, retry later"


def test_live_call_tries_twice_then_rests_and_says_why(monkeypatch):
    calls = []

    class FD:
        client_id, token = "1", None

        def post(self, path, body, tries=5):
            calls.append((path, tries))
            raise D.DhanError(f"Dhan {path}: too many retries (last answer HTTP 429)", status=429)
    it = I.DhanIntel(FD(), ".")
    monkeypatch.setattr(it, "_dead", lambda: False)
    assert it.news() == [] and calls == [("/data/newsheadline", I.LIVE_TRIES)]
    assert "HTTP 429" in it.errors["news"] and "next try" in it.errors["news"]
    assert it.news() == [] and len(calls) == 1               # resting: not asked again a minute later
    it.ids = {"1": "X"}
    it.refresh_movers(None)                                  # only the stock lists without an expiry are asked
    assert len([c for c in calls if c[0] == "/data/marketmovers"]) == 1     # busy: the other lists wait too
    assert "movers" in it.errors
    it.cool["news"] = (time.time() - 1, 120)                 # rest over: asks again, the next rest is longer
    it.news()
    assert it.cool["news"][1] == 240


def test_success_clears_the_rest_and_the_error(monkeypatch):
    class FD:
        client_id, token = "1", None

        def post(self, path, body, tries=5):
            return {"data": [{"headline": "Nifty up", "timestamp": 1793000000000}]}
    it = I.DhanIntel(FD(), ".")
    it.ids = {"1": "X"}
    monkeypatch.setattr(it, "_dead", lambda: False)
    it.errors["news"], it.cool["news"] = "old", (0, 240)
    assert it.news()[0]["title"] == "Nifty up"
    assert "news" not in it.errors and "news" not in it.cool
