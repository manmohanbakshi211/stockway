"""Stockway never places real orders: no broker order endpoints anywhere in the code."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"place_?order|modify_?order|cancel_?order|/v\d/order|order/place|api-hft\.upstox|/orders\b", re.I)


def test_no_order_endpoints_in_code():
    hits = []
    for f in list(ROOT.glob("live/*.py")) + list(ROOT.glob("lab/*.py")) + list(ROOT.glob("webapp/*.py")) + list(ROOT.glob("radar/*.py")) \
            + list(ROOT.glob("webapp/static/*.js")):
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f"{f.name}:{i}: {line.strip()[:80]}")
    assert not hits, "Order-placing code found:\n" + "\n".join(hits)
