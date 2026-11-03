"""Dated facts in rules/: same values as before they moved out of the code, and a new entry from a date only changes
days from that date on."""
import json

import pytest


def old_charges(fills, lot_units):          # the formula as it was in the code before 2.24
    buy = sum(p for s, p in fills if s > 0) * lot_units
    sell = sum(p for s, p in fills if s < 0) * lot_units
    brokerage = 20.0 * len(fills)
    stt = 0.0015 * sell
    exch = 0.0003503 * (buy + sell)
    sebi = 10 / 1e7 * (buy + sell)
    gst = 0.18 * (brokerage + exch + sebi)
    stamp = 0.00003 * buy
    return brokerage + stt + exch + sebi + gst + stamp


@pytest.mark.parametrize("fills,lot", [([(1, 100.0), (-1, 130.0)], 65), ([(-1, 210.5), (-1, 199.0), (1, 40.0), (1, 38.5)], 30),
                                       ([(1, 3.2), (-1, 0.05)], 500)])
def test_charges_identical(fills, lot):
    from lab.engine import charges
    assert charges(fills, lot) == old_charges(fills, lot)


def test_paper_costs_identical():
    from live.paper import costs
    assert costs(120.0, 150.0, 65) == round(old_charges([(1, 120.0), (-1, 150.0)], 65), 2)


def test_dated_entry_applies_only_from_its_date(tmp_path, monkeypatch):
    from lab import rules
    d = tmp_path / "rules"
    d.mkdir()
    base = json.loads((rules.DIR / "costs.json").read_text())
    base["entries"].append({**base["entries"][0], "from": "2030-01-01", "stt_sell": 0.002})
    (d / "costs.json").write_text(json.dumps(base))
    monkeypatch.setattr(rules, "DIR", d)
    monkeypatch.setattr(rules, "_cache", {})
    assert rules.costs("2029-12-31")["stt_sell"] == 0.0015 and rules.costs("2030-01-02")["stt_sell"] == 0.002


def test_broken_file_falls_back(tmp_path, monkeypatch):
    from lab import rules
    (tmp_path / "lots.json").write_text("{not json")
    monkeypatch.setattr(rules, "DIR", tmp_path)
    monkeypatch.setattr(rules, "_cache", {})
    assert rules.lot("NIFTY") == 65 and "lots" in rules.problems
    rules.problems.clear()


def test_events_include_old_and_new():
    from lab import rules
    ev = rules.events()
    assert {"2023-02-01", "2024-06-04", "2026-02-01", "2026-10-07", "2026-12-04"} <= ev
