"""Dhan token auto-renew: renews only when close to expiry, saves the new token, running clients switch to it."""
import base64
import json
import time

import pytest

from lab import dhan, dhan_token as T


def jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS512'})}.{enc({'exp': int(exp), 'dhanClientId': '1'})}.sig"


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, json.dumps(body)

    def json(self):
        return self._b


@pytest.fixture
def box(tmp_path, monkeypatch):
    saved = {"cid": "1100", "tok": None}
    T.setup(tmp_path, lambda: (saved["cid"], saved["tok"]), lambda t: saved.__setitem__("tok", t))
    monkeypatch.setattr(T, "_last_check", 0.0)
    yield saved
    T.provider = T.saver = None
    from lab import dhan_auto
    dhan_auto.setup(tmp_path / "gone")


def test_expiry_read_from_token():
    assert abs(T.expiry(jwt(1_900_000_000)) - 1_900_000_000) < 1
    assert T.expiry("not-a-jwt") is None


def test_no_renew_when_plenty_left(box, monkeypatch):
    now = time.time()
    box["tok"] = jwt(now + 20 * 3600)
    monkeypatch.setattr(T.requests, "get", lambda *a, **k: pytest.fail("should not call Dhan"))
    assert T.maybe_renew(now=now) is None


def test_renews_and_saves_when_close(box, monkeypatch):
    now = time.time()
    old, new = jwt(now + 2 * 3600), jwt(now + 26 * 3600)
    box["tok"] = old
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers)
        return Resp(200, {"accessToken": new, "expiryTime": "2026-11-02T01:00:00"})
    monkeypatch.setattr(T.requests, "get", fake_get)
    assert T.maybe_renew(now=now) == "renewed"
    assert seen["url"].endswith("/RenewToken") and seen["headers"]["access-token"] == old
    assert box["tok"] == new
    assert T.status()["hours_left"] > 25 and T.status()["error"] is None


def test_expired_token_not_renewed(box, monkeypatch):
    now = time.time()
    box["tok"] = jwt(now - 60)
    monkeypatch.setattr(T.requests, "get", lambda *a, **k: pytest.fail("expired tokens can't be renewed"))
    assert T.maybe_renew(now=now) == "expired"
    assert "web.dhan.co" in T.status()["error"]


def test_running_client_switches_to_renewed_token(box, monkeypatch):
    box["tok"] = "NEW"
    d = dhan.Dhan("1100", "OLD", per_sec=1000)
    calls = []

    def fake_post(url, json, timeout):
        calls.append(d.s.headers["access-token"])
        return Resp(401, {}) if d.s.headers["access-token"] == "OLD" else Resp(200, {"data": {"ce": {"open": [1]}}})
    monkeypatch.setattr(d.s, "post", fake_post)
    assert d.post("/charts/rollingoption", {}) == {"data": {"ce": {"open": [1]}}}
    assert calls == ["OLD", "NEW"]


def test_dead_token_still_raises(box, monkeypatch):
    box["tok"] = "OLD"
    d = dhan.Dhan("1100", "OLD", per_sec=1000)
    monkeypatch.setattr(d.s, "post", lambda url, json, timeout: Resp(401, {}))
    with pytest.raises(PermissionError):
        d.post("/charts/rollingoption", {})


def test_api_key_login_flow(box, monkeypatch, tmp_path):
    """Log in with Dhan: consent session -> Dhan's login page -> tokenId -> access token saved (no password in the app)."""
    sent = []

    def fake_post(url, params=None, headers=None, timeout=None):
        sent.append((url, params, headers))
        if url.endswith("/app/generate-consent"):
            return Resp(200, {"consentAppId": "abc123", "consentAppStatus": "GENERATED", "status": "success"})
        if url.endswith("/app/consumeApp-consent"):
            assert params == {"tokenId": "tid9"}
            return Resp(200, {"accessToken": jwt(time.time() + 86400), "dhanClientId": "1100", "expiryTime": "x"})
        raise AssertionError(url)
    monkeypatch.setattr(T.requests, "post", fake_post)
    url = T.consent_url("1100", "KEY", "SECRET")
    assert url == "https://auth.dhan.co/login/consentApp-login?consentAppId=abc123"
    assert sent[0][1] == {"client_id": "1100"} and sent[0][2] == {"app_id": "KEY", "app_secret": "SECRET"}
    res = T.consume("tid9", "KEY", "SECRET")
    assert T.expiry(res["token"]) > time.time() + 80000 and res["client_id"] == "1100"


def test_dead_token_is_quiet_until_a_new_one(box):
    box["tok"] = jwt(time.time() + 3600)
    T.mark_dead()
    assert T.is_dead() and T.status()["dead"]
    box["tok"] = jwt(time.time() + 7200)                   # a new token pasted / logged in
    assert not T.is_dead() and not T.status()["dead"]


def test_totp_matches_rfc6238():
    from lab import dhan_auto as A
    secret = base64.b32encode(b"12345678901234567890").decode()      # RFC 6238 test key (SHA-1)
    assert A.totp(secret, at=59, digits=8) == "94287082"
    assert A.totp(secret, at=1111111109, digits=8) == "07081804"
    assert A.totp(secret.lower()[:8] + " " + secret.lower()[8:], at=59, digits=8) == "94287082"   # spaces / case ok


def test_auto_login_replaces_a_dead_token(box, monkeypatch, tmp_path):
    from lab import dhan_auto as A
    monkeypatch.setattr(A, "_last_try", 0.0)
    A.store("1234", base64.b32encode(b"12345678901234567890").decode())
    assert oct((tmp_path / ".dhan_auto").stat().st_mode)[-3:] == "600"
    new = jwt(time.time() + 86400)
    seen = {}

    def fake_post(url, params=None, timeout=None):
        seen.update(url=url, params=params)
        return Resp(200, {"accessToken": new, "expiryTime": "x"})
    monkeypatch.setattr(A.requests, "post", fake_post)
    box["tok"] = jwt(time.time() - 60)                                 # Mac was off: token expired
    assert T.maybe_renew() == "auto"
    assert box["tok"] == new and seen["url"].endswith("/app/generateAccessToken")
    assert seen["params"]["pin"] == "1234" and len(seen["params"]["totp"]) == 6
    assert "1234" not in (tmp_path / "dhan_auto_state.json").read_text()


def test_wrong_pin_stops_at_once(box, monkeypatch, tmp_path):
    from lab import dhan_auto as A
    monkeypatch.setattr(A, "_last_try", 0.0)
    A.store("9999", base64.b32encode(b"12345678901234567890").decode())
    calls = []

    def fake_post(url, params=None, timeout=None):
        calls.append(1)
        return Resp(400, {"errorMessage": "Invalid pin 9999"})
    monkeypatch.setattr(A.requests, "post", fake_post)
    box["tok"] = None
    assert T.maybe_renew() == "expired"
    monkeypatch.setattr(A, "_last_try", 0.0)
    assert T.maybe_renew() == "expired" and len(calls) == 1             # blocked: no second try, account never locked
    st = A.status()
    assert st["blocked"] and "9999" not in (st["last_error"] or "")


def test_network_error_never_shows_the_pin(monkeypatch):
    from lab import dhan_auto as A

    def boom(url, params=None, timeout=None):
        raise A.requests.ConnectionError(f"Max retries exceeded with url: {url}?pin={params['pin']}")
    monkeypatch.setattr(A.requests, "post", boom)
    with pytest.raises(RuntimeError) as e:
        A.generate("1100", "4321", base64.b32encode(b"12345678901234567890").decode())
    assert "4321" not in str(e.value)


def test_no_token_churn_when_dhan_refuses_a_fresh_token(box, monkeypatch, tmp_path):
    """If Dhan refuses a token TOTP made minutes ago (e.g. the Data API plan lapsed), don't make token after token."""
    from lab import dhan_auto as A
    monkeypatch.setattr(A, "_last_try", 0.0)
    A.store("1234", base64.b32encode(b"12345678901234567890").decode())
    made = []

    def fake_post(url, params=None, timeout=None):
        made.append(jwt(time.time() + 86400 + len(made)))
        return Resp(200, {"accessToken": made[-1]})
    monkeypatch.setattr(A.requests, "post", fake_post)
    box["tok"] = None
    assert T.maybe_renew() == "auto" and len(made) == 1
    T.mark_dead(box["tok"])                                    # Dhan refuses the brand-new token
    monkeypatch.setattr(A, "_last_try", 0.0)
    assert T.maybe_renew() == "expired" and len(made) == 1     # no second login
    assert "Data API" in A.status()["last_error"]
