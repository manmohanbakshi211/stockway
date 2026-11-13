"""Tanu's AI (0.15.0, ADR-0013): Claude Code run as plain question-and-answer (no tools, empty folder, no API key in
its environment), the Claude API with a saved key, model fallback, budgets, the snapshot (Tanu's data only, never a
secret), the notes, and the API routes. No network and no real Claude Code: fakes only."""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from tanu.ai import notes
from tanu.ai.client import AI, AIError
from tanu.ai.context import ask_prompt, snapshot, symbols_in
from tanu.core.secrets import Secrets
from tanu.detect.book import AlertBook
from tanu.ui.api import create_app

from .test_tanu_market import NOW, collector

HOST = {"host": "127.0.0.1:8790"}


class Runner:
    def __init__(self, answers=None):
        self.calls, self.answers = [], list(answers or [])

    def __call__(self, cmd, prompt, timeout, cwd, env):
        self.calls.append({"cmd": cmd, "prompt": prompt, "cwd": cwd, "env": env, "timeout": timeout})
        return self.answers.pop(0) if self.answers else (0, "- Nifty looks **calm**\n- nothing passes yet", "")


def test_claude_code_runs_with_no_tools_in_an_empty_folder_and_without_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    r = Runner()
    ai = AI(tmp_path, runner=r, cli="/usr/local/bin/claude", now=lambda: NOW)
    assert ai.provider() == "claude_code" and ai.available()
    out = ai.ask("hello", job="ask")
    assert out["text"].startswith("- Nifty") and out["model"] == "sonnet"
    c = r.calls[0]
    assert c["cmd"][:2] == ["/usr/local/bin/claude", "-p"] and c["cmd"][c["cmd"].index("--tools") + 1] == ""
    assert "--no-session-persistence" in c["cmd"] and "--strict-mcp-config" in c["cmd"]
    assert "ANTHROPIC_API_KEY" not in c["env"] and c["cwd"].endswith("ai/sandbox") and c["prompt"] == "hello"
    assert ai.usage()["tiers"] == {"balanced": 1} and ai.usage()["jobs"]["ask"]["n"] == 1


def test_a_model_the_plan_refuses_falls_back_and_is_remembered_for_the_day(tmp_path):
    r = Runner([(1, "", "Error: model 'opus' is not available on your plan"), (0, "evening text", "")])
    ai = AI(tmp_path, runner=r, cli="/x/claude", now=lambda: NOW)
    assert ai.ask("p", job="evening")["model"] == "sonnet"
    assert ai.unavailable == {"opus": "2026-11-13"}
    ai.ask("p", job="evening")
    assert "opus" not in r.calls[-1]["cmd"]                            # straight to sonnet the second time


def test_plain_reasons_for_login_and_limits(tmp_path):
    ai = AI(tmp_path, runner=Runner([(1, "", "Please run /login"), (1, "", "usage limit reached")]), cli="/x/claude")
    with pytest.raises(AIError, match="not logged in"):
        ai.ask("p")
    with pytest.raises(AIError, match="usage limit"):
        ai.ask("p")


def test_api_key_path_sends_only_to_anthropic_and_never_shows_the_key(tmp_path):
    sent = []

    def transport(url, headers, body, timeout):
        sent.append((url, headers, json.loads(body)))
        return 200, json.dumps({"content": [{"type": "text", "text": "answer"}]}).encode()

    ai = AI(tmp_path, transport=transport, cli=False)
    assert ai.provider() == "off" and not ai.available()
    with pytest.raises(ValueError):
        ai.save("api", "not-a-key")
    ai.save("api", "sk-ant-test-123")
    assert ai.ask("q")["text"] == "answer"
    url, headers, body = sent[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "sk-ant-test-123"
    assert body["model"] == "claude-sonnet-5-5" and body["messages"] == [{"role": "user", "content": "q"}]
    assert "sk-ant" not in json.dumps(ai.status())
    ai.save("off")
    assert not ai.available() and ai.settings()["api_key"] == "sk-ant-test-123"   # switching off keeps the key


def test_automatic_jobs_stop_at_their_daily_budget_but_questions_do_not(tmp_path):
    ai = AI(tmp_path, runner=Runner(), cli="/x/claude", now=lambda: NOW)
    ai.spec["tiers"]["deep"]["budget_per_day"] = 1
    ai.ask("p", job="evening", user=False)
    with pytest.raises(AIError, match="allowance"):
        ai.ask("p", job="evening", user=False)
    assert ai.ask("p", job="evening", user=True)["text"]


def test_snapshot_holds_tanus_data_and_no_secrets(tmp_path):
    Secrets(tmp_path).save("upstox_token", {"access_token": "SECRET-TOKEN"})
    Secrets(tmp_path).save("ai", {"provider": "api", "api_key": "sk-ant-SECRET"})
    c = collector(tmp_path)
    c.refresh()
    (tmp_path / "live").mkdir(exist_ok=True)
    (tmp_path / "live" / "status.json").write_text(json.dumps({"day": "2026-11-13", "minute": 200, "of": 375, "done": False,
                                                               "spots": {"NIFTY": 25100}, "signals": [], "open": [],
                                                               "feed": {"token": "x"}}))
    rep = tmp_path / "research" / "2026-11-13_1202"
    rep.mkdir(parents=True)
    (rep / "report.json").write_text(json.dumps({"setups": [{"setup": "ema10_trend_5m", "passes": False, "hold": {"n": 90, "avg_pct": -2.1},
                                                             "why_not": ["unseen average not above 0"]}]}))
    book = AlertBook(tmp_path / "live" / "alerts")
    book.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "severity": 2, "title": "NIFTY broke above", "detail": ""}, NOW)
    s = snapshot(tmp_path, c, "ABC aur bank nifty ka kya haal hai?", book, now=NOW)
    text = json.dumps(s)
    assert "SECRET" not in text and "sk-ant" not in text
    assert s["live_day"]["spots"] == {"NIFTY": 25100} and s["market"]["fo_ban"]["ban"] == ["ABC"]
    assert s["research"]["setups_tested"] == 1 and s["research"]["passing"] == []
    assert s["alerts_today"]["latest"][0]["title"] == "NIFTY broke above"
    assert set(s["asked_about"]) == {"BANKNIFTY", "ABC"} and s["asked_about_insiders"][0]["sym"] == "ABC"
    p = ask_prompt("ABC?", s)
    assert p.startswith("You are Tanu's market desk analyst") and "never tell him to buy or sell with real money" in p
    assert symbols_in("nifty 50 vs NIFTY BANK", {"NIFTY", "BANKNIFTY"}) == ["BANKNIFTY", "NIFTY"]


def test_notes_are_written_listed_and_due_once_a_day(tmp_path):
    c = collector(tmp_path)
    ai = AI(tmp_path, runner=Runner(), cli="/x/claude", now=lambda: NOW)
    r = notes.write("morning", tmp_path, ai, c)
    assert r["day"] == "2026-11-13" and (tmp_path / "ai" / "notes" / "2026-11-13_morning.md").exists()
    assert notes.latest(tmp_path)[0]["kind"] == "morning"
    with pytest.raises(ValueError):
        notes.write("noon", tmp_path, ai, c)
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    t = dt.datetime(2026, 11, 13, 16, 10, tzinfo=ist)
    assert not notes.due("morning", tmp_path, t, "08:45", True)          # already written today
    assert notes.due("evening", tmp_path, t, "16:05", True) and not notes.due("evening", tmp_path, t, "16:05", False)
    assert not notes.due("evening", tmp_path, t.replace(hour=15), "16:05", True)


def test_ai_routes(tmp_path):
    c = collector(tmp_path)
    c.refresh()
    off = TestClient(create_app(tmp_path, collector=c, ai=AI(tmp_path, cli=False)))
    assert off.get("/api/ai", headers=HOST).json()["available"] is False
    r = off.post("/api/ai/ask", json={"question": "kya haal hai?"}, headers={**HOST, "X-Tanu": "1"})
    assert r.status_code == 400 and "not set up" in r.json()["detail"]
    run = Runner()
    api = TestClient(create_app(tmp_path, collector=c, ai=AI(tmp_path, runner=run, cli="/x/claude", now=lambda: NOW)))
    assert api.post("/api/ai/ask", json={"question": "Nifty?"}, headers=HOST).status_code == 403     # no X-Tanu
    a = api.post("/api/ai/ask", json={"question": "Nifty?"}, headers={**HOST, "X-Tanu": "1"}).json()
    assert a["answer"].startswith("- Nifty") and "QUESTION:\nNifty?" in run.calls[-1]["prompt"]
    assert api.post("/api/ai/ask", json={"question": " "}, headers={**HOST, "X-Tanu": "1"}).status_code == 400
    n = api.post("/api/ai/note", json={"kind": "evening"}, headers={**HOST, "X-Tanu": "1"}).json()
    assert n["kind"] == "evening" and api.get("/api/ai", headers=HOST).json()["notes"][0]["kind"] == "evening"
    s = api.post("/api/setup/ai", json={"provider": "api", "api_key": "sk-ant-x"}, headers={**HOST, "X-Tanu": "1"}).json()
    assert s["saved"] and s["provider"] == "api" and "sk-ant" not in json.dumps(s)
    assert api.post("/api/setup/ai", json={"provider": "api", "api_key": "sk-ant-x"}, headers=HOST).status_code == 403
