"""2.65 evening AI analyst: reads the day (paper, alerts, multiverse, mass search, news radar, log problems, checks),
writes worked / failed / bugs / tomorrow, and puts its ideas on trial in the multiverse. Mandeep, 11 Nov 2026."""
import json
import types

from live import analyst as A


def test_parse_keeps_only_testable_ideas():
    txt = json.dumps({"worked": ["a"], "failed": ["b"], "bugs": ["Dhan too many retries 12 times"], "tomorrow": "t",
                      "ideas": [{"name": "volume", "fact": "rel_vol", "op": ">", "v": 3, "why": "w"},
                                {"name": "bad", "fact": "nope", "op": ">", "v": 1},
                                {"name": "bad op", "fact": "rel_vol", "op": "=", "v": 1}]})
    r = A.parse("Here: " + txt, {"rel_vol", "z5"})
    assert [i["fact"] for i in r["ideas"]] == ["rel_vol"] and r["bugs"][0].startswith("Dhan")
    assert A.parse("no json", {"x"}) is None


def test_ideas_go_on_trial_in_the_multiverse_once(tmp_path):
    ideas = [{"name": "volume 3x", "fact": "rel_vol", "op": ">", "v": 3.0, "why": "w"}]
    assert A.add_ideas(tmp_path, ideas, "2026-11-11") == 1
    assert A.add_ideas(tmp_path, ideas, "2026-11-12") == 0                       # same idea: not twice
    s = json.loads((tmp_path / A.IDEAS).read_text())["singles"]
    assert s[0]["name"].startswith("AI idea:") and s[0]["source"] == "AI analyst"
    from lab import multiverse as MV
    c = MV.cfg()
    assert any(x["name"].startswith("AI idea:") for x in MV.with_ideas(c, tmp_path)["singles"])


def test_log_problems_finds_todays_errors_once(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_text("\n".join([
        "2026-11-10 09:00:00  Live scanner error: x",
        "2026-11-11 09:47:44  Live scanner error: name 'UpstoxFeed' is not defined",
        "2026-11-11 09:48:03  Live scanner error: name 'UpstoxFeed' is not defined",
        "2026-11-11 10:00:00  Paper BUY X",
        "2026-11-11 10:01:00  Dhan movers failed: too many retries (last answer HTTP 429)"]))
    p = A._log_problems(tmp_path, "2026-11-11")
    assert len(p) == 2 and "UpstoxFeed" in p[0] and "429" in p[1]


def test_run_writes_the_report_and_sends_a_summary(tmp_path, monkeypatch):
    sent = []
    reply = json.dumps({"worked": ["w1"], "failed": ["f1"], "bugs": ["b1"], "tomorrow": "t1",
                        "ideas": [{"name": "i", "fact": "rel_vol", "op": ">", "v": 2, "why": "y"}]})
    ai = types.SimpleNamespace(enabled=lambda: True, ask=lambda p, n=0, auto=True, task=None: reply if task == "analyst" else None)
    e = types.SimpleNamespace(ai=ai, telegram=types.SimpleNamespace(send=sent.append), alerts_mem=[], store=None, source="upstox",
                              paper=None, books=None, radar_news=types.SimpleNamespace(view=lambda: {}))
    monkeypatch.setattr(A, "known_facts", lambda d: ["rel_vol", "z5"])
    out = A.run(e, tmp_path, day="2026-11-11", log=lambda m: None)
    assert out["report"]["tomorrow"] == "t1" and out["ideas_added"] == 1
    assert (tmp_path / "analyst" / "2026-11-11.json").exists() and A.latest(tmp_path)["day"] == "2026-11-11"
    assert sent and "evening analyst" in sent[0]
