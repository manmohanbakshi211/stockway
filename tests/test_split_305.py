"""3.05 (Mandeep, 24 Nov 2026: "okay sab karo"): the "Half book · half trail" account: the main account's trades,
half the lots with the main exit, the rest trailed."""
import json

from live import books as B
from live.store import Store
from tests.test_books import MAIN, _t, _state, DAY
from tests.test_trail_book import _alert


def test_split_lots():
    sp = {"split": {"book_frac": 0.5, "one_lot": "trail"}}
    assert B.Books.split_lots(1, sp) == [("trail", 1)]
    assert B.Books.split_lots(2, sp) == [("book", 1), ("trail", 1)]
    assert B.Books.split_lots(3, sp) == [("book", 1), ("trail", 2)]
    assert B.Books.split_lots(4, sp) == [("book", 2), ("trail", 2)]
    assert B.Books.split_lots(1, {"split": {"one_lot": "book"}}) == [("book", 1)]


def test_half_trail_account_mirrors_main_in_two_legs(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    sp = B.cfg()["books"]["halftrail"]
    assert sp["pick"]["mirror_main"] and sp["exit_style"] == "chand" and sp["split"]["book_frac"] == 0.5
    now = _t(DAY, 10, 0)
    out = bs.consider(_alert(R=12.0), _state(O1=10.0), now, main={"id": 7, "lots": 3})
    assert out["halftrail"] == "taken" and out["trail"] == "taken"
    legs = sorted(st.trades(source="upstox#halftrail"), key=lambda t: t["mode"])
    assert [(t["mode"], t["lots"]) for t in legs] == [("leg:book", 1), ("leg:trail", 2)]
    assert legs[0]["plan"] in (None, "") and json.loads(legs[1]["plan"])["style"] == "chand"     # main exit / trailing
    assert legs[0]["target"] < legs[1]["target"]                     # the booked leg has the main account's premium target
    one = bs.consider(_alert(R=12.0, i=2, key="O2"), _state(O2=10.0), now, main={"lots": 1})
    assert one["halftrail"] == "taken" and [t["mode"] for t in st.trades(source="upstox#halftrail") if t["sym"] == "S2"] == ["leg:trail"]
    # a later alert on a symbol the account still holds is not doubled (only the two legs of one alert share a symbol)
    again = bs.consider(_alert(R=12.0, i=1, key="O1") | {"id": 99}, _state(O1=10.0), now, main={"lots": 2})
    assert again["halftrail"] != "taken" and len([t for t in st.trades(source="upstox#halftrail") if t["sym"] == "S1"]) == 2
    assert bs.consider(_alert(R=None, i=3, key="O3"), _state(O3=10.0), now, main={"lots": 2})["halftrail"].startswith("no chart risk")
    assert st.trades(source="upstox") == []                          # the main account is untouched
