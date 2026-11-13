"""Market intel: public, non-broker information for the Market page (ADR-0012).

World markets (Yahoo Finance chart data), the economic calendar (ForexFactory), NSE's public reports (FII / DII
cash, participant-wise open interest, F&O ban list, bulk and block deals, insider-trading disclosures, results and
board meetings, corporate filings), GIFT Nifty, and news headlines (RSS). Shown only: nothing here places a trade,
feeds a setup or reaches a broker (ADR-0001). Every URL passes tanu.core.safety.check_url.

Gun: http (one client, timeouts, polite gaps per site, NSE cookies), parsers (pure functions, tested on saved
samples), collect (each source fetched in isolation, cached under data/tanu/intel/, a failing source is marked failed
and the rest keep working), watchlist. Bullets: sources.json (what is fetched, how often), world.json (the markets),
feeds.json (news feeds, filing and headline rules).
"""
