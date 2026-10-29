"""The insider tracker starts on an empty store and reloads what it saved."""
from live.insiders import Insiders


class _Store(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def put(self, k, v):
        self[k] = v


class _Eng:
    def __init__(self):
        self.store = _Store()


def test_starts_empty_and_reloads():
    e = _Eng()
    ins = Insiders(e)
    assert ins.updated is None and not ins.seeded and ins.view()["rows"] == []
    ins.rows = {"g1:D1": {"pid": "g1:D1", "filed": "2026-10-27T20:28", "sym": "ABC"}}
    ins._save()
    e.store.put("insiders:updated", 1.0)
    again = Insiders(e)
    assert again.seeded and "g1:D1" in again.rows
