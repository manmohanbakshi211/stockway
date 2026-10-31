"""Keeps the Dhan access token alive: renews it before it expires (Dhan's RenewToken API), saves the new one, and lets
every running Dhan connection pick it up.

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
    st = state()
    tok = latest()
    exp = expiry(tok) or st.get("exp")
    left = (exp - time.time()) / 3600 if exp else None
    return {"expires": dt.datetime.fromtimestamp(exp).strftime("%d %b %H:%M") if exp else None,
            "hours_left": round(left, 1) if left is not None else None, "renewed": st.get("renewed"),
            "error": st.get("error") if st.get("for") == (tok or "")[-12:] else None}


def maybe_renew(now: float | None = None, force: bool = False, log=print) -> str | None:
    """Renew if the saved token has less than RENEW_BEFORE_H hours left. Returns 'renewed', 'expired', 'failed' or None."""
    global _last_check
    now = now or time.time()
    if not force and now - _last_check < CHECK_EVERY_S:
        return None
    _last_check = now
    if not provider:
        return None
    cid, tok = provider()
    if not (cid and tok):
        return None
    st = state()
    exp = expiry(tok)
    if exp is None:                                   # not a readable token: renew once a day
        last = st.get("renewed_ts") if st.get("for") == tok[-12:] or st.get("new") == tok[-12:] else None
        exp = (last or now) + 24 * 3600 if last else None
        if exp is None:
            st.update({"for": tok[-12:], "renewed_ts": now})
            _write(st)
            return None
    if not force and exp - now > RENEW_BEFORE_H * 3600:
        return None
    if exp <= now:
        st.update({"for": tok[-12:], "error": "expired: make a new token on web.dhan.co once"})
        _write(st)
        return "expired"
    try:
        res = renew(cid, tok)
    except PermissionError as e:
        st.update({"for": tok[-12:], "error": str(e)})
        _write(st)
        log(str(e))
        return "expired"
    except Exception as e:
        st.update({"for": tok[-12:], "error": str(e)[:200]})
        _write(st)
        log(str(e))
        return "failed"
    if saver:
        saver(res["token"])
    new_exp = expiry(res["token"])
    _write({"for": res["token"][-12:], "new": res["token"][-12:], "renewed": dt.datetime.fromtimestamp(now).strftime("%d %b %H:%M"),
            "renewed_ts": now, "exp": new_exp, "dhan_expiry": res.get("expiry"), "error": None})
    log(f"Dhan token renewed (valid till {dt.datetime.fromtimestamp(new_exp).strftime('%d %b %H:%M') if new_exp else res.get('expiry')})")
    return "renewed"


def setup(data_dir, get_saved, save_token):
    """Called once by the app at startup."""
    global provider, saver, _state_file
    provider, saver = get_saved, save_token
    _state_file = Path(data_dir) / "dhan_token_state.json"
