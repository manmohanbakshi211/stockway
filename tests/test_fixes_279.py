"""2.79: the week's bugs (22 Nov audit): the bot token never in a log line or the AI's notes, Telegram never loses a
message to a stray "<", the daily summary never repeats an earlier day, the speed check is not red after the close,
no internet at the start is one line with a back-off (not a traceback every 20 s), GIFT Nifty backs off too."""
import datetime as dt
import types

import requests

from live import analyst as A
from live import health as H
from live.applog import mask
from live.notify import Telegram, plain

TOKEN_URL = "Max retries exceeded with url: /bot1234567890:AAGb6jIzGXq8tx0C0fXX_Tg_5Bg0C21Mq8Hc/sendMessage"


def test_the_bot_token_is_masked_in_logs_and_the_ai_notes(tmp_path):
    assert "AAGb6" not in mask("Telegram failed: " + TOKEN_URL) and "bot<hidden>" in mask(TOKEN_URL)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_text(f"2026-11-16 09:25:48  Telegram failed: HTTPSConnectionPool(host='api.telegram.org'): {TOKEN_URL}\n")
    p = A._log_problems(tmp_path, "2026-11-16")
    assert p and not any("AAGb6" in x for x in p)
    from live.engine import LiveEngine
    import inspect
    assert "mask(" in inspect.getsource(LiveEngine.log)


class _R:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


class _Done(BaseException):
    """Ends the sender's endless loop in a test (it is not an Exception, so the sender does not catch it)."""


def _tg(monkeypatch, answers):
    calls, logs = [], []

    def post(url, data=None, timeout=None):
        if not answers:
            raise _Done()
        calls.append(dict(data))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(requests, "post", post)
    t = Telegram.__new__(Telegram)
    t.get_cfg = lambda: {"telegram": {"bot_token": "1234567890:AAGb6jIzGXq8tx0C0fXX_Tg_5Bg0C21Mq8Hc", "chat_id": "1"}}
    t.log, t.sent = logs.append, []
    return t, calls, logs


def test_telegram_sends_plain_text_when_its_html_is_refused_and_never_logs_the_url(monkeypatch):
    import queue
    t, calls, logs = _tg(monkeypatch, [_R(400, '{"ok":false,"description":"Bad Request: can\'t parse entities: Unsupported start tag \\"0.9x\\""}'), _R(200)])
    t.q = queue.Queue()
    t.q.put("🧠 <b>Analyst</b>\n🐞 ratio <0.9x on quiet days & more")
    t.q.put("end")
    try:
        t._run()
    except _Done:
        pass                                        # the second message ends the loop in this test
    assert calls[0]["parse_mode"] == "HTML" and "parse_mode" not in calls[1]
    assert calls[1]["text"] == "🧠 Analyst\n🐞 ratio <0.9x on quiet days & more" and not logs
    assert plain("<b>A</b> &lt;1 <a href='x'>link</a>") == "A <1 link"
    t2, _, logs2 = _tg(monkeypatch, [requests.exceptions.ConnectionError("HTTPSConnectionPool(host='api.telegram.org', port=443): "
                                                                          + TOKEN_URL + " (Caused by NameResolutionError)")])
    t2.q = queue.Queue()
    t2.q.put("x")
    t2.q.put("end")
    try:
        t2._run()
    except _Done:
        pass
    assert logs2 == ["Telegram not sent: no internet"]


def test_the_analysts_words_are_escaped_for_telegram(tmp_path, monkeypatch):
    import json
    sent = []
    reply = json.dumps({"worked": ["w"], "failed": [], "bugs": ["ratio <0.9x & falling"], "tomorrow": "", "ideas": []})
    ai = types.SimpleNamespace(enabled=lambda: True, ask=lambda p, n=0, auto=True, task=None: reply)
    e = types.SimpleNamespace(ai=ai, telegram=types.SimpleNamespace(send=sent.append), alerts_mem=[], store=None, source="upstox",
                              paper=None, books=None, radar_news=types.SimpleNamespace(view=lambda: {}))
    monkeypatch.setattr(A, "known_facts", lambda d: [])
    A.run(e, tmp_path, day="2026-11-21", log=lambda m: None)
    assert sent and "ratio &lt;0.9x &amp; falling" in sent[0] and "<b>Stockway evening analyst</b>" in sent[0]


def test_a_day_without_the_scanner_never_reports_an_earlier_days_counts(tmp_path, monkeypatch):
    sent = []
    old = types.SimpleNamespace(counts={"chains": 3217, "news": 192, "candles": 28837}, day="2026-11-19")
    bench = types.SimpleNamespace(status=lambda: {"styles": {"buy": {"opened": 52687}}})
    e = types.SimpleNamespace(store=types.SimpleNamespace(get=lambda k, d=None: d, put=lambda k, v: None), data_dir=tmp_path,
                              recorder=old, bench=bench, source="upstox")
    h = H.Health.__new__(H.Health)
    h.e, h.summary_day = e, None
    h.notify = lambda key, text, force=False: sent.append(text)
    now = dt.datetime(2026, 11, 20, 15, 52, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    h._summary(now, now.timestamp(), dict(H.DEFAULTS), True, False)
    assert sent and "did not run today" in sent[0] and "3,217" not in sent[0] and "52,687" not in sent[0]


def test_the_speed_check_is_not_red_after_the_close():
    from live import selfcheck
    stale = types.SimpleNamespace(primary=types.SimpleNamespace(view=lambda: {"mode": "process", "alive": False}), lag_ms=9000)
    e = types.SimpleNamespace(running=False, feed=stale, cfg=lambda: {"live": {}},
                              status={"loop_p95_ms": 18619, "loop_ms": 18619, "snapshot_ms": 7000})
    sp = next(c for c in selfcheck.runtime_checks(e, "data", {}) if c["id"] == "speed")
    assert sp["ok"] is None and "starts with the live scanner" in sp["detail"]


def test_no_internet_at_the_start_is_one_line_and_a_back_off(tmp_path, monkeypatch):
    from radar.config import load_config
    from live import engine as EN

    def cfg():
        c = load_config()
        c["live"] = {**(c.get("live") or {}), "auto_start": False, "news": False, "filings": False, "briefs": False}
        return c
    e = EN.LiveEngine(cfg, tmp_path)
    err = requests.exceptions.ConnectionError("HTTPSConnectionPool(host='assets.upstox.com', port=443): Max retries "
                                              "exceeded (Caused by NameResolutionError(...))")
    e._run = lambda: (_ for _ in ()).throw(err)            # the instrument-list download without internet
    e.source = "upstox"
    n0 = len(e.logs)
    for _ in range(3):
        e._run_safe()
    lines = list(e.logs)[n0:]
    assert e.status["state"] == "error" and e.status["message"].startswith("No internet")
    assert sum("No internet" in x for x in lines) == 1 and not any("Traceback" in x for x in lines)
    assert any("assets.upstox.com unreachable" in x for x in lines) and e._start_fail[1] == 3
    assert EN._offline(err) and not EN._offline(ValueError("x"))


def test_gift_nifty_backs_off_without_internet(monkeypatch):
    from live import giftnifty as G
    logs = []
    g = G.GiftNifty(log=logs.append)

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("HTTPSConnectionPool(host='www.nseix.com'): Failed to resolve")
    monkeypatch.setattr(requests, "get", boom)
    for _ in range(4):
        g._lock.acquire()
        g._fetch()
    assert g.fails == 4 and logs == ["GIFT Nifty not available right now: no internet"]
    g._at = G.time.time() - 100                       # 100 s later: after 4 failures it waits longer than that
    started = []
    monkeypatch.setattr(G.threading, "Thread", lambda **k: types.SimpleNamespace(start=lambda: started.append(1)))
    g.get()
    assert not started
