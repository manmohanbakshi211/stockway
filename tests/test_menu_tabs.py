"""2.92 (Mandeep, 23 Nov 2026: "kuch lite karne ke liye merge ho sakta hai toh" -> "Haan, tabs mein jodo"): the menu's
related pages sit under one entry with tabs; every page and link still works."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "webapp" / "static"
HTML = (ROOT / "index.html").read_text()
APP = (ROOT / "app.js").read_text()
LIVE = (ROOT / "live.js").read_text()


def test_menu_is_short_and_every_page_is_reachable():
    items = re.findall(r'<li><a href="#/([a-z]+)" data-(page|group)="([a-z]+)"', HTML)
    assert len(items) == 15
    direct = {p for _h, kind, p in items if kind == "page"}
    groups = dict(re.findall(r'(\w+): \{ label: "[^"]+", tabs: \[(.*?)\] \}', APP))
    tabbed = {p for g in groups.values() for p in re.findall(r'\["(\w+)", "', g)}
    assert set(groups) == {"chain", "market", "radar", "results", "system"}
    assert {g for _h, kind, g in items if kind == "group"} == set(groups)
    segs = [x.split(");", 1)[0] for x in LIVE.split("Object.assign(PAGES")[1:]]
    every = {p for seg in segs for p in re.findall(r"(\w+): page\w+", seg)} | \
        {"tomorrow", "history", "biggest", "drivers", "check", "data", "grand"}
    assert {"lens", "board", "system", "strict", "sym"} <= every
    assert every <= direct | tabbed                                  # no page lost from the menu
    assert not (direct & tabbed)
    assert 'id="tabstrip"' in HTML and "function renderTabs(page)" in APP and 'id="rail-ridx"' in HTML
