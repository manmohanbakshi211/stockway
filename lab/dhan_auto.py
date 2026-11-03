"""Automatic Dhan login with TOTP: the app makes a fresh access token by itself whenever the old one is gone.

Dhan's documented way (Authentication -> Generate Token, and "Setup TOTP": "your server can use [the secret] to
generate a fresh code every 30 seconds"): POST auth.dhan.co/app/generateAccessToken with client ID, PIN and the
current 6-digit TOTP code.

Safety, because a PIN + TOTP key can make a token for the whole Dhan account:
  - The PIN and the TOTP key live in the macOS Keychain (encrypted, unlocked with the Mac login), never in the
    settings file, the logs, the browser or git. Elsewhere (tests / Linux) a file readable only by the user.
  - They are sent only to auth.dhan.co, over HTTPS. Error messages never contain them.
  - A wrong PIN or key stops the automatic login at once (no retry loop that could lock the Dhan account) and says
    so in the app and on Telegram; it starts again only after you save the PIN / key again.
  - At most one automatic login every 5 minutes.
  - The token is used for market data only: the app has no order, position, funds or holdings calls (tests/test_safety.py).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path

import requests

AUTH = "https://auth.dhan.co/app/generateAccessToken"
SERVICE = "Stockway Dhan auto-login"
MIN_GAP_S = 300

_dir: Path | None = None
_last_try = 0.0


def setup(data_dir):
    global _dir
    _dir = Path(data_dir)


# ---------------------------------------------------------------- TOTP (RFC 6238: SHA-1, 30 s, 6 digits)
def clean_secret(secret: str) -> str:
    s = "".join(ch for ch in (secret or "").upper() if ch.isalnum())
    base64.b32decode(s + "=" * (-len(s) % 8))          # raises if it isn't a valid key
    return s


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    s = clean_secret(secret)
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    counter = int((at if at is not None else time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ---------------------------------------------------------------- where the PIN and key are kept
def _keyring():
    if sys.platform != "darwin":
        return None
    try:
        import keyring
        return keyring
    except Exception:
        return None


def _file() -> Path:
    return (_dir or Path("data")) / ".dhan_auto"


def store(pin: str, secret: str):
    kr = _keyring()
    if kr:
        kr.set_password(SERVICE, "pin", pin)
        kr.set_password(SERVICE, "totp_secret", secret)
        _file().unlink(missing_ok=True)
        return "macOS Keychain"
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"pin": pin, "secret": secret}, fh)
    os.chmod(f, 0o600)
    return "a private file on this computer"


def load() -> tuple | None:
    kr = _keyring()
    if kr:
        try:
            pin, sec = kr.get_password(SERVICE, "pin"), kr.get_password(SERVICE, "totp_secret")
            if pin and sec:
                return pin, sec
        except Exception:
            return None
    try:
        j = json.loads(_file().read_text())
        return (j["pin"], j["secret"]) if j.get("pin") and j.get("secret") else None
    except Exception:
        return None


def clear():
    kr = _keyring()
    if kr:
        for k in ("pin", "totp_secret"):
            try:
                kr.delete_password(SERVICE, k)
            except Exception:
                pass
    _file().unlink(missing_ok=True)
    _set_state({"on": False, "blocked": None})


# ---------------------------------------------------------------- state shown in the app (no secrets in it)
def _state_path() -> Path:
    return (_dir or Path("data")) / "dhan_auto_state.json"


def state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def _set_state(upd: dict):
    st = {**state(), **upd}
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(p)


def status() -> dict:
    st = state()
    return {"on": bool(load()), "blocked": st.get("blocked"), "last_login": st.get("last_login"),
            "last_error": st.get("last_error"), "where": st.get("where")}


# ---------------------------------------------------------------- making a token
class AuthRefused(Exception):
    """Dhan said the PIN / TOTP / client ID is wrong: don't retry by itself."""


def generate(client_id: str, pin: str, secret: str, timeout: int = 30) -> dict:
    code = totp(secret)
    try:
        r = requests.post(AUTH, params={"dhanClientId": str(client_id), "pin": pin, "totp": code}, timeout=timeout)
    except requests.RequestException as e:                 # never echo the URL: it carries the PIN
        raise RuntimeError(f"Dhan login service not reachable ({type(e).__name__})") from None
    try:
        j = r.json()
    except ValueError:
        j = {}
    tok = j.get("accessToken") or (j.get("data") or {}).get("accessToken") if isinstance(j, dict) else None
    if tok:
        return {"token": tok, "expiry": j.get("expiryTime"), "name": j.get("dhanClientName")}
    msg = str(j.get("errorMessage") or j.get("message") or j.get("remarks") or f"HTTP {r.status_code}")[:160]
    msg = msg.replace(pin, "****") if pin else msg
    if 400 <= r.status_code < 500 and r.status_code != 429:
        raise AuthRefused(msg)
    raise RuntimeError(f"Dhan login failed: {msg}")


def login(client_id: str, save_token, log=print, force=False, notify=None) -> str | None:
    """Make a new token with the saved PIN + TOTP key. 'ok', 'off', 'blocked', 'wait' or 'failed'."""
    global _last_try
    creds = load()
    if not creds or not client_id:
        return "off"
    st = state()
    if st.get("blocked") and not force:
        return "blocked"
    now = time.time()
    if not force and now - _last_try < MIN_GAP_S:
        return "wait"
    _last_try = now
    pin, secret = creds
    try:
        res = generate(client_id, pin, secret)
    except AuthRefused as e:
        _set_state({"blocked": dt.datetime.now().strftime("%d %b %H:%M"), "last_error": f"Dhan refused the PIN / TOTP key: {e}"})
        log(f"Dhan automatic login stopped: Dhan refused the PIN / TOTP key ({e}). Save them again in Strategy desk → Dhan keys.")
        if notify:
            notify("Stockway: Dhan automatic login stopped because Dhan refused the PIN / TOTP key. Check them in "
                   "Strategy desk → Dhan keys and save again (the app won't retry by itself, so your account isn't locked).")
        return "blocked"
    except Exception as e:
        _set_state({"last_error": str(e)[:200]})
        log(f"Dhan automatic login failed, will retry: {e}")
        return "failed"
    save_token(res["token"])
    import hashlib
    _set_state({"blocked": None, "last_error": None, "last_login": dt.datetime.now().strftime("%d %b %H:%M"),
                "made": hashlib.sha256(res["token"].encode()).hexdigest()[:16], "made_ts": time.time()})
    log("Dhan: new token made automatically (TOTP)")
    return "ok"
