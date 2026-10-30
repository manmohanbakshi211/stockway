"""Claude reviews on the Test & research pages: compact data, clear errors, user-initiated (not capped)."""
import pytest

from live.review import review, slim


class _AI:
    def __init__(self, on=True):
        self.on, self.prompts, self.last_error = on, [], None

    def enabled(self):
        return self.on

    def label(self):
        return "Claude (your Pro plan)"

    def ask(self, prompt, max_tokens=400, auto=True):
        assert auto is False
        self.prompts.append(prompt)
        return "Verdict: noise."


class _E:
    def __init__(self, on=True):
        self.ai = _AI(on)

    def cfg(self):
        return {"ai": {"language": "english"}}


def test_slim_drops_series_and_rounds():
    x = slim({"mc": {"target_first": 12.34567, "fan": {"p5": [1] * 75}}, "plan": {"entry": 100.0}})
    assert x == {"mc": {"target_first": 12.346}, "plan": {"entry": 100.0}}


def test_timemachine_review_sends_numbers_not_series(tmp_path):
    e = _E()
    r = review(e, "timemachine", {"mc": {"target_first": 20.0, "fan": {"p5": list(range(70))}}, "plan": {"r": 2}}, tmp_path)
    assert r["text"].startswith("Verdict") and "\"fan\"" not in e.ai.prompts[0].split("DATA:")[1] and "simple English" in e.ai.prompts[0]
    assert "never places real orders" in e.ai.prompts[0]


def test_review_errors_are_clear(tmp_path):
    with pytest.raises(PermissionError):
        review(_E(on=False), "desk", None, tmp_path)
    with pytest.raises(ValueError):
        review(_E(), "timemachine", None, tmp_path)
    with pytest.raises(ValueError):
        review(_E(), "nonsense", None, tmp_path)
