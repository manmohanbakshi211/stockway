"""2.77 Strict track: Tanu's pass test on the live test bench (learn / holdout by day, Bonferroni t bar, the coin-flip
yardstick), Strict alerts, the Strict paper account's pick, and nothing else reading the coin flips."""
import json
import random
import sqlite3

from live import strict as ST

DAYS = [f"2026-11-{d:02d}" for d in range(1, 21)]


def rows_for(kind, mean, n_per_day=20, style="buy", coin=False, seed=1, variant=None, days=DAYS):
    rng = random.Random(seed)
    return [{"day": d, "kind": kind, "variant": variant, "style": style, "r": rng.gauss(mean, 1.0), "net": mean * 1000,
             "coin": coin} for d in days for _ in range(n_per_day)]


def test_an_edge_passes_and_luck_does_not():
    rows = rows_for("good", 0.4, seed=1) + rows_for("noise", 0.0, seed=2) + rows_for(ST.COIN, -0.1, coin=True, seed=3) \
        + rows_for("late", 0.4, seed=4, days=DAYS[:15]) + rows_for("late", -0.5, seed=5, days=DAYS[16:])
    rep = ST.evaluate(rows)
    by = {s["setup"]: s for s in rep["setups"]}
    assert rep["passing"] == ["good||buy"] and by["good||buy"]["passes"] and not by["good||buy"]["why_not"]
    assert not by["noise||buy"]["passes"] and by["noise||buy"]["why_not"]
    assert not by["late||buy"]["passes"] and "loses after costs on the holdout" in by["late||buy"]["why_not"]
    assert rep["holdout_from"] == "2026-11-17" and rep["yardstick"]["buy"]["what"] == "coin-flip signals"
    assert all(s["kind"] != ST.COIN for s in rep["setups"])          # the yardstick is never a setup


def test_more_ideas_tried_means_a_higher_bar_and_few_trades_never_pass():
    one = ST.evaluate(rows_for("a", 0.2, seed=1))
    many = ST.evaluate([r for i in range(40) for r in rows_for(f"k{i}", 0.0, seed=10 + i)] + rows_for("a", 0.2, seed=1))
    assert many["k"] == 41 and many["t_bar"] > one["t_bar"] > 1.9
    thin = ST.evaluate(rows_for("thin", 2.0, n_per_day=1, days=DAYS[:20]))
    assert thin["passing"] == [] and "only 16 learn trades" in thin["setups"][0]["why_not"][0]


def test_without_enough_coin_flips_the_yardstick_is_every_signal():
    rep = ST.evaluate(rows_for("good", 0.4) + rows_for("bad", -0.3, seed=9) + rows_for(ST.COIN, 0.0, n_per_day=1, coin=True, days=DAYS[:5]))
    y = rep["yardstick"]["buy"]
    assert y["what"] == "every signal of this style" and y["coin_n"] == 5 and rep["passing"] == ["good||buy"]


def _bench(tmp_path, rows):
    db = sqlite3.connect(tmp_path / "bench.db")
    db.execute("CREATE TABLE bench_trades (day TEXT, kind TEXT, variant TEXT, style TEXT, r_mult REAL, net REAL, source TEXT, exit_ts REAL)")
    db.executemany("INSERT INTO bench_trades VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_run_reads_the_real_market_and_its_coins_only_and_keeps_every_run(tmp_path):
    rng = random.Random(3)
    rows = []
    for d in DAYS:
        for _ in range(20):
            rows.append((d, "factory", "rule7", "buy", rng.gauss(0.5, 1), 500, "upstox", 1.0))
            rows.append((d, "factory", "rule7", "buy", rng.gauss(-2, 1), -500, "sim", 1.0))            # practice: never read
            rows.append((d, ST.COIN, None, "buy", rng.gauss(-0.1, 1), -50, "upstox#coin", 1.0))
            rows.append((d, "factory", "rule7", "buy", None, None, "upstox", None))                  # still open
    _bench(tmp_path, rows)
    got = ST.bench_rows(tmp_path)
    assert len(got) == 20 * 20 * 2 and {r["coin"] for r in got} == {True, False}
    rep = ST.run(tmp_path, log=lambda m: None)
    assert rep["passing"] == ["factory|rule7|buy"] and (tmp_path / "strict.json").exists()
    ST.run(tmp_path, log=lambda m: None)
    lines = (tmp_path / "strict_runs.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[-1])["passing"] == ["factory|rule7|buy"]
    a = {"sym": "NIFTY", "kind": "factory", "direction": "up", "data": {"factory": {"id": "rule7"}}}
    v = ST.verdict(a, tmp_path)
    assert v["styles"] == ["buy"] and v["buy"] and v["learn"]["n"] > 0
    assert ST.verdict({**a, "data": {"factory": {"id": "rule8"}}}, tmp_path) is None   # another rule: its own test
    assert ST.verdict({**a, "direction": "neutral"}, tmp_path) is None


def test_strict_alert_text_and_the_strict_account_pick():
    from live.books import Books
    from live.notify import telegram_text
    a = {"sym": "NIFTY", "kind": "factory", "direction": "up", "severity": 2, "title": "Rule 7", "detail": "x", "ts": 1.79e9,
         "option": {"key": "K", "label": "NIFTY 25000 CE", "ltp": 100.0, "lot": 65},
         "data": {"strict": {"styles": ["buy"], "buy": True}}}
    assert "STRICT" in telegram_text(a) and "STRICT" not in telegram_text({**a, "data": {}})
    b = Books.__new__(Books)
    b.specs = {"strict": {"pick": {"strict": True, "risk_pass": True}}}
    assert b.pick("strict", a) is None
    assert b.pick("strict", {**a, "data": {}}) == "its alert type has not passed the strict test"
    assert "only in a style" in b.pick("strict", {**a, "data": {"strict": {"styles": ["sell_day"], "buy": False}}})
    assert "strict" in json.load(open(ST.RULES.parents[0] / "paper_books.json"))["books"]


def test_no_bench_trades_yet_is_a_clean_empty_result(tmp_path):
    rep = ST.run(tmp_path, log=lambda m: None)
    assert rep["days"] == 0 and rep["passing"] == [] and rep["k"] == 0 and (tmp_path / "strict_runs.jsonl").exists()


def test_rules_file_falls_back_to_defaults(tmp_path, monkeypatch):
    f = tmp_path / "strict.json"
    f.write_text(json.dumps({"min_trades": "x", "hold_frac": 5, "coin_every_min": 10}))
    monkeypatch.setattr(ST, "RULES", f)
    ST._CFG.clear()
    c = ST.cfg()
    assert c["min_trades"] == 30 and c["hold_frac"] == 0.2 and c["coin_every_min"] == 10
    f.write_text("{broken")
    ST._CFG["mt"] = None
    assert ST.cfg()["coin_every_min"] == 10                          # a broken file keeps the last good values
    ST._CFG.clear()
