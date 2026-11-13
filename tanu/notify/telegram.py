"""Telegram sender: one background thread, a small queue, at most one message a second, never raises into the caller.
The bot token and chat id are secret `telegram` (typed into the System page on the Mac). Off when not saved."""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..core import log, safety
from ..core.secrets import Secrets

API = "https://api.telegram.org"
_log = log.get("telegram")


def _post(url: str, data: dict, timeout: float = 15) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Telegram:
    def __init__(self, secrets: Secrets, transport=_post, min_gap_s: float = 1.0):
        self.secrets, self.transport, self.gap = secrets, transport, min_gap_s
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.sent, self.failed, self.last_error = 0, 0, None
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        c = self.secrets.load("telegram") or {}
        return bool(c.get("bot_token") and c.get("chat_id"))

    def save(self, bot_token: str, chat_id: str) -> None:
        bot_token, chat_id = (bot_token or "").strip(), str(chat_id or "").strip()
        if ":" not in bot_token or not chat_id.lstrip("-").isdigit():
            raise ValueError("Bot token looks like 123456:ABC... and chat id is a number. Copy them again.")
        self.secrets.save("telegram", {"bot_token": bot_token, "chat_id": chat_id})
        _log.info("telegram_saved")

    def send(self, text: str) -> bool:
        """Queues a message (dropped with a log line when Telegram is not set up or the queue is full)."""
        if not self.configured():
            return False
        try:
            self.q.put_nowait(text[:3900])
        except queue.Full:
            _log.warning("telegram_queue_full")
            return False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="tanu-telegram")
            self._thread.start()
        return True

    def send_now(self, text: str) -> tuple[bool, str]:
        """Sends immediately (the System page's test message). Returns (ok, reason)."""
        c = self.secrets.load("telegram") or {}
        if not c:
            return False, "Telegram is not set up"
        return self._deliver(c, text)

    def _deliver(self, c: dict, text: str) -> tuple[bool, str]:
        safety.check_url(f"{API}/bot/sendMessage")                  # the path without the token (never logged)
        url = f"{API}/bot{c['bot_token']}/sendMessage"
        try:
            status, body = self.transport(url, {"chat_id": c["chat_id"], "text": text, "disable_web_page_preview": "true"})
        except Exception as e:
            status, body = None, str(e).encode()
        if status == 200:
            self.sent += 1
            return True, "sent"
        self.failed += 1
        try:
            why = json.loads(body).get("description")
        except Exception:
            why = None
        self.last_error = f"HTTP {status}{': ' + why if why else ''}"
        _log.warning("telegram_failed", status=status)
        return False, self.last_error

    def _run(self) -> None:
        while True:
            try:
                text = self.q.get(timeout=60)
            except queue.Empty:
                return
            c = self.secrets.load("telegram")
            if c:
                self._deliver(c, text)
            time.sleep(self.gap)
