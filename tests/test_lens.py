"""Options lens on a real simulator session: straddle, call vs put premium, PE-CE OI change, OI by strike, breadth,
buildup heatmap and IV grid, all from the minute recording."""
import json
import time


def test_lens_from_a_simulated_session(tmp_path):
    from radar.config import load_config
    from live.engine import LiveEngine
    from live.lens import Lens

    def cfg():
        c = load_config()
        c["live"] = {**(c.get("live") or {}), "source": "sim", "sim_speed": 240, "auto_start": False, "news": False,
                     "filings": False, "briefs": False}
        return c
    e = LiveEngine(cfg, tmp_path)
    e.start("sim")
    try:
        t0 = time.time()
        while time.time() - t0 < 60:
            time.sleep(2)
            if e.recorder is not None and e.recorder.rows > 0 and len(e.metrics) > 5:
                try:
                    n = Lens(e).symbol("NIFTY")["series"].get("straddle") or []
                except Exception:
                    n = []
                if len(n) >= 4:
                    break
        lz = Lens(e)
        s = lz.symbol("NIFTY")
        assert not s.get("empty"), s
        S = s["series"]
        assert len(S["straddle"]) >= 4 and len(S["ce"]) == len(S["pe"]) == len(S["straddle"])
        assert all(abs(x["value"] - (c["value"] + p["value"])) < 0.02 for x, c, p in zip(S["straddle"], S["ce"], S["pe"]))
        assert S["pe_minus_ce_oi_lakh"] and S["straddle_vwap"] and S["spot"]
        assert s["strikes"] and all("ce_oi" in b or "pe_oi" in b for b in s["strikes"])
        m = lz.market()
        assert m["breadth"] and m["iv_grid"] and m["buildup"]
        json.dumps(s), json.dumps(m)
    finally:
        e.stop()
