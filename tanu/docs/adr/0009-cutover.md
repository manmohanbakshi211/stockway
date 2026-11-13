# ADR-0009: How Tanu takes over the market day (cutover)

- Status: Accepted (built in 0.9.0; the switch itself waits for Mandeep's go)
- Date: 2026-11-13
- Decided by: Mandeep ("ab pura manu ka system tanu se replace karna hai"; "Go", 13 Nov 2026 09:48)

## Context
Tanu replays history with real option prices, keeps paper accounts and a journal, and shows them in its app. To
replace the legacy app it must run a live market day itself: its own Upstox login, feed, minute bars, engine, paper
trades, alerts and end-of-day jobs, without ever overlapping the legacy app (ADR-0007) and without any order (ADR-0001).

## Decision
1. **One switch.** Setting `live` (default false) in `data/tanu/local.json`, set only by `python -m tanu cutover` (it
   refuses while anything answers on the legacy port 8765) and undone by `python -m tanu cutover --undo`. With the
   switch off, `python -m tanu run` is view-only (port 8790) and the Upstox login route refuses.
2. **Gates before any broker connection:** switch on; nothing answering on port 8765 (the legacy app); no other live
   Tanu (lock file `data/tanu/live/live.lock`); a valid token. A failed gate keeps Tanu view-only and says why.
3. **Same port as the legacy app after cutover (8765)**, because the Upstox app's registered redirect URL is
   `http://127.0.0.1:8765/upstox/callback`: nothing changes on Upstox's side.
4. **Secrets typed in on the Mac only** (System page): Upstox API key / secret and the Telegram bot; saved under
   `data/tanu/secrets/` (folder 0700, files 0600), never shown back, never in git, logs or chat. Daily login through
   Upstox's own page; token until 03:30 IST. The setup routes refuse other web pages (Host, Origin, X-Tanu header).
5. **The live path is the replay path** (ADR-0004): feed process -> tape -> minute bars by exchange time -> option
   bars to a live option book with OptionHistory's interface -> `Engine.on_bar` once per closed minute (2 s grace)
   -> paper accounts -> journal. Day state and the carried ATR are computed exactly as replay does. A start after
   09:15 first feeds the morning from Upstox 1-minute candles, so the day state is the same.
6. **Proof before the switch: the rehearsal.** `python -m tanu rehearse <day>` plays a past day through the whole live
   path (local fake broker websocket -> feed process -> tape -> bars -> engine -> accounts) and must give the same
   trades as a history replay of that day, and the tape must rebuild the same index bars.
7. **Watched universe** (bullet `tanu/live/universe.json`): 5 indices, every F&O stock, nearest-expiry options
   (indices +-10 strikes, stocks +-1), inside Upstox's per-connection limits; re-centred after a 2-strike move.
8. **Alerts**: Telegram cards (what, when, why, stop, target, evidence) only for setups on trial or live and symbols
   the Sniper account trades; shadow signals only on the Today page. System warnings (feed silent, login needed).
9. **End of day**: bars from the tape, gap fill from broker candles (marked `broker`), measured costs from the day's
   chain snapshots, evening review, a summary on Telegram.
10. **Rollback** at any time: close Tanu, `python -m tanu cutover --undo`, double-click "Start Radar.command". The
    legacy code stays untouched until a clean live day and Mandeep's go; then it moves to `legacy/`.

## Consequences
- Until a setup passes the tests (none has, research 0001 / 0002), Telegram stays quiet except system messages; the
  Today page shows the shadow signals. This is honest: no card without evidence.
- Live fills use the measured median spread like replay (comparable results); real-time bid / ask fills are a later
  challenger, compared on the same days.
- A tick arriving after its minute closed (over 2 s late) is counted as late; the tape keeps it and end-of-day bars
  include it. The live decision used the bar without it (logged).
- Rehearsals so far (real Mac data): 2026-10-23, 2026-10-27 (expiry day), 2026-11-06: same trades as replay, tape
  rebuilds the index bars, 0 late ticks.
