"""Market simulator. Produces the same tick format as upstox.decode() and the same REST shapes
as UpstoxREST, so the whole app can be tested (and demoed) without a broker login."""
from __future__ import annotations

import datetime as dt
import math
import random
import threading
import time

from .clock import IST, OPEN, SESSION_MIN, minutes_since_open
from .universe import INDEX_NAMES, VIX_KEY, Option, Underlying, Universe

R = 0.065

# symbol, price, lot, annual vol
INDICES = [("NIFTY", 25150, 75, 0.12, 50), ("BANKNIFTY", 56400, 35, 0.14, 100),
           ("FINNIFTY", 26700, 65, 0.13, 50), ("MIDCPNIFTY", 13150, 140, 0.16, 25)]
STOCKS = [("RELIANCE", 1415, 500, .24), ("HDFCBANK", 985, 550, .20), ("ICICIBANK", 1370, 700, .22),
          ("INFY", 1525, 400, .26), ("TCS", 3120, 175, .22), ("SBIN", 865, 750, .27),
          ("AXISBANK", 1180, 625, .26), ("KOTAKBANK", 2140, 400, .24), ("LT", 3720, 175, .25),
          ("ITC", 412, 1600, .20), ("BHARTIARTL", 1965, 475, .23), ("HINDUNILVR", 2490, 300, .21),
          ("BAJFINANCE", 1012, 750, .32), ("MARUTI", 15900, 50, .25), ("TATAMOTORS", 685, 800, .36),
          ("M&M", 3510, 200, .30), ("SUNPHARMA", 1640, 350, .24), ("TITAN", 3600, 175, .27),
          ("ADANIENT", 2410, 300, .42), ("ADANIPORTS", 1445, 475, .35), ("TATASTEEL", 168, 5500, .33),
          ("JSWSTEEL", 1105, 675, .30), ("HCLTECH", 1490, 350, .27), ("WIPRO", 248, 3000, .26),
          ("ULTRACEMCO", 12100, 50, .24), ("ONGC", 243, 2250, .30), ("NTPC", 342, 1500, .26),
          ("POWERGRID", 292, 1900, .25), ("COALINDIA", 388, 1350, .30), ("HAL", 4780, 150, .40),
          ("BEL", 412, 1425, .38), ("DIXON", 16800, 50, .45), ("ZOMATO", 318, 2425, .45),
          ("SWIGGY", 412, 1300, .50), ("PAYTM", 1190, 725, .48), ("IRFC", 128, 4250, .40),
          ("VEDL", 462, 1150, .36), ("HINDALCO", 742, 700, .30), ("DLF", 812, 825, .36),
          ("INDUSINDBK", 760, 700, .40), ("PNB", 112, 8000, .34), ("CANBK", 118, 6750, .36)]


# ---------------------------------------------------------------- Black-Scholes
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs(S, K, T, sig, kind):
    """Price and greeks (theta per day, vega per 1 vol point)."""
    T = max(T, 1 / (252 * 375))
    sig = max(sig, 0.01)
    st = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + sig * sig / 2) * T) / st
    d2 = d1 - st
    disc = math.exp(-R * T)
    if kind == "CE":
        price = S * _ncdf(d1) - K * disc * _ncdf(d2)
        delta = _ncdf(d1)
        theta = -S * _npdf(d1) * sig / (2 * math.sqrt(T)) - R * K * disc * _ncdf(d2)
    else:
        price = K * disc * _ncdf(-d2) - S * _ncdf(-d1)
        delta = _ncdf(d1) - 1
        theta = -S * _npdf(d1) * sig / (2 * math.sqrt(T)) + R * K * disc * _ncdf(-d2)
    gamma = _npdf(d1) / (S * st)
    vega = S * _npdf(d1) * math.sqrt(T) / 100
    return max(0.05, price), delta, gamma, theta / 365, vega


def tick_round(x, t=0.05):
    return round(round(x / t) * t, 2)


def _step_for(price):
    for lim, s in ((100, 1), (250, 2.5), (500, 5), (1000, 10), (2500, 20), (5000, 50), (1e9, 100)):
        if price < lim:
            return s


def _tuesday_on_or_after(d):
    return d + dt.timedelta(days=(1 - d.weekday()) % 7)


def _last_tuesday(year, month):
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 1) % 7)


def _monthly(today):
    e = _last_tuesday(today.year, today.month)
    if e < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        e = _last_tuesday(y, m)
    return e


def build_universe(today: dt.date) -> Universe:
    unds, by_key = {}, {}
    month = _monthly(today)
    for sym, px, lot, vol, step in INDICES:
        key = f"NSE_INDEX|{INDEX_NAMES[sym]}"
        u = Underlying(sym, INDEX_NAMES[sym], key, True, lot, fut_key=f"NSE_FO|SIMFUT_{sym}", fut_expiry=month)
        exps = [month]
        if sym == "NIFTY":
            w = _tuesday_on_or_after(today)
            exps = sorted({w, w + dt.timedelta(days=7), month})
        _chain(u, exps, px, step, 40, by_key)
        unds[sym] = u
    for sym, px, lot, vol in STOCKS:
        u = Underlying(sym, sym.title(), f"NSE_EQ|SIM_{sym}", False, lot, fut_key=f"NSE_FO|SIMFUT_{sym}", fut_expiry=month)
        _chain(u, [month], px, _step_for(px), 15, by_key)
        unds[sym] = u
    for sym, u in unds.items():
        by_key[u.key] = ("UND", sym, None)
        by_key[u.fut_key] = ("FUT", sym, None)
    by_key[VIX_KEY] = ("VIX", "INDIAVIX", None)
    return Universe(underlyings=unds, by_key=by_key, built=today.isoformat())


def _chain(u, exps, px, step, n, by_key):
    atm = round(px / step) * step
    for e in exps:
        for i in range(-n, n + 1):
            k = float(atm + i * step)
            if k <= 0:
                continue
            for kind in ("CE", "PE"):
                o = Option(f"NSE_FO|SIM_{u.symbol}_{e:%y%m%d}_{k:g}_{kind}", u.symbol, kind, k, e, u.lot)
                u.chains.setdefault(e, {}).setdefault(k, {})[kind] = o
                by_key[o.key] = ("OPT", u.symbol, o)
    u.expiries = sorted(u.chains)


# ---------------------------------------------------------------- market model
class _U:
    pass


class SimMarket:
    def __init__(self, clock, seed=None, shock_every_min=9.0):
        self.clock = clock
        self.rng = random.Random(seed)
        self.today = clock.now().date()
        self.uni = build_universe(self.today)
        self.lock = threading.RLock()
        self.shock_every = shock_every_min
        self.t = clock.ts()
        self.mkt_drift = 0.0
        self.u: dict[str, _U] = {}
        specs = {s[0]: s for s in INDICES} | {s[0]: s for s in STOCKS}
        for sym, und in self.uni.underlyings.items():
            sp = specs[sym]
            x = _U()
            x.sym, x.und = sym, und
            x.cp = float(sp[1])
            gap = self.rng.gauss(0, sp[3] / math.sqrt(252) * 0.4)
            x.S = x.cp * (1 + gap)
            x.o = x.h = x.l = x.S
            x.sig = sp[3]
            x.beta = 1.0 if und.is_index else self.rng.uniform(0.6, 1.4)
            x.trend = 0.0
            x.shock = None            # (minutes_left, ret_per_min, vol_mult, iv_bump, burst_key)
            x.iv_bump = 0.0
            x.adv = 0 if und.is_index else self.rng.uniform(4e6, 2.5e7) * 1000 / max(100, x.cp)
            x.vol = 0.0
            x.pv = 0.0
            x.fut_vol = 0.0
            x.fut_pv = 0.0
            x.fut_prev_oi = (und.lot * self.rng.randint(4000, 60000)) if not und.is_index else und.lot * self.rng.randint(80000, 300000)
            x.fut_oi = x.fut_prev_oi
            x.fut_bias = self.rng.choice((1, 1, -1))   # +1: OI rises with trend (fresh positions), -1: covering/unwinding
            x.hist = [x.S]
            self.u[sym] = x
        self.vix_cp = 13.4
        self.vix = self.vix_cp * (1 + self.rng.gauss(0, 0.02))
        self.vix_o = self.vix_h = self.vix_l = self.vix
        self.opt = {}   # key -> [prev_oi, oi, vol, burst_left, burst_rate, last]
        self.key_index = self.uni.by_key
        self.shocks_log = []

    # option base OI shaped around the money (calls heavier above, puts below)
    def _opt_state(self, o: Option, x: _U):
        s = self.opt.get(o.key)
        if s is None:
            step = (sorted(x.und.chains[o.expiry])[1] - sorted(x.und.chains[o.expiry])[0]) if len(x.und.chains[o.expiry]) > 1 else 1
            d = (o.strike - x.cp) / step
            side = d if o.kind == "CE" else -d
            w = math.exp(-((side - 3) / 6) ** 2) * (1.0 if side >= 0 else 0.35)
            if abs(round(o.strike / (step * 5)) * step * 5 - o.strike) < 1e-6:
                w *= 1.6                      # round strikes attract more OI
            near = 1.0 if o.expiry == x.und.expiries[0] else 0.4
            scale = (300000 if x.und.is_index else 8000) * o.lot
            prev = int(scale * w * near * self.rng.uniform(0.6, 1.4)) // o.lot * o.lot + o.lot
            s = self.opt[o.key] = [prev, prev, 0.0, 0, 0.0, None]
        return s

    def _T(self, expiry, now):
        close = dt.datetime.combine(expiry, dt.time(15, 30), IST)
        return max((close - now).total_seconds(), 60) / (365 * 86400)

    def _iv(self, x, K, now, S=None, base=False):
        m = math.log(K / (S or x.S))
        skew = 0.9 * abs(m) + (0.25 * -m if m < 0 else 0)
        if base:
            return x.sig + skew
        return x.sig * (self.vix / self.vix_cp) ** 0.5 + skew + x.iv_bump

    def step(self):
        """Advance the market to the clock's current time."""
        with self.lock:
            now_ts = self.clock.ts()
            dts = now_ts - self.t
            if dts <= 0:
                return
            self.t = now_ts
            now = self.clock.now()
            mins = minutes_since_open(now)
            if mins >= SESSION_MIN or now.time() < OPEN:
                return
            dtm = dts / 60
            shape = 1.6 if mins < 30 else (1.3 if mins > 330 else 0.8)
            # market factor with slowly changing drift
            if self.rng.random() < dtm / 25:
                self.mkt_drift = self.rng.gauss(0, 0.004)          # % per minute
            m_ret = self.mkt_drift * dtm + self.rng.gauss(0, 0.025) * math.sqrt(dtm)
            # random shocks
            if self.rng.random() < dtm / self.shock_every:
                self._new_shock()
            for x in self.u.values():
                sig_m = x.sig / math.sqrt(252 * SESSION_MIN) * 100
                if self.rng.random() < dtm / 40:
                    x.trend = self.rng.gauss(0, sig_m * 0.1)
                r = x.beta * m_ret * (0.7 if not x.und.is_index else 1) + x.trend * dtm + self.rng.gauss(0, sig_m * (0.35 if x.und.is_index else 0.6)) * math.sqrt(dtm)
                vm = 1.0
                if x.shock:
                    left, rpm, vm, ivb, burst = x.shock
                    use = min(left, dtm)
                    r += rpm * use
                    x.iv_bump = max(x.iv_bump, ivb)
                    left -= dtm
                    x.shock = (left, rpm, vm, ivb, burst) if left > 0 else None
                else:
                    x.iv_bump *= math.exp(-dtm / 30)
                x.S *= 1 + r / 100
                x.h, x.l = max(x.h, x.S), min(x.l, x.S)
                if not x.und.is_index:
                    dv = x.adv / SESSION_MIN * dtm * shape * vm * self.rng.uniform(0.5, 1.5)
                    x.vol += dv
                    x.pv += dv * x.S
                fdv = (x.adv * 0.15 if x.adv else x.und.lot * 2e5) / SESSION_MIN * dtm * shape * vm * self.rng.uniform(0.5, 1.5)
                F = x.S * (1 + R * self._T(x.und.fut_expiry, now))
                x.fut_vol += fdv
                x.fut_pv += fdv * F
                trend_sign = 1 if x.S > x.o else -1
                dOI = x.fut_prev_oi * 0.0006 * dtm * (abs(r) / max(sig_m, 1e-9)) * x.fut_bias * vm
                x.fut_oi = max(x.und.lot, x.fut_oi + dOI + self.rng.gauss(0, x.fut_prev_oi * 0.0003) * math.sqrt(dtm))
                x.hist.append(x.S)
                if len(x.hist) > 60:
                    x.hist.pop(0)
                x._trend_sign = trend_sign
                x._vm = vm
            # VIX follows the market inversely
            self.vix *= 1 - m_ret / 100 * 4 + self.rng.gauss(0, 0.002) * math.sqrt(dtm)
            self.vix_h, self.vix_l = max(self.vix_h, self.vix), min(self.vix_l, self.vix)
            # options that have state get flows
            for key, s in self.opt.items():
                kind, sym, o = self.key_index[key]
                x = self.u[sym]
                step = o.strike * 0.01
                near = math.exp(-((o.strike - x.S) / (x.S * (0.01 if x.und.is_index else 0.03))) ** 2)
                prev = s[0]
                rate = prev * 0.9 / SESSION_MIN * (0.3 + near) * shape * getattr(x, "_vm", 1.0)
                if s[3] > 0:
                    rate += s[4]
                    s[1] += s[4] * dtm * 0.25
                    s[3] -= dtm
                s[2] += rate * dtm * self.rng.uniform(0.6, 1.4)
                tsgn = getattr(x, "_trend_sign", 1)
                writing = (1 if (o.kind == "PE") == (tsgn > 0) else -0.5)
                s[1] = max(o.lot, s[1] + prev * 0.0012 * dtm * (near + 0.2) * (writing + self.rng.gauss(0.2, 0.5)))

    def _new_shock(self):
        sym = self.rng.choice(list(self.u))
        x = self.u[sym]
        if x.shock:
            return
        sig_m = x.sig / math.sqrt(252 * SESSION_MIN) * 100
        d = self.rng.choice((1, -1))
        mins = self.rng.uniform(3, 6)
        total = d * sig_m * self.rng.uniform(6, 11) * (0.6 if x.und.is_index else 1)
        burst = None
        exp = x.und.expiries[0]
        strikes = sorted(x.und.chains[exp])
        if strikes:
            atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - x.S))
            k = strikes[max(0, min(len(strikes) - 1, atm_i + d))]
            o = x.und.chains[exp][k]["CE" if d > 0 else "PE"]
            s = self._opt_state(o, x)
            s[3] = mins + 2
            s[4] = max(s[0] * 0.08, o.lot * 60) / 1.0
            burst = o.key
        x.shock = (mins, total / mins, self.rng.uniform(3, 7), self.rng.uniform(1.5, 4) / 100, burst)
        self.shocks_log.append((self.clock.now().strftime("%H:%M"), sym, "up" if d > 0 else "down"))

    # ------------------------------------------------------------ outputs
    def tick(self, key, mode="full"):
        kind, sym, o = self.key_index.get(key, (None, None, None))
        now = self.clock.now()
        ltt = int(now.timestamp() * 1000)
        if kind == "VIX":
            return {"key": key, "ltp": round(self.vix, 2), "cp": self.vix_cp, "ltt": ltt,
                    "day_o": round(self.vix_o, 2), "day_h": round(self.vix_h, 2), "day_l": round(self.vix_l, 2)}
        if kind is None:
            return None
        x = self.u[sym]
        if kind == "UND":
            t = {"key": key, "ltp": tick_round(x.S), "cp": x.cp, "ltt": ltt,
                 "day_o": tick_round(x.o), "day_h": tick_round(x.h), "day_l": tick_round(x.l)}
            if not x.und.is_index:
                sp = max(0.05, tick_round(x.S * 0.0002))
                lean = 1 if x.S >= x.hist[-2 if len(x.hist) > 1 else -1] else -1
                ltq = self.rng.randint(1, 400)
                if x.shock and self.rng.random() < 0.04:
                    ltq = int(self.rng.uniform(6e7, 3e8) / x.S)      # a block-size print during a shock
                t.update(vol=int(x.vol), atp=round(x.pv / x.vol, 2) if x.vol else None, oi=0, ltq=ltq,
                         bid=tick_round(x.S - sp), ask=tick_round(x.S + sp), bidq=self.rng.randint(50, 3000),
                         askq=self.rng.randint(50, 3000), tbq=int(self.rng.randint(100000, 900000) * (1.3 if lean > 0 else 0.8)),
                         tsq=int(self.rng.randint(100000, 900000) * (1.3 if lean < 0 else 0.8)))
            return t
        if kind == "FUT":
            F = x.S * (1 + R * self._T(x.und.fut_expiry, now))
            Fcp = x.cp * (1 + R * self._T(x.und.fut_expiry, now))
            sp = max(0.05, tick_round(F * 0.0002))
            return {"key": key, "ltp": tick_round(F), "cp": tick_round(Fcp), "ltt": ltt, "vol": int(x.fut_vol),
                    "oi": int(x.fut_oi // x.und.lot * x.und.lot), "atp": round(x.fut_pv / x.fut_vol, 2) if x.fut_vol else None,
                    "bid": tick_round(F - sp), "ask": tick_round(F + sp), "bidq": x.und.lot * self.rng.randint(1, 40),
                    "askq": x.und.lot * self.rng.randint(1, 40), "day_o": tick_round(x.o * F / x.S),
                    "day_h": tick_round(x.h * F / x.S), "day_l": tick_round(x.l * F / x.S)}
        s = self._opt_state(o, x)
        iv = self._iv(x, o.strike, now)
        T = self._T(o.expiry, now)
        p, de, ga, th, ve = bs(x.S, o.strike, T, iv, o.kind)
        p = tick_round(p)
        if s[5] is None:
            # previous close of the option: same model at yesterday's close
            s.append(tick_round(bs(x.cp, o.strike, T + 1 / 365, self._iv(x, o.strike, now, S=x.cp, base=True), o.kind)[0]))
        s[5] = p
        sp = max(0.05, tick_round(p * 0.004))
        return {"key": key, "ltp": p, "cp": s[6], "ltt": ltt, "vol": int(s[2] // o.lot * o.lot),
                "oi": int(s[1] // o.lot * o.lot), "iv": round(iv, 4), "bid": tick_round(max(0.05, p - sp)),
                "ask": tick_round(p + sp), "bidq": o.lot * self.rng.randint(1, 60), "askq": o.lot * self.rng.randint(1, 60),
                "delta": round(de, 4), "gamma": round(ga, 6), "theta": round(th, 2), "vega": round(ve, 2)}


class SimREST:
    """Same interface as UpstoxREST, answered from the simulator."""

    def __init__(self, market: SimMarket):
        self.m = market
        self.limiter = None

    def profile(self):
        return {"user_name": "Simulator", "user_id": "SIM"}

    def quotes(self, keys):
        out = {}
        with self.m.lock:
            for k in keys:
                t = self.m.tick(k)
                if not t:
                    continue
                kind, sym, o = self.m.key_index[k]
                prev_oi = 0
                if kind == "FUT":
                    prev_oi = self.m.u[sym].fut_prev_oi
                elif kind == "OPT":
                    prev_oi = self.m.opt[k][0]
                out[k] = {"instrument_token": k, "last_price": t["ltp"], "prev_close_price": t["cp"],
                          "volume": t.get("vol") or 0, "average_price": t.get("atp") or 0, "oi": t.get("oi") or 0,
                          "previous_oi": prev_oi, "net_change": round(t["ltp"] - t["cp"], 2),
                          "ohlc": {"open": t.get("day_o"), "high": t.get("day_h"), "low": t.get("day_l"), "close": t["ltp"]}}
        return out

    def option_chain(self, underlying_key, expiry):
        sym = self.m.key_index[underlying_key][1]
        u = self.m.uni.underlyings[sym]
        x = self.m.u[sym]
        rows = []
        with self.m.lock:
            for k in sorted(u.chains.get(expiry, {})):
                pair = u.chains[expiry][k]
                row = {"expiry": expiry.isoformat(), "strike_price": k, "underlying_key": underlying_key,
                       "underlying_spot_price": round(x.S, 2)}
                for side, name in (("CE", "call_options"), ("PE", "put_options")):
                    t = self.m.tick(pair[side].key)
                    s = self.m.opt[pair[side].key]
                    row[name] = {"instrument_key": pair[side].key,
                                 "market_data": {"ltp": t["ltp"], "volume": t["vol"], "oi": t["oi"], "close_price": t["cp"],
                                                 "bid_price": t["bid"], "bid_qty": t["bidq"], "ask_price": t["ask"],
                                                 "ask_qty": t["askq"], "prev_oi": s[0]},
                                 "option_greeks": {"vega": t["vega"], "theta": t["theta"], "gamma": t["gamma"],
                                                   "delta": t["delta"], "iv": round(t["iv"] * 100, 2), "pop": None}}
                rows.append(row)
            ce = sum(r["call_options"]["market_data"]["oi"] for r in rows)
            pe = sum(r["put_options"]["market_data"]["oi"] for r in rows)
            for r in rows:
                r["pcr"] = round(pe / ce, 4) if ce else 0
        return rows

    def daily_candles(self, key, days=40):
        """A year of made-up daily candles ending at yesterday's close, with realistic volatility."""
        kind, sym, _ = self.m.key_index[key]
        rng = random.Random(hash(key) & 0xffffffff)
        if kind == "VIX":
            last, sig, adv = self.m.vix_cp, 0.6, 0
        else:
            x = self.m.u[sym]
            last, sig, adv = x.cp, x.sig * rng.uniform(0.75, 1.25), x.adv
        n = min(days, 260)
        px, closes = last, [last]
        for _ in range(n - 1):
            px = px / math.exp(rng.gauss(0, sig / math.sqrt(252)))
            closes.append(px)
        closes.reverse()
        out = []
        for i, c in enumerate(reversed(closes)):
            d = self.m.today - dt.timedelta(days=i + 1)
            v = int(adv * rng.uniform(0.7, 1.3))
            out.append([f"{d.isoformat()}T00:00:00+05:30", c, c * 1.01, c * 0.99, round(c, 2), v, 0])
        return out


class SimFeed:
    """Same interface as UpstoxFeed; pushes ticks for subscribed keys a few times a second."""

    def __init__(self, market: SimMarket, on_ticks, on_status=print, interval=0.5):
        self.m = market
        self.on_ticks = on_ticks
        self.on_status = on_status
        self.interval = interval
        self.subs = {"full": set(), "option_greeks": set(), "ltpc": set()}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.connected = False
        self.msgs = 0
        self.last_msg = 0.0
        self.thread = None

    def start(self):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="sim-feed")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def subscribe(self, keys, mode):
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
            self.subs[mode].update(k for k in keys if k)

    def unsubscribe(self, keys):
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)

    def counts(self):
        return {m: len(v) for m, v in self.subs.items()}

    def _run(self):
        self.connected = True
        self.on_status("Simulator feed running")
        first = True
        while not self.stop_flag.is_set():
            self.m.step()
            with self.lock:
                keys = [k for v in self.subs.values() for k in v]
            # like the real feed: a full snapshot first, then roughly a third of instruments per message
            if not first:
                keys = [k for k in keys if self.m.rng.random() < 0.35]
            first = False
            with self.m.lock:
                ticks = [t for t in (self.m.tick(k) for k in keys) if t]
            if ticks:
                self.msgs += 1
                self.last_msg = time.time()
                self.on_ticks(ticks)
            self.stop_flag.wait(self.interval)
        self.connected = False
