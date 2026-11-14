"""Tanu's Dhan token keeper (ADR-0014). Market data only (ADR-0001).

Dhan's documented automatic login: POST auth.dhan.co/app/generateAccessToken with the client ID, the PIN and the
current 6-digit TOTP code (Dhan "Setup TOTP": a server may use the TOTP key to make a fresh code every 30 seconds).
Tokens last about a day; Tanu makes a new one when the old one is older than 20 hours or Dhan refuses it.

Safety, because a PIN + TOTP key can make a token for the whole Dhan account:
  - The PIN and the TOTP key are typed by Mandeep into the System page and kept in the macOS Keychain (encrypted,
    unlocked with the Mac login). Elsewhere (tests, Linux) in Tanu's secrets folder (0700 / 0600). Never in a log,
    the browser, git or chat; the app only says whether they are saved.
  - They are sent only to auth.dhan.co over HTTPS; errors never contain them (the URL carrying them is never logged).
  - A refusal (wrong PIN / key / client ID) stops automatic login at once (no retry loop that could lock the account)
    until they are saved again. At most one login every 5 minutes.
  - Used only after the switch-over (the supervisor's gates), when the legacy app and its own Dhan login are closed.
  - The token is used for historical market data only: every URL passes safety.check_url.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...core import log, safety
from ...core.secrets import Secrets

AUTH = "https://auth.dhan.co/app/generateAccessToken"
SERVICE = "Tanu Dhan login"
MIN_GAP_S = 300
MAX_AGE_H = 20.0
_log = log.get("dhan_auth")


class AuthRefused(Exception):
    """Dhan said the PIN, TOTP key or client ID is wrong: never retried by itself."""


# ---------------------------------------------------------------- TOTP (RFC 6238: SHA-1, 30 s steps)
def clean_secret(secret: str) -> str:
    s = "".join(ch for ch in (secret or "").upper() if ch.isalnum())
    if len(s) < 16:
        raise ValueError("The TOTP key looks too short (Dhan shows it as a long code when you set up TOTP).")
    try:
        base64.b32decode(s + "=" * (-len(s) % 8))
    except Exception:
        raise ValueError("That is not a TOTP key (letters A-Z and digits 2-7).") from None
    return s


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    s = clean_secret(secret)
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    counter = int((time.time() if at is None else at) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)).zfill(digits)


# ---------------------------------------------------------------- where the PIN and TOTP key live
class Vault:
    """macOS Keychain through the keyring package when available, else Tanu's secrets folder (0600)."""

    def __init__(self, secrets: Secrets, keychain=None):
        self.secrets = secrets
        self.kr = keychain if keychain is not None else _keyring()

    @property
    def where(self) -> str:
        return "macOS Keychain" if self.kr else "Tanu's private secrets folder"

    def save(self, pin: str, secret: str) -> str:
        if self.kr:
            self.kr.set_password(SERVICE, "pin", pin)
            self.kr.set_password(SERVICE, "totp_key", secret)
            self.secrets.delete("dhan_login")
        else:
            self.secrets.save("dhan_login", {"pin": pin, "totp_key": secret})
        return self.where

    def load(self) -> tuple[str, str] | None:
        if self.kr:
            try:
                pin, sec = self.kr.get_password(SERVICE, "pin"), self.kr.get_password(SERVICE, "totp_key")
                return (pin, sec) if pin and sec else None
            except Exception:
                return None
        v = self.secrets.load("dhan_login") or {}
        return (v["pin"], v["totp_key"]) if v.get("pin") and v.get("totp_key") else None

    def clear(self) -> None:
        if self.kr:
            for k in ("pin", "totp_key"):
                try:
                    self.kr.delete_password(SERVICE, k)
                except Exception:
                    pass
        self.secrets.delete("dhan_login")


def _keyring():
    if sys.platform != "darwin":
        return None
    try:
        import keyring
        return keyring
    except Exception:
        return None


def _post(url: str, timeout: float = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(safety.check_url(url), data=b"", method="POST", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _fp(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:16] if token else ""


class DhanKeeper:
    def __init__(self, base: Path | str, secrets: Secrets | None = None, vault: Vault | None = None, transport=_post,
                 now=time.time):
        self.base = Path(base)
        self.secrets = secrets or Secrets(self.base)
        self.vault = vault or Vault(self.secrets)
        self.transport, self.now = transport, now
        self.state_file = self.base / "dhan" / "login_state.json"
        self.last_try = 0.0

    # -------------------------------------------------------------- state (no secrets in it)
    def state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {}

    def _set(self, **upd) -> None:
        st = {**self.state(), **upd}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(self.state_file)

    def client_id(self) -> str | None:
        return (self.secrets.load("dhan") or {}).get("client_id")

    # -------------------------------------------------------------- setup from the System page
    def save_login(self, client_id: str, pin: str, totp_key: str) -> str:
        cid = str(client_id or "").strip()
        if not cid.isdigit() or not 5 <= len(cid) <= 15:
            raise ValueError("The Dhan client ID is the number shown on your Dhan profile.")
        pin = str(pin or "").strip()
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValueError("The Dhan PIN is the short number you use to log in.")
        where = self.vault.save(pin, clean_secret(totp_key))
        self.secrets.save("dhan", {"client_id": cid})
        self._set(blocked=None, last_error=None, where=where)
        return where

    def save_token(self, client_id: str, token: str) -> None:
        """Alternative: a token made on web.dhan.co (lasts about a day), for when automatic login is not set up."""
        cid, tok = str(client_id or "").strip(), str(token or "").strip()
        if not cid.isdigit() or len(tok) < 40 or len(tok) > 4000:
            raise ValueError("Paste the client ID and the whole access token from web.dhan.co.")
        self.secrets.save("dhan", {"client_id": cid})
        self.secrets.save("dhan_token", {"token": tok, "made": self.now(), "how": "pasted"})
        self._set(dead=None, last_error=None)

    def clear(self) -> None:
        self.vault.clear()
        for n in ("dhan", "dhan_token"):
            self.secrets.delete(n)
        self._set(blocked=None, last_error=None, dead=None, last_login=None)

    # -------------------------------------------------------------- the token
    def token(self) -> str | None:
        """A usable token: the saved one while it is young and not refused, else a fresh automatic login."""
        t = self.secrets.load("dhan_token") or {}
        tok = t.get("token")
        fresh = tok and self.now() - float(t.get("made") or 0) < MAX_AGE_H * 3600 and self.state().get("dead") != _fp(tok)
        if fresh:
            return tok
        if self.login() == "ok":
            return (self.secrets.load("dhan_token") or {}).get("token")
        return None

    def refused(self, token: str) -> None:
        """Dhan answered 401 for this token: never use it again; the next token() makes a new one."""
        self._set(dead=_fp(token))
        _log.warning("dhan_token_refused")

    def login(self, force: bool = False) -> str:
        """'ok', 'off' (not set up), 'blocked' (Dhan refused the PIN / key earlier), 'wait' or 'failed'."""
        creds, cid = self.vault.load(), self.client_id()
        if not creds or not cid:
            return "off"
        if self.state().get("blocked") and not force:
            return "blocked"
        if not force and self.now() - self.last_try < MIN_GAP_S:
            return "wait"
        self.last_try = self.now()
        pin, key = creds
        url = AUTH + "?" + urllib.parse.urlencode({"dhanClientId": cid, "pin": pin, "totp": totp(key, self.now())})
        try:
            code, raw = self.transport(url)
        except Exception as e:                            # never echo the URL: it carries the PIN
            self._set(last_error=f"Dhan login service not reachable ({type(e).__name__})")
            return "failed"
        try:
            j = json.loads(raw or b"{}")
        except ValueError:
            j = {}
        tok = (j.get("accessToken") or (j.get("data") or {}).get("accessToken")) if isinstance(j, dict) else None
        if tok:
            self.secrets.save("dhan_token", {"token": tok, "made": self.now(), "how": "totp", "expiry": j.get("expiryTime")})
            self._set(blocked=None, last_error=None, dead=None,
                      last_login=dt.datetime.fromtimestamp(self.now()).strftime("%d %b %H:%M"))
            _log.info("dhan_login_ok")
            return "ok"
        msg = str(j.get("errorMessage") or j.get("message") or j.get("remarks") or f"HTTP {code}")[:160].replace(pin, "****")
        if 400 <= code < 500 and code != 429:
            self._set(blocked=dt.datetime.fromtimestamp(self.now()).strftime("%d %b %H:%M"),
                      last_error=f"Dhan refused the PIN / TOTP key / client ID: {msg}")
            _log.warning("dhan_login_refused", http=code)
            return "blocked"
        self._set(last_error=f"Dhan login failed: {msg}")
        _log.warning("dhan_login_failed", http=code)
        return "failed"

    def status(self) -> dict:
        st, t = self.state(), self.secrets.load("dhan_token") or {}
        age = (self.now() - float(t["made"])) / 3600 if t.get("made") else None
        return {"client_id_saved": bool(self.client_id()), "auto_login": bool(self.vault.load()), "where": self.vault.where,
                "token_saved": bool(t.get("token")), "token_age_h": round(age, 1) if age is not None else None,
                "token_refused": bool(t.get("token")) and st.get("dead") == _fp(t.get("token")),
                "blocked": st.get("blocked"), "last_login": st.get("last_login"), "last_error": st.get("last_error")}
