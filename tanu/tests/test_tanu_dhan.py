"""Dhan 5-year download in Tanu (0.16.0, ADR-0014): TOTP login (secrets never shown, a refusal is never retried by
itself), the data-only client (URL guard, retries, a refused token makes a new one), the shared budget, the downloader
(continues from the legacy progress read-only, writes only under data/tanu, empty days, network pauses, daily cap,
never in market hours), the supervisor thread and the API. No network: fakes only."""
import datetime as dt
import hashlib
import json
import threading
import urllib.parse

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tanu.brokers.dhan import auth as A
from tanu.brokers.dhan.client import Budget, DhanData, DhanError, NoToken
from tanu.core.safety import BlockedCall
from tanu.core.secrets import Secrets
from tanu.data import dhan5y as D
from tanu.ui.api import create_app

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 11, 14, 23, 0, tzinfo=IST).timestamp()          # Saturday night
KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                                 # RFC 6238 test key ("12345678901234567890")
HOST = {"host": "127.0.0.1:8790"}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# ------------------------------------------------------------------ login
def test_totp_matches_the_rfc_test_vector():
    assert A.totp(KEY, at=59, digits=8) == "94287082" and A.totp(KEY, at=1111111109, digits=8) == "07081804"
    with pytest.raises(ValueError):
        A.clean_secret("not-a-key!")


class AuthFake:
    def __init__(self, answers=None):
        self.urls, self.answers = [], list(answers or [])

    def __call__(self, url):
        self.urls.append(url)
        return self.answers.pop(0) if self.answers else (200, json.dumps({"accessToken": "T" * 60 + str(len(self.urls))}).encode())


def keeper(tmp_path, answers=None, clock=None):
    clock = clock or Clock()
    k = A.DhanKeeper(tmp_path, vault=A.Vault(Secrets(tmp_path), keychain=False), transport=AuthFake(answers), now=clock)
    return k, clock


def test_login_saves_the_pin_and_key_privately_and_never_shows_them(tmp_path):
    k, clock = keeper(tmp_path)
    with pytest.raises(ValueError):
        k.save_login("12ab", "1234", KEY)
    with pytest.raises(ValueError):
        k.save_login("1100123456", "12", KEY)
    where = k.save_login("1100123456", "123456", KEY.lower())
    assert where == "Tanu's private secrets folder"
    f = tmp_path / "secrets" / "dhan_login.json"
    assert oct(f.stat().st_mode & 0o777) == "0o600" and oct((tmp_path / "secrets").stat().st_mode & 0o777) == "0o700"
    assert k.token().startswith("TTTT")
    url = k.transport.urls[0]
    assert url.startswith("https://auth.dhan.co/app/generateAccessToken?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["dhanClientId"] == ["1100123456"] and q["pin"] == ["123456"] and q["totp"] == [A.totp(KEY, NOW)]
    st = json.dumps(k.status()) + (tmp_path / "dhan" / "login_state.json").read_text()
    assert "123456" not in st.replace("1100123456", "") and KEY not in st and "TTTT" not in st
    assert k.status()["auto_login"] and k.status()["token_age_h"] == 0.0
    clock.t += 19 * 3600
    assert k.token() and len(k.transport.urls) == 1                   # the same token while it is young
    clock.t += 2 * 3600
    assert k.token().endswith("2") and len(k.transport.urls) == 2     # 21 h old: a new one


def test_a_refused_pin_stops_automatic_login_until_saved_again(tmp_path):
    k, clock = keeper(tmp_path, answers=[(400, b'{"errorMessage": "Invalid pin 654321"}')])
    k.save_login("1100123456", "654321", KEY)
    assert k.token() is None and k.status()["blocked"] and "****" in k.status()["last_error"]
    assert "654321" not in k.status()["last_error"]
    clock.t += 3600
    assert k.login() == "blocked" and len(k.transport.urls) == 1      # never retried by itself
    k.save_login("1100123456", "123456", KEY)
    assert k.token() and k.status()["blocked"] is None


def test_logins_are_at_least_five_minutes_apart(tmp_path):
    k, clock = keeper(tmp_path, answers=[(503, b"{}")])
    k.save_login("1100123456", "123456", KEY)
    assert k.login() == "failed" and k.login() == "wait"
    clock.t += 301
    assert k.login() == "ok"


def test_a_pasted_token_works_without_automatic_login(tmp_path):
    k, _ = keeper(tmp_path)
    with pytest.raises(ValueError):
        k.save_token("1100123456", "short")
    k.save_token("1100123456", "P" * 80)
    assert k.token() == "P" * 80 and not k.transport.urls


# ------------------------------------------------------------------ client and budget
class DataFake:
    """Answers Dhan's chart APIs: NIFTY daily candles on weekdays (minus 2 Oct), and one rolling-strike day per call."""

    def __init__(self, empty=(), fail=None, refuse=0):
        self.calls, self.empty, self.fail, self.refuse = [], set(empty), dict(fail or {}), refuse

    def __call__(self, url, headers, body, timeout):
        b = json.loads(body)
        self.calls.append((url, b, headers))
        if self.refuse:
            self.refuse -= 1
            return 401, b'{"errorCode": "DH-901"}'
        if url.endswith("/charts/historical"):
            d0, d1 = dt.date.fromisoformat(b["fromDate"]), dt.date.fromisoformat(b["toDate"])
            ts = [int(dt.datetime.combine(d0 + dt.timedelta(days=i), dt.time(9, 15), IST).timestamp())
                  for i in range((d1 - d0).days) if (d0 + dt.timedelta(days=i)).weekday() < 5
                  and d0 + dt.timedelta(days=i) != dt.date(2026, 10, 2)]
            return 200, json.dumps({"timestamp": ts, "close": [100.0] * len(ts), "volume": [1e6] * len(ts)}).encode()
        day = b["fromDate"]
        key = (b["securityId"], day)
        if key in self.fail and self.fail[key] > 0:
            self.fail[key] -= 1
            return 503, b"busy"
        if (b["securityId"], day) in self.empty:
            return 200, b'{"data": {"ce": {}, "pe": {}}}'
        t0 = int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(9, 15), IST).timestamp())
        arr = {"timestamp": [t0, t0 + 60], "open": [10, 11], "high": [12, 12], "low": [9, 10], "close": [11, 11.5],
               "iv": [20, 20], "volume": [100, 50], "oi": [1000, 1010], "strike": [100, 100], "spot": [101, 101]}
        side = "ce" if b["drvOptionType"] == "CALL" else "pe"
        return 200, json.dumps({"data": {side: arr}}).encode()


class TokenOnly:
    def __init__(self):
        self.n, self.dead = 0, []

    def token(self):
        self.n += 1
        return f"tok{self.n}"

    def client_id(self):
        return "1100123456"

    def refused(self, t):
        self.dead.append(t)


def client(tmp_path, fake, clock=None):
    clock = clock or Clock()
    b = Budget(tmp_path / "dhan" / "calls.json", rate=4.6, now=clock, sleep=clock.sleep)
    return DhanData(TokenOnly(), b, transport=fake, sleep=clock.sleep), b, clock


def test_client_sends_only_market_data_calls_and_retries_busy_answers(tmp_path):
    fake = DataFake(fail={("13", "2026-11-13"): 2})
    c, b, clock = client(tmp_path, fake)
    arr = c.rolling("NIFTY", "WEEK", -2, "PE", "2026-11-13", "2026-11-14")
    url, body, hdr = fake.calls[-1]
    assert url == "https://api.dhan.co/v2/charts/rollingoption" and body["strike"] == "ATM-2" and body["drvOptionType"] == "PUT"
    assert hdr["client-id"] == "1100123456" and arr["close"] == [11, 11.5] and len(fake.calls) == 3 and b.used() == 3
    with pytest.raises(BlockedCall):
        c.post("/orders", {})
    assert len(fake.calls) == 3                                           # the guard stops it before anything is sent


def test_a_refused_token_is_dropped_and_a_new_one_used_once(tmp_path):
    fake = DataFake(refuse=1)
    c, _, _ = client(tmp_path, fake)
    c.daily("NIFTY", "2026-11-02", "2026-11-04")
    assert c.keeper.dead == ["tok1"] and fake.calls[-1][2]["access-token"] == "tok2"
    c2, _, _ = client(tmp_path, DataFake(refuse=5))
    with pytest.raises(NoToken):
        c2.daily("NIFTY", "2026-11-02", "2026-11-04")


def test_budget_paces_calls_counts_per_ist_day_and_survives_a_restart(tmp_path):
    clock = Clock()
    b = Budget(tmp_path / "calls.json", rate=5.0, now=clock, sleep=clock.sleep)
    t0 = clock.t
    for _ in range(50):
        b.take()
    assert b.used() == 50 and clock.t - t0 == pytest.approx(49 * 0.2, abs=0.01)
    assert Budget(tmp_path / "calls.json", now=clock).used() == 50
    clock.t = dt.datetime(2026, 11, 15, 0, 1, tzinfo=IST).timestamp()
    assert b.used() == 0


# ------------------------------------------------------------------ the downloader
SPEC = {**json.loads(D.SPEC.read_text()), "start": "2026-09-01", "index_flag": {"NIFTY": "WEEK"}, "index_offsets": 1,
        "wide_offsets": 1, "narrow_offsets": 0, "wide_count": 1, "first": ["AAA"], "daily_cap": 100000, "min_free_gb": 0}
SCRIPS = ("SEM_EXM_EXCH_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES,SEM_SMST_SECURITY_ID,SEM_LOT_UNITS\n"
          "NSE,EQUITY,AAA,EQ,111,1\nNSE,OPTSTK,AAA-Nov2026-100-CE,,9001,500\nNSE,EQUITY,BBB,EQ,222,1\n"
          "NSE,OPTSTK,BBB-Nov2026-50-CE,,9002,1000\nNSE,EQUITY,CCC,EQ,333,1\n")


def legacy(tmp_path):
    lg = tmp_path / "legacy"
    (lg / "lab5y").mkdir(parents=True)
    oct_days = [d for d in range(1, 16) if dt.date(2026, 10, d).weekday() < 5 and d != 2]
    (lg / "lab5y" / "manifest.json").write_text(json.dumps({"AAA|2026-10": {"d": oct_days, "e": {}, "f": {}},
                                                            "AAA|2026-09": {"d": [], "e": {"16": "2026-09-30"}, "f": {}}}))
    (lg / "lab5y" / "universe.json").write_text(json.dumps({"turnover": {"AAA": 9.0, "BBB": 5.0}, "at": "2026-11-13"}))
    (lg / "history" / "NIFTY").mkdir(parents=True)
    (lg / "history" / "NIFTY" / "raw_2026-10-05.parquet").write_bytes(b"x")  # Upstox files cover NIFTY: no Dhan months
    return lg


def downloader(tmp_path, fake=None, spec=None, clock=None):
    lg = legacy(tmp_path) if not (tmp_path / "legacy").exists() else tmp_path / "legacy"
    c, b, clock = client(tmp_path, fake or DataFake(), clock)
    d5 = D.Dhan5y(tmp_path / "tanu", lg, c, b, spec=spec or SPEC, now=clock, sleep=clock.sleep,
                  fetch_scrips=lambda url, dest: dest.write_text(SCRIPS))
    return d5, c.transport, lg


def _tree(p):
    return {str(f.relative_to(p)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.rglob("*")) if f.is_file()}


def test_download_continues_from_the_legacy_progress_and_writes_only_its_own_folder(tmp_path):
    d5, fake, lg = downloader(tmp_path, DataFake(empty={("111", "2026-11-03")}))
    before = _tree(lg)
    assert d5.download() == "done"
    assert _tree(lg) == before                                            # the legacy folder is only read
    asked = {(b["securityId"], b["fromDate"]) for u, b, _ in fake.calls if u.endswith("rollingoption")}
    assert not any(s == "13" for s, _ in asked)                           # NIFTY months are covered by Upstox files
    aaa = {d for s, d in asked if s == "111"}
    assert "2026-10-05" not in aaa and "2026-09-16" not in aaa            # legacy days (and a final empty) skipped
    assert {"2026-10-16", "2026-11-13", "2026-09-01"} <= aaa and "2026-10-02" not in aaa and "2026-11-14" not in aaa
    f = tmp_path / "tanu" / "dhan5y" / "history" / "AAA" / "raw_2026-10.parquet"
    days = sorted(pd.to_datetime(pd.read_parquet(f)["ts"] + 19800, unit="s").dt.day.unique())
    assert days[0] == 16 and set(pd.read_parquet(f)["off"]) == {-1, 0, 1}
    man = json.loads((tmp_path / "tanu" / "dhan5y" / "manifest.json").read_text())
    assert man["AAA|2026-11"]["e"] == {"3": "2026-11-14"}                 # Dhan had no data: recorded, asked again later
    assert {s for s, _ in asked} == {"111", "222"}                      # BBB: narrow strikes; CCC has no options
    assert all(b["strike"] == "ATM" for u, b, _ in fake.calls if u.endswith("rollingoption") and b["securityId"] == "222")
    st = json.loads((tmp_path / "tanu" / "dhan5y" / "status.json").read_text())
    assert st["state"] == "done" and any("+" in line for line in st["log"])
    calls = len(fake.calls)
    assert d5.download() == "done" and all(not u.endswith("rollingoption") for u, _, _ in fake.calls[calls:])


def test_network_trouble_pauses_and_retries_the_same_day(tmp_path):
    d5, fake, _ = downloader(tmp_path, DataFake(fail={("111", "2026-11-13"): 15}))
    clock = d5.now
    t0 = clock.t
    assert d5.download() == "done"
    assert clock.t - t0 > 60                                               # it waited instead of skipping the day
    f = tmp_path / "tanu" / "dhan5y" / "history" / "AAA" / "raw_2026-11.parquet"
    assert 13 in set(pd.to_datetime(pd.read_parquet(f)["ts"] + 19800, unit="s").dt.day)


def test_daily_cap_and_pause_stop_the_download(tmp_path):
    d5, fake, _ = downloader(tmp_path, spec={**SPEC, "daily_cap": 40})
    assert d5.download() == "budget" and d5.budget.used() <= 40
    d5.set_paused(True)
    assert d5.paused() and d5.overview()["paused"]


def test_overview_counts_without_calling_dhan(tmp_path):
    d5, fake, _ = downloader(tmp_path)
    d5.download(should_stop=lambda: len(fake.calls) > 130)
    n = len(fake.calls)
    o = d5.overview()
    assert len(fake.calls) == n and o["symbols"] == 3 and 0 < o["pct"] < 100 and o["from_legacy"]
    assert o["days_missing"] > 0 and o["calls_left"] > 0 and o["state"] == "stopped"


@pytest.mark.parametrize("when,open_", [((2026, 11, 13, 10, 0), False), ((2026, 11, 13, 16, 30), True),
                                        ((2026, 11, 13, 8, 0), True), ((2026, 11, 14, 11, 0), True),
                                        ((2026, 11, 24, 11, 0), True), ((2026, 11, 16, 9, 0), False)])
def test_never_in_market_hours(when, open_):
    assert D.window_open(dt.datetime(*when, tzinfo=IST), json.loads(D.SPEC.read_text())) is open_


def test_the_thread_waits_in_market_hours_and_never_calls_dhan(tmp_path):
    clock = Clock(dt.datetime(2026, 11, 13, 11, 0, tzinfo=IST).timestamp())
    d5, fake, _ = downloader(tmp_path, clock=clock)
    stop = threading.Event()
    th = threading.Thread(target=D.run_forever, args=(d5, stop), kwargs={"idle_s": 0.01})
    th.start()
    for _ in range(200):
        if (tmp_path / "tanu" / "dhan5y" / "status.json").exists():
            break
        threading.Event().wait(0.01)
    stop.set()
    th.join(2)
    st = json.loads((tmp_path / "tanu" / "dhan5y" / "status.json").read_text())
    assert st["state"] == "waiting" and st["why"] == "market hours" and fake.calls == []


# ------------------------------------------------------------------ API
def test_dhan_routes_show_no_secrets_and_need_the_header(tmp_path):
    k, _ = keeper(tmp_path / "tanu")
    d5, _, _ = downloader(tmp_path)
    api = TestClient(create_app(tmp_path / "tanu", dhan_keeper=k, dhan5y=d5))
    w = {**HOST, "X-Tanu": "1"}
    assert api.post("/api/setup/dhan", json={"client_id": "1100123456", "pin": "123456", "totp_key": KEY}, headers=HOST).status_code == 403
    r = api.post("/api/setup/dhan", json={"client_id": "1100123456", "pin": "123456", "totp_key": KEY}, headers=w).json()
    assert r["saved"] and "after the switch-over" in r["message"] and not k.transport.urls     # no login before cutover
    assert api.post("/api/setup/dhan", json={"client_id": "1100123456", "pin": "1", "totp_key": KEY}, headers=w).status_code == 400
    g = api.get("/api/dhan", headers=HOST).json()
    assert g["login"]["auto_login"] and g["download"]["symbols"] == 3 and g["live"] is False
    assert "123456" not in json.dumps(g).replace("1100123456", "") and KEY not in json.dumps(g)
    assert api.post("/api/dhan/pause", json={"paused": True}, headers=w).json() == {"paused": True}
    assert api.get("/api/dhan", headers=HOST).json()["download"]["paused"] is True
    assert api.post("/api/setup/dhan", json={"forget": True}, headers=w).json()["saved"]
    assert not k.status()["auto_login"] and not k.status()["client_id_saved"]


# ------------------------------------------------------------------ no clash with the legacy app
def test_before_the_switch_over_tanu_starts_no_dhan_intel_or_note_threads(tmp_path, monkeypatch):
    """View mode (the legacy app still running): only the web app; no Dhan login or download, no background reads."""
    from tanu.core import config
    from tanu.live import app as live_app

    sv = live_app.Supervisor(config.Settings(data_dir=str(tmp_path / "tanu"), live=False))
    calls = []
    sv.dhan_keeper.transport = lambda url: calls.append(url) or (500, b"")
    sv.dhan5y.dhan.transport = lambda *a: calls.append(a) or (500, b"")

    class Server:
        should_exit = False

    monkeypatch.setattr(sv, "web", lambda port_check=True: Server())
    before = {t.name for t in threading.enumerate()}
    threading.Timer(0.3, sv.stop.set).start()
    assert sv.run() == 0 and sv.state["mode"] == "view"
    started = {t.name for t in threading.enumerate()} - before
    assert not started & {"tanu-dhan5y", "tanu-intel", "tanu-ai-notes"} and calls == []
    assert any("Cutover switch is off" in w for w in sv.state["why"])
