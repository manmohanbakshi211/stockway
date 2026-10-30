"""Strategy library: well-known trading setups written as Strategy Lab rules, in our own words, with where they come from.

Each entry is tested exactly like any other rule (train / validation / holdout, real charges and slippage), with every
exit rule and the usual indicator settings, so the Lab can say which version, if any, holds up on Nifty and Bank Nifty.
Being famous is not evidence: these are hypotheses. Sources point to public descriptions; where a trader's exact rules
are not published in text (several YouTube-taught setups), the common public version is used and the note says so.
"""
from __future__ import annotations

from .factory import EXITS, SELL_EXITS

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


def rules() -> list:
    """Every library rule with every exit that fits it (buys: 5 exits; straddle: 3 stops), both indices together."""
    out = []
    for name, (note, rs) in LIBRARY.items():
        for r in rs:
            for ex in (SELL_EXITS if r["dir"] == 0 else EXITS):
                out.append({**r, "exit": ex, "max_day": 1, "syms": B, "note": f"{name}. {note}"[:300],
                            "source": f"library:{name}"[:80]})
    return out


SOURCES = [
    ("Zerodha Varsity (free modules: technical analysis, options)", "https://zerodha.com/varsity/"),
    ("AlgoTest: the 920 straddle defined", "https://algotest.in/blog/the-920-straddle-strategy-defined"),
    ("marketcalls.in: is the 920 straddle no more working?", "https://www.marketcalls.in/futures-and-options/is-the-920-straddle-no-more-working.html"),
    ("Trading Setups Review: the Holy Grail setup", "https://tradingsetupsreview.com/the-holy-grail-trading-setup"),
    ("Groww: Central Pivot Range", "https://groww.in/blog/central-pivot-range"),
    ("Pocketful: 5-minute ORB", "https://www.pocketful.in/blog/trading/5-minute-orb-strategy/"),
]
