"""Claude through the Pro plan: Claude Code runs with no tools, no API key, in an empty folder; Ask builds context."""
import os
import stat

from live.ask import symbols_in
from live.notify import AI


def _fake_cli(tmp_path, body):
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_pro_runs_claude_code_with_tools_off(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    cli = _fake_cli(tmp_path, f'printf "%s|" "$@" > {log}; echo "key=${{ANTHROPIC_API_KEY:-none}}" >> {log}; cat > /dev/null; echo "Nifty above VWAP."\n')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    ai = AI(lambda: {"ai": {"provider": "claude_pro", "pro_model": "sonnet"}})
    ai._cli = cli
    assert ai.enabled()
    assert ai.ask("hello", auto=False) == "Nifty above VWAP."
    args = log.read_text()
    assert "-p|" in args and "--tools||" in args and "--model|sonnet|" in args and "key=none" in args


def test_pro_auto_notes_capped_but_questions_not(tmp_path):
    cli = _fake_cli(tmp_path, 'cat > /dev/null; echo ok\n')
    ai = AI(lambda: {"ai": {"provider": "claude_pro", "pro_auto_per_day": 2}})
    ai._cli = cli
    assert ai.ask("a") == "ok" and ai.ask("b") == "ok"
    assert ai.ask("c") is None and "used up" in ai.last_error
    assert ai.ask("question", auto=False) == "ok"


def test_pro_reports_login_needed(tmp_path):
    cli = _fake_cli(tmp_path, 'cat > /dev/null; echo "Invalid API key · Please run /login" >&2; exit 1\n')
    ai = AI(lambda: {"ai": {"provider": "claude_pro"}})
    ai._cli = cli
    assert ai.ask("x", auto=False) is None and "logged in" in ai.last_error


def test_symbols_in_question():
    known = {"RELIANCE", "NIFTY", "BANKNIFTY", "M&M"}
    assert symbols_in("bank nifty aaj kya karega?", known) == ["BANKNIFTY"]
    assert symbols_in("Reliance aur M&M mein kya ho raha hai", known) == ["M&M", "RELIANCE"] or \
        set(symbols_in("Reliance aur M&M mein kya ho raha hai", known)) == {"RELIANCE", "M&M"}


def test_pro_falls_back_for_older_claude_code(tmp_path):
    cli = _fake_cli(tmp_path, 'cat > /dev/null; for a in "$@"; do if [ "$a" = "--tools" ]; then echo "error: unknown option --tools" >&2; exit 1; fi; done; echo fine\n')
    ai = AI(lambda: {"ai": {"provider": "claude_pro"}})
    ai._cli = cli
    assert ai.ask("x", auto=False) == "fine"


def test_every_setting_the_app_saves_is_allowed():
    """A key the UI sends but the settings whitelist lacks makes the whole Save fail."""
    import re
    from pathlib import Path
    from radar.config import EDITABLE
    root = Path(__file__).resolve().parents[1] / "webapp" / "static"
    js = (root / "live.js").read_text() + (root / "app.js").read_text()
    sent = set(re.findall(r'"((?:live|ai|lab|schedule|telegram|data|model|radar|target|news)\.[a-z_.]+)"\s*:', js))
    assert sent and not [k for k in sent if k not in EDITABLE]


def test_pro_explains_unavailable_model(tmp_path):
    cli = _fake_cli(tmp_path, 'cat > /dev/null; echo "Error: model opus is not available on your plan" >&2; exit 1\n')
    ai = AI(lambda: {"ai": {"provider": "claude_pro", "pro_model": "opus"}})
    ai._cli = cli
    assert ai.ask("x", auto=False) is None and "choose Sonnet" in ai.last_error


def test_yfinance_caches_are_off_and_file_limit_raised():
    """yfinance opened a cache connection per thread and never closed it: the Mac ran out of open files."""
    import resource
    import pytest
    import radar  # noqa: F401
    import yfinance.cache as yc
    with pytest.raises(Exception):
        yc._TzDBManager.get_database()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    assert soft >= min(hard, 10240) or soft >= 10240
