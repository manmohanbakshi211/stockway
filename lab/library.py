"""Strategy library: well-known trading setups written as Strategy Lab rules, in our own words, with where they come from.

Each entry is tested exactly like any other rule (train / validation / holdout, real charges and slippage), with every
exit rule and the usual indicator settings, so the Lab can say which version, if any, holds up on Nifty and Bank Nifty.
Being famous is not evidence: these are hypotheses. Sources point to public descriptions; where a trader's exact rules
are not published in text (several YouTube-taught setups), the common public version is used and the note says so.
"""
from __future__ import annotations

from .factory import BUY_EXITS, SELL_EXITS

B = ["BANKNIFTY", "NIFTY"]

# name -> (source note, [rules]); a rule: dir (1 call / -1 put / 0 sell straddle), conds, win (minutes since 09:15)
LIBRARY = {
    "First 5-min candle breakout (\"9:20 setup\")": (
        "Popular with Indian option buyers (e.g. the 9:20 Bank Nifty setup taught by Ghanshyam Tech / Art of Trading; exact "
        "rules are in videos, so the common version is used): trade the break of the 9:15-9:20 candle.",
        [{"dir": 1, "conds": [["fc_pos", ">", 0.5]], "win": [15, 105]}, {"dir": -1, "conds": [["fc_pos", "<", -0.5]], "win": [15, 105]},
         {"dir": 1, "conds": [["fc_pos", ">", 0.5], ["vs_twap", ">", 0]], "win": [15, 105]},
         {"dir": -1, "conds": [["fc_pos", "<", -0.5], ["vs_twap", "<", 0]], "win": [15, 105]}]),
    "2:20 candle breakout (afternoon)": (
        "Afternoon variant taught on Indian YouTube (\"2:20 strategy\"): trade the break of the 14:15-14:20 candle.",
        [{"dir": 1, "conds": [["pm_pos", ">", 0.5]], "win": [310, 330]}, {"dir": -1, "conds": [["pm_pos", "<", -0.5]], "win": [310, 330]}]),
    "Opening range breakout, 15 and 30 min (Toby Crabel)": (
        "Crabel, 'Day Trading with Short Term Price Patterns and Opening Range Breakout' (1990); widely used on Bank Nifty.",
        [{"dir": 1, "conds": [["or_pos", ">", 0.5]], "win": [15, 120]}, {"dir": -1, "conds": [["or_pos", "<", -0.5]], "win": [15, 120]},
         {"dir": 1, "conds": [["or30_pos", ">", 0.5]], "win": [30, 150]}, {"dir": -1, "conds": [["or30_pos", "<", -0.5]], "win": [30, 150]}]),
    "Supertrend + VWAP trend following": (
        "Common Indian intraday system: trade with Supertrend (10,3 / 7,3 / 10,2 tested) when price is on the same side of VWAP.",
        [{"dir": 1, "conds": [["st_10_3", ">", 0], ["vs_twap", ">", 0]], "win": [30, 300]},
         {"dir": -1, "conds": [["st_10_3", "<", 0], ["vs_twap", "<", 0]], "win": [30, 300]},
         {"dir": 1, "conds": [["st_7_3", ">", 0], ["vs_twap", ">", 0]], "win": [30, 300]},
         {"dir": -1, "conds": [["st_7_3", "<", 0], ["vs_twap", "<", 0]], "win": [30, 300]},
         {"dir": 1, "conds": [["st_10_2", ">", 0], ["vs_twap", ">", 0]], "win": [30, 300]},
         {"dir": -1, "conds": [["st_10_2", "<", 0], ["vs_twap", "<", 0]], "win": [30, 300]}]),
    "Fresh Supertrend flip": (
        "Enter on the first bars after Supertrend 10,3 changes direction.",
        [{"dir": 1, "conds": [["st_10_3", ">", 0], ["st_flip", "<", 1.5]], "win": [30, 300]},
         {"dir": -1, "conds": [["st_10_3", "<", 0], ["st_flip", "<", 1.5]], "win": [30, 300]}]),
    "Holy Grail (Linda Raschke)": (
        "Raschke & Connors, 'Street Smarts' (1995): ADX 14 above 30 (strong trend), buy the pullback to the 20-period average.",
        [{"dir": 1, "conds": [["adx", ">", 30], ["di_diff", ">", 0], ["vs_sma20", "<", 0.05]], "win": [90, 300]},
         {"dir": -1, "conds": [["adx", ">", 30], ["di_diff", "<", 0], ["vs_sma20", ">", -0.05]], "win": [90, 300]}]),
    "RSI(2) pullback (Larry Connors)": (
        "Connors & Alvarez, 'Short Term Trading Strategies That Work' (2008): buy very short-term oversold pullbacks in an uptrend; "
        "adapted to intraday (day up = uptrend).",
        [{"dir": 1, "conds": [["rsi2", "<", 10], ["day_ret", ">", 0.2]], "win": [45, 300]},
         {"dir": -1, "conds": [["rsi2", ">", 90], ["day_ret", "<", -0.2]], "win": [45, 300]}]),
    "Bollinger squeeze breakout (John Bollinger)": (
        "Bollinger, 'Bollinger on Bollinger Bands' (2001): a narrow band width precedes expansion; trade the break of the band.",
        [{"dir": 1, "conds": [["bb_width", "<", 0.2], ["bb_pctb", ">", 1]], "win": [100, 320]},
         {"dir": -1, "conds": [["bb_width", "<", 0.2], ["bb_pctb", "<", 0]], "win": [100, 320]}]),
    "Bollinger band fade in a range": (
        "Band-touch mean reversion when trend strength is low (ADX under 20); 2.0 and 1.5 standard-deviation bands tested.",
        [{"dir": 1, "conds": [["bb_pctb", "<", 0], ["adx", "<", 20]], "win": [100, 320]},
         {"dir": -1, "conds": [["bb_pctb", ">", 1], ["adx", "<", 20]], "win": [100, 320]},
         {"dir": 1, "conds": [["bb15_pctb", "<", 0], ["adx", "<", 20]], "win": [100, 320]},
         {"dir": -1, "conds": [["bb15_pctb", ">", 1], ["adx", "<", 20]], "win": [100, 320]}]),
    "VWAP bands: fade the stretch, ride the trend": (
        "Institutional VWAP usage: 2 standard deviations from VWAP in a quiet market tends to revert; above 1 sd in a trending "
        "market tends to continue.",
        [{"dir": 1, "conds": [["vwap_z", "<", -2], ["adx", "<", 20]], "win": [45, 300]},
         {"dir": -1, "conds": [["vwap_z", ">", 2], ["adx", "<", 20]], "win": [45, 300]},
         {"dir": 1, "conds": [["vwap_z", ">", 1], ["adx", ">", 25]], "win": [45, 300]},
         {"dir": -1, "conds": [["vwap_z", "<", -1], ["adx", ">", 25]], "win": [45, 300]}]),
    "MACD + EMA momentum": (
        "Gerald Appel's MACD (12,26,9) with a fast EMA 5/13 and ADX filter, a common momentum stack.",
        [{"dir": 1, "conds": [["macd_h", ">", 0], ["ema5_13", ">", 0], ["adx", ">", 20]], "win": [140, 320]},
         {"dir": -1, "conds": [["macd_h", "<", 0], ["ema5_13", "<", 0], ["adx", ">", 20]], "win": [140, 320]}]),
    "EMA 9/21 trend": (
        "The 9/21 EMA pair used by many Indian intraday traders: trade in the direction of the cross with price above/below VWAP.",
        [{"dir": 1, "conds": [["ema_gap", ">", 0.03], ["vs_twap", ">", 0]], "win": [60, 300]},
         {"dir": -1, "conds": [["ema_gap", "<", -0.03], ["vs_twap", "<", 0]], "win": [60, 300]}]),
    "Narrow CPR trend day (Frank Ochoa, Pivot Boss)": (
        "Ochoa, 'Secrets of a Pivot Boss' (2010): a narrow central pivot range points to a trending day; trade the break away from it.",
        [{"dir": 1, "conds": [["cpr_width", "<", 0.15], ["cpr_pos", ">", 0.05]], "win": [30, 240]},
         {"dir": -1, "conds": [["cpr_width", "<", 0.15], ["cpr_pos", "<", -0.05]], "win": [30, 240]}]),
    "Previous-day high / low breakout": (
        "Classic level breakout: a fresh push through yesterday's high (or low) with momentum.",
        [{"dir": 1, "conds": [["pdh_dist", ">", 0], ["pdh_dist", "<", 0.25], ["ret_b3", ">", 0.1]], "win": [30, 270]},
         {"dir": -1, "conds": [["pdl_dist", "<", 0], ["pdl_dist", ">", -0.25], ["ret_b3", "<", -0.1]], "win": [30, 270]}]),
    "Late-morning quiet-day straddle / iron fly": (
        "Found by the Lab's own search on 2 years of data (Oct 2026) and in line with marketcalls.in's suggestion to sell later "
        "than 9:20: sell between 10:00 and 11:15 when the morning has been quiet (small bars, price inside the 30-min range).",
        [{"dir": 0, "conds": [["range_x", "<", 0.83], ["or30_pos", ">", -0.5], ["or30_pos", "<", 0.5]], "win": [45, 120]},
         {"dir": 0, "conds": [["prem_pct", "<", 1.2], ["or30_pos", ">", -0.5], ["or30_pos", "<", 0.5]], "win": [60, 120]}]),
    "9:20 short straddle": (
        "The best-known Indian option-selling routine: sell the ATM call and put at 9:20, a stop on each leg (25% / 35% / 50% "
        "tested), out by 15:15. Reported to have weakened as it got crowded (marketcalls.in, 2023); a 10:30 entry was suggested.",
        [{"dir": 0, "conds": [["tod", ">", 0]], "win": [5, 5]}, {"dir": 0, "conds": [["tod", ">", 0]], "win": [75, 75]},
         {"dir": 0, "conds": [["vix", ">", 14]], "win": [5, 5]}, {"dir": 0, "conds": [["dte", "<", 0.5]], "win": [5, 5]},
         {"dir": 0, "conds": [["cpr_width", ">", 0.4]], "win": [5, 5]}]),
}


# ------------------------------------------------------------------ the basics books (3 Nov 2026)
# Setups from "The Candlestick Trading Bible", the Fidelity / Kirkpatrick webinar "Identifying Chart Patterns" and "A to Z
# of Stock Market", written as Lab rules in our own words. The candle facts are in live/candles.py. "With the trend" =
# Supertrend 10,3 or EMA 9 vs 21; "at a key level" = within about half an ATR of yesterday's high / low / close, the
# opening range or the day's average: the bar came down to (or up to) that level and closed on the right side of it
# (lvl_touch, the same test the live trial alerts use). Tested on the indices and on any F&O stock with history.
BOOK_SYMS = B + ["STOCKS"]
CTB = "The Candlestick Trading Bible"
FID = "Fidelity / Kirkpatrick, Identifying Chart Patterns"
BOOKS = {
    "Pin bar with the trend at a key level": (
        f"{CTB}: trade pin bars in the trend's direction, from support / resistance; stop beyond the tail (bar2r exit).",
        [{"dir": 1, "conds": [["cdl_pin", ">", 0.5], ["lvl_touch", ">", 0.5], ["st_10_3", ">", 0]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_pin", "<", -0.5], ["lvl_touch", "<", -0.5], ["st_10_3", "<", 0]], "win": [15, 300]},
         {"dir": 1, "conds": [["cdl_pin", ">", 0.5], ["lvl_touch", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_pin", "<", -0.5], ["lvl_touch", "<", -0.5]], "win": [15, 300]}]),
    "Pin bar rejecting the 21 EMA in a trend": (
        f"{CTB}: pin bar off the 21-period moving average, with the trend (confluence).",
        [{"dir": 1, "conds": [["cdl_pin", ">", 0.5], ["ema_gap", ">", 0], ["vs_ema21", "<", 0.15], ["vs_ema21", ">", -0.15]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_pin", "<", -0.5], ["ema_gap", "<", 0], ["vs_ema21", "<", 0.15], ["vs_ema21", ">", -0.15]], "win": [15, 300]}]),
    "Engulfing bar with the trend at the 21 EMA": (
        f"{CTB}: engulfing bar after a pullback to the moving average, in the trend's direction.",
        [{"dir": 1, "conds": [["cdl_engulf", ">", 0.5], ["ema_gap", ">", 0], ["vs_ema21", "<", 0.2], ["vs_ema21", ">", -0.2]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_engulf", "<", -0.5], ["ema_gap", "<", 0], ["vs_ema21", "<", 0.2], ["vs_ema21", ">", -0.2]], "win": [15, 300]}]),
    "Engulfing bar at the 50-61.8% retracement": (
        f"{CTB} / A to Z of Stock Market (Fibonacci): engulfing bar after the day's move retraced 50-61.8%.",
        [{"dir": 1, "conds": [["cdl_engulf", ">", 0.5], ["day_ret", ">", 0.3], ["pos_in_range", ">", 0.38], ["pos_in_range", "<", 0.5]], "win": [30, 300]},
         {"dir": -1, "conds": [["cdl_engulf", "<", -0.5], ["day_ret", "<", -0.3], ["pos_in_range", ">", 0.5], ["pos_in_range", "<", 0.62]], "win": [30, 300]}]),
    "Engulfing bar at the edge of a range": (
        f"{CTB}: in a sideways market buy engulfing bars at the bottom of the range, sell them at the top.",
        [{"dir": 1, "conds": [["cdl_engulf", ">", 0.5], ["adx", "<", 20], ["pos_in_range", "<", 0.2]], "win": [45, 300]},
         {"dir": -1, "conds": [["cdl_engulf", "<", -0.5], ["adx", "<", 20], ["pos_in_range", ">", 0.8]], "win": [45, 300]}]),
    "Inside bar breakout with the trend": (
        f"{CTB}: the break of an inside bar's mother bar, in the trend's direction.",
        [{"dir": 1, "conds": [["ib_break", ">", 0.5], ["st_10_3", ">", 0]], "win": [15, 300]},
         {"dir": -1, "conds": [["ib_break", "<", -0.5], ["st_10_3", "<", 0]], "win": [15, 300]}]),
    "Inside bar false breakout (fakey) at a level": (
        f"{CTB}: when an inside bar breaks out and closes back inside near a key level, trade the other way.",
        [{"dir": 1, "conds": [["ib_fakey", ">", 0.5], ["lvl_touch", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["ib_fakey", "<", -0.5], ["lvl_touch", "<", -0.5]], "win": [15, 300]}]),
    "Morning / evening star at the day's extreme": (
        f"{CTB}: three-candle reversal at the bottom (morning star) or top (evening star) of the day's range.",
        [{"dir": 1, "conds": [["cdl_star", ">", 0.5], ["pos_in_range", "<", 0.3]], "win": [30, 300]},
         {"dir": -1, "conds": [["cdl_star", "<", -0.5], ["pos_in_range", ">", 0.7]], "win": [30, 300]}]),
    "Hammer / shooting star at support / resistance": (
        f"{CTB} and {FID}: hammer at support, shooting star at resistance.",
        [{"dir": 1, "conds": [["cdl_hammer", ">", 0.5], ["lvl_touch", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_hammer", "<", -0.5], ["lvl_touch", "<", -0.5]], "win": [15, 300]}]),
    "Dragonfly / gravestone doji at a level": (
        f"{CTB}: dragonfly doji at support, gravestone doji at resistance.",
        [{"dir": 1, "conds": [["cdl_doji_dir", ">", 0.5], ["lvl_touch", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_doji_dir", "<", -0.5], ["lvl_touch", "<", -0.5]], "win": [15, 300]}]),
    "Tweezer bottom / top at yesterday's low / high": (
        f"{CTB}: two equal lows (tops) at a key level mark a turn.",
        [{"dir": 1, "conds": [["cdl_tweezer", ">", 0.5], ["pdl_dist", "<", 0.2], ["pdl_dist", ">", -0.2]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_tweezer", "<", -0.5], ["pdh_dist", "<", 0.2], ["pdh_dist", ">", -0.2]], "win": [15, 300]}]),
    "Harami after a stretched move": (
        f"{CTB} and {FID}: a harami when RSI is stretched warns the move is tiring.",
        [{"dir": 1, "conds": [["cdl_harami", ">", 0.5], ["rsi", "<", 35]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_harami", "<", -0.5], ["rsi", ">", 65]], "win": [15, 300]}]),
    "Rectangle breakout (confirmed close)": (
        f"{FID}: a pattern counts only after the breakout; enter on a close beyond the rectangle, stop beyond the breakout bar.",
        [{"dir": 1, "conds": [["rect_break", ">", 0.5]], "win": [75, 300]}, {"dir": -1, "conds": [["rect_break", "<", -0.5]], "win": [75, 300]},
         {"dir": 1, "conds": [["rect_break", ">", 0.5], ["range_x", ">", 1.2]], "win": [75, 300]},
         {"dir": -1, "conds": [["rect_break", "<", -0.5], ["range_x", ">", 1.2]], "win": [75, 300]}]),
    "Failed breakout: stop and reverse": (
        f"{FID}: when a breakout falls back inside the pattern, reverse.",
        [{"dir": 1, "conds": [["rect_fail", ">", 0.5]], "win": [75, 300]}, {"dir": -1, "conds": [["rect_fail", "<", -0.5]], "win": [75, 300]}]),
    "Pin bar at a key level with the daily trend": (
        f"{CTB}: trade with the trend of the bigger timeframe; here the daily trend (20- / 50-day averages).",
        [{"dir": 1, "conds": [["cdl_pin", ">", 0.5], ["lvl_touch", ">", 0.5], ["d_trend", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_pin", "<", -0.5], ["lvl_touch", "<", -0.5], ["d_trend", "<", -0.5]], "win": [15, 300]}]),
    "Engulfing bar at the 21 EMA with the daily trend": (
        f"{CTB}: engulfing bar after a pullback, in the direction of the daily trend.",
        [{"dir": 1, "conds": [["cdl_engulf", ">", 0.5], ["d_trend", ">", 0.5], ["vs_ema21", "<", 0.2], ["vs_ema21", ">", -0.2]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_engulf", "<", -0.5], ["d_trend", "<", -0.5], ["vs_ema21", "<", 0.2], ["vs_ema21", ">", -0.2]], "win": [15, 300]}]),
    "Inside bar breakout with the daily trend": (
        f"{CTB}: inside-bar breakouts in the direction of the daily trend.",
        [{"dir": 1, "conds": [["ib_break", ">", 0.5], ["d_trend", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["ib_break", "<", -0.5], ["d_trend", "<", -0.5]], "win": [15, 300]}]),
    "Hammer / shooting star at a level with the daily trend": (
        f"{CTB} and {FID}: reversal candles at support in a daily uptrend (buy the dip), at resistance in a downtrend.",
        [{"dir": 1, "conds": [["cdl_hammer", ">", 0.5], ["lvl_touch", ">", 0.5], ["d_trend", ">", 0.5]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_hammer", "<", -0.5], ["lvl_touch", "<", -0.5], ["d_trend", "<", -0.5]], "win": [15, 300]}]),
    "Rectangle breakout near the 20-day high / low": (
        f"{FID}: breakouts that also clear the bigger range tend to run; here the day's box breaks with price already "
        "near its 20-day extreme.",
        [{"dir": 1, "conds": [["rect_break", ">", 0.5], ["d_pos20", ">", 0.8]], "win": [75, 300]},
         {"dir": -1, "conds": [["rect_break", "<", -0.5], ["d_pos20", "<", 0.2]], "win": [75, 300]}]),
    "Outside bar with the trend": (
        f"{FID} (engulfing / outside bars): a bar that takes out both sides of the previous one and closes with the trend.",
        [{"dir": 1, "conds": [["cdl_outside", ">", 0.5], ["st_10_3", ">", 0]], "win": [15, 300]},
         {"dir": -1, "conds": [["cdl_outside", "<", -0.5], ["st_10_3", "<", 0]], "win": [15, 300]}]),
}


def rules() -> list:
    """Every library rule with every exit that fits it (buys: 5 premium exits + 4 chart-level exits; selling: 5).
    Classic library: both indices. Book setups: both indices and any F&O stock with history."""
    out = []
    for group, syms, src in ((LIBRARY, B, "library"), (BOOKS, BOOK_SYMS, "book")):
        for name, (note, rs) in group.items():
            for r in rs:
                for ex in (SELL_EXITS if r["dir"] == 0 else BUY_EXITS):
                    out.append({**r, "exit": ex, "max_day": 1, "syms": syms, "note": f"{name}. {note}"[:300],
                                "source": f"{src}:{name}"[:80]})
    return out


SOURCES = [
    ("Zerodha Varsity (free modules: technical analysis, options)", "https://zerodha.com/varsity/"),
    ("AlgoTest: the 920 straddle defined", "https://algotest.in/blog/the-920-straddle-strategy-defined"),
    ("marketcalls.in: is the 920 straddle no more working?", "https://www.marketcalls.in/futures-and-options/is-the-920-straddle-no-more-working.html"),
    ("Trading Setups Review: the Holy Grail setup", "https://tradingsetupsreview.com/the-holy-grail-trading-setup"),
    ("Groww: Central Pivot Range", "https://groww.in/blog/central-pivot-range"),
    ("Pocketful: 5-minute ORB", "https://www.pocketful.in/blog/trading/5-minute-orb-strategy/"),
]
