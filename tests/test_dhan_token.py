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
