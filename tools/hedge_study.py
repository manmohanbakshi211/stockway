"""2.55 study: hedged straddle challengers vs the naked straddle, on the same Lab bars. Read-only research.

    python3 tools/hedge_study.py <data_dir> <out.parquet> <factory.json with the promoted rules>

First run (11 Nov 2026, the Mac's 1-year Lab data, 109 promoted straddle rules, 2,247 holdout trades, per trade after
costs, train / validation / holdout): naked ss25 +469 / +545 / +283 (66 of 109 rules positive in all three);
the same straddle + wings bought 10 strikes away +35 / +444 / +13 (22 of 109); 8 strikes -31 / +468 / -39 (18);
4 strikes -94 / +447 / -152 (14); every iron fly (wings 4 / 8 / 10, out at 30-150% of the credit) negative, 0 of 109.
The wings cost about what the straddle earns intraday.

Hedged straddle challengers vs the naked straddle, on the same Lab bars (1-year Lab data).

For every 5-minute bar where the Lab sells the ATM straddle (Nifty, Bank Nifty), add P&L per lot for:
  hs{SL}w{W}: the same straddle (each short leg out at its own +SL% stop, or 15:15) PLUS bought wings W strikes away,
              held until both short legs are out (then sold) - the naked straddle with insurance;
  fly{L}w{W}: iron fly with wings W strikes away, out when the whole position has lost L% of the credit, or 15:15.
Then score every promoted straddle rule (the Mac's factory.json) with each exit on train / validation / holdout."""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import factory as F                      # noqa: E402

DATA = Path(sys.argv[1])
OUT = Path(sys.argv[2])
RULES = Path(sys.argv[3])
SLS = (25, 35, 50)
WINGS = (4, 6, 8, 10)
FLY = [(30, 4), (60, 4), (60, 8), (100, 8), (100, 10), (150, 10)]


def short_exit(px, m0, sym, lot, sl):
    c = px["c"]
    e = c[m0]
    if math.isnan(e) or e <= 0.5:
        return None
    entry = max(0.05, e - F.slip(sym, e))
    seg = c[m0 + 1:F.M_EXIT + 1]
    hit = np.nan_to_num(seg, nan=-1) >= entry * (1 + sl / 100)
    if hit.any():
        j = int(np.argmax(hit))
    else:
        ok = np.where(~np.isnan(seg))[0]
        if not len(ok):
            return None
        j = int(ok[-1])
    x = seg[j]
    ex = x + F.slip(sym, x)
    return entry, ex, m0 + 1 + j


def hedged(legs, wings, m0, sym, lot, sl):
    outs = [short_exit(px, m0, sym, lot, sl) for px in legs]
    if any(o is None for o in outs):
        return math.nan
    pnl, fills = 0.0, []
    for entry, ex, _ in outs:
        pnl += (entry - ex) * lot
        fills += [(-1, entry), (1, ex)]
    t_out = max(o[2] for o in outs)
    for w in wings:
        c = w["c"]
        e = c[m0]
        if math.isnan(e) or e <= 0:
            return math.nan
        buy = e + F.slip(sym, e)
        seg = c[m0 + 1:t_out + 1]
        ok = np.where(~np.isnan(seg))[0]
        x = seg[ok[-1]] if len(ok) else e
        sell = max(0.05, x - F.slip(sym, x))
        pnl += (sell - buy) * lot
        fills += [(1, buy), (-1, sell)]
    return pnl - F.charges(fills, lot)


def main():
    rows = []
    t0 = time.time()
    for sym in ("NIFTY", "BANKNIFTY"):
        lot = F.UNDERLYINGS[sym]["lot"]
        n = 0
        for day in F.iter_days(DATA, sym):
            sp = day.spot
            nb = int(np.sum(~np.isnan(sp)) // 5)
            for i in range(nb):
                tod = (i + 1) * 5
                if tod < F.SELL_FIRST_TOD or tod > F.LAST_ENTRY_TOD:
                    continue
                m0 = 5 * i + 5
                if m0 >= F.M_EXIT:
                    continue
                k = day.atm(m0)
                legs = [day.px.get((float(k), sd)) for sd in ("CE", "PE")]
                if not all(lg is not None for lg in legs):
                    continue
                st = day.step
                r = {"sym": sym, "date": day.date, "bar": i}
                for W in WINGS:
                    wg = [day.px.get((float(k + W * st), "CE")), day.px.get((float(k - W * st), "PE"))]
                    ok = all(w is not None for w in wg)
                    for sl in SLS:
                        r[f"pnl_hs{sl}w{W}"] = hedged(legs, wg, m0, sym, lot, sl) if ok else math.nan
                    for L, WW in FLY:
                        if WW == W:
                            r[f"pnl_fly{L}w{W}"] = F._sim_fly(legs, wg, m0, sym, lot, L) if ok else math.nan
                rows.append(r)
            n += 1
        print(f"{sym}: {n} days, {len(rows):,} rows so far ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT)
    print("saved", OUT, df.shape, flush=True)


if __name__ == "__main__":
    if not OUT.exists():
        main()
    # ---- score
    bars = pd.read_parquet(DATA / "lab" / "factory_bars.parquet")
    extra = pd.read_parquet(OUT)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    extra["date"] = pd.to_datetime(extra["date"]).dt.date
    m = bars.merge(extra.assign(dir=0), on=["sym", "date", "bar", "dir"], how="left")
    newcols = [c for c in extra.columns if c.startswith("pnl_")]
    cover = m.loc[m["dir"] == 0, newcols[0]].notna().mean()
    print(f"straddle rows with hedged prices: {cover:.0%}", flush=True)
    for c in newcols:
        F.SELL_EXITS[c[4:]] = 0                     # let score() read these columns (study only, not saved)
        F.ALL_EXITS.append(c[4:])
    book = F.Book(m)
    rules = json.loads(Path(RULES).read_text())["rules"]
    prom = {k: r for k, r in rules.items() if r.get("dir") == 0 and r.get("stage") == "promoted"}
    print("promoted straddle rules:", len(prom), flush=True)
    exits = ["ss25", "ss35", "ss50"] + [c[4:] for c in newcols]
    res = {e: {"train": [], "valid": [], "hold": [], "pass": 0} for e in exits}
    per_rule = []
    for kid, r in prom.items():
        row = {"id": kid, "own": r.get("exit")}
        for e in exits:
            s = F.validate({**r, "exit": e, "source": r.get("source")}) or {**r, "exit": e}
            try:
                sc = F.score(book, {**s, "id": kid})
            except Exception as ex:
                continue
            for p in ("train", "valid", "hold"):
                if sc[p].get("n"):
                    res[e][p].append((sc[p]["n"], sc[p].get("avg") or 0, sc[p].get("t") or 0))
            ok = all((sc[p].get("avg") or -1) > 0 and (sc[p].get("n") or 0) >= 10 for p in ("train", "valid", "hold"))
            res[e]["pass"] += ok
            row[e] = (round(sc["train"].get("avg") or 0), round(sc["valid"].get("avg") or 0), round(sc["hold"].get("avg") or 0),
                      sc["hold"].get("n"))
        per_rule.append(row)
    print(f"\n{'exit':10s} {'train avg':>10s} {'valid avg':>10s} {'hold avg':>10s} {'hold trades':>11s} {'rules +ve in all 3':>18s}")
    for e in exits:
        def wavg(p):
            x = res[e][p]
            n = sum(a for a, _, _ in x)
            return (sum(a * b for a, b, _ in x) / n if n else float("nan")), n
        tr, va, ho = wavg("train"), wavg("valid"), wavg("hold")
        print(f"{e:10s} {tr[0]:10.0f} {va[0]:10.0f} {ho[0]:10.0f} {ho[1]:11d} {res[e]['pass']:>10d} / {len(prom)}")
    pd.DataFrame(per_rule).to_json(OUT.with_suffix(".rules.json"), orient="records")
