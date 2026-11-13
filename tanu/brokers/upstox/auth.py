"""Tanu's Upstox token keeper (ADR-0009). Market data only (ADR-0001): this logs in for the data feed and candles.

- The Upstox app's API key and secret are typed by Mandeep into Tanu's System page (saved as secret `upstox_app`).
- Daily login: the System page's "Log in with Upstox" opens Upstox's own login page; Upstox sends the browser back to
  the app's registered redirect URL with a one-time code; Tanu exchanges the code for the day's access token
  (secret `upstox_token`). Tokens end at 03:30 IST the next morning (Upstox rule).
- Never active before cutover: the login route answers only when the live switch is on (ADR-0007).
The redirect URL registered in the Upstox app is http://127.0.0.1:8765/upstox/callback (the legacy app's), so at
cutover Tanu's web app moves to port 8765 and nothing changes on Upstox's side.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets as _rand
import urllib.error
import urllib.parse
import urllib.request

from ...core import log, safety
from ...core.secrets import Secrets

API = "https://api.upstox.com"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("upstox_auth")


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/upstox/callback"


def login_url(api_key: str, redirect: str, state: str) -> str:
    q = urllib.parse.urlencode({"response_type": "code", "client_id": api_key, "redirect_uri": redirect, "state": state})
    return safety.check_url(f"{API}/v2/login/authorization/dialog?{q}")


def _post_form(url: str, data: dict, timeout: float = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def exchange_code(code: str, api_key: str, api_secret: str, redirect: str, transport=_post_form) -> dict:
    url = safety.check_url(f"{API}/v2/login/authorization/token")
    status, body = transport(url, {"code": code, "client_id": api_key, "client_secret": api_secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"})
    try:
        data = json.loads(body or b"{}")
    except Exception:
        data = {}
    if status != 200 or "access_token" not in data:
        msg = (data.get("errors") or [{}])[0].get("message") if isinstance(data.get("errors"), list) else None
        raise PermissionError(f"Upstox login failed (HTTP {status}){': ' + msg if msg else ''}")
    return data


def token_expiry(now: dt.datetime | None = None) -> dt.datetime:
    """Upstox tokens end at 03:30 IST the next morning (or this morning, if logged in before 03:30)."""
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    end = now.replace(hour=3, minute=30, second=0, microsecond=0)
    return end if now < end else end + dt.timedelta(days=1)


class TokenKeeper:
    def __init__(self, secrets: Secrets, clock=lambda: dt.datetime.now(IST)):
        self.s, self.clock = secrets, clock
        self._state: str | None = None

    # the app's key / secret (typed in by Mandeep)
    def save_app(self, api_key: str, api_secret: str) -> None:
        api_key, api_secret = (api_key or "").strip(), (api_secret or "").strip()
        if len(api_key) < 8 or len(api_secret) < 8:
            raise ValueError("API key and secret look too short. Copy them again from the Upstox developer page.")
        self.s.save("upstox_app", {"api_key": api_key, "api_secret": api_secret})
        _log.info("upstox_app_saved")

    def app(self) -> dict | None:
        return self.s.load("upstox_app")

    # daily login
    def start_login(self, port: int) -> str:
        app = self.app()
        if not app:
            raise PermissionError("Save the Upstox API key and secret first (System page).")
        self._state = _rand.token_urlsafe(16)
        return login_url(app["api_key"], redirect_uri(port), self._state)

    def finish_login(self, code: str, state: str, port: int, transport=_post_form) -> dict:
        if not self._state or state != self._state:
            raise PermissionError("This login link is old or did not start here. Click Log in with Upstox again.")
        self._state = None
        app = self.app() or {}
        body = exchange_code(code, app.get("api_key", ""), app.get("api_secret", ""), redirect_uri(port), transport)
        rec = {"access_token": body["access_token"], "user": body.get("user_name") or body.get("user_id"),
               "saved_at": self.clock().isoformat(), "expires_at": token_expiry(self.clock()).isoformat()}
        self.s.save("upstox_token", rec)
        _log.info("upstox_logged_in", expires_at=rec["expires_at"])
        return self.status()

    def token(self) -> str | None:
        rec = self.s.load("upstox_token")
        if not rec:
            return None
        if dt.datetime.fromisoformat(rec["expires_at"]) <= self.clock():
            return None
        return rec.get("access_token")

    def status(self) -> dict:
        rec = self.s.load("upstox_token") or {}
        valid = bool(self.token())
        return {"app_saved": bool(self.app()), "logged_in": valid, "user": rec.get("user") if valid else None,
                "expires_at": rec.get("expires_at") if valid else None}

    def logout(self) -> None:
        self.s.delete("upstox_token")
