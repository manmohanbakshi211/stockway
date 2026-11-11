"""2.62 AI router: each AI job uses the model that fits it (rules/ai_models.json). Mandeep, 11 Nov 2026: "model bhi vo
choose karo jo sabse zada capable ho or fit ho or bina bhot zada time liye near to accuracy analysis kar sake"."""
import subprocess

from live import notify as N


def _ai(cfg):
    a = N.AI(lambda: {"ai": cfg}, log=lambda m: None)
    a.cli = lambda: "/bin/claude"
    return a


class _Run:
    def __init__(self, refuse=()):
        self.calls, self.refuse = [], set(refuse)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        m = cmd[cmd.index("--model") + 1] if "--model" in cmd else ""
        if m in self.refuse:
            return subprocess.CompletedProcess(cmd, 1, "", f"Error: model {m} not available on your plan")
        return subprocess.CompletedProcess(cmd, 0, f"answer from {m or 'default'}", "")


def test_every_job_has_a_tier_and_every_tier_a_model():
    r = N.ai_models()
    for task, tier in r["tasks"].items():
        assert tier in r["tiers"] and r["tiers"][tier]["model"], task
    assert r["tasks"]["pulse"] == "fast" and r["tasks"]["alert_why"] == "balanced" and r["tasks"]["lab_ideas"] == "deep"


def test_quick_jobs_use_haiku_deep_jobs_opus_with_effort(monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    a = _ai({"provider": "claude_pro"})
    assert a.ask("p", task="pulse") == "answer from haiku"
    assert "--effort" in run.calls[-1] and run.calls[-1][run.calls[-1].index("--effort") + 1] == "low"
    assert a.ask("p", task="lab_ideas", auto=False) == "answer from opus"
    assert a.ask("p", task="alert_why") == "answer from sonnet"
    assert a.tasks["pulse"]["model"] == "haiku" and a.tasks["lab_ideas"]["tier"] == "deep"


def test_a_model_the_plan_refuses_falls_back_and_is_skipped_for_the_day(monkeypatch):
    run = _Run(refuse={"opus"})
    monkeypatch.setattr(subprocess, "run", run)
    a = _ai({"provider": "claude_pro"})
    assert a.ask("p", task="review", auto=False) == "answer from sonnet"
    n = len(run.calls)
    assert a.ask("p", task="review", auto=False) == "answer from sonnet"
    assert all("opus" not in c for c in run.calls[n:])                     # not asked again today


def test_routing_off_uses_your_one_model_as_before(monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    a = _ai({"provider": "claude_pro", "routing": False, "pro_model": "sonnet"})
    assert a.ask("p", task="pulse") == "answer from sonnet" and "--effort" not in run.calls[-1]
    b = _ai({"provider": "claude_pro"})
    assert b.ask("p") == "answer from default"                              # a job without a name: as before


def test_fast_tier_has_its_own_allowance(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _Run())
    a = _ai({"provider": "claude_pro", "pro_auto_per_day": 1})
    assert a.ask("p", task="alert_why") and a.ask("p", task="brain") is None   # automatic notes cap: 1
    assert a.ask("p", task="pulse") and a.ask("p", task="world")             # the fast tier counts apart
    assert a.used_auto == 1


def test_old_claude_code_without_effort_still_works(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "--effort" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "error: unknown option '--effort'")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert _ai({"provider": "claude_pro"}).ask("p", task="pulse") == "ok" and len(calls) == 2


def test_api_key_provider_uses_the_tier_api_model(monkeypatch):
    sent = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"text": "hi"}]}
    monkeypatch.setattr(N.requests, "post", lambda url, headers=None, json=None, timeout=None: sent.update(json) or R())
    a = N.AI(lambda: {"ai": {"provider": "claude", "api_key": "k"}}, log=lambda m: None)
    assert a.ask("p", task="lab_ideas", auto=False) == "hi" and sent["model"] == "claude-opus-5-5"
