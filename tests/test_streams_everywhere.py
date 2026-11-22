"""2.91 (Mandeep, 23 Nov 2026: "har chart jitni speed se upstox mein chal rha hai utni hi speed se app par bhi ho. prices
live aa rhe ho saari jagah par live page, paper trade ya jahan bhi sab same speed as upstox"): every page streams the
prices it shows from the app's own live prices."""
import json
import types
from pathlib import Path

from live import stream as ST

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "webapp" / "static" / "live.js").read_text()


def test_one_watcher_streams_prices_on_every_page():
    assert "function liveStreams()" in JS and "setInterval(liveStreams, 1000)" in JS
    body = JS.split("function lpStream() {", 1)[1].split("\n}", 1)[0]
    assert 'state.page !== "live"' not in body                     # not only the Live page any more
    assert 'data-lp="${esc(m.sym)}"' in JS and 'data-lpc="${esc(m.sym)}"' in JS     # Scanner
    assert 'data-lp="${esc(r.sym)}"' in JS and 'data-lpc="${esc(r.sym)}"' in JS     # Watchlist
    gh = JS.split("function drawGhanshyam", 1)[1].split("const closed", 1)[0]
    assert 'data-qk="${esc(t.opt_key || "")}"' in gh and 'data-q="now"' in gh and 'data-q="pnl"' in gh


def test_push_every_100_ms_and_up_to_250_symbols():
    c = json.loads((ROOT / "rules" / "chart_stream.json").read_text())
    assert c["every_ms"] == 100 and c["prices_ms"] == 100 and c["quotes_ms"] == 100 and c["events_ms"] <= 150

    class I:
        def __init__(self, p):
            self.ltp, self.cp, self.ltt = p, p, 1795000000000
    syms = [f"S{i}" for i in range(260)]
    uni = types.SimpleNamespace(underlyings={s: types.SimpleNamespace(key=f"K{s}") for s in syms})
    st = {f"K{s}": I(100.0 + i) for i, s in enumerate(syms)}
    e = types.SimpleNamespace(running=True, state=st, uni=uni)
    px = ST.prices(e, syms)
    assert len(px) == 250 and px["S0"][0] == 100.0
