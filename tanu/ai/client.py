"""One AI client, two ways in:

- "claude_code": the Claude Code command on the Mac, logged in with Mandeep's Claude plan. Run as plain
  question-and-answer: every tool switched off, no session saved, no MCP servers, in an empty folder, with the
  prompt on stdin and any API key removed from its environment (so it uses the plan's login).
- "api": the Claude API with a key Mandeep typed into the System page (saved as a secret on the Mac, never shown).

Nothing but the prompt is sent. The prompt is built by tanu.ai.context from Tanu's data and never holds a secret.
`runner` (Claude Code) and `transport` (API) can be replaced in tests.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..core import log, safety
from ..core.secrets import Secrets

SPEC = Path(__file__).with_name("models.json")
API_URL = "https://api.anthropic.com/v1/messages"
CLI_PATHS = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
             "~/.npm-global/bin/claude", "~/.volta/bin/claude", "~/.bun/bin/claude")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("ai")


class AIError(RuntimeError):
    pass


class ModelUnavailable(AIError):
    pass


def find_cli() -> str | None:
    p = shutil.which("claude")
    if p:
        return p
    for c in CLI_PATHS:
        c = os.path.expanduser(c)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _run_cli(cmd: list[str], prompt: str, timeout: int, cwd: str, env: dict) -> tuple[int, str, str]:
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    return r.returncode, r.stdout, r.stderr


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(safety.check_url(url), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AI:
    def __init__(self, base: Path | str, secrets: Secrets | None = None, *, runner=_run_cli, transport=_post,
                 cli: str | None | bool = None, now=time.time):
        self.base = Path(base)
        self.dir = self.base / "ai"
        self.secrets = secrets or Secrets(self.base)
        self.runner, self.transport, self.now = runner, transport, now
        self._cli = find_cli() if cli is None else (cli or None)
        self.spec = json.loads(SPEC.read_text())
        self.lock = threading.Lock()
        self.unavailable: dict[str, str] = {}             # model -> day it was refused

    # -------------------------------------------------------------- setup and status
    def settings(self) -> dict:
        return self.secrets.load("ai") or {}

    def save(self, provider: str, api_key: str | None = None) -> None:
        if provider not in ("claude_code", "api", "off"):
            raise ValueError("provider must be claude_code, api or off")
        cur = self.settings()
        val = {"provider": provider}
        key = (api_key or "").strip() or cur.get("api_key")
        if provider == "api":
            if not key or not key.startswith("sk-") or len(key) > 300:
                raise ValueError("Paste the Claude API key (it starts with sk-).")
        if key:
            val["api_key"] = key
        self.secrets.save("ai", val)

    def provider(self) -> str:
        p = self.settings().get("provider")
        if p in ("claude_code", "api", "off"):
            return p
        return "claude_code" if self._cli else ("api" if self.settings().get("api_key") else "off")

    def available(self) -> bool:
        p = self.provider()
        return (p == "claude_code" and bool(self._cli)) or (p == "api" and bool(self.settings().get("api_key")))

    def _day(self) -> str:
        return dt.datetime.fromtimestamp(self.now(), IST).date().isoformat()

    def usage(self) -> dict:
        try:
            u = json.loads((self.dir / "usage.json").read_text())
        except Exception:
            u = {}
        return u if u.get("day") == self._day() else {"day": self._day(), "tiers": {}, "jobs": {}, "errors": 0}

    def _count(self, tier: str, job: str, err: str | None, secs: float) -> None:
        with self.lock:
            u = self.usage()
            u["tiers"][tier] = u["tiers"].get(tier, 0) + 1
            j = u["jobs"].setdefault(job, {"n": 0, "secs": 0.0, "last_error": None})
            j["n"] += 1
            j["secs"] = round(j["secs"] + secs, 1)
            j["last_error"] = err
            u["errors"] += 1 if err else 0
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "usage.json").write_text(json.dumps(u))

    def status(self) -> dict:
        p = self.provider()
        return {"provider": p, "available": self.available(), "claude_code_found": bool(self._cli),
                "api_key_saved": bool(self.settings().get("api_key")), "usage": self.usage(),
                "label": {"claude_code": "Claude (your plan, Claude Code on this Mac)", "api": "Claude API",
                          "off": "Off"}[p]}

    # -------------------------------------------------------------- asking
    def ask(self, prompt: str, job: str = "ask", user: bool = True) -> dict:
        """Returns {"text", "model", "secs"}; raises AIError with a plain reason."""
        tier = self.spec["jobs"].get(job, "balanced")
        rt = self.spec["tiers"][tier]
        if not self.available():
            raise AIError("AI is not set up: on the System page choose Claude (your plan) or save a Claude API key.")
        used = self.usage()["tiers"].get(tier, 0)
        if not user and used >= rt.get("budget_per_day", 10 ** 6):
            raise AIError(f"Today's automatic allowance for {tier} AI work is used up.")
        t0 = time.monotonic()
        try:
            if self.provider() == "api":
                text, model = self._api(prompt, rt)
            else:
                text, model = self._routed(prompt, rt)
        except AIError as e:
            self._count(tier, job, str(e)[:200], time.monotonic() - t0)
            _log.warning("ai_failed", job=job, tier=tier, error=str(e)[:200])
            raise
        secs = round(time.monotonic() - t0, 1)
        self._count(tier, job, None, secs)
        _log.info("ai_answered", job=job, tier=tier, model=model, secs=secs, chars=len(text))
        return {"text": text.strip(), "model": model, "secs": secs}

    def _routed(self, prompt: str, rt: dict) -> tuple[str, str]:
        model, tried = rt.get("model") or "", []
        day = self._day()
        while model and self.unavailable.get(model) == day:
            model = self.spec["fallback"].get(model, "")
        while True:
            try:
                return self._cli_ask(prompt, model, rt.get("effort"), int(rt.get("timeout", 150))), model or "default"
            except ModelUnavailable:
                tried.append(model)
                if model:
                    self.unavailable[model] = day
                nxt = self.spec["fallback"].get(model, "") if model else None
                if nxt is None or nxt in tried:
                    raise
                _log.info("ai_model_fallback", refused=model, using=nxt or "default")
                model = nxt

    def _cli_ask(self, prompt: str, model: str, effort: str | None, timeout: int) -> str:
        if not self._cli:
            raise AIError("Claude Code is not installed on this Mac.")
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        sandbox = self.dir / "sandbox"                    # an empty folder: nothing to read even if a tool slipped through
        sandbox.mkdir(parents=True, exist_ok=True)
        core = [self._cli, "-p", "--output-format", "text"] + (["--model", model] if model else [])
        safe = ["--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--tools", ""]
        old = ["--disallowedTools", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
               "Task", "Read", "Glob", "Grep"]           # older Claude Code without --tools
        attempts = ([core + ["--effort", effort] + safe] if effort else []) + [core + safe, core + old]
        last = ""
        for cmd in attempts:
            try:
                code, out, err = self.runner(cmd, prompt, timeout, str(sandbox), env)
            except subprocess.TimeoutExpired:
                raise AIError(f"The AI took longer than {timeout} s.")
            if code == 0 and out.strip():
                return out
            last = (err or out or "").strip()
            if "unknown option" not in last.lower() and "error: option" not in last.lower():
                break
        low = last.lower()
        if "login" in low or "log in" in low or "auth" in low:
            raise AIError("Claude Code is not logged in: open Terminal, type claude, and log in with your Claude account.")
        if "model" in low and any(w in low for w in ("not available", "not found", "invalid", "access", "not supported")):
            raise ModelUnavailable(f"The model '{model or 'default'}' is not on this Claude plan.")
        if "limit" in low:
            raise AIError("The Claude plan's usage limit is reached for now; it resets in a few hours.")
        raise AIError(last[:300] or "Claude Code returned nothing.")

    def _api(self, prompt: str, rt: dict) -> tuple[str, str]:
        key = self.settings().get("api_key")
        model = rt.get("api_model", "claude-sonnet-5-5")
        body = json.dumps({"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}).encode()
        code, raw = self.transport(API_URL, {"x-api-key": key, "anthropic-version": "2023-06-01",
                                             "content-type": "application/json"}, body, float(rt.get("timeout", 150)))
        if code != 200:
            msg = ""
            try:
                msg = json.loads(raw).get("error", {}).get("message", "")
            except Exception:
                pass
            raise AIError(f"Claude API answered {code}{': ' + msg[:150] if msg else ''}")
        return "".join(b.get("text", "") for b in json.loads(raw).get("content", [])), model
