"""2.64 news radar: the fast AI classifies new headlines (impact, direction, stocks), graded 30 minutes later against
the keyword tone; shadow only (news alerts unchanged). Mandeep, 11 Nov 2026: "kya kya cheej bhot jaldi pakad sakta hai"."""
import json

from live import newsradar as NR


class _AI:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def enabled(self):
        return True

    def ask(self, prompt, max_tokens=400, auto=True, budget=None, task=None):
        self.calls.append((prompt, task))
        return self.reply


def test_parse_keeps_only_clean_answers():
    txt = 'Sure: [{"i":0,"impact":3,"dir":1,"syms":["SBIN","XYZ"],"why":"RBI cut"},{"i":7,"impact":2,"dir":1},{"i":1,"impact":9,"dir":-5,"syms":[]}]'
    r = NR.parse(txt, 2, {"SBIN", "HDFCBANK"})
    assert r[0] == {"impact": 3, "dir": 1, "syms": ["SBIN"], "why": "RBI cut"}     # unknown symbol dropped
    assert 7 not in r and r[1]["impact"] == 3 and r[1]["dir"] == -1               # clipped
    assert NR.parse("not json", 2, set()) == {} and NR.parse(None, 1, set()) == {}


def test_classify_then_grade_against_the_keyword_tone(tmp_path):
    now = 1_800_000_000.0
    ai = _AI(json.dumps([{"i": 0, "impact": 2, "dir": 1, "syms": ["SBIN"], "why": "profit beats"},
                         {"i": 1, "impact": 1, "dir": -1, "syms": [], "why": "weak global cues"}]))
    r = NR.NewsRadar(ai, tmp_path, log=lambda m: None)
    items = [{"title": "SBIN profit falls short of worst fears, beats estimates", "ts": now, "syms": ["SBIN"], "tone": -1},
             {"title": "Markets open on weak global cues", "ts": now, "syms": [], "tone": -1}]
    r.add(items)
    m0 = {"SBIN": {"spot": 1000.0}, "NIFTY": {"spot": 25000.0}}
    assert r.run(m0, ["SBIN", "HDFCBANK"], now=now) == 2
    assert ai.calls[0][1] == "news" and items[0]["ai"]["dir"] == 1                 # the fast tier; attached to the headline
    assert r.grade(m0, now=now + 600) == 0                                         # too early
    m1 = {"SBIN": {"spot": 1010.0}, "NIFTY": {"spot": 24900.0}}
    assert r.grade(m1, now=now + 1900) == 2
    v = r.view()
    assert v["ai"]["right_pct"] == 100.0 and v["keywords"]["right_pct"] == 50.0    # AI right on both; keywords on one
    assert NR.NewsRadar(ai, tmp_path, log=lambda m: None).view()["graded"] == 2    # kept across restarts


def test_waits_between_batches_and_skips_old_news(tmp_path):
    ai = _AI("[]")
    r = NR.NewsRadar(ai, tmp_path, log=lambda m: None)
    r.add([{"title": "old", "ts": 1_000.0, "syms": [], "tone": 0}])
    assert r.pending == []                                                         # older than 30 min: not sent
    r.add([{"title": "new", "ts": 2e9, "syms": [], "tone": 0}])
    r.run({}, [], now=2e9)
    r.add([{"title": "new2", "ts": 2e9, "syms": [], "tone": 0}])
    assert r.run({}, [], now=2e9 + 10) == 0 and len(ai.calls) == 1                 # waits EVERY_S between calls
