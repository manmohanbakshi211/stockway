"""Keeps the Dhan access token alive: renews it before it expires (Dhan's RenewToken API), saves the new one, and lets
every running Dhan connection pick it up.

Two ways to get a token:
  1. Paste one made on web.dhan.co (24 hours), or
  2. "Log in with Dhan" (API key + secret, valid 12 months, made once on web.dhan.co): the app asks Dhan for a consent
     session, you log in on Dhan's own page (your password / OTP / PIN go to Dhan only, the app never sees them),
     Dhan sends the app a one-time tokenId, and the app swaps it for an access token. One click a day at most.

Dhan Web tokens last 24 hours. GET /v2/RenewToken with the current, still-active token returns a NEW token with
another 24 hours (Dhan docs: Authentication -> Access Token -> Renew Token). An expired token can't be renewed: then
a new one has to be made on web.dhan.co once, and from there it renews by itself again.

No PIN, no TOTP, no password is stored or used. Only the market-data token the user pasted. Nothing here orders
anything or reads the trading account.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import threading
import time
from pathlib import Path

import requests

API = "https://api.dhan.co/v2"
RENEW_BEFORE_H = 3.0          # renew when less than this many hours are left
CHECK_EVERY_S = 600           # look at the token every 10 minutes

provider = None               # set by the app: () -> (client_id, token) as saved in the settings
saver = None                  # set by the app: (new_token) -> None, saves it in the settings
_state_file: Path | None = None
_last_check = 0.0


on_dead = None                # set by the app: (message) -> None, e.g. a Telegram note (sent once per dead token)


def mark_dead(token: str | None = None):
    """Dhan refused this token: remember it, so nothing keeps calling Dhan with it (no log spam), until a new one is pasted."""
    tok = token or latest()
    if not tok:
        return
    st = state()
    if st.get("dead") == _id(tok):
        return
    st.update({"dead": _id(tok), "for": _id(tok), "dead_at": dt.datetime.now().strftime("%d %b %H:%M"),
               "error": "Dhan rejected the token (expired). Make a new one on web.dhan.co and paste it in Strategy desk -> Dhan keys: everything resumes by itself."})
    _write(st)
    from . import dhan_auto
    if dhan_auto.load() and not dhan_auto.state().get("blocked"):
        return                                        # TOTP auto-login will replace it in a moment: no alarm
    if on_dead:
        try:
            on_dead("Stockway: the Dhan token has expired. Make a new one on web.dhan.co (Profile -> DhanHQ Trading APIs) "
                    "and paste it in Strategy desk -> Dhan keys. The 5-year download, news and movers resume by themselves.")
        except Exception:
            pass


def is_dead(token: str | None = None) -> bool:
    tok = token or latest()
    return bool(tok) and state().get("dead") == _id(tok)


def _id(token: str | None) -> str:
    """A short fingerprint of a token (the token itself is never written to the state file)."""
    import hashlib
    return hashlib.sha256((token or "").encode()).hexdigest()[:16] if token else ""


def latest() -> str | None:
    """The token currently saved in the app (after a renew or a paste), or None if unknown."""
    try:
        return provider()[1] if provider else None
    except Exception:
        return None


def expiry(token: str | None) -> float | None:
    """Expiry (unix seconds) read from the token itself (a JWT carries 'exp'); None if it can't be read."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        exp = json.loads(base64.urlsafe_b64decode(part)).get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def renew(client_id: str, token: str, timeout: int = 30) -> dict:
    """Ask Dhan for a new 24-hour token. Returns {'token', 'expiry'}; PermissionError if the token is already dead."""
    r = requests.get(API + "/RenewToken", headers={"access-token": token, "dhanClientId": str(client_id),
                                                   "Accept": "application/json"}, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code == 401 or str(j.get("errorCode", "")).startswith(("DH-901", "DH-902")):
        raise PermissionError("Dhan token has expired: make a new one on web.dhan.co once; after that it renews by itself.")
    if r.status_code >= 400 or not j.get("accessToken"):
        raise RuntimeError(f"Dhan renew failed: {j.get('errorMessage') or j.get('remarks') or r.text[:200]}")
    return {"token": j["accessToken"], "expiry": j.get("expiryTime")}


def state() -> dict:
    try:
        return json.loads(_state_file.read_text()) if _state_file and _state_file.exists() else {}
    except Exception:
        return {}


def _write(st: dict):
    if _state_file:
        tmp = _state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(_state_file)


def status() -> dict:
    """For the app: when the token expires, when it was last renewed, and any problem."""
    from . import dhan_auto
    st = state()
    tok = latest()
    exp = expiry(tok) or st.get("exp")
    left = (exp - time.time()) / 3600 if exp else None
    return {"expires": dt.datetime.fromtimestamp(exp).strftime("%d %b %H:%M") if exp else None,
            "hours_left": round(left, 1) if left is not None else None, "renewed": st.get("renewed"),
            "error": st.get("error") if st.get("for") == _id(tok) else None,
            "dead": bool(tok) and st.get("dead") == _id(tok), "auto": dhan_auto.status()}


REFUSED_FRESH_WAIT_S = 3600   # Dhan refused a token TOTP made minutes ago: the token isn't the problem; wait an hour


def _auto(cid, log, tok=None) -> str | None:
    """TOTP automatic login, if it is set up (lab/dhan_auto.py). 'auto' when a new token was made.
    If Dhan refuses a token that TOTP made less than an hour ago, the problem is elsewhere (e.g. the Data API plan
    lapsed): don't make token after token, say so once and try again after an hour."""
    from . import dhan_auto
    if not saver:
        return None
    st = dhan_auto.state()
    if tok and st.get("made") == _id(tok) and time.time() - (st.get("made_ts") or 0) < REFUSED_FRESH_WAIT_S:
        msg = ("Dhan refused a token it made minutes ago: check that the Data API (Pro) plan is active on web.dhan.co. "
               "The app tries again in an hour.")
        if st.get("last_error") != msg:
            dhan_auto._set_state({"last_error": msg})
            log(msg)
        return None
    r = dhan_auto.login(cid, saver, log=log, notify=on_dead)
    return "auto" if r == "ok" else None


LOCK = threading.RLock()       # one token change at a time: scheduler, Renew now, TOTP turn-on, Log in with Dhan


def maybe_renew(now: float | None = None, force: bool = False, log=print) -> str | None:
    with LOCK:
        return _maybe_renew(now, force, log)


def _maybe_renew(now: float | None = None, force: bool = False, log=print) -> str | None:
    """Renew if the saved token has less than RENEW_BEFORE_H hours left; if it is gone (expired / refused / missing) and
    TOTP auto-login is on, make a new one. Returns 'renewed', 'auto', 'expired', 'failed' or None."""
    global _last_check
    now = now or time.time()
    if not provider:
        return None
    cid, tok = provider()
    gone = cid and (not tok or is_dead(tok) or ((expiry(tok) or now + 1) <= now))
    if gone:                                          # no throttle here: dhan_auto keeps its own 5-minute gap
        r = _auto(cid, log, tok)
        if r:
            return r
        if tok and not is_dead(tok):
            mark_dead(tok)
        return "expired"
    if not force and now - _last_check < CHECK_EVERY_S:
        return None
    _last_check = now
    if not (cid and tok):
        return None
    st = state()
    exp = expiry(tok)
    if exp is None:                                   # not a readable token: renew once a day
        last = st.get("renewed_ts") if st.get("for") == _id(tok) or st.get("new") == _id(tok) else None
        exp = (last or now) + 24 * 3600 if last else None
        if exp is None:
            st.update({"for": _id(tok), "renewed_ts": now})
            _write(st)
            return None
    if not force and exp - now > RENEW_BEFORE_H * 3600:
        return None
    if exp <= now:
        mark_dead(tok)
        return "expired"
    try:
        res = renew(cid, tok)
    except PermissionError as e:
        mark_dead(tok)
        r = _auto(cid, log, tok)
        if r:
            return r
        mark_dead(tok)
        log(str(e))
        return "expired"
    except Exception as e:
        st.update({"for": _id(tok), "error": str(e)[:200]})
        _write(st)
        log(str(e))
        if exp - now < 3600:                          # renew keeps failing and the token is nearly gone
            r = _auto(cid, log, tok)
            if r:
                return r
        return "failed"
    if saver:
        saver(res["token"])
    new_exp = expiry(res["token"])
    _write({"for": _id(res["token"]), "new": _id(res["token"]), "renewed": dt.datetime.fromtimestamp(now).strftime("%d %b %H:%M"),
            "renewed_ts": now, "exp": new_exp, "dhan_expiry": res.get("expiry"), "error": None})
    log(f"Dhan token renewed (valid till {dt.datetime.fromtimestamp(new_exp).strftime('%d %b %H:%M') if new_exp else res.get('expiry')})")
    return "renewed"


def setup(data_dir, get_saved, save_token):
    """Called once by the app at startup."""
    global provider, saver, _state_file
    provider, saver = get_saved, save_token
    _state_file = Path(data_dir) / "dhan_token_state.json"
    from . import dhan_auto
    dhan_auto.setup(data_dir)


AUTH = "https://auth.dhan.co"
CALLBACK = "http://127.0.0.1:8765/dhan/callback"


def consent_url(client_id: str, api_key: str, api_secret: str, timeout: int = 30) -> str:
    """Step 1 of the API-key login: a consent session at Dhan; returns the Dhan login page to open in the browser."""
    r = requests.post(f"{AUTH}/app/generate-consent", params={"client_id": str(client_id)},
                      headers={"app_id": api_key, "app_secret": api_secret}, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        j = {}
    cid = j.get("consentAppId")
    if r.status_code >= 400 or not cid:
        raise RuntimeError(f"Dhan did not accept the API key / secret: {j.get('errorMessage') or j.get('message') or r.text[:200]}")
    return f"{AUTH}/login/consentApp-login?consentAppId={cid}"


def consume(token_id: str, api_key: str, api_secret: str, timeout: int = 30) -> dict:
    """Step 3: swap the one-time tokenId (from Dhan's redirect) for an access token."""
    r = requests.post(f"{AUTH}/app/consumeApp-consent", params={"tokenId": token_id},
                      headers={"app_id": api_key, "app_secret": api_secret}, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code >= 400 or not j.get("accessToken"):
        raise RuntimeError(f"Dhan login failed: {j.get('errorMessage') or j.get('message') or r.text[:200]}")
    return {"token": j["accessToken"], "client_id": j.get("dhanClientId"), "name": j.get("dhanClientName"),
            "expiry": j.get("expiryTime")}
