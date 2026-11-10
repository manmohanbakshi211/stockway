"""Nothing collides: the same checks the System page shows, plus code-level rules. Run after every change."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _code():
    for p in list((ROOT / "lab").glob("*.py")) + list((ROOT / "live").glob("*.py")) + list((ROOT / "webapp").glob("*.py")):
        yield p, p.read_text()


def test_static_checks_all_pass():
    from radar.config import load_config
    from live import selfcheck
    bad = [c for c in selfcheck.static_checks(load_config()) if c["ok"] is not True]
    assert not bad, bad


def test_runtime_checks_on_a_fresh_app(tmp_path):
    from radar.config import load_config
    from live import selfcheck
    from live.engine import LiveEngine
    e = LiveEngine(load_config, tmp_path)
    bad = [c for c in selfcheck.runtime_checks(e, tmp_path, {"scheduler_age_s": 5}) if c["ok"] is False and c["id"] != "disk"]
    assert not bad, bad


def test_every_dhan_data_call_goes_through_the_shared_limiter():
    """Only lab/dhan.py talks to api.dhan.co for data (it shares one speed limit and one daily count); the token
    keeper may call the renew endpoint. Nothing else calls Dhan directly."""
    allowed = {"dhan.py", "dhan_token.py"}
    offenders = [p.name for p, s in _code() if "https://api.dhan.co" in s and p.name not in allowed]   # (the order-book websocket has its own limit)
    assert not offenders, offenders


def test_token_changes_go_through_the_keeper():
    """Every place the app itself writes a Dhan token holds the keeper's lock (pasting one in Settings is your action)."""
    s = (ROOT / "webapp" / "server.py").read_text()
    lines = s.splitlines()
    for i, line in enumerate(lines):
        if re.search(r'save_user_settings\(\{"lab\.dhan_token"|ch = \{"lab\.dhan_token"', line):
            ctx = "\n".join(lines[max(0, i - 4):i + 1])
            assert "dhan_token.LOCK" in ctx or "lambda tok" in ctx, f"server.py line {i + 1} writes the token without the lock"


def test_one_live_price_feed():
    """Live prices come only from Upstox (or the simulator): no Dhan market feed in the scanner. 2.52: Kite joins only
    through the one feed router (live/feeds.py) as a backup / cross-check, and only live/kite.py talks to Kite."""
    s = (ROOT / "live" / "engine.py").read_text()
    assert "api-feed.dhan.co" not in s and "DhanFeed" not in s
    assert s.count("FeedRouter(") == 1 and s.count("UpstoxFeed(") == 1 and "KiteFeed(" in s
    assert "ws.kite.trade" not in s and "api.kite.trade" not in s
    offenders = [p.name for p, t in _code() if ("ws.kite.trade" in t or "api.kite.trade" in t) and p.name != "kite.py"]
    assert not offenders, offenders


def test_system_page_endpoint(tmp_path, monkeypatch):
    import webapp.server as srv
    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        j = c.get("/api/system").json()
        assert {s["id"] for s in j["services"]} >= {"upstox", "dhan", "claude", "telegram", "yahoo", "nse", "kite"}
        assert all(s["roles"] for s in j["services"])
        assert not [x for x in j["checks"] if x["ok"] is False and x["id"] != "disk"]


def test_no_method_is_defined_twice():
    """2.40: a second `def _watch` in LiveEngine silently replaced the watcher loop (no 9:00 auto-start, health monitor,
    keep-awake or nightly Lab from 2.11 to 2.39). Python allows it without a word, so this test does not."""
    import ast
    dup = []
    for p, src in _code():
        for node in ast.walk(ast.parse(src)):
            body = node.body if isinstance(node, (ast.ClassDef, ast.Module)) else None
            if body is None:
                continue
            seen = {}
            for b in body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prop = any(isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter") for d in b.decorator_list)
                    if b.name in seen and not prop:
                        dup.append(f"{p.name}: {getattr(node, 'name', 'module')}.{b.name} (lines {seen[b.name]} and {b.lineno})")
                    seen[b.name] = b.lineno
    assert not dup, dup
