"""2.67: the Daily Radar for every major index + a grand view (Mandeep, 11 Nov 2026: "the radar pages not only for
the bank nifty add other all major indices and show a grand view also"). Bank Nifty stays exactly as it was."""
import json

import pandas as pd
import pytest

from radar import indices as IX
from radar import pipeline
from radar.config import load_config


@pytest.fixture()
def cfg(tmp_path):
    c = load_config()
    for k in ("output_dir", "model_dir", "cache_dir"):
        (tmp_path / k).mkdir()
        c[k] = str(tmp_path / k)
    c["news"]["use_gdelt"] = False
    c["telegram"] = {}
    return c


def test_registry_is_sound():
    r = IX.registry()
    assert len({e["key"] for e in r}) == len(r) and sum(1 for e in r if e.get("main")) == 1
    assert IX.main_key() == "BANKNIFTY"
    for e in r:
        assert e.get("yahoo") and e.get("name")
        if not e.get("on"):
            assert e.get("why_off")
        for kind in ("monthly", "weekly"):
            for rule in (e.get("expiry_rules") or {}).get(kind, []):
                assert rule["weekday"] in (None, "MON", "TUE", "WED", "THU", "FRI") and pd.Timestamp(rule["from"])
    assert {"NIFTY", "FINNIFTY", "NIFTYNXT50", "SENSEX"} <= {e["key"] for e in IX.others()}


def test_main_index_config_is_untouched(cfg):
    assert IX.index_cfg(cfg, IX.entry("BANKNIFTY")) is cfg
    c = IX.index_cfg(cfg, IX.entry("NIFTY"))
    assert c["output_dir"].endswith("indices/NIFTY") and c["model_dir"].endswith("indices/NIFTY")
    assert c["radar_index"]["name"] == "Nifty 50" and c["expiry_rules"] != cfg["expiry_rules"]
    assert "radar_index" not in cfg and cfg["output_dir"] != c["output_dir"]


def test_bad_prices_are_refused():
    idx = pd.bdate_range("2020-01-01", periods=900)
    good = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5}, index=idx)
    IX.quality(good, "x")
    with pytest.raises(IX.BadData):
        IX.quality(good.iloc[:200], "short")
    flat = good.assign(High=100.0, Low=100.0, Open=100.0, Close=100.0)
    with pytest.raises(IX.BadData):
        IX.quality(flat, "flat")


def test_train_forecast_grand_and_one_telegram(cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram", lambda c, text, log=print: sent.append(text) or True)
    pipeline.train(cfg, demo=True, use_news=False, log=lambda m: None)              # the main radar, as before
    pipeline.forecast(cfg, log=lambda m: None)
    assert len(sent) == 1 and sent[0].startswith("Bank Nifty radar")
    real_swap = IX.swap_target

    def swap(mkt, c, refresh=True, demo=False):                                     # one index breaks
        if c["radar_index"]["key"] == "SENSEX":
            raise IX.BadData("Sensex: broken prices")
        return real_swap(mkt, c, refresh=refresh, demo=demo)
    monkeypatch.setattr(IX, "swap_target", swap)
    r = IX.run_job(cfg, "train", keys=["NIFTY", "SENSEX"], demo=True, log=lambda m: None)
    assert r["done"] == ["NIFTY"]
    st = IX.load_status(cfg)
    assert st["NIFTY"]["ok"] is True and st["SENSEX"]["ok"] is False and "broken" in st["SENSEX"]["error"]
    assert IX.has_model(cfg, IX.entry("NIFTY")) and not IX.has_model(cfg, IX.entry("SENSEX"))
    r = IX.run_job(cfg, "forecast", demo=True, log=lambda m: None)
    assert r["done"] == ["NIFTY"]                                                   # only trained indices forecast
    assert len(sent) == 2 and "all indices" in sent[1] and "Nifty 50" in sent[1] and "Bank Nifty" in sent[1]
    g = IX.grand(cfg)
    by = {x["key"]: x for x in g["rows"]}
    assert by["BANKNIFTY"]["main"] and by["NIFTY"]["forecast"]["for_date"] == g["for_date"]
    assert by["SENSEX"]["error"] and not by["SENSEX"]["trained"]
    assert g["forecasts"] == 2 and 0 <= g["flagged"] <= 2
    assert {o["key"] for o in g["off"]} >= {"MIDCPNIFTY"}
    # the main radar's folder holds only its own files
    assert json.loads((pd.io.common.Path(cfg["output_dir"]) / "forecast_latest.json").read_text())["last_close"] != \
        json.loads((pd.io.common.Path(cfg["output_dir"]) / "indices" / "NIFTY" / "forecast_latest.json").read_text())["last_close"]


def test_api_reads_each_index(cfg, monkeypatch):
    from webapp import server
    monkeypatch.setattr(server, "cfg", lambda: cfg)
    monkeypatch.setattr(pipeline, "send_telegram", lambda *a, **k: True)
    IX.run_job(cfg, "train", keys=["NIFTY"], demo=True, log=lambda m: None)
    IX.run_job(cfg, "forecast", keys=["NIFTY"], demo=True, log=lambda m: None)
    fc = server.forecast(idx="NIFTY")
    assert fc["forecast"]["prob_big"] is not None
    assert server.forecast()["forecast"] is None                                    # main radar: nothing trained here
    h = server.history(idx="NIFTY")
    assert len(h["dates"]) > 1000 and any(p is not None for p in h["prob"])
    f = server.factors(idx="NIFTY")
    assert f["importance"] and not any("Bank Nifty" in x["label"] for x in f["factors"] + f["importance"])
    assert server.performance(idx="NIFTY")["metrics"]["big_move"]["auc"] > 0.5
    assert server.status(idx="NIFTY")["model"] is not None
    lst = {x["key"]: x for x in server.radar_indices()}
    assert lst["NIFTY"]["trained"] and not lst["MIDCPNIFTY"]["on"]
    assert server.radar_grand()["rows"]
    with pytest.raises(Exception):
        server.forecast(idx="MIDCPNIFTY")                                           # switched off: refused


def test_index_job_runs_in_the_background_runner():
    from lab import proc
    assert "radar_indices" in proc.JOBS


def test_on_the_system_page():
    from live.selfcheck import static_checks
    c = next(x for x in static_checks() if x["id"] == "radar_indices")
    assert c["ok"] is True and "Nifty 50" in c["detail"]
