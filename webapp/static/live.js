/* Stockway live scanner pages (plugs into app.js: PAGES, api, post, esc, toast, main, C, plotLayout, plotCfg) */
"use strict";

const L = {
  snap: null, alerts: [], lastId: 0, primed: false, timer: null, pageTimer: null,
  scanner: [], sort: { key: "heat", dir: -1 }, filter: "all", q: "",
  sevFilter: 1, sym: null, expiry: null, chainAt: 0,
};
const LS = {
  get(k, d) { try { const v = localStorage.getItem("sw." + k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem("sw." + k, JSON.stringify(v)); } catch (_) {} },
};
L.sevFilter = LS.get("sevFilter2", 0);

/* ---------------- formatting (live values are already in %) */
const fp = (v, d = 2) => (v == null || isNaN(v) ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const fpc = (v, d = 2) => (v == null || isNaN(v) ? `<span class="muted">–</span>` : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${fp(v, d)}</span>`);
const fx = (v, d = 2) => {
  if (v == null || isNaN(v)) return "–";
  return Math.abs(v) >= 1000 ? v.toLocaleString("en-IN", { maximumFractionDigits: 1 }) : v.toFixed(d);
};
const fk = (v) => (v == null || isNaN(v) ? "–" : Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toLocaleString("en-IN", { maximumFractionDigits: 2 }));
const compact = (v) => {
  if (v == null || isNaN(v)) return "–";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e7) return `${s}${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${s}${(a / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)}K`;
  return `${s}${Math.round(a)}`;
};
const rupees = (v) => (v == null || isNaN(v) ? "–" : `${v < 0 ? "-" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`);
const hms = (ts) => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const hm = (ts) => new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
/* 2.38: when a block's data was fetched, and whether its market is trading now (rules/market_hours.json) */
const clockTs = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "";
const pxTime = (m) => { const t = m && (m.px_ts || (m.ltt ? m.ltt / 1000 : m.t)); return t ? `<small class="asof" title="Time of this price (exchange trade time when known)">${clockTs(t)}</small>` : ""; };
const asOf = (ts, what = "Data fetched at") => ts ? `<small class="asof" title="${esc(what)}">⟳ ${clockTs(ts)}</small>` : "";
const mkPill = (m) => !m ? "" : `<span class="mk ${m.open && !m.stale ? "mk-open" : (m.label === "Break" || m.stale) ? "mk-pause" : "mk-closed"}" title="${esc((m.session || "") + " market: " + m.label)}">${esc(m.label)}</span>`;
const arrow = (d) => (d === "up" ? `<span class="up" aria-label="up">▲</span>` : d === "down" ? `<span class="down" aria-label="down">▼</span>` : `<span class="amber" aria-label="watch">●</span>`);
const SEV = { 1: "Watch", 2: "Important", 3: "Act now" };
const KIND = { manual: "By hand",
  sharp_move: "Sharp move", orb: "Opening-range breakout", day_high: "New day high", day_low: "New day low",
  vwap_cross: "VWAP cross", volume_surge: "Volume surge", oi_buildup: "Futures OI buildup", unusual_options: "Unusual options",
  oi_wall: "OI wall shift", pcr_shift: "PCR shift", iv_spike: "IV spike", beyond_priced: "Bigger than priced",
  vix_jump: "VIX jump", vix_drop: "VIX drop", news: "News", order_flow: "Order flow", absorption: "Absorption",
  gamma_flip: "Gamma flip", big_print: "Big trade", filing: "Company filing", macro: "Global / macro",
  setup_ema_pullback: "Setup: EMA pullback", setup_vwap_reclaim: "Setup: VWAP reclaim", setup_cpr_breakout: "Setup: CPR breakout",
  setup_pdh_break: "Setup: prev-day break", setup_wall_fade: "Setup: OI wall rejection",
  pattern_double_top: "Pattern: double top", pattern_double_bottom: "Pattern: double bottom", pattern_head_shoulders: "Pattern: head & shoulders",
  pattern_inv_hs: "Pattern: inverse H&S", pattern_triangle: "Pattern: triangle", pattern_flag: "Pattern: flag",
  insider: "Insider trade", flat_by: "Square-off reminder",
  pattern_breakout_retest: "Pattern: breakout + retest", pattern_rsi_divergence: "Pattern: RSI divergence", pattern_candle_at_level: "Pattern: candle at level",
};

// labels come from the bullet catalogue (live/registry.py); the table above is the fallback
api("/api/registry").then((r) => (r.kinds || []).forEach((k) => { if (k.label) KIND[k.kind] = k.label; })).catch(() => {});

/* ---------------- polling + notifications */
function livePage() { return ["live", "chart", "world", "desk", "scanner", "sym", "alerts", "paper", "connect", "context", "scorecard", "briefs", "insiders"].includes(state.page); }

async function pollSnapshot() {
  try {
    const s = await api(`/api/live/snapshot?since=${L.lastId}`);
    s.fetched = s.fetched || Date.now() / 1000;
    L.snap = s;
    if (s.alerts.length) {
      const fresh = s.alerts.filter((a) => a.id > L.lastId);
      L.lastId = Math.max(L.lastId, ...s.alerts.map((a) => a.id));
      L.alerts = [...fresh, ...L.alerts.filter((a) => !fresh.some((f) => f.id === a.id))].slice(0, 400);
      if (L.primed) fresh.slice().reverse().forEach(notify);
    }
    L.primed = true;
    if (!L.brainAt || Date.now() - L.brainAt > 30000) { L.brainAt = Date.now(); api("/api/brain").then((b) => { L.brain = b.latest; }).catch(() => {}); }
    railLive();
    if (state.page === "live") renderLive();
  } catch (_) { /* server restarting */ }
  clearTimeout(L.timer);
  L.timer = setTimeout(pollSnapshot, livePage() ? 1000 : 4000);
}

const isFocus = (a) => (a.data && a.data.focus !== undefined) ? !!a.data.focus : (a.severity >= 3 && !(a.data && a.data.muted));
const SEVSEG = [[0, "Focus"], [1, "All"], [2, "Important"], [3, "Act now"]];
const sevPass = (a, f) => f === 0 ? isFocus(a) : a.severity >= f;

function notify(a) {
  const min = LS.get("notifySev", 2);
  if (a.severity < min) return;
  if (!LS.get("notifyAll", false) && !isFocus(a)) return;     // pop-ups and sounds for Focus alerts only
  if (LS.get("sound", true) && a.severity >= 3) beep();
  if ("Notification" in window && Notification.permission === "granted" && document.visibilityState !== "visible" || (LS.get("notifyAlways", false) && "Notification" in window && Notification.permission === "granted")) {
    const n = new Notification(`${a.direction === "up" ? "▲" : a.direction === "down" ? "▼" : "●"} ${a.title}`, { body: a.detail + (a.option ? `\nIdea: ${a.option.label} @ ₹${a.option.ltp}` : ""), tag: `sw-${a.id}` });
    n.onclick = () => { window.focus(); location.hash = `#/sym?s=${encodeURIComponent(a.sym)}`; };
  } else if (!livePage() || state.page !== "live") {
    alertPop(a);
  }
}

function alertPop(a) {
  // a small corner card (not over the page content); click opens the alert, closes by itself after 7 s
  const box = $("#alert-pops"); if (!box) return;
  const el = document.createElement("a");
  el.className = `alert-pop sev${a.severity}`; el.href = "#/alerts";
  el.innerHTML = `<span class="sevtag s${a.severity}">${SEV[a.severity]}</span> ${arrow(a.direction)} ${esc(a.title)}`;
  el.onclick = () => el.remove();
  box.prepend(el);
  while (box.children.length > 3) box.lastChild.remove();
  setTimeout(() => el.remove(), 7000);
}

let _ac = null;
function beep() {
  try {
    _ac = _ac || new (window.AudioContext || window.webkitAudioContext)();
    const o = _ac.createOscillator(), g = _ac.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(_ac.destination);
    g.gain.setValueAtTime(0.0001, _ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.2, _ac.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, _ac.currentTime + 0.35);
    o.start(); o.stop(_ac.currentTime + 0.4);
  } catch (_) {}
}

function railLive() {
  const el = $("#rail-live");
  if (!el || !L.snap) return;
  const st = L.snap.status;
  const label = { running: "Live", starting: "Starting", stopped: "Off", closed: "Closed", error: "Error", login_needed: "Login needed" }[st.state] || st.state;
  el.innerHTML = `<span class="dot-st st-${esc(st.state)}"></span>${esc(label)}${st.source === "sim" && st.running ? " · Sim" : ""}`;
}

/* ---------------- shared pieces */
/* 2.48: when the scanner starts next, from the NSE calendar (holidays, special sessions such as the Diwali Muhurat) */
function nextSessionText(ses) {
  ses = ses || {};
  const nx = ses.next || {};
  if (ses.open_now) return ses.paused_today ? "Paused by you for today: press Resume to start it again." : "It starts by itself in a moment.";
  if (!nx.open) return "It starts by itself before every NSE session.";
  return `${ses.holiday ? "NSE holiday today. " : ""}It starts by itself at ${esc((nx.start_scanner || "").slice(-5))} ${esc((nx.start_scanner || "").slice(0, 10))}${nx.kind === "special" ? ` (${esc(nx.name)}, ${esc(nx.open.slice(-5))}–${esc((nx.close || "").slice(-5))})` : ""}.`;
}

function statusBar() {
  const s = L.snap;
  if (!s) return `<div class="livebar"><span class="muted">Connecting…</span></div>`;
  const st = s.status;
  const clock = s.clock ? new Date(s.clock).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "";
  const feed = st.feed;
  const lag = feed && feed.last_msg_age != null ? (feed.last_msg_age < 5 ? "streaming" : `no data for ${Math.round(feed.last_msg_age)}s`) : "";
  const subs = feed ? `${(feed.subs.full || 0) + (feed.subs.option_greeks || 0)} instruments` : "";
  const vix = s.vix && st.running ? `<span class="vixchip" title="India VIX: the market's fear gauge">VIX <b>${s.vix.ltp.toFixed(2)}</b> ${fpc(s.vix.chg_pct, 1)}</span>` : "";
  const dl = feed && feed.lag_ms != null ? feed.lag_ms : null;
  const speed = st.running ? ` · <span class="${dl != null && dl > 3000 ? "down" : "muted"}" title="Delay from Upstox's server to this app, and how long one full analysis of every instrument takes">${dl != null ? `data delay ${(dl / 1000).toFixed(1)}s · ` : ""}analysis ${st.loop_ms != null ? st.loop_ms + " ms" : "–"}</span>` : "";
  // 2.48: no Start button. The scanner starts and stops by itself by the NSE calendar (holidays, special sessions such as
  // the Diwali Muhurat). Pause stops it for the rest of today; Resume brings it back.
  const ses = s.session || {}, nx = ses.next || {};
  const sesMsg = ses.today && ses.open_now ? (ses.paused_today ? "Paused by you for today." : "Starting by itself…")
    : nx.open ? `${ses.holiday ? "NSE holiday today. " : ""}Next session ${esc(nx.open)}${nx.kind === "special" ? ` (${esc(nx.name)})` : ""}.` : "";
  const msg = st.state === "running" ? `${st.source === "sim" ? "Simulator" : "Upstox"} · ${subs} · ${lag}${speed}`
    : esc(st.message || "") + (st.logged_in && sesMsg ? ` <span class="muted">${sesMsg}</span>` : "");
  let btns = "";
  if (st.running) btns = `<button class="btn" data-live-stop title="Stops the scanner for the rest of today. It starts by itself again at the next session.">${st.source === "sim" ? "Stop simulator" : "Pause for today"}</button>`;
  else {
    if (!st.logged_in) btns += `<a class="btn primary" href="/upstox/login">Log in with Upstox</a>`;
    else if (ses.open_now && (ses.paused_today || ses.auto_start === false || st.state === "error")) btns += `<button class="btn primary" data-live-start="upstox">${ses.paused_today ? "Resume" : "Start live"}</button>`;
    btns += `<button class="btn" data-live-start="sim" title="A made-up market for trying the app; never mixed with real data">Run simulator</button>`;
  }
  return `<div class="livebar"><div class="lb-left"><span class="dot-st st-${esc(st.state)}"></span>
    <b>${st.running ? (st.source === "sim" ? "Simulator" : "Live") : ({ stopped: "Scanner off", closed: "Market closed", error: "Error", login_needed: "Login needed", starting: "Starting" }[st.state] || st.state)}</b>
    ${st.running ? `<span class="clock">${clock}</span>` : ""}${vix}<span class="muted small">${msg}</span></div>
    <div class="btn-row">${btns}</div></div>`;
}

function bindLiveButtons(root = main) {
  root.querySelectorAll("[data-live-start]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await post("/api/live/start", { source: b.dataset.liveStart }); toast(b.dataset.liveStart === "sim" ? "Simulator starting" : "Live scanner starting"); L.alerts = []; L.lastId = 0; L.primed = false; }
    catch (e) { toast(e.message); }
    b.disabled = false; pollSnapshot();
  }));
  root.querySelectorAll("[data-live-stop]").forEach((b) => b.addEventListener("click", async () => {
    try { await post("/api/live/stop", {}); toast("Stopped"); } catch (e) { toast(e.message); }
    pollSnapshot();
  }));
}

function biasMeter(score) {
  if (score == null) return "";
  const w = Math.abs(score) / 2, left = score >= 0 ? 50 : 50 - w;
  const label = score >= 40 ? "Bullish" : score >= 15 ? "Leaning up" : score <= -40 ? "Bearish" : score <= -15 ? "Leaning down" : "Neutral";
  return `<div class="bias"><div class="bias-track"><i class="${score >= 0 ? "pos" : "neg"}" style="left:${left}%;width:${w}%"></i><b></b></div>
    <span class="${score >= 15 ? "up" : score <= -15 ? "down" : "muted"}">${label} ${score > 0 ? "+" : ""}${score}</span></div>`;
}

function rangeBar(m) {
  const lo = m.day_l, hi = m.day_h;
  if (!lo || !hi || hi <= lo) return "";
  const p = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const or = m.or_high && m.or_low ? `<span class="rb-or" style="left:${p(m.or_low)}%;width:${p(m.or_high) - p(m.or_low)}%" title="Opening range"></span>` : "";
  const vw = m.vwap ? `<span class="rb-vwap" style="left:${p(m.vwap)}%" title="VWAP ${fx(m.vwap)}"></span>` : "";
  return `<div class="rangebar" aria-label="Day range">${or}${vw}<span class="rb-spot" style="left:${p(m.spot)}%"></span></div>
    <div class="rb-labels"><span>L ${fx(lo)}</span><span>H ${fx(hi)}</span></div>`;
}

function dhanStrip(x) {
  if (!x) return "";
  const dep = x.depth ? Object.entries(x.depth).map(([k, v]) => `${esc(k)} ${v === "live" ? "✓" : esc(v)}`).join(", ") : "off";
  const mv = x.movers ? Object.values(x.movers).reduce((a, b) => a + b, 0) : 0;
  const err = Object.entries(x.errors || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
  return `<p class="small muted" style="margin:6px 0">Dhan Pro: order book ${dep} · market movers ${mv ? `${mv} names` : "waiting"}${(x.in_play || []).length ? ` · in play: <b>${x.in_play.map((s) => `<a href="#/chart?s=${encodeURIComponent(s)}">${esc(s)}</a>`).join(", ")}</b>` : ""}${err ? ` · <span class="down">${esc(err)}</span>` : ""}</p>`;
}

function depthLine(d) {
  if (!d) return "";
  const buy = Math.round((1 + d.imb_near) / 2 * 100);
  return `<p class="small" style="margin:4px 0" title="Dhan 200-level order book of the future: quantity waiting to buy vs sell within ±0.25% of the price (all 200 levels: ${Math.round((1 + d.imb) / 2 * 100)}% buyers). Biggest walls: buy ${fx(d.wall_bid)} (${d.wall_bid_qty.toLocaleString("en-IN")}), sell ${fx(d.wall_ask)} (${d.wall_ask_qty.toLocaleString("en-IN")}).">Order book: <b class="${buy >= 55 ? "up" : buy <= 45 ? "down" : ""}">${buy}% buyers</b> near price · walls ${fx(d.wall_bid)} / ${fx(d.wall_ask)}</p>`;
}

function indexCard(m) {
  const o = m.opt || {}, f = m.fut || {};
  const bu = f.buildup_label ? `<span class="pill ${f.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(f.buildup_label)}</span>` : "";
  return `<a class="icard" href="#/sym?s=${encodeURIComponent(m.sym)}">
    <div class="ic-head"><b>${esc(m.name)} ${mkPill(L.snap && L.snap.nse)}</b>${bu}</div>
    <div class="ic-price">${fx(m.spot)} ${fpc(m.chg_pct)} ${pxTime(m)}</div>
    ${rangeBar(m)}
    <dl class="ic-grid">
      <div><dt>PCR</dt><dd>${o.pcr != null ? o.pcr.toFixed(2) : "–"}</dd></div>
      <div><dt>Max pain</dt><dd>${o.max_pain ? fk(o.max_pain) : "–"}</dd></div>
      <div><dt>Support</dt><dd class="up">${o.support ? fk(o.support) : "–"}</dd></div>
      <div><dt>Resistance</dt><dd class="down">${o.resistance ? fk(o.resistance) : "–"}</dd></div>
      <div><dt>IV</dt><dd>${o.atm_iv ? o.atm_iv.toFixed(1) + "%" : "–"}${o.iv_chg_15m != null ? ` <small class="${o.iv_chg_15m > 0 ? "down" : "up"}">${o.iv_chg_15m > 0 ? "+" : ""}${o.iv_chg_15m.toFixed(1)}</small>` : ""}</dd></div>
      <div><dt>Move</dt><dd>${o.exp_move_pct ? "±" + o.exp_move_pct.toFixed(2) + "%" : "–"}<small class="muted"> ${o.days_to_expiry != null ? o.days_to_expiry + "d" : ""}</small></dd></div>
      ${m.gex ? `<div title="Dealer gamma: pinning = moves get absorbed, accelerating = moves run"><dt>Gamma</dt><dd class="${m.gex.regime === "pinning" ? "" : "amber"}">${m.gex.regime === "pinning" ? "Pinning" : "Accelerating"}</dd></div>
      <div><dt>Flip</dt><dd>${m.gex.flip ? fk(Math.round(m.gex.flip)) : "–"}</dd></div>` : ""}
    </dl>
    ${m.regime && m.regime.code !== "opening" ? `<p class="ic-regime"><b>${esc(m.regime.label)}</b></p>` : ""}
    ${idxMore(m)}
    ${depthLine(L.snap && L.snap.depth ? L.snap.depth[m.sym] : null)}
    ${biasMeter(m.score)}
    ${m.reasons && m.reasons.length ? `<p class="ic-why">${esc(m.reasons.slice(0, 3).join(" · "))}</p>` : ""}
    <p class="ic-asof">${L.snap ? asOf(L.snap.status && !L.snap.status.running && L.snap.last_session ? L.snap.last_session.asof : L.snap.fetched, L.snap.status && !L.snap.status.running ? "Last session's data" : "Data fetched at") : ""}</p>
  </a>`;
}

/* 2.29: day / 52-week / all-time range, merged support and resistance, daily and weekly trend */
function idxMore(m) {
  const e = m.ext || {}, lv = m.levels || {}, tr = m.trend || {};
  const arrowT = (v) => v == null ? "–" : v > 0 ? `<span class="up">↑ up</span>` : v < 0 ? `<span class="down">↓ down</span>` : "sideways";
  const zone = (z) => `<li title="${esc(z.names.join(" + "))}"><b>${fk(Math.round(z.price))}</b> <small class="muted">${esc(z.names.slice(0, 2).join(" + "))}${z.names.length > 2 ? " +" + (z.names.length - 2) : ""}</small> <small class="${Math.abs(z.dist_pct) < 0.3 ? "amber" : "muted"}">${z.dist_pct > 0 ? "+" : ""}${z.dist_pct}%</small></li>`;
  const rows = [
    m.day_h && m.day_l ? `<div><dt>Day</dt><dd>${fk(Math.round(m.day_l))} – ${fk(Math.round(m.day_h))}</dd></div>` : "",
    e.hi52 ? `<div><dt>52 weeks</dt><dd>${fk(Math.round(e.lo52))} – ${fk(Math.round(e.hi52))}</dd></div>` : "",
    e.ath && Number(e.since) <= new Date().getFullYear() - 3 ? `<div title="All-time high (${esc(e.ath_date || "")}) and low (${esc(e.atl_date || "")}) since ${esc(e.since || "")}, Yahoo"><dt>All-time high</dt><dd>${fk(Math.round(e.ath))} <small class="${(e.from_ath_pct ?? -1) >= -1 ? "up" : "muted"}">${e.from_ath_pct != null ? e.from_ath_pct + "%" : ""}</small></dd></div>
      <div><dt>All-time low</dt><dd>${fk(Math.round(e.atl))} <small class="muted">${esc(e.atl_date || "")}</small></dd></div>` : "",
    tr.daily !== undefined ? `<div><dt>Daily trend</dt><dd>${arrowT(tr.daily)}</dd></div><div><dt>Weekly</dt><dd>${arrowT(tr.weekly)}</dd></div>` : ""].join("");
  const sr = (lv.support || []).length || (lv.resistance || []).length ? `<div class="ic-sr"><div><span class="down">Resistance</span><ul>${(lv.resistance || []).map(zone).join("") || `<li class="muted">–</li>`}</ul></div>
      <div><span class="up">Support</span><ul>${(lv.support || []).map(zone).join("") || `<li class="muted">–</li>`}</ul></div></div>` : "";
  return (rows ? `<dl class="ic-grid ic-more">${rows}</dl>` : "") + sr;
}

function alertItem(a, full = false, extra = "") {
  const o = a.option, d = a.data || {};
  const lv = d.levels || {}, st = d.structure, tr = d.track;
  const link = d.link ? ` <a href="${esc(d.link)}" target="_blank" rel="noopener">Read</a>` : "";
  const score = d.score != null ? `<span class="score" style="--s:${d.score}" title="Signal score: confluence, track record, market type${d.model_p != null ? " and the learned model" : ""}">${d.score}</span>` : "";
  const track = tr && tr.decided ? `<span class="track ${tr.hit_rate >= 60 ? "good" : tr.hit_rate < 45 ? "bad" : ""}" title="Option +20% before −20% within an hour, among ${tr.decided} decided of ${tr.n} alerts">Worked ${tr.hit_rate}% of ${tr.decided} decided</span>` :
    tr && tr.n && tr.decided === undefined ? `<span class="track muted">Worked ${tr.hit_rate}% of ${tr.n} times (old count)</span>` :
    (a.direction === "up" || a.direction === "down") ? `<span class="track muted">New signal type: no track record yet</span>` : "";
  const ev = (d.evidence || []).map((e) => `<span class="ev ${e.agree ? "yes" : "no"}">${e.agree ? "✓" : "✗"} ${esc(e.label)}</span>`).join("");
  const plan = lv.stop ? `<div class="plan"><span>Target <b>${fx(lv.target)}</b> <small>(${lv.target_pct}%)</small></span><span>Wrong ${a.direction === "up" ? "below" : "above"} <b>${fx(lv.stop)}</b> <small>(${lv.stop_pct}%)</small></span>${lv.rr ? `<span>Reward:risk <b>${lv.rr}</b></span>` : ""}</div>` : "";
  const pp = d.plan;
  const pro = pp && (a.direction === "up" || a.direction === "down") ? `<details class="pro-plan"><summary><b>Pro plan</b> <span class="pill p-amber" title="Shown to learn from; paper trades keep the current exits until this wins on live results">testing</span> <small class="muted">${esc(pp.who)}</small></summary>
      <div class="plan"><span>Chart entry <b>${fx(pp.und_entry)}</b></span><span>Stop-loss <b>${fx(pp.und_stop)}</b> <small>(1R = ${fx(pp.R_pts)} pts, ATR ${fx(pp.atr)})</small></span><span>1R <b>${fx(pp.und_1r)}</b></span>${pp.und_target ? `<span>Target <b>${fx(pp.und_target)}</b> <small>(${pp.rr}R)</small></span>` : `<span>Trail: best − 2.5 ATR</span>`}</div>
      ${pp.opt_entry ? `<div class="plan"><span>Option: buy up to <b>₹${fx(pp.opt_entry_max)}</b></span><span>SL ≈ <b>₹${fx(pp.opt_stop)}</b></span>${pp.opt_target ? `<span>Target ≈ <b>₹${fx(pp.opt_target)}</b></span>` : ""}<span>Max loss <b>${rupees(pp.max_loss)}</b>/lot</span><span class="${pp.lots ? "up" : "down"}">${esc(pp.fits)}</span></div>` : ""}
      <p class="small muted" style="margin:2px 0 0">${esc(pp.words)}. Option levels are estimates from delta; the chart levels decide.</p></details>` : "";
  const nowTs = L.snap && L.snap.clock ? Date.parse(L.snap.clock) / 1000 : Date.now() / 1000;
  const ageMin = (nowTs - a.ts) / 60;
  const stale = ageMin > 5;
  const px = L.snap && L.snap.px ? L.snap.px[a.sym] : null;
  const since = px && a.price ? (px[0] / a.price - 1) * 100 : null;
  const live = px ? `<span class="live-px" title="Live price now">Now <b>${fx(px[0])}</b> <small class="${px[1] >= 0 ? "up" : "down"}">${px[1] >= 0 ? "+" : ""}${px[1].toFixed(2)}% today</small>${since != null ? ` · <small class="${since * (a.direction === "down" ? -1 : 1) >= 0 ? "up" : "down"}">${since >= 0 ? "+" : ""}${since.toFixed(2)}% since alert</small>` : ""}</span>` : "";
  let trade = "";
  if (o) {
    const s2 = st && st.type === "spread" && st.spread ? ` · or spread ${fk(st.spread.buy)}/${fk(st.spread.sell)} for ${rupees(st.spread.cost)}, max ${rupees(st.spread.max_profit)}` : "";
    const tag = st ? `<span class="pill ${st.type === "buy" ? "p-up" : st.type === "spread" ? "p-amber" : "p-down"}">${st.type === "buy" ? "Buy option" : st.type === "spread" ? "Use a spread" : "Skip"}</span>` : "";
    trade = `<div class="idea"><span>${tag} <b>${esc(o.label)}</b> @ ₹${fx(o.ltp)} · ${rupees(o.cost_per_lot)}/lot${s2}${st ? `<br><small class="muted">${esc(st.why)}</small>` : ""}</span>
      <span class="btn-row">${lv.stop ? `<button class="link-btn" data-tm="${a.id}">Time Machine</button>` : ""}${stale ? `<span class="muted small" title="Signals older than 5 minutes can't be paper-traded: the price has moved">${Math.round(ageMin)} min old</span>` : `<button class="link-btn${d.risk && d.risk.verdict !== "PASS" ? " muted" : ""}" data-paper="${a.id}" title="${d.risk && d.risk.verdict !== "PASS" ? "Risk check said BLOCK: re-checked when you click" : "Paper trade at the live price"}">${d.risk && d.risk.verdict !== "PASS" ? "Paper trade (blocked)" : "Paper trade"}</button>`}<button class="link-btn" data-explain="${a.id}">Why?</button></span></div>`;
  } else trade = `<div class="idea"><span class="muted small">${esc(d.regime ? "Market: " + d.regime : "")}</span><span class="btn-row"><button class="link-btn" data-explain="${a.id}">Why?</button></span></div>`;
  const cx = d.context || {};
  const ag = (v, label) => v == null || v === 0 ? "" : `<span class="ev ${v > 0 ? "yes" : "no"}">${v > 0 ? "✓" : "✗"} ${label}</span>`;
  const ctxChips = [ag(cx.nifty_al, "Nifty"), ag(cx.sector_al, "sector"), ag(cx.global_al, "global"), ag(cx.breadth_al, "breadth"),
    cx.news_n ? `<span class="ev ${cx.news_al > 0 ? "yes" : cx.news_al < 0 ? "no" : ""}">📰 ${cx.news_n} news</span>` : ""].join("");
  const conv = d.conviction != null ? `<span class="pill ${d.conviction >= 85 ? "p-up" : ""}" title="Conviction: where this alert ranks among all graded alerts by the learned model (90 = top 10%)">Conviction ${d.conviction}</span>` : "";
  const fits = "";                                  // 2.48: no ₹40K pill; the risk check sizes every idea for your capital
  return `<li class="alert sev${a.severity}" data-aid="${a.id}">
    <div class="al-time">${hms(a.ts)}<span class="sevtag s${a.severity}">${SEV[a.severity]}</span>${score}</div>
    <div class="al-body"><div class="al-title">${arrow(a.direction)} <a href="${a.sym === "GLOBAL" ? "#/world" : `#/chart?s=${encodeURIComponent(a.sym)}`}">${esc(a.title)}</a></div>
      <p>${esc(a.detail)}${link} <span class="muted small">${esc(KIND[a.kind] || a.kind)}</span> ${track}</p>${live ? `<p class="small" style="margin:2px 0 6px">${live}</p>` : ""}
      ${d.muted ? `<p class="muted small">⤓ ${esc(d.muted)}</p>` : ""}${bigLine(d.bigger)}${eventLine(d.event_risk)}
      ${ev || ctxChips || conv || fits ? `<div class="evs">${conv}${fits}${ev}${ctxChips}</div>` : ""}${plan}${pro}${trade}${riskLine(d.risk)}
      ${a.ai ? `<div class="ai-text">${fmtAI(a.ai)}</div>` : ""}${extra}</div></li>`;
}

/* The bigger picture (live/htf.py + results calendar): information only, not in the score until it proves itself */
/* 2.35: event risk on an alert (information and warnings only) */
function eventLine(r) {
  if (!r || !(r.items || []).length) return "";
  const col = r.level === "high" ? "down" : r.level === "medium" ? "amber" : "muted";
  return `<p class="small" style="margin:2px 0 6px" title="Information only: never blocks an alert or a trade"><span class="${col}">⚡ Event risk${r.level ? ` (${esc(r.level)})` : ""}:</span> ${r.items.slice(0, 3).map((i) => esc(i.what)).join(" · ")}</p>`;
}

function bigLine(b) {
  if (!b || !(b.line || b.event || b.index_event)) return "";
  const ev = b.event ? `${b.event.results ? "Results" : esc(b.event.purpose || "Board meeting")} ${b.event.days === 0 ? "today" : b.event.days === 1 ? "tomorrow" : `in ${b.event.days} trading days`} (${esc(b.event.date.slice(5))})` : "";
  const parts = [b.line ? esc(b.line) : "", ev ? `<span class="${b.event.days <= 1 ? "amber" : ""}">${ev}</span>` : "", b.index_event ? `<span class="amber">${esc(b.index_event)} today</span>` : ""].filter(Boolean);
  return `<p class="small muted" style="margin:2px 0 6px" title="Information only: recorded with every alert and tested by a challenger model before it can change scores or Focus">🔭 ${parts.join(" · ")}</p>`;
}

function exitBoardLive(x) {
  if (!x || !(x.styles || []).length) return `<div class="panel tight" style="margin-top:14px"><h3>Stop-loss &amp; target styles <span class="pill p-amber">shadow</span></h3><p class="small muted">Every directional alert's option is followed under the current exit and 4 chart-level styles (ATR 2R, signal candle 2R, breakeven 3R, Chandelier trail). Results appear after the first live session.</p></div>`;
  const rs = (v) => v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(v).toLocaleString("en-IN")}</span>`;
  return `<div class="panel tight" style="margin-top:14px"><h3>Stop-loss &amp; target styles on live alerts <span class="pill p-amber">shadow</span></h3>
    <p class="small muted">${esc(x.note)}</p>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Exit style</th><th class="r">Alerts</th><th class="r">Days</th><th class="r">Option avg</th><th class="r">Won</th><th class="r">₹ per lot</th><th class="r" title="Only alerts whose option lot costs ₹10,000 or less">Lots ≤ ₹10K: ₹/lot</th></tr></thead><tbody>
    ${x.styles.map((r) => `<tr><td>${r.style === "current" ? "<b>Current exit</b> (champion)" : esc(r.style)} <small class="muted">${esc(r.who)}</small></td><td class="r">${r.n}</td><td class="r">${r.days}</td><td class="r">${r.avg_pct >= 0 ? "+" : ""}${r.avg_pct}%</td><td class="r">${r.win_pct}%</td><td class="r">${rs(r.avg_rs)}</td><td class="r">${r.n40 ? rs(r.avg_rs40) + ` <small class="muted">(${r.n40})</small>` : "–"}</td></tr>`).join("")}</tbody></table></div></div>`;
}

function riskLine(r) {
  if (!r) return "";
  if (r.verdict === "PASS") return `<p class="risk pass small"><b>Risk check: PASS</b> · ${r.lots} lot${r.lots > 1 ? "s" : ""} · max loss ${rupees(r.max_loss)} at the stop · uses ${rupees(r.capital_used)}${(r.warnings || []).length ? ` · <span class="amber">${r.warnings.map(esc).join(" ")}</span>` : ""}</p>`;
  return `<p class="risk block small"><b>Risk check: BLOCK</b> · ${(r.reasons || []).map(esc).join(" ")} <span class="muted">${r.can_override ? "No paper trade unless you override." : "No paper trade."}</span></p>`;
}

function storyList(alerts) {
  const groups = new Map();
  alerts.forEach((a) => { const k = a.story || a.id; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(a); });
  return [...groups.values()].map((g) => {
    const [top, ...rest] = g;
    const more = rest.length ? `<details class="story"><summary>${rest.length} earlier signal${rest.length > 1 ? "s" : ""} in this ${esc(top.sym)} story</summary><ul>${rest.map((a) => `<li>${hm(a.ts)} ${arrow(a.direction)} ${esc(a.title)}</li>`).join("")}</ul></details>` : "";
    return alertItem(top, false, more);
  }).join("");
}

function fmtAI(t) {
  return esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split(/\n+/).map((l) => l.replace(/^\s*[-*•]\s*/, "")).filter(Boolean).map((l) => `<p>${l}</p>`).join("");
}

function bindAlertButtons(root) {
  root.querySelectorAll("[data-paper]").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); b.disabled = true;
    try { await post(`/api/live/alerts/${b.dataset.paper}/paper`, {}); toast("Paper trade opened (risk check PASS)"); }
    catch (err) {
      if (err.status === 409 && err.detail && err.detail.can_override) {
        const ok = window.confirm(`${err.message}\n\nTake it anyway as an OVERRIDE? 1 lot only, tagged "override" so its record stays separate.`);
        if (ok) { try { await post(`/api/live/alerts/${b.dataset.paper}/paper`, { override: true }); toast("Override paper trade opened (1 lot)"); } catch (e2) { toast(e2.message); } }
      } else toast(err.message);
    }
    b.disabled = false;
  });
  root.querySelectorAll("[data-tm]").forEach((b) => b.onclick = (e) => { e.preventDefault(); location.hash = `#/timemachine?alert=${b.dataset.tm}`; });
  root.querySelectorAll("[data-explain]").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); b.disabled = true; b.textContent = "Thinking…";
    try {
      const r = await post(`/api/live/alerts/${b.dataset.explain}/explain`, {});
      const a = L.alerts.find((x) => x.id === Number(b.dataset.explain)); if (a) a.ai = r.text;
      const li = b.closest(".alert");
      let box = li.querySelector(".ai-text");
      if (!box) { box = document.createElement("div"); box.className = "ai-text"; li.querySelector(".al-body").appendChild(box); }
      box.innerHTML = fmtAI(r.text);
      b.remove();
    } catch (err) { toast(err.message); b.disabled = false; b.textContent = "Why?"; }
  });
}

function stopPageTimer() { clearTimeout(L.pageTimer); L.pageTimer = null; chStreamStop(); }

/* 2.51 (Mandeep: "websocket for charting, tick-by-tick and price at the same time"): an open chart keeps a websocket to
   this app (/ws/ticks) and moves with every new price (~0.1 s) instead of asking every 2 seconds. The app reads its own
   prices; nothing new connects to a broker. Falls back to asking every 2 s if the stream drops. */
function chStreamStop() { if (typeof CH !== "undefined" && CH.ws) { try { CH.ws.onclose = null; CH.ws.close(); } catch (_) {} CH.ws = null; CH.wsAt = 0; } }
function chStream() {
  chStreamStop();
  if (!window.WebSocket) return;
  let ws;
  try { ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/ticks`); } catch (_) { return; }
  CH.ws = ws; CH.wsAt = 0;
  const sym = CH.sym, tf = CH.tf;
  ws.onopen = () => { try { ws.send(JSON.stringify({ sym, tf })); } catch (_) {} };
  ws.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (_) { return; }
    if (d.hb) { if (d.running === false) CH.wsAt = 0; return; }
    if (d.error || d.sym !== CH.sym || d.tf !== CH.tf || !CH.series.candle || !CH.bars.length) return;
    CH.wsAt = Date.now();
    const lastBar = CH.bars[CH.bars.length - 1];
    if (d.candle) {
      const c = { time: d.candle.time, open: d.candle.open, high: d.candle.high, low: d.candle.low, close: d.candle.close };
      if (c.time < lastBar.time) return;
      CH.series.candle.update(c);
      const v = { time: c.time, value: d.candle.volume || 0, color: c.close >= c.open ? "rgba(30,138,90,.35)" : "rgba(194,59,50,.35)" };
      if (c.time > lastBar.time) { CH.bars.push(c); CH.vol.push(v); } else { CH.bars[CH.bars.length - 1] = c; CH.vol[CH.vol.length - 1] = v; }
      if (CH.series.vol && CH.hasVol) CH.series.vol.update(v);
    } else if (d.p) {                                   // daily / weekly / monthly: move the last candle's close
      const c = { ...lastBar, close: d.p, high: Math.max(lastBar.high, d.p), low: Math.min(lastBar.low, d.p) };
      CH.bars[CH.bars.length - 1] = c; CH.series.candle.update(c);
    }
    const old = $("#ch-price"); if (old) old.style.display = "none";          // one price on screen: the streamed one
    const el = $("#ch-live"); if (el) el.innerHTML = `<b>${fx(d.p)}</b> ${d.chg_pct != null ? fpc(d.chg_pct) : ""} <small class="muted" title="Exchange time of this price · streamed live">● ${d.tt ? clockTs(d.tt) : ""}</small>`;
  };
  ws.onclose = () => { if (CH.ws === ws) { CH.ws = null; CH.wsAt = 0; const o = $("#ch-price"), l = $("#ch-live"); if (o) o.style.display = ""; if (l) l.innerHTML = ""; if (state.page === "chart") setTimeout(() => { if (state.page === "chart" && !CH.ws && CH.sym === sym && CH.tf === tf) chStream(); }, 5000); } };
}

/* ---------------- page: Live */
async function pageLive() {
  stopPageTimer();
  if (!L.snap) await pollSnapshot();
  main.innerHTML = `<div class="live-head"><div><h1 class="page-title">Live market</h1>
      <p class="lede" style="margin-bottom:14px">Nifty, Bank Nifty and every F&amp;O stock with its options, checked every second.</p></div></div>
    <div id="lv-bar"></div><div id="lv-body"></div>`;
  renderLive(true);
  loadWorldBoard(); loadBoardMini(); loadEventsMini();
  let k = 0;
  const tick = () => { if (state.page !== "live") return; loadWorldBoard(); if (++k % 2 === 0) { loadBoardMini(); loadEventsMini(); } L.pageTimer = setTimeout(tick, 30000); };
  L.pageTimer = setTimeout(tick, 30000);
}

function setupCard() {
  const s = L.snap.status;
  return `<div class="empty" style="max-width:none"><h2>Connect live data</h2>
    <p>Live prices come from your free Upstox account. Set it up once, then log in each morning (Upstox needs a fresh login every day).</p>
    <div class="btn-row"><a class="btn primary" href="#/connect">Set up Upstox</a><button class="btn" data-live-start="sim">Try the simulator first</button></div>
    <p class="note">The simulator plays a made-up trading day with real-looking moves, so you can learn the screens before connecting. ${s.state === "error" ? `<br><b class="down">${esc(s.message)}</b>` : ""}</p></div>`;
}

function extraIdx(x) {
  // Sensex (Upstox live, else Yahoo) and GIFT Nifty (NSE IX near-month future) beside the main index cards
  if (!x || !x.length) return "";
  const row = (k, v) => v ? `<div><dt>${k}</dt><dd>${v}</dd></div>` : "";
  const pts = (v) => v == null ? "" : `<small class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${fx(v)}</small>`;
  return `<div class="xcards">${x.map((c) => {
    const e = c.ext || {};
    const giftGap = c.vs_nifty != null ? `<span class="${c.vs_nifty >= 0 ? "up" : "down"}" title="GIFT Nifty minus Nifty ${c.vs_nifty_when === "now" ? "now (futures premium)" : "at its last close (hint for the next open)"}">${c.vs_nifty >= 0 ? "+" : ""}${fx(c.vs_nifty)} pts</span> <small class="muted">${c.vs_nifty_when === "now" ? "now" : "close"} ${fx(c.nifty_ref)}</small>` : "";
    const grid = [row("Change", c.chg != null ? pts(c.chg) : ""), row("Open", c.open ? fx(c.open) : ""), row("Prev close", c.prev ? fx(c.prev) : ""),
      row("Day range", c.day_l && c.day_h ? `${fx(c.day_l)} – ${fx(c.day_h)}` : ""), row("52 weeks", e.hi52 ? `${fk(Math.round(e.lo52))} – ${fk(Math.round(e.hi52))}` : ""),
      row("All-time high", e.ath ? `${fk(Math.round(e.ath))} <small class="muted">${esc(e.ath_date || "")}</small>` : ""),
      row("Avg price", c.atp ? fx(c.atp) : ""), row("Contracts", c.contracts ? fk(Math.round(c.contracts)) : ""),
      row(c.name === "GIFT Nifty" ? "vs Nifty" : "", giftGap)].join("");
    const lv = c.levels || {};
    const sr = (lv.support || []).length ? `<p class="small" style="margin:6px 0 0"><span class="down">R</span> ${(lv.resistance || []).slice(0, 2).map((z) => fk(Math.round(z.price))).join(" · ") || "–"} &nbsp; <span class="up">S</span> ${(lv.support || []).slice(0, 2).map((z) => fk(Math.round(z.price))).join(" · ")}</p>` : "";
    return `<div class="xcard"><span>${esc(c.name)} ${mkPill(c.mkt)}</span>
      <b>${c.spot != null ? fx(c.spot) : "–"}</b> ${c.chg_pct != null ? `<em class="${c.chg_pct >= 0 ? "up" : "down"}">${c.chg_pct >= 0 ? "+" : ""}${c.chg_pct.toFixed(2)}%</em>` : ""}
      ${grid ? `<dl class="ic-grid xc-grid">${grid}</dl>` : ""}${sr}
      <small class="muted">${esc(c.src || "")}${c.expiry ? ` · expiry ${esc(c.expiry)}` : ""}${c.at ? ` · ${esc(c.at.slice(-8, -3))}` : ""}</small></div>`; }).join("")}</div>`;
}

/* 2.40: the indication tile takes whatever is left of its row in the market strip (no empty box beside it) */
function fitIndTile() {
  const strip = document.querySelector(".mstrip"), tile = strip && strip.querySelector(".ms-ind");
  if (!tile) return;
  tile.style.gridColumn = "";
  const cols = getComputedStyle(strip).gridTemplateColumns.split(" ").filter(Boolean).length || 1;
  let used = 0;
  for (const el of strip.children) {
    if (el === tile) break;
    const span = /span (\d+)/.exec(getComputedStyle(el).gridColumnStart + " " + getComputedStyle(el).gridColumnEnd);
    const w = Math.min(cols, span ? parseInt(span[1], 10) : 1);
    used = (used % cols) + w > cols ? w : used + w;          // an item that does not fit starts a new row
  }
  const left = cols - (used % cols);
  tile.style.gridColumn = `span ${left === 0 ? cols : left}`;
}
window.addEventListener("resize", () => { clearTimeout(window._fitT); window._fitT = setTimeout(fitIndTile, 150); });

/* 2.40: India market indication (live/indication.py): one word for the whole Indian market, AI every 15 min */
function indTile(v) {
  if (!v) return "";
  const x = v.latest;
  const col = (w) => (w === "Gap up" || w === "Positive" ? "up" : w === "Gap down" || w === "Negative" ? "down" : "");
  const hhmm = (iso) => { try { return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false }); } catch (_) { return ""; } };
  const nextTxt = v.next ? (new Date(v.next).toDateString() === new Date().toDateString() ? hhmm(v.next) : `${new Date(v.next).toLocaleDateString("en-IN", { weekday: "short" })} ${hhmm(v.next)}`) : "";
  const rec = v.record || {};
  const ph = x ? ({ "pre-open": "pre-open", session: "market hours", closed: "after the close" }[x.phase] || x.phase) : "";
  const tag = x ? (x.by === "AI" ? `<em class="ind-tag">AI suggestion</em>` : `<em class="ind-tag rules" title="${esc(x.error || "AI off")}">rules</em>`) : "";
  return `<div class="ms-item ms-ind"><span>India market ${tag}</span>
    ${x ? `<b class="ind-word ${col(x.word)}">${esc(x.word)}</b><small class="ind-line">${esc(x.line)}</small>
      ${x.insiders && x.insiders.n ? `<small class="ind-ins" title="Promoters, directors and key managers trading in the market (SEBI disclosures, ₹50 lakh+), last ${x.insiders.days} days. Background only."><a href="#/insiders">Insiders ${x.insiders.days}d</a>: bought ₹${x.insiders.buy_cr} cr · sold ₹${x.insiders.sell_cr} cr${(x.insiders.top || []).length ? ` · ${x.insiders.top.slice(0, 2).map((t) => `${esc(t.sym)} ${t.net_cr >= 0 ? "+" : ""}${t.net_cr}`).join(", ")}` : ""}</small>` : ""}
      <small>${esc(ph)} · ${esc(x.day === new Date().toLocaleDateString("en-CA") ? x.at : `${x.day.slice(5)} ${x.at}`)}${v.busy ? " · updating…" : nextTxt ? ` · next ${esc(nextTxt)}` : ""}${rec.open_n ? ` · open call right ${rec.open_right} of ${rec.open_n} days` : ""}</small>`
      : `<b class="ind-word">–</b><small>${v.busy ? "Reading the market…" : `First reading at ${esc(nextTxt || "8:30")}.`}</small>`}</div>`;
}

/* World markets that move India (Yahoo, about a minute behind): grouped cards, refreshed every 30 s */
function worldBoard(w) {
  if (!w || !(w.groups || []).length) return `<div id="lv-world"></div>`;
  const sp = (a, up) => { if (!a || a.length < 2) return ""; const lo = Math.min(...a), hi = Math.max(...a), r = hi - lo || 1;
    return `<svg viewBox="0 0 60 16" class="spark" aria-hidden="true"><polyline fill="none" stroke="${up ? "#1E8A5A" : "#C23B32"}" stroke-width="1.3" points="${a.map((v, i) => `${(i / (a.length - 1)) * 60},${15 - ((v - lo) / r) * 13}`).join(" ")}"/></svg>`; };
  const pr = w.pressure || {};
  const word = pr.label ? pr.label.charAt(0).toUpperCase() + pr.label.slice(1) : "";
  const drv = (pr.drivers || []).slice(0, 3).map((d) => `${esc(d.name)} ${d.move >= 0 ? "+" : ""}${Number(d.move).toFixed(1)}%`).join(", ");
  return `<div id="lv-world" class="panel tight wboard"><div class="toolbar" style="margin:0 0 6px"><h3 style="margin:0">World markets ${asOf(w.fetched)}</h3>
      ${pr.implied_pct != null ? `<span class="small">Global pressure on Nifty: <b class="${pr.implied_pct > 0.1 ? "up" : pr.implied_pct < -0.1 ? "down" : ""}">${esc(word)}</b> <span class="${pr.implied_pct >= 0 ? "up" : "down"}">(${pr.implied_pct >= 0 ? "+" : ""}${pr.implied_pct}%)</span>${drv ? ` <span class="muted">· from ${drv}</span>` : ""}</span>` : ""}
      <a class="small" href="#/world">World page →</a></div>
    ${w.groups.map((g) => `<h4 class="wb-group">${esc(g.group)}</h4><div class="wb-grid">${g.items.map((x) => `<a class="wb" href="#/world" title="${x.day_l && x.day_h ? `Day ${fx(x.day_l)} – ${fx(x.day_h)}` : ""}${x.hi52 ? ` · 52 weeks ${fx(x.lo52)} – ${fx(x.hi52)}` : ""}${x.ath ? ` · all-time high ${fx(x.ath)}` : ""}">
        <span>${esc(x.name)} ${mkPill(x.mkt)}</span><b>${fx(x.last)}</b> ${fpc(x.chg_pct)}${sp(x.spark, (x.chg_pct || 0) >= 0)}
        ${x.day_l && x.day_h ? `<small class="muted">${fx(x.day_l)} – ${fx(x.day_h)}</small>` : ""}${x.ts ? `<small class="muted">price at ${clockTs(x.ts).slice(0, 5)}</small>` : ""}</a>`).join("")}</div>`).join("")}
    <p class="muted small" style="margin:6px 0 0">Yahoo, about 1 minute behind${w.updated ? ` · updated ${hm(w.updated)}` : ""}. Hover a card for its day, 52-week and all-time range.</p></div>`;
}

async function loadWorldBoard() {
  try { L.world = await api("/api/live/markets"); } catch (_) { return; }
  const el = $("#lv-world"); if (el) el.outerHTML = worldBoard(L.world);
}

function watchStrip(w) {
  if (!w || !w.length) return "";
  const sp = (a) => { if (!a || a.length < 2) return ""; const lo = Math.min(...a), hi = Math.max(...a), r = hi - lo || 1;
    return `<svg viewBox="0 0 60 18" class="spark" aria-hidden="true"><polyline fill="none" stroke="${a[a.length - 1] >= a[0] ? "#1E8A5A" : "#C23B32"}" stroke-width="1.4" points="${a.map((v, i) => `${(i / (a.length - 1)) * 60},${16 - ((v - lo) / r) * 14}`).join(" ")}"/></svg>`; };
  return `<div class="watch-strip" aria-label="US markets">${w.map((x) => `<a class="watch" href="#/world"><span>${esc(x.name)}</span><b>${fx(x.last)}</b> ${fpc(x.chg_pct)}${sp(x.spark)}<small>${x.ts ? hm(x.ts) : ""}</small></a>`).join("")}
    <span class="muted small watch-note">Yahoo, about 1 min behind</span></div>`;
}

function renderLive(force = false) {
  const bar = $("#lv-bar"), body = $("#lv-body");
  if (!bar || !body || !L.snap) return;
  const ae = document.activeElement;
  if (!force && ae && body.contains(ae) && ae.tagName === "BUTTON") return;   // don't re-render under a click
  if (!force && body.querySelector("details.story[open]")) return;             // reading a story: hold still
  bar.innerHTML = statusBar(); bindLiveButtons(bar);
  const s = L.snap;
  if (!s.status.running && !s.indices.length && !s.status.logged_in) {
    body.innerHTML = extraIdx(s.extra) + worldBoard(L.world) + setupCard();
    bindLiveButtons(body); return;
  }
  const nx = (s.session || {}).next, autoTxt = nextSessionText(s.session);       // 2.48: by the NSE calendar
  const closedNote = !s.status.running && s.last_session && s.last_session.asof ? `<p class="banner small">Scanner off: showing the last session (${new Date(s.last_session.asof * 1000).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}). ${autoTxt}</p>` : !s.status.running ? `<p class="banner small">Scanner off. ${autoTxt}</p>` : "";
  const pulse = s.pulse || {};
  const pulseHTML = pulse.ai ? fmtAI(pulse.ai) + `<p class="muted small">AI pulse at ${hm(pulse.ts)}</p>` : (pulse.lines || []).map((l) => `<p>${esc(l)}</p>`).join("") || `<p class="muted">Building the picture…</p>`;
  const nFocus = L.alerts.filter(isFocus).length;
  const mv = s.movers || { hot: [], gainers: [], losers: [] };
  const tab = LS.get("moversTab", "hot");
  const mrows = (mv[tab] || []).map((m) => `<tr class="click" data-sym="${esc(m.sym)}"><td><b>${esc(m.sym)}</b></td><td class="r">${fx(m.spot)}</td><td class="r">${fpc(m.chg_pct)}</td><td class="r">${m.rel_vol ? m.rel_vol.toFixed(1) + "×" : "–"}</td><td class="r">${m.heat ?? "–"}</td></tr>`).join("");
  const pp = s.paper;
  const news = (s.news || []).slice(0, 8).map((n) => `<li><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a><span class="muted small"> ${esc(n.source)} · ${hm(n.ts)}${n.syms && n.syms.length ? " · " + n.syms.map(esc).join(", ") : ""}</span></li>`).join("");
  const mk = s.market || {}, br = mk.breadth, cx = s.context || {};
  const off = s.status && !s.status.running && s.last_session;      // 2.38: each block says when its data is from
  const T = asOf(off ? s.last_session.asof : s.fetched, off ? "Last session's data" : "Data fetched at");
  const fii = cx.fii, fd = cx.fii_dii || {};
  const g = Object.fromEntries((cx.global || []).map((x) => [x.name, x]));
  const reg = mk.regime;
  const strip = `<div class="mstrip">
    <div class="ms-item"><span>NSE ${mkPill(s.nse)}</span><b>${clockTs(off ? s.last_session.asof : s.fetched)}</b><small>${off ? "last session's data" : "data fetched"}</small></div>
    ${reg ? `<div class="ms-item ms-wide"><span>Day type</span><b>${esc(reg.label)}</b><small>${esc(reg.detail || "")}</small></div>` : ""}
    ${br ? `<div class="ms-item"><span>Breadth</span><b><span class="up">${br.adv}</span> / <span class="down">${br.dec}</span></b>
      <div class="adbar"><i style="width:${(br.adv / Math.max(1, br.adv + br.dec)) * 100}%"></i></div><small>${br.above_vwap} of ${br.n} above VWAP</small></div>
      <div class="ms-item"><span>F&amp;O buildups</span><b><span class="up">${br.long_buildup}</span> long · <span class="down">${br.short_buildup}</span> short</b><small>${br.short_covering} covering · ${br.long_unwinding} unwinding</small></div>` : ""}
    ${fii ? `<div class="ms-item"><span>FII index futures</span><b class="${fii.fut_long_pct >= 50 ? "up" : "down"}">${fii.fut_long_pct}% long</b><small>${fii.fut_net_chg != null ? `${fii.fut_net_chg > 0 ? "+" : ""}${fk(fii.fut_net_chg)} contracts yesterday` : ""}</small></div>` : ""}
    ${fd.FII ? `<div class="ms-item"><span>Cash (${esc(fd.FII.date)})</span><b>FII <span class="${fd.FII.net >= 0 ? "up" : "down"}">${fd.FII.net >= 0 ? "+" : ""}${fk(Math.round(fd.FII.net))}</span> · DII <span class="${(fd.DII || {}).net >= 0 ? "up" : "down"}">${(fd.DII || {}).net >= 0 ? "+" : ""}${fk(Math.round((fd.DII || {}).net || 0))}</span></b><small>₹ crore</small></div>` : ""}
    ${g["US S&P 500 fut"] ? `<div class="ms-item"><span>Global</span><b>US ${fpc(g["US S&P 500 fut"].chg_pct, 1)} · Crude ${fpc((g["Brent crude"] || {}).chg_pct, 1)}</b><small>USD/INR ${g["USD/INR"] ? g["USD/INR"].last.toFixed(2) : "–"}</small></div>` : ""}
    ${cx.ban && cx.ban.length ? `<div class="ms-item"><span>F&amp;O ban</span><b class="down">${cx.ban.slice(0, 4).map(esc).join(", ")}${cx.ban.length > 4 ? "…" : ""}</b><small><a href="#/context">Market context →</a></small></div>` : ""}
    ${indTile(s.indication)}
  </div>`;
  const secs = (mk.sectors || []).filter((x) => x.n >= 2);
  const secHTML = secs.length ? `<div class="panel tight"><h3>Sectors ${T}</h3><div class="sectors">${secs.slice(0, 12).map((x) => `<div class="sec" style="--c:${Math.max(-3, Math.min(3, x.chg))}" title="${x.up} of ${x.n} up · best ${esc(x.best[0])} ${x.best[1].toFixed(1)}% · worst ${esc(x.worst[0])} ${x.worst[1].toFixed(1)}%"><span>${esc(x.sector)}</span><b>${x.chg > 0 ? "+" : ""}${x.chg.toFixed(2)}%</b></div>`).join("")}</div></div>` : "";
  const ctab = LS.get("contribTab", "BANKNIFTY");
  const cb = (mk.contrib || {})[ctab];
  const maxPts = cb ? Math.max(1, ...cb.rows.map((r) => Math.abs(r.points))) : 1;
  const contribHTML = mk.contrib && Object.keys(mk.contrib).length ? `<div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3>Who is moving the index ${T}</h3>
      <div class="seg small-seg">${Object.keys(mk.contrib).map((k) => `<button data-ctab="${k}" aria-pressed="${ctab === k}">${k === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</button>`).join("")}</div></div>
    ${cb ? `<div class="contrib">${cb.rows.slice(0, 8).map((r) => `<div class="cb-row"><span>${esc(r.sym)}</span><div class="cb-bar"><i class="${r.points >= 0 ? "pos" : "neg"}" style="width:${Math.abs(r.points) / maxPts * 50}%;${r.points >= 0 ? "left:50%" : `right:50%`}"></i></div><b class="${r.points >= 0 ? "up" : "down"}">${r.points > 0 ? "+" : ""}${r.points.toFixed(0)}</b></div>`).join("")}</div>
      <p class="muted small" style="margin:6px 0 0">Points from index moves × approximate weights. These explain ${cb.explained > 0 ? "+" : ""}${cb.explained.toFixed(0)} of the ${cb.actual > 0 ? "+" : ""}${cb.actual.toFixed(0)}-point move.</p>` : ""}</div>` : "";
  body.innerHTML = `${closedNote}${strip}
    <div class="icards">${s.indices.slice(0, 4).map(indexCard).join("")}</div>
    ${extraIdx(s.extra)}${dhanStrip(s.dhan)}
    ${worldBoard(L.world)}
    <div class="live-grid">
      <section>
        ${eventsMini(L.eventsMini)}
        ${boardMini(L.boardMini)}
        ${brainCard(L.brain, { button: true })}
        <div class="panel pulse"><h3>Market pulse ${T}</h3>${pulseHTML}</div>
        ${contribHTML}${secHTML}
        <p class="small"><a href="#/alerts">Alerts${L.alerts.length ? ` (${nFocus} in focus · ${L.alerts.length} today)` : ""} →</a></p>
      </section>
      <aside>
        <div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3>Movers ${T}</h3>
          <div class="seg small-seg">${[["hot", "Most active"], ["gainers", "Gainers"], ["losers", "Losers"]].map(([k, l]) => `<button data-mtab="${k}" aria-pressed="${tab === k}">${l}</button>`).join("")}</div></div>
          <table class="mini"><thead><tr><th>Stock</th><th class="r">Price</th><th class="r">Chg</th><th class="r">Vol</th><th class="r">Heat</th></tr></thead><tbody>${mrows}</tbody></table>
          <a class="small" href="#/scanner">Open full scanner →</a></div>
        ${pp ? `<a class="panel tight paperbox" href="#/paper"><h3>Paper trading${pp.guard && pp.guard.blocked ? ` <span class="pill p-down">Stopped for today</span>` : ""} ${T}</h3><div class="pb-row"><div><span>Booked</span><b class="${pp.net >= 0 ? "up" : "down"}">${rupees(pp.net)}</b></div><div><span>Open</span><b class="${pp.unreal >= 0 ? "up" : "down"}">${rupees(pp.unreal)}</b></div><div><span>Trades</span><b>${pp.n}${pp.open ? ` <small class="muted">+${pp.open} open</small>` : ""}</b></div><div><span>Win rate</span><b>${pp.win_rate != null ? pp.win_rate + "%" : "–"}</b></div></div></a>` : ""}
        <div class="panel tight"><h3>Market news ${asOf(s.fetched)}</h3><ul class="news">${news || `<li class="muted">Loading headlines…</li>`}</ul></div>
      </aside>
    </div>`;
  bindLiveButtons(body);
  body.querySelectorAll("[data-mtab]").forEach((b) => b.onclick = () => { LS.set("moversTab", b.dataset.mtab); renderLive(true); });
  body.querySelectorAll("[data-ctab]").forEach((b) => b.onclick = () => { LS.set("contribTab", b.dataset.ctab); renderLive(true); });
  body.querySelectorAll("[data-brain-run]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Thinking…"; try { L.brain = await post("/api/brain/run", {}); renderLive(true); } catch (e) { toast(e.message); b.disabled = false; } });
  body.querySelectorAll("tr[data-sym]").forEach((tr) => tr.onclick = () => { location.hash = `#/sym?s=${encodeURIComponent(tr.dataset.sym)}`; });
  fitIndTile();
}

/* ---------------- page: Scanner */
const SCAN_COLS = [
  ["sym", "Symbol", (m) => `<b>${esc(m.sym)}</b>${m.is_index ? ` <span class="pill p-idx">Index</span>` : ""}`, false],
  ["spot", "Price", (m) => `${fx(m.spot)}${m.px_ts ? `<br>${pxTime(m)}` : ""}`, true],
  ["chg_pct", "Day", (m) => fpc(m.chg_pct), true],
  ["ret_5m", "5 min", (m) => fpc(m.ret_5m), true],
  ["vs_vwap_pct", "vs VWAP", (m) => fpc(m.vs_vwap_pct), true],
  ["rel_vol", "Volume", (m) => (m.rel_vol ? `<span class="${m.rel_vol >= 2 ? "hot" : ""}">${m.rel_vol.toFixed(1)}×</span>` : "–"), true],
  ["fut_oi_chg", "Fut OI", (m) => fpc(m.fut_oi_chg, 1), true],
  ["buildup", "Buildup", (m) => (m.buildup ? `<span class="pill ${m.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(m.buildup)}</span>` : ""), false],
  ["atm_iv", "ATM IV", (m) => (m.atm_iv ? m.atm_iv.toFixed(1) + "%" : "–"), true],
  ["iv_chg_15m", "IV 15m", (m) => (m.iv_chg_15m != null ? `<span class="${m.iv_chg_15m > 1 ? "down" : ""}">${m.iv_chg_15m > 0 ? "+" : ""}${m.iv_chg_15m.toFixed(1)}</span>` : "–"), true],
  ["pcr", "PCR", (m) => (m.pcr != null ? m.pcr.toFixed(2) : "–"), true],
  ["iv_hv", "IV/HV", (m) => (m.iv_hv ? `<span class="${m.iv_hv >= 1.3 ? "down" : m.iv_hv <= 1 ? "up" : ""}" title="Option IV vs 20-day realised volatility: above 1.3 = options expensive">${m.iv_hv.toFixed(2)}</span>` : "–"), true],
  ["flow", "Flow 5m", (m) => (m.flow != null ? `<span class="${m.flow > 30 ? "up" : m.flow < -30 ? "down" : "muted"}" title="Buyer minus seller initiated volume, last 5 minutes">${m.flow > 0 ? "+" : ""}${Math.round(m.flow)}%</span>` : "–"), true],
  ["unusual", "Unusual", (m) => (m.unusual ? `<span class="pill p-sig">${m.unusual}</span>` : ""), true],
  ["score", "Bias", (m) => (m.score != null ? `<span class="${m.score >= 15 ? "up" : m.score <= -15 ? "down" : "muted"}">${m.score > 0 ? "+" : ""}${m.score}</span>` : "–"), true],
  ["heat", "Heat", (m) => (m.heat != null ? `<span class="heat" style="--h:${m.heat}">${m.heat}</span>` : "–"), true],
];
const SCAN_FILTERS = {
  all: ["All", () => true],
  long: ["Long buildup", (m) => m.buildup === "Long buildup"],
  short: ["Short buildup", (m) => m.buildup === "Short buildup"],
  cover: ["Short covering", (m) => m.buildup === "Short covering"],
  unusual: ["Unusual options", (m) => m.unusual > 0],
  volume: ["Volume 2×+", (m) => (m.rel_vol || 0) >= 2],
  buyers: ["Buyers aggressive", (m) => (m.flow || 0) >= 40],
  sellers: ["Sellers aggressive", (m) => (m.flow || 0) <= -40],
  cheap: ["Options cheap", (m) => m.iv_hv != null && m.iv_hv <= 1],
  bull: ["Bullish bias", (m) => (m.score || 0) >= 40],
  bear: ["Bearish bias", (m) => (m.score || 0) <= -40],
};

async function pageScanner() {
  stopPageTimer();
  L.scanMode = LS.get("scanMode", "simple");
  main.innerHTML = `<div class="toolbar"><h1 class="page-title" style="margin:0">Scanner</h1>
      <div class="seg" role="group" aria-label="View">${[["simple", "Simple"], ["detail", "Detailed"]].map(([k, l]) => `<button data-mode="${k}" aria-pressed="${L.scanMode === k}">${l}</button>`).join("")}</div></div>
    <p class="lede">Every F&amp;O stock and index, ranked by how much is happening right now. Simple view says it in words; Detailed shows the numbers. Click a row for its chart.</p>
    <details class="panel tight scan-help"><summary><b>How to read this</b></summary><ul class="small">
      <li><b>Bullish / Bearish / Mixed</b>: the scanner's combined read of trend vs VWAP, futures activity, momentum and the opening range (Bias −100 to +100).</li>
      <li><b>Volume ×</b>: today's pace vs a normal day at this time. 2× or more = big players are active.</li>
      <li><b>Fresh buying / selling</b>: futures open interest rising with price up (buyers adding) or down (sellers adding). <b>Short covering</b> = sellers exiting, often a fast rally; <b>long unwinding</b> = buyers exiting.</li>
      <li><b>Above / below VWAP</b>: above the day's average traded price = buyers in control so far.</li>
      <li><b>Unusual options</b>: heavy option trading at one strike in 5 minutes. <b>Heat</b> = how unusual all of this is together.</li>
      <li>This is a radar, not a trade: trade ideas come as alerts with a risk check.</li></ul></details>
    <div id="lv-bar"></div>
    <div class="toolbar"><div class="chips">${Object.entries(SCAN_FILTERS).map(([k, [l]]) => `<button class="chip" data-f="${k}" aria-pressed="${L.filter === k}">${l}</button>`).join("")}</div>
      <input type="search" id="scan-q" placeholder="Find a stock" value="${esc(L.q)}" aria-label="Find a stock"></div>
    <div class="table-wrap scan"><table><thead id="scan-head"></thead><tbody id="scan-body"></tbody></table></div>
    <p class="note">Heat = how unusual activity is right now (volume pace, speed of the move, futures OI change, unusual option trades). Bias = −100 (bearish) to +100 (bullish) from VWAP, buildup, momentum and opening range.</p>`;
  $("#lv-bar").innerHTML = statusBar(); bindLiveButtons($("#lv-bar"));
  main.querySelectorAll("[data-f]").forEach((b) => b.onclick = () => { L.filter = b.dataset.f; main.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x === b)); drawScanner(); });
  main.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => { L.scanMode = b.dataset.mode; LS.set("scanMode", L.scanMode);
    main.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", x === b)); drawScanHead(); drawScanner(); });
  drawScanHead();
  $("#scan-q").oninput = (e) => { L.q = e.target.value; drawScanner(); };
  const tick = async () => {
    if (state.page !== "scanner") return;
    try { L.scanner = await api("/api/live/scanner"); drawScanner(); } catch (_) {}
    const bar = $("#lv-bar"); if (bar && L.snap) { bar.innerHTML = statusBar(); bindLiveButtons(bar); }
    L.pageTimer = setTimeout(tick, 2000);
  };
  tick();
}

function drawScanHead() {
  const h = $("#scan-head"); if (!h) return;
  if (L.scanMode === "simple") { h.innerHTML = `<tr><th>Stock</th><th class="r">Price</th><th>Read</th><th>What is happening</th><th></th></tr>`; return; }
  h.innerHTML = `<tr>${SCAN_COLS.map(([k, l, , r]) => `<th class="${r ? "r" : ""} sortable" data-k="${k}" aria-sort="${L.sort.key === k ? (L.sort.dir > 0 ? "ascending" : "descending") : "none"}">${l}</th>`).join("")}</tr>`;
  h.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
    const k = th.dataset.k; L.sort = { key: k, dir: L.sort.key === k ? -L.sort.dir : (k === "sym" ? 1 : -1) };
    h.querySelectorAll("th[data-k]").forEach((x) => x.setAttribute("aria-sort", x.dataset.k === k ? (L.sort.dir > 0 ? "ascending" : "descending") : "none"));
    drawScanner();
  });
}

const BUILDUP_WORDS = { "Long buildup": "fresh buying in futures", "Short buildup": "fresh selling in futures", "Short covering": "sellers exiting (short covering)", "Long unwinding": "buyers exiting (long unwinding)" };
function scanWords(m) {
  const out = [];
  if (m.chg_pct != null) out.push(`${m.chg_pct >= 0 ? "up" : "down"} ${Math.abs(m.chg_pct).toFixed(2)}% today`);
  if (m.ret_15m != null && Math.abs(m.ret_15m) >= 0.4) out.push(`${m.ret_15m > 0 ? "rising" : "falling"} ${Math.abs(m.ret_15m).toFixed(2)}% in 15 min`);
  if (m.rel_vol != null && m.rel_vol >= 1.5) out.push(`volume ${m.rel_vol.toFixed(1)}× normal${m.rel_vol >= 2 ? " (big players active)" : ""}`);
  if (m.buildup) out.push(BUILDUP_WORDS[m.buildup] || m.buildup.toLowerCase());
  if (m.vs_vwap_pct != null) out.push(`${m.vs_vwap_pct >= 0 ? "above" : "below"} VWAP${Math.abs(m.vs_vwap_pct) >= 0.5 ? ` by ${Math.abs(m.vs_vwap_pct).toFixed(1)}%` : ""}`);
  if (m.unusual) out.push(`${m.unusual} unusual option trade${m.unusual > 1 ? "s" : ""}`);
  if (m.iv_chg_15m != null && m.iv_chg_15m >= 2) out.push(`options getting pricier (IV +${m.iv_chg_15m.toFixed(1)})`);
  return out.join(" · ");
}
function scanRead(score) {
  if (score == null) return `<span class="pill">–</span>`;
  return score >= 30 ? `<span class="pill p-up">Bullish</span>` : score <= -30 ? `<span class="pill p-down">Bearish</span>` : `<span class="pill">Mixed</span>`;
}

function drawScanner() {
  const tb = $("#scan-body"); if (!tb) return;
  const simple = L.scanMode === "simple";
  const q = L.q.trim().toUpperCase(), f = SCAN_FILTERS[L.filter][1];
  const { key, dir } = simple ? { key: "heat", dir: -1 } : L.sort;
  const rows = L.scanner.filter((m) => f(m) && (!q || m.sym.includes(q) || (m.name || "").toUpperCase().includes(q)))
    .sort((a, b) => { const x = a[key], y = b[key]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
  const cols = simple ? 5 : SCAN_COLS.length;
  tb.innerHTML = (simple
    ? rows.slice(0, 150).map((m) => `<tr class="click" data-sym="${esc(m.sym)}"><td><b>${esc(m.sym)}</b><br><small class="muted">${esc(m.name || "")}</small></td>
        <td class="r"><b>${fx(m.spot)}</b> ${pxTime(m)}<br><small class="${(m.chg_pct || 0) >= 0 ? "up" : "down"}">${(m.chg_pct || 0) >= 0 ? "+" : ""}${(m.chg_pct || 0).toFixed(2)}%</small></td>
        <td>${scanRead(m.score)}</td><td class="small">${esc(scanWords(m)) || "<span class='muted'>quiet</span>"}</td>
        <td class="nw small"><a href="#/chart?s=${encodeURIComponent(m.sym)}">Chart</a> · <a href="#/sym?s=${encodeURIComponent(m.sym)}">Chain</a> · <a href="#" data-watch="${esc(m.sym)}">+ Watch</a></td></tr>`).join("")
    : rows.map((m) => `<tr class="click" data-sym="${esc(m.sym)}">${SCAN_COLS.map(([k, , fn, r]) => `<td class="${r ? "r" : ""}">${fn(m)}</td>`).join("")}</tr>`).join("")) ||
    `<tr><td colspan="${cols}" class="muted">${L.scanner.length ? "Nothing matches this filter right now." : "Fills in while the live scanner runs (it starts by itself before every NSE session)."}</td></tr>`;
  tb.querySelectorAll("[data-watch]").forEach((a) => a.onclick = async (ev) => { ev.preventDefault(); ev.stopPropagation();
    try { await post("/api/watchlist", { add: a.dataset.watch }); toast(`${a.dataset.watch} added to Watchlist`); } catch (e) { toast(e.message); } });
  tb.querySelectorAll("tr[data-sym]").forEach((tr) => tr.onclick = (ev) => { if (ev.target.closest("a")) return; location.hash = `#/${simple ? "chart" : "sym"}?s=${encodeURIComponent(tr.dataset.sym)}`; });
}

/* ---------------- page: Symbol / option chain */
/* ---------------- symbol picker (indices + F&O stocks), works before the scanner starts */
async function symbolList() {
  if (L.symList && Date.now() - L.symList._at < 300000) return L.symList;
  try { L.symList = { ...(await api("/api/symbols")), _at: Date.now() }; } catch (_) { L.symList = { indices: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"], stocks: [], _at: Date.now() }; }
  return L.symList;
}
function symbolOptions(list, sel) {
  return `<optgroup label="Indices">${list.indices.map((x) => `<option value="${esc(x)}" ${x === sel ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup>
    <optgroup label="F&amp;O stocks">${list.stocks.map((x) => `<option value="${esc(x)}" ${x === sel ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup>`;
}

async function pageSym() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const sym = (params.get("s") || LS.get("lastSym", "NIFTY")).toUpperCase();
  if (L.sym !== sym) { L.sym = sym; L.expiry = null; }
  LS.set("lastSym", sym);
  const list = await symbolList();
  if (![...list.indices, ...list.stocks].includes(sym)) list.stocks = [sym, ...list.stocks];
  main.innerHTML = `<div class="sym-pick"><label class="sr-only" for="sym-pick">Symbol</label><select id="sym-pick" aria-label="Symbol">${symbolOptions(list, sym)}</select>
      <a class="btn" href="#/chart?s=${encodeURIComponent(sym)}">Chart</a><a class="btn" href="#/timemachine?s=${encodeURIComponent(sym)}">Time Machine</a></div>
    <div id="sym-body"><p class="muted">Loading ${esc(sym)}…</p></div>`;
  $("#sym-pick").onchange = (e) => { location.hash = `#/sym?s=${encodeURIComponent(e.target.value)}`; };
  const tick = async (first) => {
    if (state.page !== "sym" || L.sym !== sym) return;
    try {
      const d = await api(`/api/live/symbol/${encodeURIComponent(sym)}${L.expiry ? `?expiry=${L.expiry}` : ""}`);
      if (d && d.closed) throw new Error(d.message || "No live data yet.");
      drawSym(d, first);
    } catch (e) {
      if (first) $("#sym-body").innerHTML = `<div class="empty"><h2>${esc(sym)}: option chain needs the live feed</h2><p>${esc(e.message)} The live scanner starts by itself before every NSE session. Meanwhile the chart shows the last session.</p><div class="btn-row"><a class="btn primary" href="#/chart?s=${encodeURIComponent(sym)}">Open last chart</a><a class="btn" href="#/live">Go to Live</a></div></div>`;
    }
    L.pageTimer = setTimeout(() => tick(false), $("#sym-body .empty") ? 20000 : 3000);
  };
  tick(true);
}

function drawSym(d, first) {
  const m = d.metrics, o = m.opt || {}, f = m.fut || {}, ch = d.chain;
  const body = $("#sym-body"); if (!body) return;
  const keepScroll = !first && $(".chain-wrap") ? $(".chain-wrap").scrollTop : null;
  const rows = ch ? ch.rows : (o.rows || []);
  const maxOI = Math.max(1, ...rows.flatMap((r) => [r.CE && r.CE.oi || 0, r.PE && r.PE.oi || 0]));
  const atm = o.atm;
  const expiries = ch ? ch.expiries : [o.expiry];
  const side = (x, s) => {
    if (!x) return s === "CE" ? `<td colspan="6"></td>` : `<td colspan="6"></td>`;
    const bar = `<span class="oibar ${s}" style="width:${((x.oi || 0) / maxOI) * 100}%"></span>`;
    const oiChg = x.oi_chg != null ? `<small class="${x.oi_chg >= 0 ? "up" : "down"}">${x.oi_chg >= 0 ? "+" : ""}${compact(x.oi_chg)}</small>` : "";
    const cells = [`<td class="r oi">${bar}<span>${compact(x.oi)}</span>${oiChg}</td>`, `<td class="r">${compact(x.vol)}</td>`, `<td class="r">${x.iv ? x.iv.toFixed(1) : "–"}</td>`,
      `<td class="r ltp">${fx(x.ltp)}${x.key ? `<span class="bs"><button class="b" data-bs="${esc(x.key)}" data-d="1" title="Paper BUY 1 lot">B</button><button class="s" data-bs="${esc(x.key)}" data-d="-1" title="Paper SELL (exit if you hold it, else short)">S</button></span>` : ""}</td>`, `<td class="r">${fpc(x.chg_pct, 1)}</td>`, `<td class="r muted">${x.delta != null ? x.delta.toFixed(2) : ""}</td>`];
    return (s === "CE" ? cells : cells.slice().reverse()).join("");
  };
  const chainRows = rows.map((r) => {
    const itmCE = r.strike < m.spot, itmPE = r.strike > m.spot;
    const tags = [r.strike === o.max_pain ? `<span class="tag">Max pain</span>` : "", r.strike === o.resistance ? `<span class="tag t-down">Resistance</span>` : "", r.strike === o.support ? `<span class="tag t-up">Support</span>` : ""].join("");
    return `<tr class="${r.strike === atm ? "atm" : ""}"><td class="ce-side ${itmCE ? "itm" : ""}" hidden></td>${side(r.CE, "CE").replace(/<td class="/g, `<td class="${itmCE ? "itm " : ""}`)}<td class="strike">${fk(r.strike)}${tags}</td>${side(r.PE, "PE").replace(/<td class="/g, `<td class="${itmPE ? "itm " : ""}`)}</tr>`;
  }).join("");
  const unusual = (o.unusual || []).map((u) => `<li>${arrow(u.direction)} <b>${fk(u.strike)} ${u.side}</b> — heavy ${u.kind}: ${u.lots_5m} lots in 5 min (${u.x_pace}× pace), premium ${fp(u.prem_chg_5m, 1)}</li>`).join("");
  body.innerHTML = `
    <div class="sym-head"><div><a class="small" href="#/scanner">← Scanner</a> · <a class="small" href="#/chart?s=${encodeURIComponent(m.sym)}">Open chart</a>
      <h1 class="page-title">${esc(m.name)} <span class="muted" style="font-weight:500">${esc(m.sym)}</span></h1>
      <div class="sym-price">${fx(m.spot)} ${fpc(m.chg_pct)} ${pxTime(m)} ${f.buildup_label ? `<span class="pill ${f.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(f.buildup_label)}</span>` : ""}</div></div>
      <div class="sym-bias">${biasMeter(m.score)}${m.reasons && m.reasons.length ? `<p class="ic-why">${esc(m.reasons.join(" · "))}</p>` : ""}</div></div>
    <div class="statgrid">
      ${[["VWAP", fx(m.vwap)], ["Day range", `${fx(m.day_l)} – ${fx(m.day_h)}`], ["Opening range", m.or_low ? `${fx(m.or_low)} – ${fx(m.or_high)}` : "after 9:30"],
         ["Volume pace", m.rel_vol ? m.rel_vol.toFixed(1) + "× normal" : "–"], ["5 / 15 min", `${fp(m.ret_5m)} / ${fp(m.ret_15m)}`],
         ["Futures OI", f.oi_chg_pct != null ? `${fp(f.oi_chg_pct, 1)} <small class="muted">vs ${esc(f.oi_since)}</small>` : "–"],
         ["ATM IV", o.atm_iv ? `${o.atm_iv.toFixed(1)}%${o.iv_chg_15m != null ? ` <small>(${o.iv_chg_15m > 0 ? "+" : ""}${o.iv_chg_15m.toFixed(1)} in 15m)</small>` : ""}` : "–"],
         ["Straddle / priced move", o.straddle ? `₹${fx(o.straddle)} · ±${o.exp_move_pct.toFixed(2)}%` : "–"],
         ["PCR", o.pcr != null ? o.pcr.toFixed(2) + (o.pcr_open != null ? ` <small class="muted">open ${o.pcr_open.toFixed(2)}</small>` : "") : "–"],
         ["Max pain", o.max_pain ? fk(o.max_pain) : "–"], ["Support / resistance", `<span class="up">${o.support ? fk(o.support) : "–"}</span> / <span class="down">${o.resistance ? fk(o.resistance) : "–"}</span>`],
         ["Fresh writing", `CE ${o.ce_writing ? fk(o.ce_writing) : "–"} · PE ${o.pe_writing ? fk(o.pe_writing) : "–"}`]].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("")}
    </div>
    ${symInsights(d)}
    <div class="sym-grid">
      <div class="chart" id="sym-chart" style="height:300px"></div>
      <div class="panel tight"><h3>Unusual option activity</h3><ul class="plain">${unusual || `<li class="muted">Nothing unusual in the watched strikes right now.</li>`}</ul>
        <h3 style="margin-top:14px">Alerts</h3><ul class="plain">${d.alerts.slice(0, 6).map((a) => `<li>${hm(a.ts)} ${arrow(a.direction)} ${esc(a.title)}</li>`).join("") || `<li class="muted">None yet today.</li>`}</ul>
        ${d.news.length ? `<h3 style="margin-top:14px">News</h3><ul class="plain">${d.news.slice(0, 5).map((n) => `<li><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join("")}</ul>` : ""}</div>
    </div>
    <div class="toolbar" style="margin-top:22px"><h2 style="margin:0">Option chain <span class="muted small">${ch ? "all strikes" : esc(o.oi_scope || "")}${ch ? ` · updated ${hms(ch.fetched)}` : ""}</span></h2>
      <label class="field inline" title="Lots for the B / S paper buttons">Lots <input type="number" id="bs-lots" min="1" max="50" value="${L.bsLots || 1}" style="width:64px"></label>
      <label class="field inline">Expiry <select id="exp-sel">${expiries.filter(Boolean).map((e) => `<option value="${e}" ${e === (ch ? ch.expiry : o.expiry) ? "selected" : ""}>${new Date(e + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</option>`).join("")}</select></label></div>
    <div class="chain-sides"><b class="ce-h">← CALLS (CE)</b><b class="pe-h">PUTS (PE) →</b></div>
    <div class="table-wrap chain-wrap"><table class="chain"><thead>
      <tr><th class="r">OI · change</th><th class="r">Volume</th><th class="r">IV</th><th class="r">LTP</th><th class="r">Chg</th><th class="r">Δ</th><th class="strike">Strike</th>
          <th class="r">Δ</th><th class="r">Chg</th><th class="r">LTP</th><th class="r">IV</th><th class="r">Volume</th><th class="r">OI · change</th></tr></thead>
      <tbody>${chainRows}</tbody></table></div>
    <p class="note"><b>B / S</b> = paper Buy / Sell at the live price (S closes a position you hold, otherwise opens a paper short). Shaded = in the money. Biggest call OI above price acts as resistance, biggest put OI below acts as support. Max pain = the expiry price where option buyers collectively lose the most.</p>`;
  $("#exp-sel").onchange = (e) => { L.expiry = e.target.value; pageSym(); };
  $("#bs-lots").onchange = (e) => { L.bsLots = Math.max(1, Number(e.target.value) || 1); };
  if (!body.dataset.bsBound) {          // paper Buy / Sell straight from the chain (one handler, survives refreshes)
    body.dataset.bsBound = "1";
    body.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-bs]"); if (!b) return;
      ev.preventDefault(); b.disabled = true;
      try {
        const r = await post("/api/live/paper/open", { key: b.dataset.bs, direction: Number(b.dataset.d), lots: L.bsLots || 1 });
        toast(r.closed ? `Paper position closed · net ${rupees(r.closed.net)}` : `Paper ${Number(b.dataset.d) > 0 ? "BUY" : "SELL"} done · see Paper trades`);
        navPnl();
      } catch (e) { toast(e.message); }
      b.disabled = false;
    });
  }
  const wrap = $(".chain-wrap");
  if (keepScroll != null) wrap.scrollTop = keepScroll;
  else { const a = wrap.querySelector("tr.atm"); if (a) wrap.scrollTop = a.offsetTop - wrap.clientHeight / 2; }
  drawSymChart(d);
  drawGex(d);
}

function planCard(dir, lv, st) {
  if (!lv || !lv.stop) return "";
  const up = dir === "up";
  const sp = st && st.spread;
  return `<div class="plan-card ${up ? "pc-up" : "pc-down"}"><h4>${up ? "▲ If it goes up" : "▼ If it goes down"}</h4>
    <dl><div><dt>Target</dt><dd>${fx(lv.target)} <small>${lv.target_pct}%</small></dd></div>
    <div><dt>Idea wrong ${up ? "below" : "above"}</dt><dd>${fx(lv.stop)} <small>${lv.stop_pct}%</small></dd></div>
    <div><dt>Reward : risk</dt><dd>${lv.rr ?? "–"}</dd></div></dl>
    ${st ? `<p><span class="pill ${st.type === "buy" ? "p-up" : st.type === "spread" ? "p-amber" : "p-down"}">${st.type === "buy" ? "Buy the option" : st.type === "spread" ? "Use a spread" : "Skip"}</span> <small class="muted">${esc(st.why)}</small></p>
    ${st.buy ? `<p class="small">Buy ${fk(st.buy.strike)} ${st.side} @ ₹${fx(st.buy.ltp)} = ${rupees(st.buy.cost)}/lot${sp ? `<br>Spread: buy ${fk(sp.buy)} sell ${fk(sp.sell)} for ${rupees(sp.cost)}, max profit ${rupees(sp.max_profit)}, breakeven ${fx(sp.breakeven)}` : ""}</p>` : ""}` : ""}</div>`;
}

function symInsights(d) {
  const m = d.metrics, v = m.vol || {}, fl = m.flow || {}, g = d.gex, r = d.regime, dl = d.daily || {};
  const ivhv = v.iv_hv != null ? `${v.iv_hv.toFixed(2)}× <small class="${v.iv_hv >= 1.3 ? "down" : v.iv_hv <= 1 ? "up" : "muted"}">${v.iv_hv >= 1.3 ? "options expensive" : v.iv_hv <= 1 ? "options cheap" : "fair"}</small>` : "–";
  const volBox = `<div class="panel tight"><h3>Volatility</h3><dl class="kvs">
    <div><dt>Option IV (ATM)</dt><dd>${v.iv ? v.iv.toFixed(1) + "%" : "–"}</dd></div>
    <div><dt>Actual moves, 20 days</dt><dd>${v.hv20 ? v.hv20.toFixed(1) + "%" : "–"}</dd></div>
    <div><dt>IV ÷ actual</dt><dd>${ivhv}</dd></div>
    <div><dt>${v.iv_pct != null ? "IV rank (own history)" : m.is_index ? "India VIX, 1-year rank" : "IV rank"}</dt><dd>${v.iv_pct != null ? v.iv_pct + "%" : v.vix_pct != null ? v.vix_pct + "%" : "<small class=muted>builds after ~15 days</small>"}</dd></div>
    <div><dt>Actual-move rank, 1 year</dt><dd>${v.hv_pct != null ? v.hv_pct + "%" : "–"}</dd></div>
    <div><dt>Put skew</dt><dd>${v.skew != null ? `${v.skew > 0 ? "+" : ""}${v.skew.toFixed(1)} pts <small class="muted">${v.skew > 2 ? "fear priced in" : v.skew < -1 ? "calls in demand" : "normal"}</small>` : "–"}</dd></div>
    ${v.term != null ? `<div><dt>Next expiry IV</dt><dd>${v.next_iv.toFixed(1)}% <small class="muted">${v.term < -0.5 ? "near expiry stressed" : "normal"}</small></dd></div>` : ""}
    ${dl.close_1y_hi ? `<div><dt>1-year range</dt><dd>${fx(dl.close_1y_lo)} – ${fx(dl.close_1y_hi)}</dd></div>` : ""}
  </dl></div>`;
  const flowBox = `<div class="panel tight"><h3>Order flow ${m.is_index ? "<small class=muted>(futures)</small>" : ""}</h3><dl class="kvs">
    <div><dt>Buyers vs sellers, 5 min</dt><dd>${fl.delta_pct_5m != null ? `<span class="${fl.delta_pct_5m > 0 ? "up" : "down"}">${fl.delta_pct_5m > 0 ? "+" : ""}${Math.round(fl.delta_pct_5m)}%</span> <small class="muted">${fl.delta_pct_5m > 30 ? "buyers lifting offers" : fl.delta_pct_5m < -30 ? "sellers hitting bids" : "balanced"}</small>` : "–"}</dd></div>
    <div><dt>5-min volume pace</dt><dd>${fl.rel_5m ? fl.rel_5m.toFixed(1) + "×" : "–"}</dd></div>
    <div><dt>Net aggressive volume today</dt><dd class="${(fl.cvd || 0) >= 0 ? "up" : "down"}">${fl.cvd != null ? compact(fl.cvd) : "–"}</dd></div>
    <div><dt>Order book</dt><dd>${fl.book_imb != null ? `${fl.book_imb > 0 ? "+" : ""}${fl.book_imb}% <small class="muted">${fl.book_imb > 15 ? "more buyers waiting" : fl.book_imb < -15 ? "more sellers waiting" : "even"}</small>` : "–"}</dd></div>
  </dl>${d.prints.length ? `<h3 style="margin-top:10px">Big trades</h3><ul class="plain">${d.prints.slice().reverse().slice(0, 5).map((p) => `<li>${hm(p.ts)} ${p.side > 0 ? '<span class="up">Buy</span>' : '<span class="down">Sell</span>'} ₹${(p.qty * p.price / 1e7).toFixed(1)} cr ${esc(p.what)} @ ${fx(p.price)}</li>`).join("")}</ul>` : ""}</div>`;
  const gBox = g ? `<div class="panel tight"><h3>Dealer gamma</h3><dl class="kvs">
    <div><dt>Mode</dt><dd class="${g.regime === "pinning" ? "" : "amber"}"><b>${g.regime === "pinning" ? "Pinning" : "Accelerating"}</b></dd></div>
    <div><dt>Net gamma</dt><dd>₹${fk(g.net_cr)} cr per 1%</dd></div>
    <div><dt>Gamma flip</dt><dd>${g.flip ? fk(Math.round(g.flip)) : "none nearby"}</dd></div>
    <div><dt>Call wall / put wall</dt><dd><span class="down">${g.call_wall ? fk(g.call_wall) : "–"}</span> / <span class="up">${g.put_wall ? fk(g.put_wall) : "–"}</span></dd></div>
  </dl><p class="muted small" style="margin:6px 0 0">${g.regime === "pinning" ? "Dealers hedge against the move: dips get bought, rallies sold. Price tends to stick near big strikes." : "Dealers hedge with the move: breaks can run fast. Below the flip, volatility rises."} Standard assumption: dealers are long calls and short puts.</p></div>` : "";
  const regBox = r && r.code !== "opening" ? `<div class="panel tight"><h3>Day type: ${esc(r.label)}</h3><p class="small" style="margin:0">${esc(r.detail)}</p></div>` : "";
  return `<div class="insights">${volBox}${flowBox}${gBox}${regBox}</div>
    <div class="plans">${planCard("up", d.levels.up, d.structure.up)}${planCard("down", d.levels.down, d.structure.down)}</div>
    ${g && g.profile ? `<div class="chart" id="gex-chart" style="height:220px;margin-top:14px"></div>` : ""}`;
}

// Plotly keeps the width it first measured; when the layout settles later, refit it to its box
const plotFit = (el) => requestAnimationFrame(() => { try { if (el && el._fullLayout && Math.abs(el._fullLayout.width - el.clientWidth) > 4) Plotly.Plots.resize(el); } catch (_) {} });

function drawGex(d) {
  const el = $("#gex-chart"); if (!el || !window.Plotly || !d.gex) return;
  const spot = d.metrics.spot;
  const prof = d.gex.profile.filter((p) => Math.abs(p.strike / spot - 1) < 0.04);
  Plotly.react(el, [{ type: "bar", x: prof.map((p) => p.strike), y: prof.map((p) => p.gex_cr), marker: { color: prof.map((p) => (p.gex_cr >= 0 ? C.signal : C.down)) }, hovertemplate: "%{x}: ₹%{y:,.0f} cr<extra></extra>" }],
    plotLayout({ margin: { l: 60, r: 12, t: 24, b: 30 }, title: { text: "Gamma by strike (purple = calls dampen, red = puts accelerate)", font: { size: 12 }, x: 0 },
      yaxis: { gridcolor: "#EEF1F4", ticksuffix: " cr" }, xaxis: { gridcolor: "#EEF1F4" },
      shapes: [{ type: "line", x0: spot, x1: spot, yref: "paper", y0: 0, y1: 1, line: { color: C.ink, width: 1.5 } },
        ...(d.gex.flip ? [{ type: "line", x0: d.gex.flip, x1: d.gex.flip, yref: "paper", y0: 0, y1: 1, line: { color: C.amber, width: 1.5, dash: "dash" } }] : [])],
      annotations: [{ x: spot, yref: "paper", y: 1, text: "Now", showarrow: false, yanchor: "bottom", font: { size: 11 } },
        ...(d.gex.flip ? [{ x: d.gex.flip, yref: "paper", y: 0.9, text: "Flip", showarrow: false, font: { size: 11, color: C.amber } }] : [])] }), plotCfg);
  plotFit(el);
}

function drawSymChart(d) {
  const el = $("#sym-chart"); if (!el || !window.Plotly) return;
  const b = d.bars, m = d.metrics, o = m.opt || {};
  if (!b.length) { el.innerHTML = `<p class="muted" style="padding:16px">Chart fills in as minutes pass.</p>`; return; }
  const x = b.map((r) => new Date(r[0] * 1000));
  const lines = [];
  const hl = (y, color, name, dash = "dot") => y && lines.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color, width: 1, dash }, name });
  hl(m.vwap, C.signal, "VWAP", "solid"); hl(m.or_high, C.muted, "OR high"); hl(m.or_low, C.muted, "OR low");
  hl(o.resistance, C.down, "Resistance", "dash"); hl(o.support, C.up, "Support", "dash");
  const ann = [["VWAP", m.vwap, C.signal], ["Resistance", o.resistance, C.down], ["Support", o.support, C.up]].filter((a) => a[1]).map(([t, y, c]) => ({ xref: "paper", x: 1, y, text: t, showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 11, color: c } }));
  const lo = Math.min(...b.map((r) => r[3])), hi = Math.max(...b.map((r) => r[2]));
  const pad = (hi - lo) * 0.15 || hi * 0.002;
  const inRange = (v) => v && v > lo - pad * 3 && v < hi + pad * 3;
  Plotly.react(el, [{ type: "candlestick", x, open: b.map((r) => r[1]), high: b.map((r) => r[2]), low: b.map((r) => r[3]), close: b.map((r) => r[4]),
    increasing: { line: { color: C.up } }, decreasing: { line: { color: C.down } }, hoverinfo: "x+y" }],
    plotLayout({ margin: { l: 56, r: 12, t: 8, b: 30 }, xaxis: { rangeslider: { visible: false }, gridcolor: "#EEF1F4", type: "date", tickformat: "%H:%M" },
      yaxis: { gridcolor: "#EEF1F4", range: [Math.min(lo, ...[o.support, m.vwap].filter(inRange)) - pad, Math.max(hi, ...[o.resistance, m.vwap].filter(inRange)) + pad] },
      shapes: lines.filter((l) => inRange(l.y0)), annotations: ann.filter((a) => inRange(a.y)), hovermode: "x" }), plotCfg);
  plotFit(el);
}

/* ---------------- page: Alerts history */
async function pageAlerts() {
  stopPageTimer();
  // 2.29: Act now → Focus (with the other important ones) → All; type chips filter; choices remembered
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const VIEWS = [["act", "Act now", "Strongest alerts (act now)"], ["focus", "Focus", "Focus alerts plus the other important ones that are not demoted"], ["all", "All", "Every alert"]];
  const view = params.get("view") || LS.get("alertsView", "focus");
  const sym = params.has("sym") ? params.get("sym") : LS.get("alertsSym", "");
  const kind = params.has("kind") ? params.get("kind") : LS.get("alertsKind", "");
  const day = params.get("day") || "latest";
  LS.set("alertsView", view); LS.set("alertsSym", sym); LS.set("alertsKind", kind);
  const d = await api(`/api/live/alerts?day=${day}&view=${view}${sym ? `&sym=${encodeURIComponent(sym)}` : ""}`);
  if (d.shadow) VIEWS.push(["shadow", "Shadow Focus", `On trial: ${d.shadow.label}. Recorded only: nothing you see, get on Telegram or paper-trade changes`]);
  const cnt = d.counts || {};
  const byKind = {};
  d.alerts.forEach((a) => { byKind[a.kind] = (byKind[a.kind] || 0) + 1; });
  const shown = kind ? d.alerts.filter((a) => a.kind === kind) : d.alerts;
  const go = (p) => { const q = new URLSearchParams({ day: d.day || "", view, sym, kind, ...p }); location.hash = `#/alerts?${q}`; };
  const vName = (VIEWS.find((v) => v[0] === (d.view || view)) || VIEWS[1])[1];
  main.innerHTML = `<h1 class="page-title">Alerts</h1><p class="lede">Every alert, saved by day. ${d.day ? `${shown.length} ${esc(vName.toLowerCase())}${kind ? ` · ${esc(KIND[kind] || kind)}` : ""} (of ${d.total}) on ${new Date(d.day + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}.` : "None yet: they appear here as the scanner finds them."}</p>
    <div class="toolbar"><div class="btn-row">
      <label class="field inline">Day <select id="al-day">${d.days.map((x) => `<option value="${x.day}" ${x.day === d.day ? "selected" : ""}>${x.day} (${x.n})</option>`).join("") || `<option>No alerts yet</option>`}</select></label>
      <div class="seg">${VIEWS.map(([v, l, t]) => `<button data-v="${v}" aria-pressed="${(d.view || view) === v}" title="${esc(t)}">${l}${cnt[v] != null ? ` <small class="muted">${cnt[v]}</small>` : ""}</button>`).join("")}</div>
      <input type="search" id="al-sym" placeholder="Symbol" value="${esc(sym)}" aria-label="Filter by symbol"></div></div>
    <div class="chips small al-kinds" role="group" aria-label="Filter by alert type">
      <button class="chip${kind ? "" : " on"}" data-k="" aria-pressed="${!kind}">All types ${d.alerts.length}</button>
      ${Object.entries(byKind).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<button class="chip${kind === k ? " on" : ""}" data-k="${esc(k)}" aria-pressed="${kind === k}">${esc(KIND[k] || k)} ${n}</button>`).join("")}
      ${kind && !byKind[kind] ? `<button class="chip on" data-k="${esc(kind)}" aria-pressed="true">${esc(KIND[kind] || kind)} 0</button>` : ""}</div>
    ${d.view === "shadow" && d.shadow ? `<p class="small muted" style="margin:0 0 8px"><span class="pill">shadow</span> What the challenger <b>${esc(d.shadow.label)}</b> picks (or would have picked, on days before it existed). On trial only: the current Focus is still the one that reaches you. <a href="#/scorecard">Compare them →</a></p>` : ""}
    <ul class="alerts">${shown.map((a) => alertItem(a, true)).join("") || `<li class="muted" style="padding:18px">No alerts for this filter.</li>`}</ul>`;
  $("#al-day").onchange = (e) => go({ day: e.target.value });
  main.querySelectorAll("[data-v]").forEach((b) => b.onclick = () => go({ view: b.dataset.v }));
  main.querySelectorAll("[data-k]").forEach((b) => b.onclick = () => go({ kind: b.dataset.k }));
  $("#al-sym").onchange = (e) => go({ sym: e.target.value.trim().toUpperCase() });
  bindAlertButtons(main);
}

/* 2.38: lot size, quantity in a lot, lots, and the money the trade took (and the account size then) */
function sizeLine(t) {
  const lot = t.lot || 0, lots = t.lots || 1, qty = t.qty || lot * lots;
  const used = t.capital != null ? t.capital : (t.entry && qty && (t.dir || 1) > 0 ? t.entry * qty : null);
  return `<small class="muted">Lot ${lot ? lot.toLocaleString("en-IN") : "–"} qty × ${lots} = <b>${qty ? qty.toLocaleString("en-IN") : "–"}</b> qty${used != null ? ` · ${(t.dir || 1) < 0 ? "margin ~" : "used "}${rupees(used)}` : ""}${t.acct_capital ? ` of ${rupees(t.acct_capital)}` : ""}</small><br>`;
}

/* ---------------- page: Paper trades */
async function pagePaper() {
  stopPageTimer();
  { const b = new URLSearchParams(location.hash.split("?")[1] || "").get("b"); if (b) L.paperBook = b; }
  L.paperBook = L.paperBook || "main";
  main.innerHTML = `<div id="pp-main"></div><div id="pp-top"></div>`;     // the top (capital, challenge) is not redrawn every 3 s
  let lastBooks = 0;
  const tick = async (first) => {
    if (state.page !== "paper") return;
    let busy = false;
    try {
      if (first || Date.now() - lastBooks > 15000) {                     // 2.49: the accounts + their comparison
        lastBooks = Date.now(); L.books = await api("/api/live/paper/books").catch(() => L.books || null);
      }
      const bk = L.paperBook;
      if (bk !== "main" && !((L.books || {}).books || []).some((b) => b.id === bk)) L.paperBook = "main";
      const p = await api(L.paperBook === "main" ? "/api/live/paper" : `/api/live/paper?book=${encodeURIComponent(L.paperBook)}`);
      busy = (p.open || []).length > 0; drawPaper(p, first);
    } catch (e) { if (first) main.innerHTML = `<div class="empty"><h2>Paper trading</h2><p>${esc(e.message)}</p></div>`; }
    L.pageTimer = setTimeout(() => tick(false), busy ? 1500 : 3000);     // 2.48: open trades refresh every 1.5 s
  };
  tick(true);
}

/* 2.49: the paper accounts (main + Best only + 3% a day) as tabs, and how they compare on the same days */
function bookTabs() {
  const bs = (L.books || {}).books || [];
  if (!bs.length) return "";
  const all = [{ id: "main", name: "Main account" }, ...bs.filter((b) => !b.error || b.name)];
  return `<div class="seg pp-tabs" role="group" aria-label="Paper account">${all.map((b) => `<button data-book="${esc(b.id)}" aria-pressed="${L.paperBook === b.id}">${esc(b.name)}</button>`).join("")}</div>`;
}

function bookCompare() {
  const rows = (L.books || {}).compare || [];
  if (rows.length < 2) return "";
  const pc = (v) => v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  return `<h2>Accounts side by side</h2>
    <p class="muted small" style="margin-top:-6px">Each account has its own money and trades; none of them mixes with another. After a month this shows whether picking only the best alerts beats taking them all.</p>
    <div class="table-wrap"><table><thead><tr><th>Account</th><th class="r">Trades</th><th class="r">Won</th><th class="r">Net</th><th class="r">Return</th><th class="r">Avg / trade</th><th class="r">Worst drop</th><th class="r">Green days</th><th class="r">+3% days</th><th>Since</th></tr></thead><tbody>
    ${rows.map((r) => `<tr${r.id === L.paperBook ? ` class="hl"` : ""}><td><b>${esc(r.name)}</b>${r.open ? ` <small class="muted">${r.open} open</small>` : ""}</td><td class="r">${r.trades}</td><td class="r">${r.won_pct != null ? r.won_pct + "%" : "–"}</td>
      <td class="r ${(r.net || 0) >= 0 ? "up" : "down"}"><b>${rupees(r.net)}</b></td><td class="r">${pc(r.return_pct)}</td><td class="r">${r.avg != null ? rupees(r.avg) : "–"}</td>
      <td class="r down">${r.max_drop ? rupees(r.max_drop) : "–"}</td><td class="r">${r.days ? `${r.green_days} of ${r.days}` : "–"}</td><td class="r">${r.target_days != null ? `${r.target_days} of ${r.days}` : "–"}</td><td class="nw">${esc(r.since || "–")}</td></tr>`).join("")}</tbody></table></div>`;
}

function bookHead(p) {
  const d = p.day || {}, why = Object.entries(p.today_why || {});
  const prog = d.target != null ? `<div class="kpis kpi3" style="margin-top:12px">
      <div class="kpi"><b>${rupees(d.start)}</b><span>Money this morning${p.compound ? " (yesterday's result added)" : ""}</span></div>
      <div class="kpi"><b class="${(d.now || 0) >= 0 ? "up" : "down"}">${rupees(d.now)} <small>${d.pct != null ? (d.pct >= 0 ? "+" : "") + d.pct.toFixed(2) + "%" : ""}</small></b><span>Today so far (booked + open)</span></div>
      <div class="kpi"><b>${rupees(d.target)}</b><span>Day target +${d.target_pct}% · stop −${d.stop_pct}% (${rupees(-d.stop)})</span></div></div>` : "";
  return `<div class="panel tight" style="margin-top:12px"><b>${esc(p.name)}</b>: ${esc(p.explain || p.short || "")}
      <form class="btn-row" id="bk-cap-form" style="margin:10px 0 0;align-items:center"><label class="small" for="bk-cap">Capital for this account</label>
        <input id="bk-cap" inputmode="text" autocomplete="off" style="max-width:160px" placeholder="${esc(String(p.account && p.account.capital || ""))}">
        <button class="btn" type="submit">Save</button><small class="muted">Now ${rupees((p.account || {}).capital)}. Type 1 lakh, 2,00,000 or 5L.</small></form></div>
    ${prog}
    <p class="note">Alerts looked at today: <b>${p.today_seen || 0}</b>, taken: <b>${p.today_taken || 0}</b>${why.length ? `. Not taken: ${why.map(([k, n]) => `${n} × ${esc(k)}`).join(" · ")}` : ""}. Paper only.</p>
    ${p.error ? `<div class="banner">This account had an error and was skipped: ${esc(p.error)}</div>` : ""}`;
}

/* 2.48: why paper trades were not taken today (no free money, market closed, old quote) */
function skippedLine(sk) {
  const e = Object.entries(sk || {});
  if (!e.length) return "";
  return `<div class="note">Not taken today: ${e.map(([k, n]) => `<b>${n}</b> × ${esc(k)}`).join(" · ")}. Paper trades never use more than the free money and never open outside an NSE session or on an old price.</div>`;
}

function drawPaper(p, first) {
  const s = p.settings || {}, A = p.account || {};
  const mode = (t) => t.manual === 3 ? ` · <span class="amber">aggressive</span>` : t.manual === 2 ? ` · <span class="down">override</span>` : t.manual === 4 ? " · by you (chain/chart)" : t.manual ? " · by you" : " · auto";
  const sideTag = (t) => (t.dir || 1) > 0 ? `<span class="pill p-up">BUY</span>` : `<span class="pill p-down">SELL</span>`;
  const links = (t) => `<span class="row-links"><a href="#/chart?s=${encodeURIComponent(t.sym)}">Chart</a> · <a href="#/sym?s=${encodeURIComponent(t.sym)}">Option chain</a></span>`;
  const stockNow = (t) => {
    const u = t.und_now || {}, now = t.und_last || u.spot;
    const since = t.und_entry && now ? (now / t.und_entry - 1) * 100 : null;
    return `<b>${esc(t.sym)}</b> ${now ? fx(now) : "–"} ${u.chg_pct != null ? `<small class="${u.chg_pct >= 0 ? "up" : "down"}">${u.chg_pct >= 0 ? "+" : ""}${u.chg_pct.toFixed(2)}% today</small>` : ""}${since != null ? `<br><small class="muted">since entry ${since >= 0 ? "+" : ""}${since.toFixed(2)}%</small>` : ""}`;
  };
  const stockClosed = (t) => {
    if (t.und_entry && t.und_exit) { const m = (t.und_exit / t.und_entry - 1) * 100;
      return `<b>${esc(t.sym)}</b> ${fx(t.und_entry)} → ${fx(t.und_exit)} <small class="${m >= 0 ? "up" : "down"}">${m >= 0 ? "+" : ""}${m.toFixed(2)}%</small>`; }
    const u = t.und_now || {};
    return `<b>${esc(t.sym)}</b> ${u.spot ? `now ${fx(u.spot)}` : ""} ${u.chg_pct != null ? `<small class="${u.chg_pct >= 0 ? "up" : "down"}">${u.chg_pct >= 0 ? "+" : ""}${u.chg_pct.toFixed(2)}% today</small>` : ""}`;
  };
  const open = p.open.map((t) => {
    const chg = t.last && t.entry ? (t.last / t.entry - 1) * 100 : null;
    return `<tr><td>${sideTag(t)} <b>${esc(t.label)}</b> × ${t.lots}<br><small class="muted">${esc(KIND[t.kind] || t.kind)}${mode(t)}</small><br>${sizeLine(t)}${links(t)}</td><td>${stockNow(t)}</td><td>${esc((t.day || "").slice(5))} ${hm(t.entry_ts)}</td><td class="r">${fx(t.entry)}</td><td class="r">${fx(t.last)} ${fpc(chg, 1)}${t.px_ts ? `<br><small class="muted" title="Exchange time of this price">at ${hms(t.px_ts)}</small>` : ""}</td>
      <td class="r">${fx(t.target)} / ${fx(t.stop)}</td><td class="r ${t.unreal >= 0 ? "up" : "down"}"><b>${rupees(t.unreal)}</b></td><td>${p.book ? "" : `<button class="link-btn" data-close="${t.id}">Exit</button>`}</td></tr>`;
  }).join("");
  // 2.40: the latest 25 closed trades (the rest one tap away), dates short and on one line
  const shortDay = (d) => { try { return new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" }); } catch (_) { return d; } };
  const nShow = window.innerWidth < 700 ? 10 : 25;
  const allClosed = L.paperAll || p.closed.length <= nShow;
  const closed = (allClosed ? p.closed : p.closed.slice(0, nShow)).map((t) => `<tr><td>${sideTag(t)} <b>${esc(t.label)}</b><br><small class="muted">${esc(KIND[t.kind] || t.kind)}${mode(t)}</small><br>${sizeLine(t)}${links(t)}</td><td>${stockClosed(t)}</td><td class="nw">${esc(shortDay(t.day))}<br><small class="muted">${hm(t.entry_ts)}–${hm(t.exit_ts)}</small></td><td class="r nw">${fx(t.entry)} → ${fx(t.exit)}</td><td class="nw">${esc(t.reason)}</td><td class="r muted">${rupees(t.costs)}</td><td class="r ${t.net >= 0 ? "up" : "down"}"><b>${rupees(t.net)}</b></td></tr>`).join("");
  const kinds = p.by_kind.map((b) => `<tr><td>${esc(KIND[b.kind] || b.kind)}</td><td class="r">${b.n}</td><td class="r">${b.win_rate}%</td><td class="r ${b.avg >= 0 ? "up" : "down"}">${rupees(b.avg)}</td><td class="r ${b.net >= 0 ? "up" : "down"}"><b>${rupees(b.net)}</b></td></tr>`).join("");
  const cls = (v) => v >= 0 ? "up" : "down";
  const keepTop = $("#pp-top"); if (keepTop) main.appendChild(keepTop);       // out of the way while the rest redraws
  if (!$("#pp-main")) main.insertAdjacentHTML("afterbegin", `<div id="pp-main"></div>`);
  const isMain = !p.book;                                      // 2.49: Main account or an extra one (Best only, 3% a day)
  $("#pp-main").innerHTML = `<div class="toolbar"><h1 class="page-title" style="margin:0">Paper trading</h1>
      <div class="btn-row" style="margin:0">${isMain && p.open.length ? `<button class="btn danger" id="pp-closeall">Close all (${p.open.length})</button>` : ""}
        <a class="btn" href="#/briefs">Day report</a></div></div>
    ${bookTabs()}
    ${isMain ? "" : bookHead(p)}
    ${skippedLine(p.skipped_today)}
    <div class="kpis kpi6 acct">
      <div class="kpi"><b>${rupees(A.equity)}</b><span>Account value now <small class="${cls(A.return_pct || 0)}">${A.return_pct >= 0 ? "+" : ""}${(A.return_pct || 0).toFixed(2)}%</small></span></div>
      <div class="kpi"><b class="${cls(A.today || 0)}">${rupees(A.today)}</b><span>Today: booked ${rupees(A.today_booked)} + open ${rupees(A.unrealised)}</span></div>
      <div class="kpi"><b>${rupees(A.free)}</b><span>Free to use</span></div>
      <div class="kpi"><b>${rupees(A.used)}</b><span>In open positions (${p.open.length})</span></div>
      <div class="kpi"><b class="${cls(A.realised || 0)}">${rupees(A.realised)}</b><span>All booked P&amp;L (${p.n} trades, ${p.win_rate != null ? p.win_rate + "% won" : "–"})</span></div>
      <div class="kpi"><b>${rupees(A.capital)}</b><span>Starting capital</span></div></div>
    <div id="pp-slot"></div>
    ${isMain ? "" : bookCompare()}
    <div class="panel tight agg ${p.aggressive ? "on" : ""}" style="margin-top:14px;${isMain ? "" : "display:none"}"><label class="check"><input type="checkbox" id="pp-agg" ${p.aggressive ? "checked" : ""}>
      <span><b>Aggressive auto trading</b> ${p.aggressive ? `<span class="pill p-amber">ON</span>` : ""}<small>Paper only. The system takes every up/down idea automatically (Important and Act-now), even when the risk check says BLOCK; up to ${s.aggressive_max_open || 15} open at once, between 09:16 and ${esc(s.exit_time || "15:15")}. Tagged "aggressive" so the Day report shows what it earns apart. Day 1 data: trades against the risk check lost money, so watch this record before trusting it.</small></span></label></div>
    ${p.guard ? `<div class="${p.guard.blocked ? "banner" : "note"}" style="${p.guard.blocked ? "margin-top:14px" : ""}">${p.guard.blocked ? "⛔ " + esc(p.guard.why) + (p.aggressive ? " (aggressive mode keeps trading)" : "") : `Risk guard: ₹${fk(p.guard.risk_per_trade)} at risk per trade, stops new trades after a ₹${fk(Math.abs(p.guard.loss_limit))} loss or ${s.max_trades_day} trades in a day. Today: ${p.guard.trades_today} trades.`}</div>` : ""}
    <h2>Open positions</h2>
    <div class="table-wrap"><table><thead><tr><th>Option</th><th>Stock</th><th>In at</th><th class="r">Entry</th><th class="r">Now</th><th class="r">Target / stop</th><th class="r">P&amp;L</th><th></th></tr></thead><tbody>${open || `<tr><td colspan="8" class="muted">No open paper trades. Buy from an alert, the option chain or the chart.</td></tr>`}</tbody></table></div>
    <div class="split" style="margin-top:28px"><div><h2 style="margin-top:0">Which alerts make money</h2>
      <div class="table-wrap"><table><thead><tr><th>Alert type</th><th class="r">Trades</th><th class="r">Win</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${kinds || `<tr><td colspan="5" class="muted">Fills in as trades close.</td></tr>`}</tbody></table></div></div>
      <div><h2 style="margin-top:0">Equity</h2><div class="chart" id="eq-chart" style="height:240px"></div></div></div>
    <h2>Closed trades <span class="muted small">${p.closed.length.toLocaleString("en-IN")}</span></h2>
    <div class="table-wrap"><table class="pp-closed"><thead><tr><th>Option</th><th>Stock</th><th>When</th><th class="r">Price</th><th>Exit</th><th class="r">Charges</th><th class="r">Net</th></tr></thead><tbody>${closed || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table></div>
    ${p.closed.length > nShow ? `<p style="margin:8px 0 0"><button class="btn" id="pp-all">${allClosed ? `Show the latest ${nShow} only` : `Show all ${p.closed.length.toLocaleString("en-IN")} closed trades`}</button></p>` : ""}
    ${isMain ? bookCompare() : ""}
    <p class="note">Paper only: no order ever reaches a broker. Charges are real (brokerage, STT, exchange, GST, stamp). Target +${s.target_pct}% / stop −${s.stop_pct}% on the option; alert trades exit after ${s.max_hold_min} min, all by ${esc(s.exit_time)}. ${p.source === "sim" ? "<b>Showing simulator trades.</b>" : ""}</p>`;
  main.querySelectorAll("[data-close]").forEach((b) => b.onclick = async () => { try { await post(`/api/live/paper/${b.dataset.close}/close`, {}); toast("Closed"); pagePaper(); } catch (e) { toast(e.message); } });
  main.querySelectorAll("[data-book]").forEach((b) => b.onclick = () => { L.paperBook = b.dataset.book; L.paperAll = false;
    location.hash = L.paperBook === "main" ? "#/paper" : `#/paper?b=${encodeURIComponent(L.paperBook)}`; });
  { const f = $("#bk-cap-form"); if (f) f.onsubmit = async (ev) => { ev.preventDefault();
      const v = ($("#bk-cap").value || "").trim(); if (!v) return;
      try { const r = await post(`/api/live/paper/books/${encodeURIComponent(p.book)}`, { amount: v }); toast(`${p.name}: capital ${rupees(r.capital)}`); L.books = null; pagePaper(); } catch (e) { toast(e.message); } }; }
  { const b = $("#pp-all"); if (b) b.onclick = () => { L.paperAll = !L.paperAll; pagePaper(); }; }
  // capital box + challenge + patterns: built once, then moved into place on every redraw (typing is never reset)
  let top = $("#pp-top");
  if (!top) { main.insertAdjacentHTML("beforeend", `<div id="pp-top"></div>`); top = $("#pp-top"); }
  if (first || !top.firstChild) {
    top.innerHTML = capitalBox(A.capital) + `<div id="pp-coach"></div>`;
    bindCapitalBox(() => pagePaper());
    api("/api/live/coach").then((c) => { const el = $("#pp-coach"); if (el) { el.innerHTML = coachBox(c);
      const b = $("#ch-new"); if (b) b.onclick = async () => { if (!window.confirm("Start a new challenge from today? Earlier trades stop counting for it.")) return;
        try { await post("/api/live/coach/challenge", {}); toast("New challenge started today"); pagePaper(); } catch (e) { toast(e.message); } }; } }).catch(() => {});
  }
  const slot = $("#pp-slot"); if (slot && top) { slot.appendChild(top); top.style.display = isMain ? "" : "none"; }
  const ca = $("#pp-closeall");
  if (ca) ca.onclick = async () => { if (!window.confirm(`Close all ${p.open.length} open paper positions at the live price?`)) return;
    ca.disabled = true; try { const r = await post("/api/live/paper/close_all", {}); toast(`Closed ${r.closed} · net ${rupees(r.net)}`); pagePaper(); } catch (e) { toast(e.message); ca.disabled = false; } };
  $("#pp-agg").onchange = async (e) => {
    const on = e.target.checked;
    if (on && !window.confirm("Aggressive auto trading (paper only): the system will take every up/down idea, even ones the risk check blocks. Turn on?")) { e.target.checked = false; return; }
    try { await post("/api/live/paper/settings", { aggressive: on }); toast(on ? "Aggressive auto trading ON (paper)" : "Aggressive auto trading off"); pagePaper(); } catch (err) { toast(err.message); e.target.checked = !on; } };
  const el = $("#eq-chart");
  if (p.equity.length && window.Plotly) {
    const first = p.closed.length ? Math.min(...p.closed.map((t) => t.entry_ts)) : p.equity[0].ts - 60;
    const eq = [{ ts: first, equity: 0 }, ...p.equity];
    Plotly.react(el, [{ x: eq.map((e) => new Date(e.ts * 1000)), y: eq.map((e) => e.equity), type: "scatter", mode: "lines", line: { color: C.signal, width: 2, shape: "hv" }, fill: "tozeroy", fillcolor: "rgba(61,47,168,.08)" }],
      plotLayout({ margin: { l: 60, r: 12, t: 8, b: 30 }, xaxis: { gridcolor: "#EEF1F4", type: "date", tickformat: "%d %b %H:%M" }, yaxis: { gridcolor: "#EEF1F4", tickprefix: "₹", zeroline: true, zerolinecolor: C.rule } }), plotCfg);
  } else el.innerHTML = `<p class="muted" style="padding:16px">Your running P&amp;L appears here.</p>`;
}

/* ---------------- page: Connect / live settings */
/* 2.52: Kite (Zerodha) as the backup live feed and cross-check for Upstox. Market data only, never orders. */
function kitePanel(kf, s, q) {
  kf = kf || {};
  const r = kf.router || {}, b = r.backup || {}, c = r.cross || {};
  const pill = kf.kite_logged_in ? `<span class="pill p-up">Logged in${kf.kite_user ? " as " + esc(kf.kite_user) : ""}</span>` : kf.kite_keys ? `<span class="pill p-amber">Keys saved, log in needed</span>` : `<span class="pill">optional</span>`;
  const fo = s["live.feeds_failover"] || kf.failover || "shadow";
  const now = kf.running && kf.router ? `<p class="small" style="margin:10px 0 0"><b>Now:</b> ${esc(b.message || b.status || "")}${b.connected ? ` · ${(b.instruments || 0).toLocaleString("en-IN")} instruments streaming` : ""}${c.compared ? ` · cross-check: <b>${c.match_pct}%</b> of ${c.compared} prices agree, Upstox behind on ${c.upstox_behind}, Kite behind on ${c.kite_behind}` : ""}${r.active === "Kite" ? ` · <span class="amber">prices from Kite now</span>` : ""}${(r.would_switch || []).length ? ` · would have switched ${r.would_switch.length} time(s) today` : ""}</p>` : "";
  return `<div class="panel" style="margin-top:18px"><h2 style="margin-top:0">1b. Kite (Zerodha): backup feed ${pill}</h2>
    <p class="muted" style="margin-top:0">A second live feed next to Upstox: the same prices, compared every few seconds (bad or frozen prices get caught), and a backup if Upstox goes quiet. Market data only: no order is ever placed.</p>
    <ol class="steps">
      <li>Open <a href="https://developers.kite.trade" target="_blank" rel="noopener">developers.kite.trade</a>, log in with your Zerodha account and create a <b>Kite Connect</b> app (₹500 / month; live prices and history are included).</li>
      <li>Set its <b>Redirect URL</b> to exactly:<div class="copy"><code>${esc(kf.kite_redirect_uri || "http://127.0.0.1:8765/kite/callback")}</code><button class="link-btn" id="copy-kredir" type="button">Copy</button></div></li>
      <li>Copy the app's <b>API key</b> and <b>API secret</b> into the boxes below and save.</li>
      <li>Click <b>Log in with Kite</b> once a day (Zerodha ends every login at 6:00 AM).</li></ol>
    <form class="form-grid" style="margin-top:14px" onsubmit="return false">
      <label class="field">Kite API key<input type="text" id="s-kkey" value="${q(s["live.kite_api_key"])}" autocomplete="off"></label>
      <label class="field">Kite API secret<input type="password" id="s-ksec" value="${q(s["live.kite_api_secret"])}" autocomplete="off"></label>
      <label class="field">When Upstox goes quiet<select id="s-kfo">
        <option value="shadow" ${fo === "shadow" ? "selected" : ""}>Only record (start here)</option>
        <option value="on" ${fo === "on" ? "selected" : ""}>Use Kite's prices until Upstox is back</option>
        <option value="off" ${fo === "off" ? "selected" : ""}>Do nothing</option></select>
        <small>Start with "Only record": after a few market days, if the cross-check shows the two agree, switch to "Use Kite".</small></label>
    </form>
    <div class="btn-row" style="margin-top:16px"><button class="btn" id="kt-save" type="button">Save</button>
      <a class="btn primary" href="/kite/login">${kf.kite_logged_in ? "Log in again" : "Log in with Kite"}</a>
      ${kf.kite_logged_in ? `<button class="btn" id="kt-logout" type="button">Log out</button>` : ""}</div>
    ${now}
    <p class="note">Your Kite keys and login stay on this Mac only. Upstox stays the main feed; without Kite everything works exactly as before.</p></div>`;
}

async function pageConnect() {
  stopPageTimer();
  const [info, s, kf] = await Promise.all([api("/api/live/info"), api("/api/settings"), api("/api/live/feeds").catch(() => ({}))]);
  const q = (v) => (v == null ? "" : esc(v));
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  main.innerHTML = `<h1 class="page-title">Live setup</h1>
    <p class="lede">Connect Upstox for live data, connect Claude (your Pro plan) or another AI, and choose how you get alerts.</p>
    <div class="panel"><h2 style="margin-top:0">1. Upstox ${info.logged_in ? `<span class="pill p-up">Logged in${info.user ? " as " + esc(info.user) : ""}</span>` : info.has_keys ? `<span class="pill p-amber">Keys saved, log in needed</span>` : ""}</h2>
      <ol class="steps">
        <li>Open <a href="https://account.upstox.com/developer/apps" target="_blank" rel="noopener">account.upstox.com/developer/apps</a> and log in with your Upstox account. (No Upstox account? Open one free, it takes a day.)</li>
        <li>Click <b>New app</b>. Name it <i>Stockway</i>. Set the <b>Redirect URL</b> to exactly:<div class="copy"><code id="redir">${esc(info.redirect_uri)}</code><button class="link-btn" id="copy-redir" type="button">Copy</button></div></li>
        <li>Create the app, then copy its <b>API Key</b> and <b>API Secret</b> into the boxes below and save.</li>
        <li>Click <b>Log in with Upstox</b>. Do this once every morning: Upstox ends every login at 3:30 AM.</li>
      </ol>
      <form id="up-form" class="form-grid" style="margin-top:14px">
        <label class="field">API key<input type="text" id="s-ukey" value="${q(s["live.upstox_api_key"])}" autocomplete="off"></label>
        <label class="field">API secret<input type="password" id="s-usec" value="${q(s["live.upstox_api_secret"])}" autocomplete="off"></label>
      </form>
      <div class="btn-row" style="margin-top:16px"><button class="btn" id="up-save" type="button">Save keys</button>
        <a class="btn primary" href="/upstox/login">${info.logged_in ? "Log in again" : "Log in with Upstox"}</a>
        ${info.logged_in ? `<button class="btn" id="up-logout" type="button">Log out</button>` : ""}</div>
      <p class="note">Your keys and login stay on this Mac only. Live data is free with an Upstox account. Orders are never placed: this app only reads prices.</p></div>
    ${kitePanel(kf, s, q)}

    <div class="panel" style="margin-top:18px"><h2 style="margin-top:0">2. Scanner</h2><div class="form-grid">
      <label class="check"><input type="checkbox" id="s-auto" ${s["live.auto_start"] !== false ? "checked" : ""}><span>Start by itself at 9:00 AM<small>On market days, when you're logged in and this app is open.</small></span></label>
      <label class="field">Data source<select id="s-src"><option value="upstox" ${s["live.source"] !== "sim" ? "selected" : ""}>Upstox (real market)</option><option value="sim" ${s["live.source"] === "sim" ? "selected" : ""}>Simulator (practice)</option></select></label>
      <label class="check"><input type="checkbox" id="s-news" ${s["live.news"] !== false ? "checked" : ""}><span>Market news<small>Headlines from ET, Business Standard, Mint, Moneycontrol and RBI every 5 minutes.</small></span></label>
      <label class="check"><input type="checkbox" id="s-fil" ${s["live.filings"] !== false ? "checked" : ""}><span>Company filings<small>Results, order wins, M&amp;A, pledges, resignations from NSE, checked every minute.</small></span></label>
      <label class="check"><input type="checkbox" id="s-brief" ${s["live.briefs"] !== false ? "checked" : ""}><span>Morning and evening briefs<small>8:45 AM plan and post-market review, also sent to Telegram.</small></span></label>
      <label class="field">Simulator speed<select id="s-speed">${[1, 5, 10, 30, 60].map((v) => `<option value="${v}" ${Number(s["live.sim_speed"] || 10) === v ? "selected" : ""}>${v === 1 ? "Real time" : v + "× faster"}</option>`).join("")}</select></label>
    </div></div>

    <div class="panel" style="margin-top:18px"><h2 style="margin-top:0">3. Alerts</h2><div class="form-grid">
      <label class="field">Pop-up alerts on this Mac<span class="btn-row"><button class="btn" type="button" id="np-btn" ${perm === "granted" || perm === "unsupported" ? "disabled" : ""}>${perm === "granted" ? "Turned on" : perm === "denied" ? "Blocked in browser settings" : perm === "unsupported" ? "Not supported" : "Turn on"}</button></span><small>Shows alerts even when the app is in the background.</small></label>
      <label class="field">Which alerts pop up<select id="s-nsev">${[1, 2, 3].map((v) => `<option value="${v}" ${LS.get("notifySev", 2) === v ? "selected" : ""}>${v === 1 ? "Everything" : v === 2 ? "Important and above" : "Only act-now"}</option>`).join("")}</select></label>
      <label class="check"><input type="checkbox" id="s-sound" ${LS.get("sound", true) ? "checked" : ""}><span>Sound for act-now alerts</span></label>
      <label class="field">Which alerts go to Telegram<select id="s-tsev">${[1, 2, 3].map((v) => `<option value="${v}" ${Number(s["live.telegram_min_severity"] || 3) === v ? "selected" : ""}>${v === 1 ? "Everything" : v === 2 ? "Important and above" : "Only act-now"}</option>`).join("")}</select>
        <small>${info.telegram ? "Telegram is set up." : `Add your bot in <a href="#/data">Data &amp; settings</a> first (free).`}</small></label>
    </div></div>

    <div class="panel" style="margin-top:18px" id="ai-panel"><h2 style="margin-top:0">4. AI: Ask Stockway, "Why?", briefs <span class="muted small">optional</span></h2>
      <p class="muted" style="margin-top:0">Powers the Ask Stockway chat, the "Why?" answer on alerts, the AI chart read and the written notes. Only prices and public headlines are sent.</p>
      <div class="form-grid">
        <label class="field">AI<select id="s-aip">
          <option value="claude_pro" ${s["ai.provider"] === "claude_pro" ? "selected" : ""}>Claude: my Pro / Max plan (via Claude Code on this Mac)</option>
          <option value="gemini" ${!["claude", "claude_pro"].includes(s["ai.provider"]) ? "selected" : ""}>Google Gemini (free tier, needs a key)</option>
          <option value="claude" ${s["ai.provider"] === "claude" ? "selected" : ""}>Claude API (pay per use, needs a key)</option></select></label>
        <label class="field" id="f-aik">API key<input type="password" id="s-aik" value="${q(s["ai.api_key"])}" autocomplete="off"></label>
        <label class="field" id="f-aim">Claude model<select id="s-aim">${[["sonnet", "Sonnet (recommended)"], ["opus", "Opus (deepest, uses your limit faster)"], ["haiku", "Haiku (fastest, lightest)"], ["", "Claude Code's default"]].map(([v, l]) => `<option value="${v}" ${(s["ai.pro_model"] ?? "sonnet") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Explain automatically<select id="s-aisev">${[[2, "Important and act-now alerts"], [3, "Only act-now alerts"], [9, "Never (only when I click Why?)"]].map(([v, l]) => `<option value="${v}" ${Number(s["ai.explain_min_severity"] || 3) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Market pulse every<select id="s-aipulse">${[5, 10, 15, 30, 60].map((v) => `<option value="${v}" ${Number(s["ai.pulse_every_min"] || 15) === v ? "selected" : ""}>${v} minutes</option>`).join("")}</select></label>
        <label class="field">Answer language<select id="s-ailang">${[["hinglish", "Hinglish"], ["english", "English"]].map(([v, l]) => `<option value="${v}" ${(s["ai.language"] || "hinglish") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field" id="f-aiauto">Automatic AI notes per day<select id="s-aiauto">${[0, 6, 12, 24].map((v) => `<option value="${v}" ${Number(s["ai.pro_auto_per_day"] ?? 12) === v ? "selected" : ""}>${v === 0 ? "None (only when I ask)" : v}</option>`).join("")}</select><small>Your Pro limit is shared with your Claude chats; questions you ask are not capped by this.</small></label>
      </div>
      <div id="ai-pro-box" class="ai-pro"></div>
      <div class="btn-row" style="margin-top:12px"><button class="btn" id="ai-test" type="button">Save and test AI</button><span class="small muted" id="ai-test-out"></span></div></div>

    <div class="panel" style="margin-top:18px"><h2 style="margin-top:0">5. Paper trading</h2><div class="form-grid">
      <label class="check"><input type="checkbox" id="s-pauto" ${s["live.paper.auto"] !== false ? "checked" : ""}><span>Auto paper-trade strong alerts<small>Buys the suggested option on important breakouts, surges, OI buildups and unusual option buying.</small></span></label>
      <input type="hidden" id="s-plots" value="${q(s["live.paper.lots"] ?? 1)}">
      <label class="field">Target<select id="s-ptgt">${[20, 30, 40, 50, 75, 100].map((v) => `<option value="${v}" ${Number(s["live.paper.target_pct"] || 30) === v ? "selected" : ""}>+${v}% on premium</option>`).join("")}</select></label>
      <label class="field">Capital for sizing (₹)<input type="text" id="s-pcap" value="${q(s["live.paper.capital"] ?? 500000)}" inputmode="numeric"><small>One capital for the whole app: paper trades, the risk check and the Strategy desk.</small></label>
      <label class="field">Risk per trade<select id="s-prisk">${[0.5, 1, 1.5, 2].map((v) => `<option value="${v}" ${Number(s["live.paper.risk_pct"] ?? 1) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select></label>
      <label class="field">Stop trading for the day after losing<select id="s-pdl">${[2, 3, 4, 5].map((v) => `<option value="${v}" ${Number(s["live.paper.max_daily_loss_pct"] ?? 3) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select></label>
      <label class="field">Max trades per day<select id="s-pmt">${[5, 8, 12, 20].map((v) => `<option ${Number(s["live.paper.max_trades_day"] ?? 12) === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field">Stop loss<select id="s-pstop">${[10, 15, 20, 25, 30, 40].map((v) => `<option value="${v}" ${Number(s["live.paper.stop_pct"] || 20) === v ? "selected" : ""}>−${v}% on premium</option>`).join("")}</select></label>
    </div></div>
    <div class="btn-row" style="margin-top:18px"><button class="btn primary" id="all-save" type="button">Save all</button></div>`;
  const collect = () => ({
    "live.upstox_api_key": $("#s-ukey").value.trim(), "live.upstox_api_secret": $("#s-usec").value.trim(),
    "live.kite_api_key": $("#s-kkey").value.trim(), "live.kite_api_secret": $("#s-ksec").value.trim(), "live.feeds_failover": $("#s-kfo").value,
    "live.auto_start": $("#s-auto").checked, "live.source": $("#s-src").value, "live.news": $("#s-news").checked,
    "live.sim_speed": Number($("#s-speed").value), "live.telegram_min_severity": Number($("#s-tsev").value),
    "ai.provider": $("#s-aip").value, "ai.api_key": $("#s-aik").value.trim(), "ai.explain_min_severity": Number($("#s-aisev").value),
    "ai.pro_model": $("#s-aim").value, "ai.pro_auto_per_day": Number($("#s-aiauto").value), "ai.language": $("#s-ailang").value,
    "ai.pulse_every_min": Number($("#s-aipulse").value), "live.paper.auto": $("#s-pauto").checked, "live.paper.lots": Number($("#s-plots").value),
    "live.paper.target_pct": Number($("#s-ptgt").value), "live.paper.stop_pct": Number($("#s-pstop").value),
    "live.paper.capital": (parseAmount($("#s-pcap").value).value) || (() => { throw new Error(parseAmount($("#s-pcap").value).error); })(), "live.paper.risk_pct": Number($("#s-prisk").value),
    "live.paper.max_daily_loss_pct": Number($("#s-pdl").value), "live.paper.max_trades_day": Number($("#s-pmt").value),
    "live.filings": $("#s-fil").checked, "live.briefs": $("#s-brief").checked,
  });
  const save = async () => {
    LS.set("notifySev", Number($("#s-nsev").value)); LS.set("sound", $("#s-sound").checked);
    try { await post("/api/settings", collect()); toast("Saved"); return true; } catch (e) { toast(e.message); return false; }
  };
  $("#all-save").onclick = save;
  $("#up-save").onclick = async () => { if (await save()) pageConnect(); };
  $("#kt-save").onclick = async () => { if (await save()) pageConnect(); };
  { const b = $("#copy-kredir"); if (b) b.onclick = async () => { try { await navigator.clipboard.writeText(kf.kite_redirect_uri); toast("Copied"); } catch (_) { toast(kf.kite_redirect_uri); } }; }
  if ($("#kt-logout")) $("#kt-logout").onclick = async () => { await post("/api/kite/logout", {}); toast("Logged out of Kite"); pageConnect(); };
  $("#copy-redir").onclick = async () => { try { await navigator.clipboard.writeText(info.redirect_uri); toast("Copied"); } catch (_) { toast(info.redirect_uri); } };
  if ($("#up-logout")) $("#up-logout").onclick = async () => { await post("/api/live/logout", {}); toast("Logged out"); pageConnect(); };
  $("#np-btn").onclick = async () => { const r = await Notification.requestPermission(); toast(r === "granted" ? "Pop-up alerts on" : "Not allowed"); pageConnect(); };
  ["#s-nsev", "#s-sound"].forEach((id) => $(id).addEventListener("change", () => { LS.set("notifySev", Number($("#s-nsev").value)); LS.set("sound", $("#s-sound").checked); }));
  const aiShow = async () => {
    const pro = $("#s-aip").value === "claude_pro";
    $("#f-aik").style.display = pro ? "none" : ""; $("#f-aim").style.display = pro ? "" : "none"; $("#f-aiauto").style.display = pro ? "" : "none";
    const box = $("#ai-pro-box");
    if (!pro) { box.innerHTML = ""; return; }
    let st = {}; try { st = await api("/api/ai/status"); } catch (_) {}
    box.innerHTML = st.claude_cli
      ? `<p class="small" style="margin:12px 0 0"><span class="pill p-up">Claude Code found</span> <span class="muted">${esc(st.claude_cli)}</span> · uses the Claude account Claude Code is logged in with. Runs with every tool switched off: it can't read your files, run commands or browse; it only answers.</p>`
      : `<div class="ai-steps"><p class="small" style="margin:12px 0 6px"><span class="pill p-amber">Claude Code not found on this Mac</span> One-time setup (5 minutes):</p>
         <ol class="steps small"><li>Open <b>Terminal</b> and paste: <code>curl -fsSL https://claude.ai/install.sh | bash</code></li>
         <li>Type <code>claude</code>, press Enter, and log in with your Claude Pro account (the same one you chat with).</li>
         <li>Close Terminal, restart Stockway, come back here and click <b>Save and test AI</b>.</li></ol></div>`;
  };
  $("#s-aip").onchange = aiShow; aiShow();
  $("#ai-test").onclick = async (e) => {
    const out = $("#ai-test-out"); e.target.disabled = true; out.textContent = "Saving and asking…";
    if (!(await save())) { e.target.disabled = false; out.textContent = ""; return; }
    try { const r = await post("/api/ai/test", {}); out.innerHTML = `<span class="up">✓ ${esc(r.by)} answered: “${esc(r.text)}”</span>`; aiStatus(true); }
    catch (err) { out.innerHTML = `<span class="down">${esc(err.message)}</span>`; }
    e.target.disabled = false;
  };
}


/* ---------------- page: Market context */
async function pageContext(auto = false) {
  stopPageTimer();
  let c;
  try { c = await api("/api/live/context"); } catch (e) { if (!auto) throw e; L.pageTimer = setTimeout(() => state.page === "context" && pageContext(true), 30000); return; }
  if (state.page !== "context") return;
  const P = (c.participants || {}).rows || [];
  const nm = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fk(Math.round(v))}`);
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const glob = (c.global || []).map((g) => `<tr><td>${esc(g.name)}</td><td class="r">${fx(g.last)}</td><td class="r">${fpc(g.chg_pct)}</td><td class="muted small">${esc(g.asof)}</td></tr>`).join("");
  const fd = c.fii_dii || {};
  const fil = (c.filings || []).map((f) => `<li><span class="muted small">${esc((f.time || "").slice(-8, -3))}</span> <a href="#/sym?s=${encodeURIComponent(f.sym)}"><b>${esc(f.sym)}</b></a> ${f.label ? `<span class="pill ${f.tone > 0 ? "p-up" : f.tone < 0 ? "p-down" : "p-sig"}">${esc(f.label)}</span>` : ""} ${esc(f.desc)} ${f.link ? `<a href="${esc(f.link)}" target="_blank" rel="noopener">PDF</a>` : ""}<br><small class="muted">${esc(f.text)}</small></li>`).join("");
  const deals = (c.deals_fo.length ? c.deals_fo : c.deals_all).slice(0, 25).map((d) => `<tr><td><b>${esc(d.sym)}</b> <small class="muted">${esc(d.type)}</small></td><td>${esc(d.client)}</td><td class="${d.side === "BUY" ? "up" : "down"}">${esc(d.side)}</td><td class="r">${fx(d.price)}</td><td class="r"><b>₹${fx(d.value_cr)} cr</b></td></tr>`).join("");
  const errs = Object.entries(c.errors || {});
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Market context</h1><p class="lede" style="margin-bottom:0">Who is positioned where, what money moved, and what companies just announced. From NSE and global markets.</p></div>
      <div style="text-align:right"><button class="btn" id="ctx-refresh">Refresh now</button><p class="small muted" style="margin:6px 0 0"><span class="dot-live"></span> Auto-refresh every 30s · ${new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}</p></div></div>
    ${errs.length ? `<div class="banner">Some sources didn't answer: ${errs.map(([k, v]) => `${esc(k)} (${esc(v)})`).join("; ")}. They retry every 30 minutes.</div>` : ""}
    <h2>Positioning in index derivatives <span class="muted small">${c.participants ? "NSE, " + esc(c.participants.date) : ""}</span></h2>
    <div class="table-wrap"><table><thead><tr><th>Who</th><th class="r">Index futures long %</th><th class="r">Futures net</th><th class="r">Change</th><th class="r">Calls net</th><th class="r">Change</th><th class="r">Puts net</th><th class="r">Change</th><th class="r">Stock futures net</th></tr></thead>
      <tbody>${P.map((r) => `<tr><td><b>${esc(r.who)}</b></td><td class="r"><b class="${r.fut_long_pct >= 50 ? "up" : "down"}">${r.fut_long_pct}%</b></td><td class="r ${cls(r.fut_net)}">${nm(r.fut_net)}</td><td class="r ${cls(r.fut_net_chg)}">${nm(r.fut_net_chg)}</td><td class="r ${cls(r.call_net)}">${nm(r.call_net)}</td><td class="r ${cls(r.call_net_chg)}">${nm(r.call_net_chg)}</td><td class="r ${cls(r.put_net)}">${nm(r.put_net)}</td><td class="r ${cls(r.put_net_chg)}">${nm(r.put_net_chg)}</td><td class="r ${cls(r.stock_fut_net)}">${nm(r.stock_fut_net)}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">Not loaded yet.</td></tr>`}</tbody></table></div>
    <p class="note">Contracts, end of day. FIIs are the "smart money" to watch: a low long % means they are betting on a fall. Calls/puts net = bought minus sold, so negative calls means they are selling calls (capping the upside).</p>
    <div class="split" style="margin-top:28px">
      <div><h2 style="margin-top:0">Cash market flows</h2>
        <div class="kpis two">${["FII", "DII"].map((k) => fd[k] ? `<div class="kpi"><b class="${fd[k].net >= 0 ? "up" : "down"}">${fd[k].net >= 0 ? "+" : ""}₹${fk(Math.round(fd[k].net))} cr</b><span>${k === "FII" ? "Foreign investors" : "Domestic funds"} net, ${esc(fd[k].date)}</span></div>` : "").join("") || `<div class="kpi"><span>Not loaded yet</span></div>`}</div>
        <h2>F&amp;O ban <span class="muted small">${c.mwpl_date ? esc(c.mwpl_date) : ""}</span></h2>
        <div class="panel tight">${c.ban.length ? `<p style="margin:0 0 8px"><b class="down">In ban:</b> ${c.ban.map((x) => `<a href="#/sym?s=${encodeURIComponent(x)}">${esc(x)}</a>`).join(", ")}</p>` : `<p style="margin:0 0 8px">No stock in the ban today.</p>`}
          <p class="muted small" style="margin:0 0 6px">Close to the 95% limit (open interest ÷ market-wide limit). Near the ban, only position cuts are allowed, which often causes sharp moves:</p>
          <div class="chips">${c.near_ban.map((x) => `<a class="chip static" href="#/sym?s=${encodeURIComponent(x.sym)}">${esc(x.sym)} ${x.pct}%</a>`).join("") || `<span class="muted small">None above 75%.</span>`}</div></div>
      </div>
      <div><h2 style="margin-top:0">Global markets</h2><div class="table-wrap"><table><thead><tr><th>Market</th><th class="r">Last</th><th class="r">Change</th><th>As of</th></tr></thead><tbody>${glob || `<tr><td colspan="4" class="muted">Loading…</td></tr>`}</tbody></table></div></div>
    </div>
    <h2>Big deals today <span class="muted small">bulk and block deals${c.deals_fo.length ? " in F&amp;O stocks" : ""}</span></h2>
    <div class="table-wrap"><table><thead><tr><th>Stock</th><th>Buyer / seller</th><th>Side</th><th class="r">Price</th><th class="r">Value</th></tr></thead><tbody>${deals || `<tr><td colspan="5" class="muted">None yet. NSE publishes these after market hours.</td></tr>`}</tbody></table></div>
    <h2>Company filings, live <span class="muted small">F&amp;O stocks, from NSE</span></h2>
    <ul class="alerts filings">${fil || `<li class="muted" style="padding:16px">Filings appear here as companies announce them (checked every minute, 7:30 AM to 8 PM).</li>`}</ul>`;
  $("#ctx-refresh").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "Refreshing…"; try { await post("/api/live/context/refresh", {}); } catch (err) { toast(err.message); } pageContext(); };
  L.pageTimer = setTimeout(() => state.page === "context" && pageContext(true), 30000);
}

/* ---------------- page: Scorecard */
function scoreBuckets(sc) {
  // Hit = the option gained 20% before losing 20% within an hour, so ~50% is break-even before costs.
  const nm = (r) => `${esc(KIND[r.kind] || r.kind)} <span class="muted">${r.is_index ? "indices" : "stocks"}</span>`;
  const chip = (r) => `<li>${nm(r)} <b>${r.hit_rate}%</b> <span class="muted small">of ${r.decided ?? r.n} decided</span></li>`;
  const rows = (sc.rows || []).filter((r) => r.hit_rate != null);
  const dc = (r) => r.decided ?? r.n;
  const trust = rows.filter((r) => dc(r) >= 30 && r.hit_rate >= 60);
  const prom = rows.filter((r) => dc(r) >= 10 && dc(r) < 30 && r.hit_rate >= 60);
  const mute = rows.filter((r) => dc(r) >= 20 && r.hit_rate < 45);
  const early = rows.filter((r) => dc(r) < 10).length;
  const box = (cls, title, why, list) => `<div class="sb ${cls}"><h4>${title}</h4><p class="small muted">${why}</p>${list.length ? `<ul class="plain small">${list.map(chip).join("")}</ul>` : `<p class="small muted">None yet.</p>`}</div>`;
  return `<div class="panel" style="margin-top:14px"><h3>In plain words</h3>
    <div class="sbuckets">${box("ok", "Trust these", "Worked 60%+ over 30+ decided alerts.", trust)}
      ${box("mid", "Promising, keep watching", "60%+ but under 30 decided: could still be luck.", prom)}
      ${box("bad", "Ignore these", "Under 45% over 20+ decided alerts. Already demoted: out of Focus, no Telegram, no auto paper trade.", mute)}</div>
    <p class="small muted" style="margin:8px 0 0">${early ? `${early} alert type${early > 1 ? "s have" : " has"} fewer than 10 decided alerts: too early to judge. ` : ""}"Decided" = the option went +20% or −20% within an hour (the rest stayed flat). About 50% is break-even before costs. Real-market results count; simulator results don't.</p></div>`;
}

/* 2.50 (#11): is the score / conviction model / Focus credible, and which facts make a trade better? */
async function credBox(src) {
  const el = $("#cred-box"); if (!el) return;
  let r; try { r = await api(`/api/live/credibility${src ? `?source=${src}` : ""}`); } catch (e) { el.innerHTML = `<p class="small muted">${esc(e.message)}</p>`; return; }
  const A = r.alerts || {}, P = r.params || {};
  const show = ["Signal score", "Model chance (conviction model)", "Focus alert", "Strength (1 watch, 2 important, 3 act now)", "Risk check", "Time of day"];
  const nice = { True: "yes", False: "no" };
  const grp = show.map((g) => { const rows = (A.groups || []).filter((x) => x.group === g); if (!rows.length) return "";
    return `<tr><td colspan="4" class="small"><b>${esc(g)}</b></td></tr>` + rows.map((x) => `<tr><td class="small">${esc(nice[x.value] || x.value)}</td><td class="r small">${x.decided}</td>
      <td class="r small"><b class="${x.worked_pct >= 70 ? "up" : x.worked_pct < 55 ? "down" : ""}">${x.worked_pct != null ? x.worked_pct + "%" : "–"}</b>${x.enough ? "" : ` <small class="muted">few</small>`}</td><td class="r small">${fpc(x.o60, 1)}</td></tr>`).join(""); }).join("");
  const vcls = (v) => v.startsWith("helps") || v.startsWith("leans helpful") ? "up" : v.startsWith("hurts") || v.startsWith("leans harmful") ? "down" : "muted";
  const facts = (P.rows || []).slice(0, 24).map((x) => `<tr${x.candle ? ` class="hl"` : ""}><td class="small">${esc(x.name)}${x.candle ? ` <span class="pill">candle</span>` : ""}</td>
      <td class="r small">${esc(x.a_label)}<br>${x.a.n.toLocaleString("en-IN")} · ${rupees(x.a.avg_net)}</td><td class="r small">${esc(x.b_label)}<br>${x.b.n.toLocaleString("en-IN")} · ${rupees(x.b.avg_net)}</td>
      <td class="r small"><b class="${x.gap >= 0 ? "up" : "down"}">${rupees(x.gap)}</b></td><td class="small ${vcls(x.verdict)}">${esc(x.verdict)}</td></tr>`).join("");
  el.innerHTML = `<h3 style="margin-top:0">Credibility check <span class="muted small">${A.n ? `${A.decided.toLocaleString("en-IN")} decided alerts, ${A.first} → ${A.last}` : ""}</span></h3>
    <ul class="small">${(A.verdicts || []).map((v) => `<li><b>${esc(v.what)}:</b> ${esc(v.verdict)}</li>`).join("")}
      ${P.candles ? `<li><b>Candles as chart reading:</b> ${esc(P.candles)}</li>` : ""}</ul>
    <details><summary class="small">Alerts by what the app said about them</summary>
      <div class="table-wrap"><table class="mini"><thead><tr><th></th><th class="r">Decided</th><th class="r">Worked</th><th class="r">Option 60 min</th></tr></thead><tbody>${grp}</tbody></table></div></details>
    <details><summary class="small">Which facts make a trade better <span class="muted">(test bench, Buy ATM 1 lot after charges: ${(P.n || 0).toLocaleString("en-IN")} trades over ${P.days || 0} days, average ${rupees((P.base || {}).avg_net)})</span></summary>
      <div class="table-wrap"><table class="mini"><thead><tr><th>Fact at the signal</th><th class="r">With / high</th><th class="r">Against / low</th><th class="r">Gap a trade</th><th>Verdict</th></tr></thead><tbody>${facts || `<tr><td colspan="5" class="muted">No test-bench trades yet.</td></tr>`}</tbody></table></div>
      <p class="small muted">"Helps" needs a clear gap that points the same way in the older and the newer half of the days, and 10+ days of data. Before that it can only "lean". Information only: nothing changes by itself.</p></details>`;
}

async function pageScorecard() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const sc = await api(`/api/live/scorecard${params.get("src") ? `?source=${params.get("src")}` : ""}`);
  const m = sc.model || {};
  const rows = sc.rows.map((r) => `<tr><td><b>${esc(KIND[r.kind] || r.kind)}</b> <span class="muted small">${r.is_index ? "indices" : "stocks"}</span></td><td class="r">${r.n}</td>
    <td class="r"><b class="${r.hit_rate >= 60 ? "up" : r.hit_rate < 45 ? "down" : ""}">${r.hit_rate ?? "–"}%</b> <small class="muted">${r.decided ?? ""} decided</small><div class="meter thin"><i class="${r.hit_rate >= 50 ? "on" : ""}" style="width:${r.hit_rate || 0}%"></i></div></td>
    <td class="r">${r.recent_rate != null ? r.recent_rate + "%" : "–"} <small class="muted">last ${r.recent_n}</small></td>
    <td class="r">${fpc(r.avg_o15, 1)}</td><td class="r">${fpc(r.avg_o60, 1)}</td><td class="r">${fpc(r.avg_r15, 2)}</td>
    <td class="r" title="The same alerts paper-traded 1 lot on the test bench: ask in, bid out, all charges, the paper account's exits">${r.honest ? `<b class="${r.honest.avg >= 0 ? "up" : "down"}">${rupees(r.honest.avg)}</b> <small class="muted">× ${r.honest.n}, ${r.honest.win_pct}% won</small>` : `<span class="muted small">–</span>`}</td>
    <td>${(r.decided ?? 0) >= 20 && r.hit_rate < 45 || (r.n >= 40 && (r.avg_o60 ?? 0) < -2) ? `<span class="pill p-down">Demoted</span>` : (r.decided ?? 0) < 10 ? `<span class="muted small">too few yet</span>` : r.hit_rate >= 60 ? `<span class="pill p-up">Reliable</span>` : ""}</td></tr>`).join("");
  const recent = sc.recent.slice(0, 80).map((o) => `<tr><td>${o.day.slice(5)} ${hm(o.ts)}</td><td>${arrow(o.direction)} ${esc(o.title || o.sym)}</td>
    <td>${o.result ? `<span class="pill ${o.result === "hit" ? "p-up" : o.result === "miss" ? "p-down" : ""}">${o.result === "hit" ? "Worked" : o.result === "miss" ? "Failed" : "Flat"}</span>` : `<span class="muted small">tracking…</span>`}</td>
    <td class="r">${fpc(o.r5, 2)}</td><td class="r">${fpc(o.r15, 2)}</td><td class="r">${fpc(o.r60, 2)}</td><td class="r">${o.o0 ? fpc(o.omax, 0) + " / " + fpc(o.omin, 0) : "–"}</td></tr>`).join("");
  main.innerHTML = `<h1 class="page-title">Signal scorecard</h1>
    <p class="lede">Every alert is followed for 60 minutes and graded. This is how you know which signals deserve real money. ${sc.source === "sim" ? "<b>Showing simulator results (random market, not evidence).</b>" : ""}
      <a href="#/scorecard?src=${sc.source === "sim" ? "upstox" : "sim"}">Show ${sc.source === "sim" ? "real market" : "simulator"} results</a></p>
    ${scoreBuckets(sc)}
    <div class="panel tight" id="cred-box" style="margin-top:14px"><p class="small muted">Credibility check: loading…</p></div>
    ${aiReviewBox("scorecard", "Claude's read on your alerts")}
    <div class="kpis"><div class="kpi"><b>${sc.graded}</b><span>Alerts graded (recent)</span></div>
      <div class="kpi"><b>${sc.overall != null ? sc.overall + "%" : "–"}</b><span>Worked overall</span></div>
      <div class="kpi"><b class="${m.trained ? "up" : ""}">${m.auc != null ? m.auc : "–"}</b><span>Conviction model: holdout AUC (0.5 = coin toss, trusted at 0.60+ with 5+ days)</span></div>
      <div class="kpi"><b>${sc.recordings_mb} MB</b><span>Market data recorded on this Mac</span></div></div>
    <div class="panel tight" style="margin-top:14px"><h3>Conviction model <span class="pill ${m.trained ? "p-up" : ""}">${m.trained ? "trusted: it now picks Focus" : "learning"}</span></h3>
      <p class="small">Learns which alerts are worth an option buy from every graded alert: the stock (move, VWAP, futures buildup, order flow, unusual options, IV), the option itself (price per lot, spread), and everything around it (Nifty, breadth, its sector, global markets, VIX, fresh news). Trained on earlier days, judged on the latest days it never saw.</p>
      <p class="small">${m.n ? `${(m.n || 0).toLocaleString("en-IN")} graded alerts over ${m.days || 0} day${m.days === 1 ? "" : "s"}. ` : ""}${m.hold_n ? `Holdout ${m.hold_n.toLocaleString("en-IN")} alerts: top 10% by conviction worked ${m.top10_hit}% vs ${m.base_rate}% for all (option ${m.top10_o60 >= 0 ? "+" : ""}${m.top10_o60}% vs ${m.all_o60 >= 0 ? "+" : ""}${m.all_o60}% after an hour). ` : ""}<b>${esc(m.why || (m.need ? `needs ${m.need} graded alerts` : ""))}</b></p>
      ${(m.drivers || []).length ? `<p class="small muted">What matters most so far: ${m.drivers.map((d) => esc(d.feature)).join(", ")}.</p>` : ""}
      ${(m.models || []).length ? `<p class="small">Models compared on the same unseen days: ${m.models.map((x) => `${esc(x.name)} <b>${x.auc}</b>${x.chosen ? " ✓ in use" : ""}${x.champion ? " (champion)" : ""}`).join(" · ")}. A challenger replaces the champion only if it is 0.02 AUC better.</p>` : ""}</div>
    ${exitBoardLive(sc.exits)}
    <div id="trials-box"></div>
    <div id="focus-shadow-box"></div>
    <p class="note">${esc(sc.rule)} Alert types under 45% of decided cases (20+) are demoted automatically: out of Focus, no Telegram, no auto paper trade.</p>
    <h2>By alert type</h2>
    <div class="table-wrap"><table><thead><tr><th>Alert</th><th class="r">Graded</th><th class="r">Worked</th><th class="r">Recently</th><th class="r">Option 15 min</th><th class="r">Option 60 min</th><th class="r" title="Price move in the alert's favour">Price 15 min</th><th class="r">Honest ₹ a trade<br><small class="muted">after costs (bench)</small></th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="9" class="muted">Nothing graded yet. Run the scanner for a session.</td></tr>`}</tbody></table></div>
    <p class="note">"Worked" is the old grade (option +20% before −20% within an hour, flat hours not counted). <b>Honest ₹ a trade</b> is what a 1-lot paper trade made after the bid-ask spread and every charge, with the paper account's own exits: the number that decides money. Demotion, Focus and the conviction model still use the old grade until you switch.</p>
    <h2>Latest graded alerts</h2>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Alert</th><th>Result</th><th class="r" title="Price move in the alert's favour">In favour 5m</th><th class="r">15m</th><th class="r">60m</th><th class="r">Option best / worst</th></tr></thead><tbody>${recent || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
  bindAIReview(main);
  api(`/api/live/trials${sc.source ? `?source=${sc.source}` : ""}`).then((t) => { const el = $("#trials-box"); if (el) el.innerHTML = trialsBox(t); }).catch(() => {});
  credBox(sc.source);
  api(`/api/live/focus_shadow?days=10${sc.source ? `&source=${sc.source}` : ""}`).then((f) => { const el = $("#focus-shadow-box"); if (el) el.innerHTML = focusShadowBox(f); }).catch(() => {});
}

/* 2.37 shadow Focus: the current Focus vs the challenger versions on the same alerts (live/focuslab.py) */
function focusShadowBox(f) {
  if (!f || !(f.selectors || []).length || !(f.days || []).length) return "";
  const B = (b) => b && b.n ? `<b class="${b.avg >= 0 ? "up" : "down"}">${rupees(b.avg)}</b> <small class="muted">× ${b.n}, ${b.win_pct}% won</small><br><small class="muted">total ${rupees(b.net)}</small>` : `<span class="muted small">collecting</span>`;
  const mix = (s) => (s.top || []).slice(0, 2).map((t) => `${esc(t.label)} ${t.pct}%`).join(" · ");
  const rows = f.selectors.map((s) => `<tr${s.main ? ` class="best"` : ""}><td><b>${esc(s.label)}</b> ${s.champion ? `<span class="pill">in use</span>` : s.baseline ? "" : `<span class="pill">shadow</span>`}
      ${!s.champion && !s.baseline && s.replayed_days ? `<br><small class="muted">${s.live_days} live, ${s.replayed_days} replayed</small>` : ""}</td>
    <td class="r">${s.n.toLocaleString("en-IN")}<br><small class="muted">${s.per_day}/day${s.both != null ? `, ${s.both} in Focus too` : ""}</small></td>
    <td class="small">${s.top_pct != null ? `<b class="${s.top_pct > 40 ? "down" : ""}">${s.top_pct}%</b> top type · ${s.types} types<br><span class="muted">${mix(s)}</span>` : `<span class="muted">–</span>`}</td>
    <td class="r">${B(s.bench)}</td>
    <td class="r">${s.worked_pct != null ? s.worked_pct + "%" : "–"}<br><small class="muted">${fpc(s.o60, 1)} 1 h</small></td></tr>`).join("");
  const ver = (f.verdicts || []).filter((v) => v.verdict !== "new").slice(0, 10)
    .map((v) => `<span class="pill ${v.verdict === "good" ? "p-up" : v.verdict === "bad" ? "p-down" : ""}">${esc(v.label)}${v.is_index ? " (indices)" : ""} ${rupees(v.avg)} × ${v.n}</span>`).join(" ");
  const nNew = (f.verdicts || []).filter((v) => v.verdict === "new").length;
  const ids = f.selectors.map((s) => s.id);
  const short = { focus: "Focus", all: "All" };
  const dayRows = (f.per_day || []).slice().reverse().map((d) => `<tr><td>${esc(d.day.slice(5))}${d.from ? ` <small class="muted">from ${hm(d.from)}</small>` : ""}</td>
    ${ids.map((k) => { const x = d.sel[k] || {}; return `<td class="r">${x.n ?? 0}${x.top_pct != null ? ` <small class="muted">${x.top_pct}% ${esc(((x.top || [])[0] || {}).label || "")}</small>` : ""}${x.bn ? `<br><small class="${x.net >= 0 ? "up" : "down"}">${rupees(x.net)}</small>` : ""}</td>`; }).join("")}</tr>`).join("");
  return `<div class="panel tight" style="margin-top:14px"><h3>Focus: current vs challengers <span class="pill">shadow</span></h3>
    <p class="small">Is Focus leaning on one or two alert types? Each challenger judges the same alerts at the same moment; only the current Focus reaches you, Telegram and the paper account. Last ${f.days.length} day${f.days.length === 1 ? "" : "s"}, ${(f.bench_trades || 0).toLocaleString("en-IN")} honest bench trades on these alerts. <a href="#/alerts?view=shadow">See the shadow Focus alerts →</a></p>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Way of choosing</th><th class="r">Picks</th><th>Mix</th><th class="r">Honest ₹/trade<br><small class="muted">bench</small></th><th class="r">Old grade</th></tr></thead><tbody>${rows}</tbody></table></div>
    <ul class="plain small muted" style="margin:6px 0 0">${f.selectors.filter((s) => s.rule).map((s) => `<li><b>${esc(s.label)}:</b> ${esc(s.rule)}.</li>`).join("")}</ul>
    <p class="small" style="margin:8px 0 0"><b>Honest record the challengers use today</b> <span class="muted">(earlier days, ${esc(f.style)} style)</span>: ${ver || `<span class="muted">no alert type has enough bench trades yet, so they use the current Focus bar</span>`}${nNew ? ` <span class="muted">· ${nNew} type${nNew === 1 ? "" : "s"} still collecting</span>` : ""}</p>
    ${dayRows ? `<details><summary class="small muted">By day</summary><div class="table-wrap"><table class="mini"><thead><tr><th>Day</th>${f.selectors.map((s) => `<th class="r">${esc(short[s.id] || s.id)}</th>`).join("")}</tr></thead><tbody>${dayRows}</tbody></table></div></details>` : ""}
    ${(f.errors || []).length ? `<p class="small down">Skipped: ${f.errors.map(esc).join("; ")}</p>` : ""}
    <p class="note">${esc(f.note)}</p></div>`;
}

/* Bullets on trial (live/trials.py): recorded and graded only */
function trialsBox(t) {
  if (!t || !(t.on_trial || []).length) return "";
  const res = (x) => x.result === "hit" ? `<span class="pill p-up">Target</span>` : x.result === "miss" ? `<span class="pill p-down">Stop</span>`
    : x.result === "time" ? `<span class="pill">${fpc(x.r_exit, 2)}R at ${t.hold_min} min</span>` : x.result === "ungraded" ? `<span class="muted small">no stop</span>` : `<span class="muted small">following…</span>`;
  const rows = (t.kinds || []).map((k) => `<tr><td><b>${esc(k.label.replace(/^Candle: /, ""))}</b></td><td class="r">${k.n} <small class="muted">${k.days} day${k.days === 1 ? "" : "s"}</small></td>
    <td class="r">${k.hits} / ${k.misses} / ${k.timed}</td><td class="r"><b class="${k.hit_rate >= 40 ? "up" : k.hit_rate != null && k.hit_rate < 30 ? "down" : ""}">${k.hit_rate != null ? k.hit_rate + "%" : "–"}</b></td>
    <td class="r"><b class="${(k.avg_r ?? 0) > 0 ? "up" : (k.avg_r ?? 0) < 0 ? "down" : ""}">${k.avg_r != null ? (k.avg_r >= 0 ? "+" : "") + k.avg_r + "R" : "–"}</b></td></tr>`).join("");
  const last = (t.recent || []).slice(0, 15).map((x) => `<tr><td>${esc((x.day || "").slice(5))} ${hm(x.ts)}</td><td>${arrow(x.direction)} ${esc(x.title || x.sym)}</td><td>${res(x)}</td></tr>`).join("");
  return `<div class="panel tight" style="margin-top:14px"><h3>On trial <span class="pill">shadow</span></h3>
    <p class="small">New ideas being tested live before you ever see them: ${t.on_trial.map((k) => esc(k.label.replace(/^Candle: /, ""))).join(", ")}. ${esc(t.note)}</p>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Idea</th><th class="r">Fired</th><th class="r">Target / stop / time</th><th class="r">Target first</th><th class="r">Average</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="muted">Nothing recorded yet. They start with the next scanner session.</td></tr>`}</tbody></table></div>
    ${last ? `<details><summary class="small muted">Latest trials</summary><div class="table-wrap"><table class="mini"><tbody>${last}</tbody></table></div></details>` : ""}</div>`;
}

/* ---------------- page: Briefs */
async function pageBriefs() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const b = await api(`/api/live/briefs${params.get("day") ? `?day=${params.get("day")}` : ""}`);
  const card = (x, title, kind) => `<div class="panel brief"><div class="toolbar" style="margin:0 0 8px"><h2 style="margin:0">${title}</h2>
      <button class="btn" data-mk="${kind}">${x ? "Make again" : "Make now"}</button></div>
    ${x ? `${x.ai ? `<div class="ai-text big">${fmtAI(x.ai)}</div><details><summary class="small muted">Facts used</summary><ul>${x.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>` : `<ul class="brief-lines">${x.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`}
      <p class="muted small">Made ${new Date(x.ts).toLocaleString("en-IN", { hour12: false })}${x.source === "sim" ? " · simulator day" : ""}</p>` :
      `<p class="muted">${kind === "pre" ? "Made automatically at 8:45 AM on market days (and sent to Telegram)." : "Made automatically when the market closes."}</p>`}</div>`;
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Briefs</h1><p class="lede" style="margin-bottom:0">Your morning plan and evening review, written from the data. Connect Claude (your Pro plan) or another AI in Live setup for a written analysis.</p></div>
      ${b.days.length ? `<label class="field inline">Day <select id="br-day">${b.days.map((d) => `<option ${d === b.day ? "selected" : ""}>${d}</option>`).join("")}</select></label>` : ""}</div>
    <div id="day-report"></div>
    <div class="split" style="margin-top:18px">${card(b.pre, "🌅 Pre-market brief", "pre")}${card(b.post, "🌇 Post-market review", "post")}</div>`;
  const drawRep = (rebuild) => api(`/api/live/report?day=${encodeURIComponent(b.day || "")}${rebuild ? "&rebuild=true" : ""}`)
    .then((r) => { const el = $("#day-report"); if (!el) return; el.innerHTML = dayReportCard(r);
      const rb = $("#rep-rebuild"); if (rb) rb.onclick = () => { rb.disabled = true; rb.textContent = "Building…"; drawRep(true); }; })
    .catch(() => {});
  if (b.day) drawRep(false);
  if ($("#br-day")) $("#br-day").onchange = (e) => { location.hash = `#/briefs?day=${e.target.value}`; };
  main.querySelectorAll("[data-mk]").forEach((btn) => btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "Writing…";
    try { await post(`/api/live/briefs/${btn.dataset.mk}`, {}); pageBriefs(); } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Make now"; }
  });
}


/* End-of-day report (live/dayreport.py) */
function dayReportCard(r) {
  if (!r || !r.paper) return "";
  const p = r.paper, a = r.alerts;
  const rs = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
  const g = p.by_gate || {};
  const kb = (v) => `<b class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</b>`;
  const gate = [["pass", "Risk check PASS"], ["override", "Overrides"], ["blocked", "Taken although BLOCKED"]].filter(([k]) => g[k])
    .map(([k, l]) => `<div class="kpi">${kb(g[k].net)}<span>${l} · ${g[k].n} trade${g[k].n > 1 ? "s" : ""}</span></div>`).join("");
  const tr = (t) => `<tr><td>${esc(t.time)}</td><td>${esc(t.label)}<div class="muted small">${esc(KIND[t.kind] || t.kind)} · ${esc(t.exit)}${t.gate !== "pass" ? ` · <span class="down">${t.gate === "override" ? "override" : "against risk check"}</span>` : ""}</div></td><td class="r">${rs(t.net)}</td></tr>`;
  const kinds = (list) => list.map((k) => `<li>${esc(KIND[k.kind] || k.kind)} <b>${k.win_pct}%</b> <span class="muted small">of ${k.hits + k.misses} decided · option ${fpc(k.avg_o60, 1)} after 1h</span></li>`).join("") || `<li class="muted">Not enough decided alerts yet.</li>`;
  const fg = a.focus_graded || {}, gg = a.graded || {};
  return `<div class="panel" style="margin-top:18px"><div class="toolbar" style="margin:0 0 8px"><h2 style="margin:0">📋 Day report <span class="muted small">${esc(r.day)}${r.source === "sim" ? " · simulator" : ""}</span></h2>
      <button class="btn" id="rep-rebuild">Rebuild</button></div>
    <div class="kpis kpi6">
      <div class="kpi">${kb(p.net)}<span>Paper net after ₹${Math.round(p.costs || 0).toLocaleString("en-IN")} charges</span></div>
      <div class="kpi"><b>${p.wins} / ${p.losses}</b><span>Won / lost of ${p.n} trades</span></div>
      ${gate}
      <div class="kpi"><b>${a.focus} <small class="muted">/ ${a.total.toLocaleString("en-IN")}</small></b><span>Alerts in Focus / all (${a.symbols} symbols)</span></div>
      <div class="kpi"><b>${fg.win_pct ?? "–"}% <small class="muted">vs ${gg.win_pct ?? "–"}%</small></b><span>Focus vs all alerts worked (decided)</span></div></div>
    ${(r.lessons || []).length ? `<h3>Lessons for tomorrow</h3><ul class="brief-lines">${r.lessons.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
    <div class="split" style="margin-top:10px">
      <div><h3>Best and worst paper trades</h3><div class="table-wrap"><table class="mini"><tbody>${(p.best || []).map(tr).join("")}${(p.worst || []).map(tr).join("")}</tbody></table></div>
        <p class="small muted">Exits: ${(p.by_exit || []).map((x) => `${esc(x.key)} ${x.n} (${rs(x.net)})`).join(" · ")}</p></div>
      <div><h3>Alert types today</h3><p class="small muted">Worked = option +20% before −20% within an hour.</p>
        <h4 class="small">Best</h4><ul class="plain small">${kinds(a.best_kinds || [])}</ul>
        <h4 class="small">Worst</h4><ul class="plain small">${kinds(a.worst_kinds || [])}</ul></div></div>
    <p class="note">From the app's own records. Paper only. One day is not evidence: the Scorecard decides over weeks.</p></div>`;
}

/* ---------------- page: Strategy desk */
const SNAME = { iron_fly: "Iron fly", iron_condor: "Iron condor", expiry_condor: "Expiry-day condor", orb_spread: "Breakout debit spread", trend_spread: "Trend debit spread" };
async function pageDesk() {
  stopPageTimer();
  const [st, settings] = await Promise.all([api("/api/lab/status"), api("/api/settings")]);
  let res = null;
  if (st.results) { try { res = await api("/api/lab/results"); } catch (_) {} }
  const q = (v) => (v == null ? "" : esc(v));
  const dataRows = Object.entries(st.data).map(([k, v]) => `<tr><td>${k === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</td><td class="r">${v.months}</td><td>${v.first || "–"} → ${v.last || "–"}</td><td class="r">${v.mb} MB</td></tr>`).join("");
  const S = res && res.summary;
  main.innerHTML = `<h1 class="page-title">Strategy desk</h1>
    <p class="lede">Test strategies on years of real prices; only what passes on data it never saw gets paper-traded. Follow the steps below in order. Everything here is paper: no real orders, ever.</p>
    ${st.synthetic ? `<div class="banner">⚠️ You are looking at a <b>demo on synthetic (fake) data</b>. It shows how the desk works, not whether it makes money. Download real history below (Upstox Plus).</div>` : ""}
    <div id="desk-guide"><div class="panel"><p class="muted">Loading your research status…</p></div></div>
    ${aiReviewBox("desk", "Claude research review of the desk")}
    <div id="desk-scenario"></div>
    <div id="desk-live"></div>
    <div id="desk-edge"></div>
    <div id="desk-eod"></div>
    <div id="desk-expiry"></div>
    ${S ? deskResults(res) : `<div class="empty" style="max-width:none"><h2>No backtest yet</h2><p>Log in to Upstox (Plus plan), download history, then run the backtest. Or see a quick demo on synthetic data first.</p>
      <div class="btn-row"><button class="btn" id="lab-demo">See a demo (synthetic data)</button></div></div>`}
    <h2 id="desk-data">Data and settings</h2>
    <div class="split">
      <div class="panel"><h3>1. Historical option data</h3>
        <p><b>Upstox Plus</b> ${st.upstox ? `<span class="pill p-up">logged in</span>` : `<span class="pill p-down">not logged in</span>`}: 1-minute prices and OI of expired Nifty and Bank Nifty options (from 2022).</p>
        <ol class="steps"><li>Upstox Plus plan active on your account.</li>
          <li>Log in on <a href="#/connect">Live setup</a> (after upgrading, log out and log in once so the new plan is picked up).</li>
          <li><b>Check access</b>, then <b>Download history</b>. It resumes where it stopped; leave the app open.</li></ol>
        <div class="form-grid" style="margin-top:12px">
          <label class="field">History to download<select id="l-years">${[1, 2, 3, 4].map((v) => `<option value="${v}" ${Number(settings["lab.years"] || 3) === v ? "selected" : ""}>${v} year${v > 1 ? "s" : ""}</option>`).join("")}</select><small>About 5,000 requests per year (roughly 1 hour per year of history, within Upstox limits).</small></label>
        </div>
        <div class="btn-row" style="margin-top:14px"><button class="btn" id="l-uptest" ${st.upstox ? "" : "disabled"}>Check access</button>
          <button class="btn primary" id="l-dl" ${st.upstox ? "" : "disabled"}>Download history</button></div>
        <details style="margin-top:12px" ${st.dhan ? "" : "open"}><summary><b>Dhan keys</b> <span class="muted small">for the 5-year Lab and the 200-level order book ${st.dhan ? "· saved" : ""}</span></summary>
        <div class="form-grid" style="margin-top:10px">
          <label class="field">Dhan client ID<input type="text" id="l-cid" value="${q(settings["lab.dhan_client_id"])}" autocomplete="off"></label>
          <label class="field">Access token<input type="password" id="l-tok" value="${q(settings["lab.dhan_token"])}" autocomplete="off"><small>Either paste a token from web.dhan.co → Profile → DhanHQ Trading APIs → Access Token (24 h), or use <b>Log in with Dhan</b> below. Only market data is read: no orders, no holdings.</small></label>
          <label class="field">API key <span class="muted">(optional, for Log in with Dhan)</span><input type="text" id="l-akey" value="${q(settings["lab.dhan_api_key"])}" autocomplete="off"><small>web.dhan.co → DhanHQ Trading APIs → switch to <b>API Key</b> → name <code>Stockway</code>, Redirect URL <code>http://127.0.0.1:8765/dhan/callback</code> → Generate. Valid 12 months.</small></label>
          <label class="field">API secret<input type="password" id="l-asec" value="${q(settings["lab.dhan_api_secret"])}" autocomplete="off"><small>Shown once by Dhan with the API key. Stays on your Mac.</small></label>
        </div>
        ${st.dhan_token && st.dhan_token.expires ? `<p class="small" style="margin:10px 0 0;${st.dhan_token.error ? "color:#C23B32" : ""}">Token valid till <b>${st.dhan_token.expires}</b> (${st.dhan_token.hours_left} h left). It renews by itself about 3 hours before it expires, while the app is open${st.dhan_token.renewed ? ` · last renewed ${st.dhan_token.renewed}` : ""}.${st.dhan_token.error ? ` ${q(st.dhan_token.error)}` : ""}</p>` : ""}
        <div style="margin-top:12px;padding:10px 12px;border:1px solid #D5DCE3;border-radius:8px">
          <b>Automatic login (TOTP)</b> ${st.dhan_token && st.dhan_token.auto && st.dhan_token.auto.on
            ? `<span class="pill p-up">on</span> <span class="small muted">PIN and TOTP key kept in ${esc(st.dhan_token.auto.where || "the macOS Keychain")}${st.dhan_token.auto.last_login ? " · last automatic login " + esc(st.dhan_token.auto.last_login) : ""}</span>${st.dhan_token.auto.blocked ? `<div class="small down">Stopped ${esc(st.dhan_token.auto.blocked)}: ${esc(st.dhan_token.auto.last_error || "Dhan refused the PIN / key")}. Enter them again below.</div>` : ""}`
            : `<span class="small muted">off</span>`}
          <p class="small muted" style="margin:6px 0">The app makes a new Dhan token by itself whenever the old one is gone (Mac was off, token expired). One-time setup on web.dhan.co → Profile → DhanHQ Trading APIs → <b>Set-up TOTP</b> → Add TOTP: scan the QR in Google Authenticator <i>and</i> copy the text key under it; confirm with the first 6-digit code on Dhan. Then enter your Dhan PIN and that key here. They are checked with Dhan, then kept in the macOS Keychain (not in settings, logs or git). A wrong PIN stops it at once, so your Dhan account is never locked by retries.</p>
          <div class="form-grid"><label class="field">Dhan PIN<input type="password" id="l-pin" inputmode="numeric" autocomplete="off"></label>
            <label class="field">TOTP key (text under the QR)<input type="password" id="l-totp" autocomplete="off"></label></div>
          <div class="btn-row" style="margin-top:8px"><button class="btn primary" id="l-auto-on">Turn on automatic login</button>${st.dhan_token && st.dhan_token.auto && st.dhan_token.auto.on ? `<button class="btn" id="l-auto-off">Turn off</button>` : ""}</div>
        </div>
        <div class="btn-row" style="margin-top:10px"><button class="btn" id="l-save">Save</button><button class="btn" id="l-test">Test Dhan</button>
          <button class="btn" id="l-dlogin">Log in with Dhan</button><button class="btn" id="l-renew">Renew now</button>
          <a class="btn" href="#/lab">Go to 5-year Lab →</a></div>
        <p class="small muted">The 5-year download is on Strategy Lab → 5-year Lab: it keeps Dhan's data in its own folder, so the current Lab is not changed.</p></details>
        <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Index</th><th class="r">Months</th><th>Range</th><th class="r">Size</th></tr></thead><tbody>${dataRows}</tbody></table></div>
        <div class="btn-row" style="margin-top:12px"><button class="btn primary" id="l-bt" ${Object.values(st.data).some((v) => v.months) ? "" : "disabled"}>Run backtest</button>
          ${!st.synthetic && !Object.values(st.data).some((v) => v.months) ? `<button class="btn" id="lab-demo2">Demo on synthetic data</button>` : ""}</div>
        <p class="note">Re-run the backtest once a month (download first): the plan picks strategies on the latest 6 months.</p></div>
      <div class="panel"><h3>2. Money and risk</h3><div class="form-grid">
        <label class="field">Capital (₹)<input type="text" id="l-cap" value="${q(settings["lab.capital"] ?? 500000)}" inputmode="numeric"><small>Same capital as paper trading and the risk check.</small></label>
        <label class="field">Risk per trade<select id="l-risk">${[0.5, 1, 1.5, 2].map((v) => `<option value="${v}" ${Number(settings["lab.risk_pct"] ?? 1.5) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select><small>Loss if the stops hit, plus a 50% buffer.</small></label>
        <label class="field">Daily loss limit<select id="l-stop">${[1, 1.5, 2, 3].map((v) => `<option value="${v}" ${Number(settings["lab.daily_stop_pct"] ?? 2) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select><small>Everything closes for the day at this loss.</small></label>
        <label class="check"><input type="checkbox" id="l-auto" ${settings["lab.auto_trade"] !== false ? "checked" : ""}><span>Paper-trade the plan automatically<small>On live or simulator prices, every market day.</small></span></label>
      </div><div class="btn-row" style="margin-top:14px"><button class="btn" id="l-save2">Save</button></div>
      <p class="note">Changing capital or risk: run the backtest again to re-size.</p></div>
    </div>`;
  const save = async () => {
    try {
      await post("/api/settings", { "lab.dhan_client_id": $("#l-cid").value.trim(), "lab.dhan_token": $("#l-tok").value.trim(),
        "lab.dhan_api_key": $("#l-akey").value.trim(), "lab.dhan_api_secret": $("#l-asec").value.trim(),
        "lab.years": Number($("#l-years").value), "lab.capital": (parseAmount($("#l-cap").value).value) || (() => { throw new Error(parseAmount($("#l-cap").value).error); })(),
        "lab.risk_pct": Number($("#l-risk").value), "lab.daily_stop_pct": Number($("#l-stop").value), "lab.auto_trade": $("#l-auto").checked });
      toast("Saved"); return true;
    } catch (e) { toast(e.message); return false; }
  };
  const job = async (path, body = {}) => { try { await post(path, body); state.jobSince = 0; $("#job-log").textContent = ""; watchJob(); } catch (e) { toast(e.message); } };
  $("#l-save").onclick = async () => { if (await save()) pageDesk(); };
  $("#l-save2").onclick = save;
  $("#l-auto-on").onclick = async (e) => {
    if (!(await save())) return;
    const pin = $("#l-pin").value.trim(), secret = $("#l-totp").value.trim();
    if (!pin || !secret) { toast("Enter your Dhan PIN and the TOTP key"); return; }
    e.target.disabled = true; e.target.textContent = "Checking with Dhan…";
    try { const r = await post("/api/lab/dhan/auto", { pin, secret }); $("#l-pin").value = ""; $("#l-totp").value = "";
      toast(`Automatic login on: new token valid till ${(r.token && r.token.expires) || "tomorrow"}`); pageDesk(); }
    catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Turn on automatic login"; } };
  if ($("#l-auto-off")) $("#l-auto-off").onclick = async () => { await fetch("/api/lab/dhan/auto", { method: "DELETE" }); toast("Automatic login off: PIN and key removed"); pageDesk(); };
  $("#l-dlogin").onclick = async () => { if (!(await save())) return; window.open("/dhan/login", "_blank"); };
  $("#l-renew").onclick = async () => { if (!(await save())) return; try { const r = await post("/api/lab/dhan/renew", {}); toast(`Token renewed: valid till ${r.token.expires}`); pageDesk(); } catch (e) { toast(e.message); } };
  $("#l-test").onclick = async () => { if (!(await save())) return; try { const r = await post("/api/lab/dhan/test", {}); toast(`Dhan connected (${r.profile.dhanClientId || "ok"}${r.profile.dataPlan ? ", data plan: " + r.profile.dataPlan : ""}${r.token && r.token.expires ? ", token valid till " + r.token.expires + ", auto-renews" : ""})`); pageDesk(); } catch (e) { toast(e.message); } };
  $("#l-dl").onclick = async () => { if (await save()) job("/api/lab/download", { source: "upstox" }); };
  $("#l-uptest").onclick = async () => {
    try { const r = await post("/api/lab/upstox/test", {}); const e = r.expiries;
      toast(`Upstox Plus OK: Nifty ${e.NIFTY.n} expiries (from ${e.NIFTY.first}), Bank Nifty ${e.BANKNIFTY.n} (from ${e.BANKNIFTY.first})`);
    } catch (err) { toast(err.message); } };
  $("#l-bt").onclick = async () => { if (await save()) job("/api/lab/backtest"); };
  ["#lab-demo", "#lab-demo2"].forEach((id) => { if ($(id)) $(id).onclick = () => job("/api/lab/demo"); });
  if (res) drawDeskCharts(res);
  bindAIReview(main);
  Promise.all([api("/api/lab/eod/status").catch(() => ({})), api("/api/lab/eod").catch(() => null), api("/api/lab/expiry").catch(() => null)])
    .then(([es, er, ex]) => { const el = $("#desk-guide"); if (!el) return; el.innerHTML = deskGuide(st, res, es, er, ex);
      el.querySelectorAll("[data-gjob]").forEach((b) => b.onclick = () => job(b.dataset.gjob));
      el.querySelectorAll("[data-gscroll]").forEach((b) => b.onclick = () => document.getElementById(b.dataset.gscroll)?.scrollIntoView({ behavior: "smooth" })); });
  api("/api/lab/edge").then((e) => { const el = $("#desk-edge"); if (el) el.innerHTML = edgeCard(e); }).catch(() => {});
  const drawScen = (x) => { const el = $("#desk-scenario"); if (!el) return; el.innerHTML = likeTodayCard(x);
    const b = $("#scen-run"); if (b) b.onclick = async () => { b.disabled = true; b.textContent = "Checking…";
      try { drawScen(await post("/api/lab/scenario/run", {})); toast("Checked against the latest close"); } catch (e) { toast(e.message); b.disabled = false; b.textContent = "Check again"; } }; };
  api("/api/lab/scenario").then(drawScen).catch(() => {});
  api("/api/lab/expiry").then((x) => { const el = $("#desk-expiry"); if (el) el.innerHTML = expiryCard(x); }).catch(() => {});
  Promise.all([api("/api/lab/eod/status"), api("/api/lab/eod").catch(() => null)]).then(([st2, r]) => {
    const el = $("#desk-eod"); if (!el) return;
    el.innerHTML = eodCard(st2, r);
    $("#eod-dl").onclick = () => job("/api/lab/eod/download");
    if ($("#eod-run")) $("#eod-run").onclick = () => job("/api/lab/eod/run");
  }).catch(() => {});
  const tick = async () => {
    if (state.page !== "desk") return;
    try { drawDeskLive(await api("/api/lab/desk")); } catch (_) {}
    L.pageTimer = setTimeout(tick, 3000);
  };
  tick();
}

function legsText(legs) {
  return legs.map((l) => `${l.q > 0 ? "Buy" : "Sell"} ${fk(l.strike)} ${l.side} @ ${fx(l.entry)}${l.exit != null ? ` → ${fx(l.exit)}` : ""}`).join(" · ");
}

function drawDeskLive(d) {
  const el = $("#desk-live"); if (!el) return;
  const s = d.stats || {};
  const plan = d.plan.map((p) => `<div class="plan-item"><div><b>${esc(SNAME[p.family] || p.family)}</b> <span class="pill p-idx">${esc(p.sym)}</span>
      <span class="pill ${p.selling ? "p-sig" : "p-amber"}">${p.selling ? "Sell premium" : "Directional"}</span>
      <p class="small muted" style="margin:4px 0 0">${esc(p.describe || "")}</p>
      <p class="small" style="margin:2px 0 0">Last 6 months: ${p.train && p.train.n ? `${p.train.n} trades, ${Math.round(p.train.win)}% winners, avg ₹${fk(Math.round(p.train.mean))} per lot, profit factor ${p.train.pf.toFixed(2)}` : "–"}</p></div>
      <div class="pi-status">${esc(p.status)}</div></div>`).join("");
  const open = d.today.map((t) => `<tr><td><b>${esc(SNAME[t.family] || t.family)}</b> ${esc(t.sym)}<br><small class="muted">${t.entry_ts ? `in ${hm(t.entry_ts)}${t.exit_ts ? ` · out ${hm(t.exit_ts)}` : ""} · ` : ""}${t.lots} lot${t.lots > 1 ? "s" : ""}${t.lot ? ` × ${t.lot} = ${(t.lots * t.lot).toLocaleString("en-IN")} qty` : ""} · max loss ₹${fk(Math.round(t.max_loss))} · margin ~₹${fk(Math.round(t.margin))}</small></td>
    <td class="small">${esc(legsText(t.legs))}</td><td>${t.status === "open" ? `<span class="pill p-sig">Open</span>` : esc(t.reason || "")}</td>
    <td class="r"><b class="${((t.status === "open" ? t.mtm : t.net) || 0) >= 0 ? "up" : "down"}">${rupees(t.status === "open" ? t.mtm : t.net)}</b></td></tr>`).join("");
  el.innerHTML = `<div class="kpis"><div class="kpi"><b class="${d.today_pnl >= 0 ? "up" : "down"}">${rupees(d.today_pnl)}</b><span>Today (${esc(d.source === "sim" ? "simulator" : "live paper")})</span></div>
      <div class="kpi"><b>${s.avg_day != null ? rupees(s.avg_day) : "–"}</b><span>Average day, ${s.days || 0} paper days</span></div>
      <div class="kpi"><b>${s.green_pct != null ? Math.round(s.green_pct) + "%" : "–"}</b><span>Green days</span></div>
      <div class="kpi"><b>${rupees(s.total || 0)}</b><span>Total paper P&amp;L</span></div></div>
    ${d.halted ? `<div class="banner" style="margin-top:12px">⛔ ${esc(d.halted)}</div>` : `<p class="note">Daily loss limit: ₹${fk(Math.round((d.config.capital || 0) * (d.config.daily_stop_pct || 2) / 100))}. Capital ₹${fk(Math.round(d.config.capital || 0))}.${d.config.enabled === false ? " <b>Auto paper trading is off.</b>" : ""}</p>`}
    <h2>Today's plan ${d.plan_info.generated ? `<span class="muted small">from the backtest of ${new Date(d.plan_info.generated).toLocaleDateString("en-IN")}</span>` : ""}</h2>
    <div class="plan-list">${plan || `<p class="muted" style="padding:14px">No strategy qualifies yet. Run the backtest; only strategies that made money reliably in the last 6 months are traded.</p>`}</div>
    ${(d.inactive || []).length ? `<p class="note">Not traded now (didn't pass the bar in the last 6 months): ${d.inactive.map((p) => `${esc(SNAME[p.family] || p.family)} ${esc(p.sym)}`).join(", ")}.</p>` : ""}
    ${d.today.length ? `<h2>Positions today</h2><div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Legs</th><th>Status</th><th class="r">P&amp;L</th></tr></thead><tbody>${open}</tbody></table></div>` : ""}
    ${d.days.length ? `<h2>Paper P&amp;L by day</h2><div class="chart" id="desk-days" style="height:200px"></div>` : ""}`;
  if (d.days.length && window.Plotly) {
    Plotly.react($("#desk-days"), [{ type: "bar", x: d.days.map((x) => x.day), y: d.days.map((x) => x.pnl), marker: { color: d.days.map((x) => (x.pnl >= 0 ? C.up : C.down)) } }],
      plotLayout({ margin: { l: 60, r: 10, t: 8, b: 30 }, yaxis: { gridcolor: "#EEF1F4", tickprefix: "₹" } }), plotCfg);
  }
}

function deskGuide(st, res, es, er, ex) {
  const rs = (v) => v == null ? "–" : `${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
  const months = Object.entries(st.data || {}).map(([k, v]) => `${k === "BANKNIFTY" ? "Bank Nifty" : "Nifty"} ${v.months || 0} months`).join(", ");
  const haveMin = Object.values(st.data || {}).some((v) => v.months);
  const day = (t) => t ? new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "";
  const step = (state, title, detail, btn) => `<li class="gstep ${state}"><span class="gmark" aria-hidden="true">${state === "done" ? "✓" : state === "part" ? "◐" : "○"}</span>
    <div><b>${title}</b><br><span class="small muted">${detail}</span></div><div>${btn || ""}</div></li>`;
  const steps = [
    step(es.days ? "done" : "todo", "10 years of end-of-day option prices (NSE, free)", es.days ? `${es.days.toLocaleString("en-IN")} days, ${esc(es.first)} → ${esc(es.last)}` : "Every Nifty and Bank Nifty option's daily close since 2015. About 45 minutes.",
      `<button class="btn ${es.days ? "" : "primary"}" data-gjob="/api/lab/eod/download">${es.days ? "Update" : "Download"}</button>`),
    step(er ? "done" : "todo", "10-year test (premium selling held to expiry, straddles)", er ? `Run ${day(er.generated)} · walk-forward by year, 2015 → today` : "Needs step 1. Takes a few minutes.",
      es.days ? `<button class="btn ${er ? "" : "primary"}" data-gjob="/api/lab/eod/run">${er ? "Run again" : "Run"}</button>` : ""),
    step(haveMin ? (st.synthetic ? "todo" : "part") : "todo", "1-minute option history (Upstox Plus)", haveMin && !st.synthetic ? `${months}. Keeps adding where it stopped.` : "Needs your Upstox Plus login. About an hour per year.",
      `<button class="btn" data-gscroll="desk-data">${haveMin ? "Continue" : "Set up"}</button>`),
    step(res && !st.synthetic ? "done" : "todo", "Intraday backtest (Nifty / Bank Nifty strategies)", res ? `Run ${day(res.generated)} · ${esc(res.period.test_from || "")} → ${esc(res.period.to)}` : "Needs step 3. Re-run once a month.",
      haveMin ? `<button class="btn ${res ? "" : "primary"}" data-gjob="/api/lab/backtest">${res ? "Run again" : "Run"}</button>` : ""),
  ];
  // what passed, in plain words
  const out = [];
  const yr = new Date().getFullYear();
  Object.values((er && er.results) || {}).forEach((f) => {
    const o = f.oos || {}, nowPid = (f.years.find((y) => y.year === yr) || {}).pid;
    const nm = `${f.sym === "BANKNIFTY" ? "Bank Nifty" : "Nifty"} ${f.name.split(" (")[0].toLowerCase()}`;
    if (f.family === "long_straddle") { out.push(`<li class="bad">✗ <b>${esc(nm)}</b>: buying straddles lost money over 10 years. Not traded.</li>`); return; }
    if (!o.n) { out.push(`<li class="wait">… <b>${esc(nm)}</b>: no year passed the bar. Cash.</li>`); return; }
    if (o.avg > 0 && nowPid) out.push(`<li class="ok">✓ <b>${esc(nm)}</b>: made money on years it never saw: ${o.n} trades, ${rs(o.avg)} a lot per trade, ${o.win_pct}% wins, worst ${rs(o.worst)}. ${o.t >= 2 ? "Strong evidence." : o.t >= 1 ? `Evidence is moderate (t ${o.t}), so it is paper-traded first.` : `Evidence is weak (only ${o.n} trades, t ${o.t}): could be luck, paper only.`} <span class="muted">Now on the Expiry desk (paper).</span></li>`);
    else if (o.avg > 0) out.push(`<li class="wait">… <b>${esc(nm)}</b>: positive overall (${rs(o.total)}) but not chosen for ${yr}. Cash this year.</li>`);
    else out.push(`<li class="bad">✗ <b>${esc(nm)}</b>: lost on unseen years (${rs(o.total)} over ${o.n} trades). Not traded.</li>`);
  });
  ((res && res.plan) || []).forEach((p) => { if (p.active) out.push(`<li class="ok">✓ <b>${esc(p.sym)} ${esc(p.name)}</b>: passed the last 6 months; paper-traded intraday.</li>`); });
  if (res && res.plan && !res.plan.some((p) => p.active)) out.push(`<li class="wait">… <b>Intraday strategies</b>: nothing passed the last 6 months, so the intraday desk stays in cash. No trade beats a bad trade.</li>`);
  const live = ex && ex.plan && ex.plan.length ? `Expiry desk is paper-trading: ${ex.plan.map((p) => `<b>${esc(p.sym === "BANKNIFTY" ? "Bank Nifty" : p.sym)}</b> ${esc(p.name.split(" (")[0].toLowerCase())}`).join(", ")}${ex.n ? ` · ${ex.n} expiries settled, ${rs(ex.total)}` : " · first trade on the next entry day (about 15:20)"}.` : "";
  return `<div class="panel desk-guide"><h3>Your research, step by step</h3><ol class="gsteps">${steps.join("")}</ol>
    <h3 style="margin-top:16px">What passed, in plain words</h3>
    ${out.length ? `<ul class="gpass">${out.join("")}</ul>` : `<p class="muted small">Nothing tested yet. Start with step 1.</p>`}
    ${live ? `<p class="small" style="margin:8px 0 0">${live}</p>` : ""}
    <p class="small muted" style="margin:8px 0 0">"Passed" means profitable on years the test never trained on, after costs. Even then it is paper-traded for 4–8 weeks before trusting it.</p></div>`;
}

function expiryCard(x) {
  const rs = (v) => v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
  const plan = x.plan.map((p) => `<li><b>${esc(p.sym)}</b>: ${esc(p.name)} <span class="muted small">${esc(p.pid)}</span></li>`).join("") || ((x.cash || []).length ? "" : `<li class="muted">No strategy passed for this year: in cash.</li>`);
  const legs = (t) => t.legs.map((l) => `${l.q > 0 ? "Buy" : "Sell"} ${fx(l.strike)} ${l.side} @ ${l.entry}`).join(" · ");
  const open = x.open.map((t) => `<tr><td><b>${esc(t.sym)}</b><br><small class="muted">expiry ${esc(t.expiry)}</small></td><td class="small">${esc(legs(t))}</td><td class="r">${t.lots}</td><td class="r">${t.credit}</td><td class="r">${rupees(t.max_loss)}</td><td class="r">${rs(t.mtm)}</td></tr>`).join("");
  const closed = x.closed.slice(0, 12).map((t) => `<tr><td>${esc(t.sym)} <small class="muted">${esc(t.expiry)}</small></td><td class="r">${t.lots}</td><td class="r">${fx(t.settle)}</td><td class="r">${rs(t.net)}</td><td class="small muted">${esc(t.reason || "")}</td></tr>`).join("");
  return `<div class="panel"><h3>Expiry desk <span class="muted small">paper, held to expiry · the strategy that passed the 10-year test</span></h3>
    <ul class="plain small">${plan}${(x.cash || []).map((c) => `<li class="muted">${esc(c)}</li>`).join("")}</ul>
    <p class="small">Paper result so far: ${x.n ? `${rs(x.total)} over ${x.n} expiries, ${x.won} won` : "no expiry settled yet"}. Entry about 15:20 on the entry day at live bid/ask; settled at the expiry close.</p>
    ${open ? `<div class="table-wrap"><table><thead><tr><th>Open</th><th>Legs</th><th class="r">Lots</th><th class="r">Credit (pts)</th><th class="r">Max loss</th><th class="r">Now</th></tr></thead><tbody>${open}</tbody></table></div>` : ""}
    ${closed ? `<div class="table-wrap"><table><thead><tr><th>Settled</th><th class="r">Lots</th><th class="r">Index at expiry</th><th class="r">Net</th><th></th></tr></thead><tbody>${closed}</tbody></table></div>` : ""}</div>`;
}

/* Days like today: how each end-of-day strategy did when the market looked like now (lab/scenario.py) */
function likeTodayCard(x) {
  if (!x) return "";
  if (x.error) return `<div class="panel"><h3>Days like today</h3><p class="muted small">${esc(x.error)}</p></div>`;
  const rs = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
  const idx = (s) => s === "BANKNIFTY" ? "Bank Nifty" : s === "NIFTY" ? "Nifty" : esc(s);
  const pill = { good: "p-up", bad: "p-down", weak: "p-down", maybe: "", few: "" };
  const today = Object.entries(x.today || {}).map(([s, t]) => `<div class="scen-today"><b>${idx(s)} ${fk(t.spot)}</b> <span class="muted small">close ${esc(t.date)} · next expiry ${esc(t.next_expiry)}</span>
    <ul class="small">${(t.conditions || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul><span class="muted small">${t.similar_days} similar past days found</span></div>`).join("");
  const b = x.best;
  const best = b ? `<div class="scen-best"><div class="small muted">Best match for these conditions</div>
      <b>${idx(b.sym)} ${esc(b.name)}, enter ${esc(b.entry_name)}</b> <span class="pill p-up">next: ${esc(b.next_entry)}</span>
      <p class="small">On ${b.near.n} similar past days: won ${Math.round(b.near.win_pct)}%, average ${rs(b.near.avg)} per lot, worst ${rs(b.near.worst)}.
      ${b.strict && b.strict.near && b.strict.near.n ? `The strict 10-year test's own picks on those days: ${b.strict.near.n} trades, won ${Math.round(b.strict.near.win_pct)}%, average ${rs(b.strict.near.avg)}, worst ${rs(b.strict.near.worst)}.` : ""}
      The Expiry desk below paper-trades it automatically on that day.</p></div>`
    : `<div class="scen-best"><b>Nothing clearly worked in days like today.</b> <span class="small">Sitting out is a valid choice; the desks stay on paper.</span></div>`;
  const order = { good: 0, maybe: 1, few: 2, weak: 3, bad: 4 };
  const rows = (x.strategies || []).slice().sort((a, c) => (order[a.verdict] - order[c.verdict]) || ((c.near.avg || 0) - (a.near.avg || 0))).map((r) => `<tr>
      <td><b>${idx(r.sym)}</b> ${esc(r.name)}<div class="muted small">${esc(r.entry_name)} · next ${esc(r.next_entry || "–")}</div><span class="pill sc-m ${pill[r.verdict] || ""}">${esc(r.verdict_text)}</span></td>
      <td class="r">${r.near.n || 0}</td><td class="r">${r.near.n ? Math.round(r.near.win_pct) + "%" : "–"}</td><td class="r">${rs(r.near.avg)}</td><td class="r sc-x">${rs(r.near.worst)}</td>
      <td class="r sc-x">${rs(r.all.avg)}</td><td class="sc-x"><span class="pill ${pill[r.verdict] || ""}">${esc(r.verdict_text)}</span></td></tr>`).join("");
  return `<div class="panel scen"><div class="toolbar" style="margin:0 0 8px"><h3 style="margin:0">Days like today <span class="muted small">which strategy worked when the market looked like now · ${esc((x.generated || "").replace("T", " "))}</span></h3>
      <div class="btn-row" style="margin:0"><button class="btn" id="scen-run">Check again</button></div></div>
    <div class="scen-grid">${today}</div>${best}
    <details style="margin-top:10px"><summary class="small">All strategies in these conditions (${(x.strategies || []).length})</summary>
    <div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="r">Similar <span class="sc-x">days</span></th><th class="r">Won</th><th class="r">Avg / lot</th><th class="r sc-x">Worst</th><th class="r sc-x">Avg, all days</th><th class="sc-x"></th></tr></thead><tbody>${rows}</tbody></table></div></details>
    <p class="note">${(x.notes || []).map(esc).join(" ")}</p></div>`;
}

function eodCard(st, r) {
  const rs = (x) => x == null ? "–" : `<span class="${x >= 0 ? "up" : "down"}">${x >= 0 ? "+" : "−"}₹${Math.abs(Math.round(x)).toLocaleString("en-IN")}</span>`;
  const head = `<div class="toolbar" style="margin:0 0 8px"><h3 style="margin:0">10+ years, end of day <span class="muted small">NSE bhavcopy · ${st.days ? `${st.days.toLocaleString("en-IN")} days, ${esc(st.first)} → ${esc(st.last)}` : "not downloaded"}</span></h3>
    <div class="btn-row" style="margin:0"><button class="btn" id="eod-dl">${st.days ? "Update data" : "Download (free, ~45 min)"}</button>${st.days ? `<button class="btn primary" id="eod-run">Run 10-year test</button>` : ""}</div></div>`;
  if (!r) return `<div class="panel">${head}<p class="muted small">Every Nifty and Bank Nifty option contract's daily close since 2015, free from NSE. Tests hedged premium selling (iron condor / iron fly held to expiry) and buying straddles over 10+ years, walk-forward by year.</p></div>`;
  const fams = Object.values(r.results || {});
  const rows = fams.map((f) => { const o = f.oos || {}; const yrs = f.years.map((y) => `<span class="chip static small" title="${esc(y.pid || y.note || "")}">${y.year}: ${y.pid ? rs(y.pnl) : "cash"}</span>`).join(" ");
    return `<tr><td>${f.sym === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</td><td>${esc(f.name)}</td><td class="r">${o.n || 0}</td><td class="r">${o.n ? rs(o.avg) : "–"}</td><td class="r">${o.n ? rs(o.total) : "–"}</td><td class="r">${o.win_pct ?? "–"}${o.n ? "%" : ""}</td><td class="r">${o.n ? rs(o.worst) : "–"}</td></tr>
      <tr><td></td><td colspan="6" class="small">${yrs}</td></tr>`; }).join("");
  const vrp = (r.vrp || []).map((v) => `<tr><td>${v.sym === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</td><td>${v.year}</td><td class="r">${v.implied_pct}%</td><td class="r">${v.realised_pct}%</td><td class="r">${v.implied_over_pct}%</td></tr>`).join("");
  return `<div class="panel">${head}
    <p class="muted small">${(r.notes || []).map(esc).join(" ")}</p>
    <div class="table-wrap"><table><thead><tr><th>Index</th><th>Strategy</th><th class="r">Trades (out of sample)</th><th class="r">Avg / lot</th><th class="r">Total / lot</th><th class="r">Won</th><th class="r">Worst</th></tr></thead><tbody>${rows}</tbody></table></div>
    <details style="margin-top:10px"><summary class="small">Volatility risk premium by year (option-implied move vs the move that happened)</summary>
      <div class="table-wrap"><table class="mini"><thead><tr><th>Index</th><th>Year</th><th class="r">Implied move</th><th class="r">Actual move</th><th class="r">Implied was bigger</th></tr></thead><tbody>${vrp}</tbody></table></div></details></div>`;
}

function edgeCard(e) {
  if (!e) return "";
  const wf = e.walk_forward || {};
  const name = { ema_pullback: "EMA pullback", vwap_reclaim: "VWAP reclaim", cpr_breakout: "CPR breakout", pdh_break: "Prev-day high/low break", wall_fade: "OI wall rejection" };
  const rs = (x) => `<span class="${x >= 0 ? "up" : "down"}">${x >= 0 ? "+" : "−"}₹${Math.abs(Math.round(x)).toLocaleString("en-IN")}</span>`;
  const rows = (e.setups || []).map((r) => {
    const g = r.avg_rs > 0 && r.t >= 2 ? `<span class="pill p-up">edge</span>` : r.avg_rs < 0 && r.t <= -1 ? `<span class="pill p-down">losing</span>` : `<span class="pill">not proven</span>`;
    return `<tr><td>${r.sym === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</td><td>${esc(name[r.setup] || r.setup)} ${r.dir > 0 ? "long" : "short"}</td><td class="r">${r.n}</td><td class="r">${r.win_pct.toFixed(0)}%</td><td class="r">${rs(r.avg_rs)}</td><td class="r">${r.pf == null || r.pf > 99 ? "–" : r.pf.toFixed(2)}</td><td class="r">${r.t.toFixed(1)}</td><td>${g}</td></tr>`;
  }).join("");
  const t = wf.taken || {}, a = wf.all || {};
  return `<div class="panel"><h3>Chart setups on real history <span class="muted small">${esc((e.period || {}).from || "")} → ${esc((e.period || {}).to || "")} · ${(e.signals || 0).toLocaleString("en-IN")} signals</span></h3>
    <p class="muted small">Every live setup signal, replayed on 1-minute option prices: buy the at-the-money option one minute after the signal, exit at the setup's target/stop or after 90 minutes. Per lot, after all charges and slippage. "t" above 2 means clearly better than luck.</p>
    <div class="table-wrap"><table><thead><tr><th>Index</th><th>Setup</th><th class="r">Signals</th><th class="r">Won</th><th class="r">Avg / lot</th><th class="r">PF</th><th class="r">t</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <h4 style="margin-top:14px">AI signal filter ${wf.ok ? `<span class="pill p-up">on</span>` : `<span class="pill">off (not proven)</span>`}</h4>
    <p class="small">Tested month by month on months it never saw: picked ${t.n || 0} signals, avg ${t.avg_rs != null ? rs(t.avg_rs) : "–"} vs all ${a.n || 0} signals avg ${a.avg_rs != null ? rs(a.avg_rs) : "–"}${wf.auc ? ` (AUC ${wf.auc})` : ""}. ${esc(wf.why || "")}</p></div>`;
}

function deskResults(r) {
  const S = r.summary, cfgc = r.config || {};
  const pct = (v) => (v == null ? "–" : `${v.toFixed(0)}%`);
  const months = r.monthly.map((m) => `<div class="mcell ${m.pnl >= 0 ? "pos" : "neg"}" style="--a:${Math.min(1, Math.abs(m.pnl) / Math.max(1, ...r.monthly.map((x) => Math.abs(x.pnl))))}" title="${m.days} trading days, ${m.green} green"><span>${esc(m.month)}</span><b>${rupees(m.pnl)}</b></div>`).join("");
  const by = r.by_strategy.map((b) => `<tr><td><b>${esc(SNAME[b.family] || b.family)}</b> <span class="muted small">${esc(b.sym)}</span></td><td class="r">${b.n}</td><td class="r">${pct(b.win)}</td>
    <td class="r">${b.pf >= 99 ? "∞" : b.pf.toFixed(2)}</td><td class="r">${rupees(b.mean)}</td><td class="r">${b.avg_lots.toFixed(1)}</td><td class="r muted">${rupees(b.costs)}</td><td class="r"><b class="${b.sum >= 0 ? "up" : "down"}">${rupees(b.sum)}</b></td></tr>`).join("");
  const reg = r.regimes.map((g) => `<tr><td>${esc(g.bucket)}</td><td class="r">${g.trades}</td><td class="r">${pct(g.win)}</td><td class="r">${rupees(g.avg)}</td><td class="r"><b class="${g.pnl >= 0 ? "up" : "down"}">${rupees(g.pnl)}</b></td></tr>`).join("");
  const ins = r.insample.filter((x) => x.hindsight_best || x.walk_forward).map((x) => `<tr><td>${esc(SNAME[x.family] || x.family)} <span class="muted small">${esc(x.sym)}</span></td><td class="r">${rupees(x.hindsight_best)}</td><td class="r"><b class="${x.walk_forward >= 0 ? "up" : "down"}">${rupees(x.walk_forward)}</b></td></tr>`).join("");
  return `<h2>Backtest: out-of-sample results <span class="muted small">${esc(r.period.test_from || "")} to ${esc(r.period.to)} · ${r.variants} strategy variants tested · capital ₹${fk(Math.round(cfgc.capital))}</span></h2>
    <div class="kpis kpi6">
      <div class="kpi"><b class="${S.avg_day >= 0 ? "up" : "down"}">${rupees(S.avg_day)}</b><span>Average per day (all ${S.days} days)</span></div>
      <div class="kpi"><b>${pct(S.green_pct)}</b><span>Green days (of ${S.trade_days} days with trades)</span></div>
      <div class="kpi"><b>${S.pf >= 99 ? "∞" : S.pf.toFixed(2)}</b><span>Profit factor (₹ won ÷ ₹ lost)</span></div>
      <div class="kpi"><b class="down">${rupees(S.max_dd)}</b><span>Worst drawdown (${S.max_dd_pct.toFixed(1)}% of capital)</span></div>
      <div class="kpi"><b class="${S.annual_pct >= 0 ? "up" : "down"}">${S.annual_pct.toFixed(1)}%</b><span>Per year on capital</span></div>
      <div class="kpi"><b>${S.losing_months ?? "–"} / ${S.months ?? "–"}</b><span>Losing months</span></div></div>
    <p class="note">Worst day ${rupees(S.worst_day)}, best day ${rupees(S.best_day)}, Sharpe ${S.sharpe.toFixed(2)}. Each month used only strategies chosen on the 6 months before it.</p>
    <div class="split" style="margin-top:16px"><div class="chart" id="bt-eq" style="height:260px"></div><div class="chart" id="bt-hist" style="height:260px"></div></div>
    <h2>Month by month</h2><div class="months">${months}</div>
    <h2>By strategy</h2><div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="r">Trades</th><th class="r">Winners</th><th class="r">Profit factor</th><th class="r">Avg trade</th><th class="r">Avg lots</th><th class="r">Charges</th><th class="r">Total</th></tr></thead><tbody>${by || `<tr><td colspan="8" class="muted">No strategy passed the selection in any month.</td></tr>`}</tbody></table></div>
    <div class="split" style="margin-top:22px">
      <div><h2 style="margin-top:0">By market condition</h2><div class="table-wrap"><table><thead><tr><th>Condition</th><th class="r">Trades</th><th class="r">Winners</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${reg}</tbody></table></div></div>
      <div><h2 style="margin-top:0">Honesty check</h2><div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="r">Best variant in hindsight</th><th class="r">What walk-forward got</th></tr></thead><tbody>${ins}</tbody></table></div>
        <p class="note">Hindsight always looks better. A big gap means a strategy only works if you know the future.</p></div></div>
    <ul class="note" style="padding-left:18px">${(r.caveats || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
}

function drawDeskCharts(r) {
  if (!window.Plotly) return;
  const eq = r.equity;
  if ($("#bt-eq") && eq.length) Plotly.react($("#bt-eq"), [{ x: eq.map((e) => e.date), y: eq.map((e) => e.eq), type: "scatter", mode: "lines", line: { color: C.signal, width: 2 }, fill: "tozeroy", fillcolor: "rgba(61,47,168,.08)" }],
    plotLayout({ margin: { l: 64, r: 10, t: 26, b: 30 }, title: { text: "Out-of-sample equity (₹)", font: { size: 12 }, x: 0 }, yaxis: { gridcolor: "#EEF1F4", tickprefix: "₹" } }), plotCfg);
  if ($("#bt-hist") && r.hist.length) Plotly.react($("#bt-hist"), [{ type: "bar", x: r.hist.map((h) => (h.from + h.to) / 2), y: r.hist.map((h) => h.n), marker: { color: r.hist.map((h) => ((h.from + h.to) / 2 >= 0 ? C.up : C.down)) } }],
    plotLayout({ margin: { l: 40, r: 10, t: 26, b: 30 }, title: { text: "Daily P&L on days with trades (number of days)", font: { size: 12 }, x: 0 }, xaxis: { tickprefix: "₹", gridcolor: "#EEF1F4" }, bargap: 0.05 }), plotCfg);
}


/* ---------------- page: Pro chart (TradingView Lightweight Charts) */
const CH = { chart: null, sym: null, tf: "5m", tfMin: 5, series: {}, lines: [], markers: null, sig: "", data: null, bars: [], more: false, loading: false };
const CH_TOGGLES = [["scen", "Move prediction (scenarios)"], ["em", "Expected move (from options)"], ["vol", "Volume"],
  ["vwap", "VWAP + bands"], ["ema", "EMA 9/21/50"], ["st", "Supertrend"], ["prev", "Yesterday H/L/C"], ["cpr", "CPR + pivots"],
  ["or", "Opening range"], ["oi", "OI walls + max pain"], ["gamma", "Gamma levels"], ["marks", "Alerts + trades"],
  ["rsi", "RSI"], ["macd", "MACD"], ["pat", "Chart patterns"], ["swing", "Swing labels (HH / HL / LH / LL)"]];
const PAT_SHORT = { double_top: "DT", double_bottom: "DB", head_shoulders: "H&S", inv_hs: "iH&S", triangle: "Tri", flag: "Flag",
  breakout_retest: "Retest", rsi_divergence: "Div", candle_at_level: "Candle" };
// 2.29: a clean chart by default (candles + the move prediction); every layer is one click away and remembered
function chOn(k) { return LS.get("ch2_" + k, ["scen", "em"].includes(k)); }
const CH_TFS = ["1m", "3m", "5m", "15m", "30m", "1H", "1D", "1W", "1M"];
const CH_MIN = { "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60, "1D": 1440, "1W": 10080, "1M": 43200 };
const CH_VIEW = { "1m": 150, "3m": 125, "5m": 150, "15m": 130, "30m": 130, "1H": 140, "1D": 130, "1W": 156, "1M": 120 };   // bars in view at first
const chIntraday = () => CH_MIN[CH.tf] < 1440;
function chTf(v) { const old = { 1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1H" }; v = String(v || ""); return CH_TFS.includes(v) ? v : old[Number(v)] || "5m"; }

/* client-side indicators on every loaded bar (so they cover the whole scrolled history) */
const IND = {
  ema(c, n) { const k = 2 / (n + 1); const o = []; c.forEach((x, i) => o.push(i ? x * k + o[i - 1] * (1 - k) : x)); return o; },
  rsi(c, n = 14) { const o = Array(c.length).fill(null); let g = 0, l = 0;
    for (let i = 1; i < c.length; i++) { const d = c[i] - c[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
      if (i <= n) { g += up / n; l += dn / n; if (i === n) o[i] = l ? 100 - 100 / (1 + g / l) : 100; }
      else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; o[i] = l ? 100 - 100 / (1 + g / l) : 100; } } return o; },
  macd(c) { const a = IND.ema(c, 12), b = IND.ema(c, 26), m = a.map((x, i) => x - b[i]), s = IND.ema(m, 9); return { m, s, h: m.map((x, i) => x - s[i]) }; },
  st(h, l, c, n = 10, k = 3) { const tr = c.map((x, i) => i ? Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])) : h[i] - l[i]);
    const atr = []; tr.forEach((x, i) => atr.push(i < n ? (i ? (atr[i - 1] * i + x) / (i + 1) : x) : (atr[i - 1] * (n - 1) + x) / n));
    const line = [], dir = []; let fu = 0, fd = 0, d = 1;
    for (let i = 0; i < c.length; i++) { const m = (h[i] + l[i]) / 2, up = m - k * atr[i], dn = m + k * atr[i];
      fu = i && c[i - 1] > fu ? Math.max(up, fu) : up; fd = i && c[i - 1] < fd ? Math.min(dn, fd) : dn;
      if (d === 1 && c[i] < fu) d = -1; else if (d === -1 && c[i] > fd) d = 1; dir.push(d); line.push(d === 1 ? fu : fd); } return { line, dir }; },
  vwap(bars, vol) { const out = { v: [], u1: [], d1: [], u2: [], d2: [] }; let day = null, pv = 0, vv = 0, p2 = 0;
    bars.forEach((b, i) => { const dd = Math.floor((b.time) / 86400); if (dd !== day) { day = dd; pv = 0; vv = 0; p2 = 0; }
      const w = vol && vol[i] && vol[i].value > 0 ? vol[i].value : 1, tp = (b.high + b.low + b.close) / 3;
      pv += tp * w; vv += w; p2 += tp * tp * w; const m = pv / vv, sd = Math.sqrt(Math.max(0, p2 / vv - m * m));
      out.v.push(m); out.u1.push(m + sd); out.d1.push(m - sd); out.u2.push(m + 2 * sd); out.d2.push(m - 2 * sd); }); return out; },
};

async function pageChart() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  CH.sym = (params.get("s") || LS.get("lastSym", "NIFTY")).toUpperCase();
  CH.tf = chTf(params.get("tf") || LS.get("ch2_tf", "5m"));
  CH.tfMin = CH_MIN[CH.tf];
  LS.set("lastSym", CH.sym); LS.set("ch2_tf", CH.tf);
  const IDX = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
  const stocks = (await symbolList()).stocks.filter((x) => !IDX.includes(x));
  if (!IDX.includes(CH.sym) && !stocks.includes(CH.sym)) stocks.unshift(CH.sym);
  main.classList.add("wide");
  main.innerHTML = `<div class="ch-top">
      <div class="ch-sym"><select id="ch-sym" aria-label="Symbol"><optgroup label="Indices">${IDX.map((x) => `<option value="${x}" ${x === CH.sym ? "selected" : ""}>${x}</option>`).join("")}</optgroup>
        <optgroup label="F&amp;O stocks">${stocks.map((x) => `<option value="${esc(x)}" ${x === CH.sym ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup></select>
        <span id="ch-price" class="ch-price"></span><span id="ch-live" class="ch-price ch-live"></span></div>
      <div class="seg ch-tfs" role="group" aria-label="Timeframe">${CH_TFS.map((t) => `<button data-tf="${t}" aria-pressed="${CH.tf === t}">${t}</button>`).join("")}</div>
      <details class="ch-ind"><summary class="btn">Indicators &amp; levels</summary><div class="ch-menu">${CH_TOGGLES.map(([k, l]) => `<label class="check"><input type="checkbox" data-tog="${k}" ${chOn(k) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></details>
      <a class="btn" href="#/sym?s=${encodeURIComponent(CH.sym)}">Option chain</a>
      <a class="btn" href="#/timemachine?s=${encodeURIComponent(CH.sym)}">Time Machine</a>
      <span class="btn-row" style="margin:0"><button class="btn buy" id="ch-buy" title="Paper: buy the at-the-money call, 1 lot">Paper BUY</button><button class="btn sell" id="ch-sell" title="Paper: buy the at-the-money put, 1 lot">Paper SELL</button></span>
    </div>
    <div class="ch-wrap"><div id="ch-chart" class="ch-chart"></div>
      <aside class="ch-side"><div class="panel tight"><h3>Trade signals <span class="muted small">BUY / SELL marks on the chart</span></h3><div id="ch-sigs"><p class="muted small">Focus alerts on this symbol show here.</p></div></div>
        <div class="panel tight"><h3>All timeframes read <span class="muted small">5m → daily</span></h3><div id="ch-mtfread"><p class="muted small">Loading…</p></div></div>
        <div class="panel tight"><h3>Candles on every timeframe <span class="muted small">on trial</span></h3><div id="ch-cdl"><p class="muted small">Loading…</p></div></div>
        <div class="panel tight"><h3>Chart read <span class="muted small">this chart</span></h3><div id="ch-read"><p class="muted">Loading…</p></div>
        <button class="link-btn" id="ch-ai">AI read this chart</button><div id="ch-ai-out"></div></div>
        <div class="panel tight"><h3>Patterns <span class="muted small">on this chart</span></h3><div id="ch-pats"></div></div>
        ${["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"].includes(CH.sym) ? "" : `<div class="panel tight"><h3>Company <span class="muted small">results and insiders</span></h3><div id="ch-company"><p class="muted small">Loading…</p></div></div>`}
        <div class="panel tight" id="ch-scen-wrap"><h3>Scenarios <span class="muted small">next 5 / 15 / 60 min</span></h3><div id="ch-scen"><p class="muted small">Nifty and Bank Nifty.</p></div></div>
        <div class="panel tight"><h3>Setups today</h3><div id="ch-setups"></div></div>
        <div class="panel tight"><h3>Key levels</h3><div id="ch-levels"></div></div></aside></div>
    <section class="fortune panel tight" id="fortune"><div class="toolbar" style="margin:0 0 6px"><div><h2 style="margin:0">Fortune chart <span class="pill p-amber">testing</span></h2>
      <div class="small muted" id="fo-sub">Fortune's own market: every candle is what it predicted, <span class="fo-up">blue = up</span>, <span class="fo-dn">orange = down</span></div></div>
      <div class="btn-row small" style="margin:0"><div class="seg small-seg" id="fo-tfs">${FO_TFS.map((t) => `<button data-fotf="${t}" aria-pressed="false">${t}</button>`).join("")}</div>
        <label class="check"><input type="checkbox" id="fo-real"><span>Real close line</span></label></div></div>
      <div class="fo-grid"><div id="fo-chart" class="fo-chart"></div><div id="fo-side" class="small"><p class="muted">Working it out from the history…</p></div></div>
      <div id="fo-report"></div></section>
    <section class="mtf" id="mtf">
      <div class="toolbar" style="margin:14px 0 8px"><div><h2 style="margin:0">All timeframes</h2><div id="mtf-trend" class="mtf-trend small"></div></div>
        <div class="btn-row" style="margin:0;align-items:center">
          <label class="small">Test a strategy on the chart <select id="mtf-ov" aria-label="Strategy to test"><option value="">Choose…</option></select></label>
          <select id="mtf-days" aria-label="Days">${[5, 10, 20].map((d) => `<option value="${d}" ${d === 10 ? "selected" : ""}>${d} days</option>`).join("")}</select>
          <button class="btn primary" id="mtf-apply">Apply</button><button class="btn" id="mtf-remove" disabled>Remove</button>
          <details class="ch-ind"><summary class="btn">Layers</summary><div class="ch-menu">${MTF_LAYERS.map(([k, l]) => `<label class="check"><input type="checkbox" data-mtf="${k}" ${mtfOn(k) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></details>
        </div></div>
      <div id="mtf-ovres"></div>
      <div class="mtf-grid" id="mtf-grid">${MTF_TFS.map((tf) => `<div class="mtf-cell"><div class="mtf-head" id="mtf-h-${tf}"><b>${tf}</b></div><div class="mtf-chart" id="mtf-${tf}"></div></div>`).join("")}</div>
      <div class="panel tight" style="margin-top:12px"><h3>Confluence zones <span class="muted small">where levels from 2+ timeframes meet</span></h3><div id="mtf-conf"><p class="muted small">Loading…</p></div></div>
    </section>
    <p class="note">Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a> Lightweight Charts. ${""}Index VWAP counts each bar equally (indices have no volume).</p>`;
  $("#ch-sym").onchange = (e) => { location.hash = `#/chart?s=${encodeURIComponent(e.target.value)}&tf=${CH.tf}`; };
  main.querySelectorAll("[data-tf]").forEach((b) => b.onclick = () => { LS.set("ch2_tf", b.dataset.tf); location.hash = `#/chart?s=${encodeURIComponent(CH.sym)}&tf=${b.dataset.tf}`; });
  main.querySelectorAll("[data-tog]").forEach((c) => c.onchange = () => { LS.set("ch2_" + c.dataset.tog, c.checked); const r = CH.chart && CH.chart.timeScale().getVisibleLogicalRange(); buildChart(); drawBars(false); if (CH.data) drawChart(CH.data, false); if (r) CH.chart.timeScale().setVisibleLogicalRange(r); });
  const quick = async (side, btn) => {
    btn.disabled = true;
    try { const r = await post("/api/live/paper/open", { sym: CH.sym, side }); toast(`Paper ${side === "buy" ? "BUY" : "SELL"}: bought ${r.label || "the option"} · see Paper trades`); navPnl(); }
    catch (err) { toast(err.message); }
    btn.disabled = false;
  };
  $("#ch-buy").onclick = (e) => quick("buy", e.target);
  $("#ch-sell").onclick = (e) => quick("sell", e.target);
  $("#ch-ai").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Reading…";
    try { const r = await post(`/api/live/chart/${encodeURIComponent(CH.sym)}/ai`, {}); $("#ch-ai-out").innerHTML = `<div class="ai-text">${fmtAI(r.text)}</div>`; } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "AI read this chart";
  };
  if ($("#ch-company")) api(`/api/company/${encodeURIComponent(CH.sym)}`).then((c) => { const el = $("#ch-company"); if (el) el.innerHTML = companyCard(c); }).catch((e) => { const el = $("#ch-company"); if (el) el.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; });
  CH.bars = []; CH.vol = []; CH.more = false; CH.loading = false; CH.data = null;
  buildChart();
  const loadBars = async () => {
    try {
      const r = await api(`/api/live/candles/${encodeURIComponent(CH.sym)}?tf=${CH.tf}`);
      CH.bars = r.candles; CH.vol = r.volume; CH.more = r.more; CH.src = r.source; CH.hasVol = r.has_volume;
      if (!CH.chart) buildChart();
      drawBars(true);
    } catch (err) {
      $("#ch-chart").innerHTML = `<div class="empty" style="margin:20px"><h2>Loading ${esc(CH.sym)} history…</h2><p class="small muted">${esc(err.message)} Trying again in a few seconds.</p></div>`;
      CH.chart = null; L.pageTimer = setTimeout(loadBars, 6000); return false;
    }
    return true;
  };
  const payloadTf = () => Math.min(60, CH_MIN[CH.tf]);
  const overlay = async () => {                    // levels, prediction, signals, patterns, side panels
    if (state.page !== "chart") return;
    try { CH.data = await api(`/api/live/chart/${encodeURIComponent(CH.sym)}?tf=${payloadTf()}&days=${chIntraday() ? 3 : 1}`); drawChart(CH.data, false); }
    catch (err) { const el = $("#ch-read"); if (el) el.innerHTML = `<p class="muted small">${esc(err.message)}</p>`; }
  };
  const live = async () => {                       // while the scanner runs: the last bars every 2 s, the rest every 10 s
    if (state.page !== "chart") return;
    const running = L.snap && L.snap.status && L.snap.status.running;
    const streaming = CH.wsAt && Date.now() - CH.wsAt < 4000;              // 2.51: the stream moves the chart; this only checks
    if (running && !CH.ws) chStream();
    if (streaming && (CH.n2 = (CH.n2 || 0) + 1) % 5 !== 0) { L.pageTimer = setTimeout(live, 2000); return; }
    if (running && CH.chart && CH.bars.length) {
      try {
        const r = await api(`/api/live/candles/${encodeURIComponent(CH.sym)}?tf=${CH.tf}&tail=3`);
        r.candles.forEach((c, i) => { if (c.time >= CH.bars[CH.bars.length - 1].time) { CH.series.candle.update(c); const v = r.volume[i];
          if (c.time > CH.bars[CH.bars.length - 1].time) { CH.bars.push(c); CH.vol.push(v); } else { CH.bars[CH.bars.length - 1] = c; CH.vol[CH.vol.length - 1] = v; }
          if (CH.series.vol && v) CH.series.vol.update(v); } });
        CH.n = (CH.n || 0) + 1;
        if (CH.n % 5 === 0 || streaming) { drawBars(false); overlay(); }
      } catch (_) {}
    }
    L.pageTimer = setTimeout(live, running ? 2000 : 30000);
  };
  if (await loadBars()) { overlay(); L.pageTimer = setTimeout(live, 2000); }
  mtfInit();
  fortuneInit();
}

/* 2.36 Fortune chart: ranges for the forming candle and the next few, from the symbol's own history (testing) */
const FO = { chart: null, s: {}, lines: [], key: null, timer: null };
const FO_TFS = ["5m", "15m", "1H", "1D"];              // 2.40.1: Fortune draws its own market on the cascade timeframes
const FO_UP = "#2F6FDE", FO_DN = "#E07B00";           // predicted up = blue, predicted down = orange (never green / red)
function fortuneInit(tf) {
  clearTimeout(FO.timer);
  if (FO.chart) { try { FO.chart.remove(); } catch (_) {} }
  FO.tf = tf || LS.get("fo_tf", FO_TFS.includes(CH.tf) ? CH.tf : "5m");
  if (!FO_TFS.includes(FO.tf)) FO.tf = "5m";
  LS.set("fo_tf", FO.tf);
  FO.chart = null; FO.s = {}; FO.lines = []; FO.fitted = false; FO.key = `${CH.sym}|${FO.tf}`;
  document.querySelectorAll("[data-fotf]").forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.fotf === FO.tf)); b.onclick = () => fortuneInit(b.dataset.fotf); });
  const el = $("#fo-chart"); if (el) el.innerHTML = "";
  const loop = async () => {
    if (state.page !== "chart" || !$("#fo-chart") || FO.key !== `${CH.sym}|${FO.tf}`) return;
    let building = false;
    const key = FO.key;
    try { const d = await api(`/api/live/fortune/${encodeURIComponent(CH.sym)}?tf=${FO.tf}`); if (key !== FO.key) return; building = !!(d.next && d.next.status === "building"); fortuneDraw(d); }
    catch (e) { const el = $("#fo-side"); if (el) el.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; }
    const running = L.snap && L.snap.status && L.snap.status.running;      // 2.38: redraw often while the candle forms
    FO.timer = setTimeout(loop, building ? 3000 : running ? (FO.refresh || 10) * 1000 : 300000);   // 2.39: soon while it learns
  };
  loop();
}
/* 2.40.1: Fortune's own market (live/nextcandle.py): every candle is what it predicted for that candle, after its first
   small candles closed (3 x 1m for 5m, 2 x 5m for 15m, 3 x 15m for 1H, 3 x 1H for 1D); blue = up, orange = down; the
   lighter one at the right edge is the candle forming now. No real candles on this chart (they are on the chart above). */
function nextCandleSide(d, nx) {
  if (!nx || !nx.cascade) return `<p class="muted">Fortune draws its own 5m, 15m, 1H and 1D market: pick one above.</p>`;
  const sub = esc(nx.from), tf = esc(d.tf);
  if (nx.status === "building") return `<p style="margin:0"><b>Fortune ${tf}</b>: learning from ${esc(d.sym)}'s past ${tf} candles… (a few seconds)</p>`;
  if (nx.status === "error") return `<p class="muted" style="margin:0">Fortune ${tf}: ${esc(nx.error || "not available")}</p>`;
  const p = nx.now, b = nx.backtest || {};
  const dir = (x) => x.close >= x.open ? `<b class="fo-up">Up</b>` : `<b class="fo-dn">Down</b>`;
  const running = !!(L.snap && L.snap.status && L.snap.status.running);
  const nowHTML = p ? `<p style="margin:0 0 6px">Candle forming now (${hm(p.time - 19800)}): ${dir(p)} · close ~<b>${fx(p.close)}</b> <span class="muted">(likely ${fx(p.close_lo)} – ${fx(p.close_hi)})</span> · ${p.p_green}% chance up</p>
      <p class="muted" style="margin:0 0 6px">Made after ${p.k} of ${p.n_sub} closed ${sub} candles, from the ${p.similar} most similar past moments. It updates with every ${sub} candle.</p>`
    : `<p class="muted" style="margin:0 0 6px">${running ? `The next ${tf} candle appears after ${nx.after} of its ${sub} candles close.` : "Market closed: the next one comes after the open. On the chart: what it predicted before."}</p>`;
  const honest = b.calls ? `<p class="small" style="margin:0 0 6px">How good: on ${b.test_days} days it never saw it called up / down right <b>${b.hit_pct}%</b> of the time; the plain rule "the candle keeps the colour it has so far" was right <b>${b.mom_pct}%</b>. ${b.edge ? "So it adds a real edge." : "So far it adds nothing beyond what the chart already shows."}</p>` : "";
  return `<div class="fo-next"><p style="margin:0 0 6px"><b>Fortune ${tf}</b> <span class="muted">· each candle predicted after ${nx.after} closed ${sub} candles</span></p>${nowHTML}${honest}
    <p class="small muted" style="margin:0"><span class="fo-up">■</span> blue = it expected the candle to close up · <span class="fo-dn">■</span> orange = to close down · the lighter one at the right edge = the candle forming now${LS.get("fo_real", false) ? " · grey line = the real close" : ""}.</p>
    <details class="small muted" style="margin:6px 0 0"><summary>Facts it uses</summary>${(nx.factors || []).map(esc).join(", ")}${nx.market_ref ? ` (market = ${esc(nx.market_ref)})` : ""}. Recorded with every prediction for a later test: ${(nx.recorded || []).map(esc).join(", ")}.</details></div>`;
}
function nextCandleReport(nx) {
  if (!nx || !nx.cascade || nx.status !== "ready") return "";
  const b = nx.backtest || {}, lv = nx.live || {}, td = nx.today || {};
  const col = (x) => !x || !x.n ? `<td class="muted">–</td><td class="muted">–</td><td class="muted">–</td><td class="muted">–</td>`
    : `<td class="r">${x.calls ? `<b>${x.hit_pct}%</b> <small class="muted">of ${x.calls}</small>` : "–"}</td><td class="r">${x.mom_pct != null ? x.mom_pct + "%" : "–"}</td>
       <td class="r">${x.skill_pct != null ? `${x.skill_pct >= 0 ? "+" : ""}${x.skill_pct}%` : "–"}</td><td class="r">${x.in68_pct != null ? x.in68_pct + "%" : "–"}</td>`;
  const rec = (nx.recent || []).slice(0, 10).map((r) => { const a = r.actual || {}, p = r.pred || {};
    return `<tr><td>${new Date((r.time - 19800) * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</td>
      <td class="r">${r.k}/${p.n_sub}</td><td class="r">${fx(p.close)} <small class="muted">${p.p_green}% green</small></td><td class="r">${fx(a.close)} <small class="${a.close > a.open ? "up" : "down"}">${a.close > a.open ? "green" : "red"}</small></td>
      <td>${r.right == null ? `<span class="muted small">no call</span>` : r.right ? `<span class="pill p-up">Right</span>` : `<span class="pill p-down">Wrong</span>`} ${r.in68 ? `<small class="muted">in box</small>` : `<small class="down">outside box</small>`}</td></tr>`; }).join("");
  return `<div class="fo-report"><h3 style="margin:10px 0 4px">Smart report <span class="muted small">next ${esc(nx.tf)} candle after ${nx.after} × ${esc(nx.from)}</span></h3>
    <ul class="plain small" style="margin:0 0 8px">${(nx.report || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <div class="table-wrap"><table class="mini"><thead><tr><th></th><th class="r">Colour right</th><th class="r">"Keeps its colour"</th><th class="r">Close vs "stays put"</th><th class="r">Close in 68% box</th></tr></thead><tbody>
      <tr><td>Days it never saw <small class="muted">(${b.test_days || 0})</small></td>${col(b)}</tr><tr><td>Live, all days</td>${col(lv)}</tr><tr><td>Live, today</td>${col(td)}</tr></tbody></table></div>
    ${rec ? `<details><summary class="small muted">Latest graded predictions</summary><div class="table-wrap"><table class="mini"><thead><tr><th>Candle</th><th class="r">At</th><th class="r">Predicted</th><th class="r">Real</th><th></th></tr></thead><tbody>${rec}</tbody></table></div></details>` : ""}
    <p class="small muted" style="margin:4px 0 0">"Keeps its colour" = the plain rule that the candle ends the colour it has so far: the bar the model must beat. Pool: ${(nx.pool || 0).toLocaleString("en-IN")} past moments from ${(nx.history_rows || 0).toLocaleString("en-IN")} ${esc(nx.from)} bars. Information only: never a signal.</p></div>`;
}
/* 2.41 candle forecast (live/candlecast.py): for the candle forming now, its colour with a strength (PAKKA = the strength
   from which calls were 90% right on validation days; the numbers shown are from holdout days it never saw), its shape,
   multi-candle patterns, a 90% close zone and the most similar past charts, next to the plain rule "it keeps its colour". */
const CAST_PAT = { engulfing: "Engulfing", harami: "Harami", inside_bar: "Inside bar", outside_bar: "Outside bar", star: "Morning / evening star",
  tweezer: "Tweezer", pin_bar: "Pin bar", hammer: "Hammer / shooting star" };
function castSide(c, d) {
  if (!c || !c.on) return "";
  const head = `<p style="margin:0 0 4px"><b>Candle forecast</b> <span class="pill p-amber">trial</span></p>`;
  if (c.status === "symbol") return `<div class="fo-cast">${head}<p class="muted small" style="margin:0">${esc(c.message || "")}</p></div>`;
  if (c.status !== "ready") return `<div class="fo-cast">${head}<p class="muted small" style="margin:0">${c.training || c.status === "training"
    ? "Learning from 5 years of 1-minute candles in the background (about 15 minutes, the first time only)…" : c.status === "error" ? esc(c.error || "Not available.") : "Starts learning in a moment…"}</p></div>`;
  const p = c.now, rep = c.report || {};
  const running = !!(L.snap && L.snap.status && L.snap.status.running);
  if (!p) return `<div class="fo-cast">${head}<p class="muted small" style="margin:0">${running ? `Starts when the first ${esc(rep.from || "")} candle of the next ${esc(d.tf)} candle closes.`
    : "Market closed: the forecast starts with the first candle after the open. How good it was: in the table under the chart."}</p></div>`;
  const st = (rep.stages || []).find((s) => s.k === p.k) || {};
  const arrow = p.dir === "up" ? `<b class="fo-up">▲ Up (green)</b>` : `<b class="fo-dn">▼ Down (red)</b>`;
  const badge = p.pakka ? `<span class="pill p-up">PAKKA</span>` : `<span class="pill">Watching</span>`;
  const band = p.band ? `At this strength, unseen days were right <b>${p.band.right}%</b> (${(p.band.n || 0).toLocaleString("en-IN")} candles).` : "";
  const pk = st.bar != null ? `PAKKA from ${Math.round(st.bar * 100)}%: ${st.right}% right on ${st.cover}% of candles.` : "No 90% level at this stage.";
  const simple = p.simple == null ? "" : p.agrees ? `The plain rule (it keeps the colour it has so far) says the same.`
    : `<b class="down">Against the plain rule</b>: such calls were right ${st.flips_right != null ? st.flips_right + "%" : "–"} (${st.flips || 0} on unseen days).`;
  const shape = (p.shape || []).map((x) => `${esc(x.name)} <b>${x.pct}%</b>`).join(" · ");
  const pats = (p.patterns || []).map((x) => `<span class="pill">${esc(x.name)}</span> <small class="muted">${x.hold != null ? `${x.hold}% right when called` : ""}${x.already ? " · already formed" : ""}</small>`).join(" ");
  const when = (t) => new Date((t - 19800) * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const sim = p.similar ? `${p.similar.n} most similar charts since ${esc((rep.first_day || "").slice(0, 4))}: <b>${p.similar.green_pct}% ended green</b>, close ~${fx(p.similar.close)} (${p.similar.agrees ? "agree" : "disagree"}).`
    + ((p.similar.closest || []).length ? ` Closest: ${p.similar.closest.map((x) => `${x.sym ? esc(x.sym) + " " : ""}${when(x.time)} <span class="${x.green ? "up" : "down"}">${x.green ? "green" : "red"}</span>`).join(", ")}.` : "") : "";
  return `<div class="fo-cast">${head}
    <p style="margin:0 0 4px">${badge} ${arrow} · strength <b>${p.strength}%</b> <span class="muted small">after ${p.k} of ${p.n_sub} ${esc(rep.from || "")} candles</span></p>
    <p class="small" style="margin:0 0 4px">${band} ${pk}</p>
    ${simple ? `<p class="small muted" style="margin:0 0 4px">${simple}</p>` : ""}
    ${shape ? `<p class="small" style="margin:0 0 4px">Shape: ${shape} <span class="muted">(so far: ${esc(p.shape_so_far || "")})</span></p>` : ""}
    ${pats ? `<p class="small" style="margin:0 0 4px">Pattern: ${pats}</p>` : ""}
    ${p.zone ? `<p class="small" style="margin:0 0 4px">90% close zone <b>${fx(p.zone[0])} – ${fx(p.zone[1])}</b></p>` : ""}
    ${sim ? `<p class="small muted" style="margin:0">${sim}</p>` : ""}</div>`;
}
function castReport(c) {
  if (!c || c.status !== "ready" || !c.report) return "";
  const r = c.report, lv = {};
  (c.live || []).forEach((x) => { lv[x.k] = x; });
  const pc = (v) => v == null ? "–" : `${v}%`;
  const rows = (r.stages || []).map((s) => { const l = lv[s.k];
    const pats = Object.entries(s.patterns || {}).filter(([, v]) => v.bar != null && (v.calls || 0) >= 20).map(([k, v]) => `${esc(CAST_PAT[k] || k)} ${pc(v.right)}`).join(", ");
    return `<tr><td>${esc(s.label)}</td><td class="r">${s.bar != null ? `<b>${pc(s.right)}</b> <small class="muted">on ${pc(s.cover)}</small>` : "–"}</td><td class="r">${s.bar != null ? pc(s.simple_same) : "–"}</td>
      <td class="r">${pc(s.all_right)} <small class="muted">vs ${pc(s.simple_all)}</small></td><td class="r">${s.shape ? `${pc(s.shape.all_right)} <small class="muted">vs ${pc(s.shape.plain_all)}</small>` : "–"}</td>
      <td class="r">${pc((s.zone || {}).held)}</td><td class="small">${pats || "–"}</td>
      <td class="r">${l && l.calls ? `<b>${pc(l.right)}</b> <small class="muted">of ${l.calls} · plain ${pc(l.simple_same)}</small>` : l ? `<small class="muted">${l.n} graded</small>` : "–"}</td></tr>`; }).join("");
  return `<div class="fo-report"><h3 style="margin:12px 0 4px">Candle forecast: how good <span class="pill p-amber">trial</span></h3>
    <p class="small muted" style="margin:0 0 6px">Learned from ${esc(r.first_day || "")} → ${esc(r.train_to || "")}; every number here is from the ${r.hold_days || 0} days after ${esc(r.hold_from || "")} that no model or level ever saw. Counted only for calls made before the candle closed.</p>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Stage</th><th class="r">PAKKA right <small>(share of candles)</small></th><th class="r">Plain rule, same candles</th><th class="r">Every candle vs plain</th>
      <th class="r">Shape right vs "so far"</th><th class="r">90% zone held</th><th>Patterns: right when called</th><th class="r">Live PAKKA</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin:4px 0 0">PAKKA = the strength from which calls were 90% right on validation days (patterns too: shown when they reached 90% there; the number is what they did on the unseen days). Plain rule = the candle ends the colour it has so far: where both columns match, the chart already showed it; the edge is the gap between them. Information only: never a signal.</p></div>`;
}
function fortuneDraw(d) {
  const el = $("#fo-chart"), side = $("#fo-side"), rep = $("#fo-report");
  if (!el || !side) return;
  FO.last = d; FO.refresh = d.refresh_s || 10;
  const nx = d.next || {};
  side.innerHTML = castSide(nx.cast, d) + nextCandleSide(d, nx);
  if (rep) rep.innerHTML = nextCandleReport(nx) + castReport(nx.cast);
  if (!nx.cascade || nx.status !== "ready") { if (!FO.chart) el.innerHTML = `<p class="muted small" style="padding:12px">${nx.status === "building" ? "Fortune is reading the history…" : ""}</p>`; return; }
  const LW = window.LightweightCharts;
  if (!LW) return;
  const showReal = LS.get("fo_real", false);
  const rb = $("#fo-real"); if (rb) { rb.checked = showReal; rb.onchange = () => { LS.set("fo_real", rb.checked); if (FO.last) fortuneDraw(FO.last); }; }
  if (!FO.chart) {
    el.innerHTML = "";
    FO.chart = LW.createChart(el, { autoSize: true, layout: { background: { color: "#ffffff" }, textColor: "#16212C", fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: "#F4F6F8" }, horzLines: { color: "#F4F6F8" } }, rightPriceScale: { borderColor: "#D5DCE3" },
      timeScale: { borderColor: "#D5DCE3", timeVisible: FO.tf !== "1D", secondsVisible: false, rightOffset: 3 }, crosshair: { mode: 0 } });
    FO.s.r = FO.chart.addSeries(LW.LineSeries, { color: "rgba(94,107,120,.8)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    FO.s.p = FO.chart.addSeries(LW.CandlestickSeries, { upColor: FO_UP, downColor: FO_DN, borderVisible: false, wickUpColor: FO_UP, wickDownColor: FO_DN,
      priceLineVisible: false, lastValueVisible: true });
    FO.mk = LW.createSeriesMarkers ? LW.createSeriesMarkers(FO.s.p, []) : null;
  }
  const all = (nx.past || []).concat(nx.now ? [{ ...nx.now, now: true }] : []).sort((a, b) => a.time - b.time);
  const preds = all.filter((x, i) => i === all.length - 1 || x.time !== all[i + 1].time).slice(-200);
  FO.s.p.setData(preds.map((x) => x.now
    ? { time: x.time, open: x.open, high: x.high, low: x.low, close: x.close, color: x.close >= x.open ? "rgba(47,111,222,.45)" : "rgba(224,123,0,.45)",
        wickColor: x.close >= x.open ? "rgba(47,111,222,.6)" : "rgba(224,123,0,.6)", borderColor: x.close >= x.open ? FO_UP : FO_DN }
    : { time: x.time, open: x.open, high: x.high, low: x.low, close: x.close }));
  FO.s.r.setData(showReal ? preds.filter((x) => x.actual_close != null).map((x) => ({ time: x.time, value: x.actual_close })) : []);
  FO.lines.forEach((pl) => { try { FO.s.p.removePriceLine(pl); } catch (_) {} });
  FO.lines = [];
  if (nx.now) ["close_hi", "close_lo"].forEach((k) => FO.lines.push(FO.s.p.createPriceLine({ price: nx.now[k], color: "#5E6B78", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "likely close" })));
  const cn = nx.cast && nx.cast.now, onBar = cn && nx.now && cn.time === nx.now.time;       // 2.41: the forecast on the forming candle
  if (onBar && cn.zone) cn.zone.forEach((v) => FO.lines.push(FO.s.p.createPriceLine({ price: v, color: "#9AA6B2", lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: "90%" })));
  if (FO.mk) FO.mk.setMarkers(onBar ? [{ time: cn.time, position: cn.dir === "up" ? "aboveBar" : "belowBar", color: cn.dir === "up" ? FO_UP : FO_DN,
    shape: cn.dir === "up" ? "arrowUp" : "arrowDown", text: `${cn.pakka ? "PAKKA " : ""}${Math.round(cn.strength)}%` }] : []);
  if (!FO.fitted && preds.length) { FO.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, preds.length - 40), to: preds.length + 2 }); FO.fitted = true; }
}

/* scroll-back: near the left edge, load the bars before the first one and keep the view where it was */
async function loadOlder() {
  if (CH.loading || !CH.more || !CH.bars.length) return;
  CH.loading = true;
  try {
    const r = await api(`/api/live/candles/${encodeURIComponent(CH.sym)}?tf=${CH.tf}&before=${CH.bars[0].time - 19800}`);
    const old = r.candles.filter((c) => c.time < CH.bars[0].time);
    if (old.length) {
      const range = CH.chart.timeScale().getVisibleLogicalRange();
      CH.bars = old.concat(CH.bars); CH.vol = r.volume.filter((v) => v.time < CH.vol[0].time).concat(CH.vol);
      drawBars(false);
      if (range) CH.chart.timeScale().setVisibleLogicalRange({ from: range.from + old.length, to: range.to + old.length });
    }
    CH.more = r.more && old.length > 0;
  } catch (_) { CH.more = false; }
  CH.loading = false;
}


/* ---------------- all timeframes (live/mtf.py): 1D / 75m / 1H / 15m / 5m, markings, higher-timeframe levels,
   confluence, trend strip, and strategy overlays (apply / remove) */
const MTF_TFS = ["5m", "15m", "1H", "75m", "1D"];       // small to large (2.29)
const MTF_LAYERS = [["own", "Levels of each timeframe"], ["htf", "Higher-timeframe levels on lower charts"], ["conf", "Confluence zones"],
  ["ind", "Moving averages, VWAP, Supertrend"], ["marks", "Swings and patterns"]];
const mtfOn = (k) => LS.get("mtf_" + k, k !== "htf" ? true : true);
const MTF_COL = { "1D": "#3D2FA8", "75m": "#9A6A12", "1H": "#B04ACF", "15m": "#0F7C8C", "5m": "#5E6B78" };
const MTF_SER = { ema20: "#3D2FA8", ema50: "#9A6A12", ema200: "#5E6B78", vwap: "#0F7C8C", u1: "rgba(15,124,140,.35)", d1: "rgba(15,124,140,.35)",
  ema9: "#1E8A5A", ema21: "#C23B32", st_up: "#1E8A5A", st_dn: "#C23B32" };
const MT = { charts: {}, data: null, ov: null, timer: null };

async function mtfInit() {
  Object.values(MT.charts).forEach((c) => { try { c.chart.remove(); } catch (_) {} });
  MT.charts = {}; MT.ov = null; CH.overlayMarks = null; clearTimeout(MT.timer);
  const sel = $("#mtf-ov");
  try { const r = await api(`/api/live/mtf/${encodeURIComponent(CH.sym)}/overlays`);
    sel.innerHTML = `<option value="">Choose…</option>` + r.overlays.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("");
    const keep = LS.get("mtf_ov_sel", ""); if (keep && r.overlays.some((o) => o.id === keep)) sel.value = keep; } catch (_) {}
  sel.onchange = () => LS.set("mtf_ov_sel", sel.value);
  const dsel = $("#mtf-days"); if (dsel) { dsel.value = String(LS.get("mtf_days", 10)); dsel.onchange = () => LS.set("mtf_days", Number(dsel.value)); }
  main.querySelectorAll("[data-mtf]").forEach((c) => c.onchange = () => { LS.set("mtf_" + c.dataset.mtf, c.checked); drawMTF(MT.data); });
  $("#mtf-apply").onclick = () => applyOverlay();
  $("#mtf-remove").onclick = () => { MT.ov = null; CH.overlayMarks = null; $("#mtf-ovres").innerHTML = ""; $("#mtf-remove").disabled = true; drawMTF(MT.data); if (CH.data) drawChart(CH.data, false); };
  const loop = async () => {
    if (state.page !== "chart" || !$("#mtf-grid")) return;
    try { MT.data = await api(`/api/live/mtf/${encodeURIComponent(CH.sym)}`); drawMTF(MT.data); }
    catch (e) { const g = $("#mtf-conf"); if (g) g.innerHTML = `<p class="small muted">${esc(e.message)}</p>`; }
    candlesNow(CH.sym);
    MT.timer = setTimeout(loop, 60000);                      // higher timeframes: refresh as candles close
  };
  loop();
}

/* one read of all five timeframes (live/mtf.py report): verdict, each timeframe small to large, what to watch */
/* 2.31: the latest closed candle's patterns on every timeframe, and how the forming one looks so far (not a signal) */
async function candlesNow(sym) {
  const el = $("#ch-cdl"); if (!el) return;
  let v; try { v = await api(`/api/live/candles_now/${encodeURIComponent(sym)}`); } catch (e) { el.innerHTML = `<p class="small muted">${esc(e.message)}</p>`; return; }
  if (!$("#ch-cdl")) return;
  const pat = (xs) => (xs || []).length ? xs.map((x) => `<span class="${x.d > 0 && !["doji", "inside_bar", "nr7"].includes(x.pattern) ? "up" : x.d < 0 ? "down" : "muted"}">${esc(x.name)}${x.d > 0 && !["doji", "inside_bar", "nr7"].includes(x.pattern) ? " ▲" : x.d < 0 ? " ▼" : ""}</span>`).join(", ") : `<span class="muted">none</span>`;
  el.innerHTML = (v.rows || []).length ? `<table class="mini"><tbody>${v.rows.map((r) => `<tr><td><b>${esc(r.tf)}</b></td><td class="small">Last: ${pat(r.last)}${r.forming ? `<br><span class="muted">Forming (${r.mins_left} min left): </span>${pat(r.forming)}` : ""}</td></tr>`).join("")}</tbody></table>
    <p class="small muted" style="margin:6px 0 0">${esc(v.note || "")} Every pattern is recorded and paper-tested on the test bench; none is an alert until its record earns it.</p>`
    : `<p class="small muted">${esc(v.note || "Starts with the live scanner.")}</p>`;
}

function mtfReport(r) {
  const el = $("#ch-mtfread"); if (!el) return;
  if (!r || !r.verdict) { el.innerHTML = `<p class="muted small">${esc((r && r.error) || "Builds when the timeframes have loaded.")}</p>`; return; }
  const col = r.bias >= 25 ? "up" : r.bias <= -25 ? "down" : "";
  el.innerHTML = `<p style="margin:0 0 6px"><b class="${col}">${esc(r.verdict)}</b> <span class="pill ${col === "up" ? "p-up" : col === "down" ? "p-down" : ""}" title="Weighted by timeframe: daily counts most">bias ${r.bias > 0 ? "+" : ""}${r.bias}</span></p>
    <ul class="plain small">${(r.per_tf || []).map((x, i) => `<li><b>${esc(x.tf)}</b> <span class="${x.trend === "up" ? "up" : x.trend === "down" ? "down" : "muted"}">${x.trend === "up" ? "▲ up" : x.trend === "down" ? "▼ down" : "▬ sideways"}</span> · RSI ${Math.round(x.rsi)} <span class="muted">(${esc(x.momentum)})</span>${x.structure ? ` · ${esc(x.structure)}` : ""}
      ${x.res || x.sup ? `<br><span class="muted">${x.res ? `R ${fx(x.res.price)} ${esc(x.res.name)}` : ""}${x.res && x.sup ? " · " : ""}${x.sup ? `S ${fx(x.sup.price)} ${esc(x.sup.name)}` : ""}</span>` : ""}</li>`).join("")}</ul>
    ${(r.watch || []).length ? `<p class="small" style="margin:6px 0 0"><b>Watch:</b> ${r.watch.map(esc).join(" ")}</p>` : ""}
    <p class="muted small" style="margin:4px 0 0">${esc(r.note || "")}</p>`;
}

function mtfChart(tf) {
  if (MT.charts[tf]) return MT.charts[tf];
  const el = $("#mtf-" + tf); if (!el || !window.LightweightCharts) return null;
  const LW = window.LightweightCharts;
  const chart = LW.createChart(el, { autoSize: true, layout: { background: { color: "#ffffff" }, textColor: "#16212C", fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: "#F4F6F8" }, horzLines: { color: "#F4F6F8" } }, rightPriceScale: { borderColor: "#D5DCE3" },
    timeScale: { borderColor: "#D5DCE3", timeVisible: tf !== "1D", secondsVisible: false, rightOffset: 4 }, crosshair: { mode: 0 } });
  const candle = chart.addSeries(LW.CandlestickSeries, { upColor: "#1E8A5A", downColor: "#C23B32", borderVisible: false, wickUpColor: "#1E8A5A", wickDownColor: "#C23B32" });
  MT.charts[tf] = { chart, candle, lines: {}, plines: [], markers: LW.createSeriesMarkers(candle, []), first: true };
  return MT.charts[tf];
}

function drawMTF(d) {
  if (!d || !$("#mtf-grid")) return;
  const LW = window.LightweightCharts;
  mtfReport(d.report);
  $("#mtf-trend").innerHTML = `${(d.trend || []).slice().sort((a, b) => MTF_TFS.indexOf(a.tf) - MTF_TFS.indexOf(b.tf)).map((t) => `<span class="pill ${t.trend === "up" ? "p-up" : t.trend === "down" ? "p-down" : ""}" title="${esc(t.structure || "")}">${t.tf} ${t.trend === "up" ? "▲" : t.trend === "down" ? "▼" : "▬"}</span>`).join(" ")}
    ${d.summary ? `<b style="margin-left:6px">${esc(d.summary)}</b>${d.aligned ? ` <span class="pill p-amber">all aligned</span>` : ""}` : ""}`;
  const conf = mtfOn("conf") ? (d.confluence || []) : [];
  MTF_TFS.forEach((tf) => {
    const p = (d.panels || {})[tf]; const head = $("#mtf-h-" + tf);
    if (!p) return;
    if (head) head.innerHTML = `<b>${tf}</b> <span class="small muted">${esc(p.label)}</span> ${p.trend ? `<span class="pill ${p.trend === "up" ? "p-up" : p.trend === "down" ? "p-down" : ""}">${p.trend}</span>` : ""}${p.structure ? ` <span class="small muted">${esc(p.structure)}</span>` : ""}${p.empty ? ` <span class="small down">${esc(p.empty)}</span>` : ""}${(p.failed || []).length ? ` <span class="small muted" title="${esc(p.failed.join("; "))}">· ${p.failed.length} layer(s) skipped</span>` : ""}`;
    const C = mtfChart(tf); if (!C || !p.candles.length) return;
    C.candle.setData(p.candles);
    const want = mtfOn("ind") ? p.series || {} : {};
    Object.keys(C.lines).forEach((k) => { if (!want[k]) { C.chart.removeSeries(C.lines[k]); delete C.lines[k]; } });
    Object.entries(want).forEach(([k, pts]) => {
      if (!C.lines[k]) C.lines[k] = C.chart.addSeries(LW.LineSeries, { color: MTF_SER[k] || "#5E6B78", lineWidth: k.startsWith("u") || k.startsWith("d") ? 1 : 2, lineStyle: k === "u1" || k === "d1" ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      C.lines[k].setData(pts);
    });
    C.plines.forEach((pl) => C.candle.removePriceLine(pl)); C.plines = [];
    const add = (price, color, style, width, title) => C.plines.push(C.candle.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title }));
    // less clutter: only levels near the price, nearest first; a confluence zone replaces the levels inside it
    const spot = d.spot || p.last, near = { "1D": 0.08, "75m": 0.04, "1H": 0.04, "15m": 0.025, "5m": 0.015 }[tf] * spot;
    const zones = conf.filter((z) => Math.abs(z.mid - spot) <= near).slice(0, 3);
    const inZone = (x) => zones.some((z) => x >= z.lo - spot * 0.0005 && x <= z.hi + spot * 0.0005);
    const pick = (arr, n) => arr.filter((l) => Math.abs(l.price - spot) <= near && !inZone(l.price)).sort((a, b) => Math.abs(a.price - spot) - Math.abs(b.price - spot)).slice(0, n);
    if (mtfOn("own")) pick(p.levels || [], 6).forEach((l) => add(l.price, MTF_COL[tf], l.group === "swing" ? 1 : 0, 1, l.name));
    if (mtfOn("htf")) pick(p.htf_levels || [], 5).forEach((l) => add(l.price, MTF_COL[l.tf] || "#5E6B78", 2, 1, l.name));
    zones.forEach((z) => add(z.mid, "rgba(224,123,0,.6)", 0, 3, `Zone ×${z.strength}`));
    let mk = mtfOn("marks") ? (p.markers || []).slice() : [];
    if (MT.ov && tf === "5m") mk = mk.concat(MT.ov.marks);
    if (MT.ov && tf === "5m" && MT.ov.last) { add(MT.ov.last.stop, "#C23B32", 2, 1, "Test stop"); add(MT.ov.last.target, "#1E8A5A", 2, 1, "Test target"); }
    mk.sort((a, b) => a.time - b.time);
    C.markers.setMarkers(mk);
    if (C.first) {                          // each timeframe opens at its own zoom: small ones close in, big ones wide
      const n = p.candles.length, want = { "5m": 100, "15m": 90, "1H": 90, "75m": 80, "1D": 120 }[tf] || 100;
      C.chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, n - want), to: n + 3 }); C.first = false; }
  });
  $("#mtf-conf").innerHTML = (d.confluence || []).length
    ? `<table class="mini"><thead><tr><th>Zone</th><th class="r">From price</th><th>Timeframes</th><th>Levels</th></tr></thead><tbody>${d.confluence.map((z) => `<tr><td><b>${fk(z.lo)}${z.hi !== z.lo ? "–" + fk(z.hi) : ""}</b></td><td class="r">${z.dist_pct > 0 ? "+" : ""}${z.dist_pct}%</td><td>${z.tfs.join(", ")}</td><td class="small muted">${esc(z.names.join(", "))}</td></tr>`).join("")}</tbody></table>`
    : `<p class="small muted" style="margin:0">No price where 2+ timeframes agree right now.</p>`;
}

async function applyOverlay() {
  const id = $("#mtf-ov").value; if (!id) { toast("Choose a strategy to test"); return; }
  const b = $("#mtf-apply"); b.disabled = true; b.textContent = "Testing…";
  try {
    const r = await api(`/api/live/mtf/${encodeURIComponent(CH.sym)}/overlay?id=${encodeURIComponent(id)}&days=${$("#mtf-days").value}`);
    const sigs = r.signals || [];
    const res = (s) => s.result === "target" ? "#1E8A5A" : s.result === "stop" ? "#C23B32" : "#9A6A12";
    const marks = [];
    sigs.forEach((s) => {
      marks.push({ time: s.time, position: s.dir === "up" ? "belowBar" : "aboveBar", color: s.dir === "up" ? "#1E8A5A" : "#C23B32", shape: s.dir === "up" ? "arrowUp" : "arrowDown", text: "TEST" });
      marks.push({ time: s.exit_time, position: "inBar", color: res(s), shape: "circle", text: `${s.r > 0 ? "+" : ""}${s.r}R` });
    });
    MT.ov = { id, marks, last: sigs[sigs.length - 1] || null };
    CH.overlayMarks = marks;                      // placed on the bar they fall in, on any timeframe (drawChart)
    const st = r.stats || {};
    $("#mtf-ovres").innerHTML = `<div class="panel tight" style="margin-bottom:10px"><h3>${esc($("#mtf-ov").selectedOptions[0].textContent)} <span class="muted small">last ${st.days || 0} sessions on ${esc(CH.sym)}</span></h3>
      ${r.error ? `<p class="small down">${esc(r.error)}</p>` : ""}
      <p style="margin:0">${st.n || 0} signals · <span class="up">${st.wins || 0} won</span> · <span class="down">${st.losses || 0} lost</span>${st.avg_r != null ? ` · average <b>${st.avg_r > 0 ? "+" : ""}${st.avg_r}R</b>` : ""}</p>
      ${sigs.length ? `<div class="table-wrap" style="margin-top:6px;max-height:220px;overflow:auto"><table class="mini"><thead><tr><th>When</th><th></th><th class="r">Entry</th><th class="r">Stop</th><th class="r">Target</th><th>Result</th><th class="r">R</th></tr></thead><tbody>
        ${sigs.slice().reverse().map((s) => `<tr><td class="small">${new Date((s.time - 19800) * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })}</td><td>${arrow(s.dir)}</td><td class="r">${fk(s.entry)}</td><td class="r">${fk(s.stop)}</td><td class="r">${fk(s.target)}</td><td>${s.result}</td><td class="r ${s.r > 0 ? "up" : s.r < 0 ? "down" : ""}">${s.r}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <p class="small muted" style="margin:6px 0 0">${esc(r.note || "")} Shown on the 5-minute panel and the main chart; <b>Remove</b> clears it.</p></div>`;
    $("#mtf-remove").disabled = false;
    drawMTF(MT.data); if (CH.data) drawChart(CH.data, false);
  } catch (e) { toast(e.message); }
  b.disabled = false; b.textContent = "Apply";
}

function buildChart() {
  const el = $("#ch-chart"); if (!el || !window.LightweightCharts) return;
  if (CH.chart) { CH.chart.remove(); CH.chart = null; }
  el.innerHTML = "";
  const LW = window.LightweightCharts;
  const intra = chIntraday();
  const chart = LW.createChart(el, { autoSize: true, layout: { background: { color: "#ffffff" }, textColor: "#16212C", fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 12, panes: { separatorColor: "#D5DCE3" } },
    grid: { vertLines: { color: "#F4F6F8" }, horzLines: { color: "#F4F6F8" } }, rightPriceScale: { borderColor: "#D5DCE3" },
    timeScale: { borderColor: "#D5DCE3", timeVisible: intra, secondsVisible: false, rightOffset: 6 }, crosshair: { mode: 0 } });
  const S = {};
  S.candle = chart.addSeries(LW.CandlestickSeries, { upColor: "#1E8A5A", downColor: "#C23B32", borderVisible: false, wickUpColor: "#1E8A5A", wickDownColor: "#C23B32" });
  const ln = (color, width = 1, style = 0, pane = 0, extra = {}) => chart.addSeries(LW.LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra }, pane);
  if (chOn("vwap") && intra) { S.vwap = ln("#3D2FA8", 2); S.u1 = ln("rgba(61,47,168,.45)", 1, 2); S.d1 = ln("rgba(61,47,168,.45)", 1, 2); S.u2 = ln("rgba(61,47,168,.3)", 1, 1); S.d2 = ln("rgba(61,47,168,.3)", 1, 1); }
  if (chOn("ema")) { S.ema9 = ln("#E07B00", 1); S.ema21 = ln("#0A84C1", 1); S.ema50 = ln("#5E6B78", 1, 2); }
  if (chOn("st")) { S.st_up = ln("#1E8A5A", 2, 0, 0, { lineType: 1 }); S.st_dn = ln("#C23B32", 2, 0, 0, { lineType: 1 }); }
  if (chOn("scen") && intra) {
    const sl = (color, style, w = 2) => chart.addSeries(LW.LineSeries, { color, lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false }, 0);
    S.sc_up = sl("rgba(30,138,90,.9)", 2); S.sc_side = sl("rgba(94,107,120,.9)", 2); S.sc_down = sl("rgba(194,59,50,.9)", 2);
    S.sc_lo = sl("rgba(94,107,120,.45)", 1, 1); S.sc_hi = sl("rgba(94,107,120,.45)", 1, 1);
    S.sc_lo.applyOptions({ lastValueVisible: false }); S.sc_hi.applyOptions({ lastValueVisible: false });
  }
  let pane = 1;
  if (chOn("vol")) { S.vol = chart.addSeries(LW.HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, pane); S.volPane = pane++; }
  if (chOn("rsi")) { S.rsi = ln("#3D2FA8", 1, 0, pane, { lastValueVisible: true }); S.rsi.createPriceLine({ price: 70, color: "#C23B32", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }); S.rsi.createPriceLine({ price: 30, color: "#1E8A5A", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }); S.rsiPane = pane++; }
  if (chOn("macd")) { S.macd_hist = chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane); S.macd = ln("#0A84C1", 1, 0, pane); S.macd_sig = ln("#E07B00", 1, 0, pane); S.macdPane = pane++; }
  chart.panes().forEach((p, i) => p.setStretchFactor(i === 0 ? 4 : 1));
  S.markers = LW.createSeriesMarkers(S.candle, []);
  const lg = document.createElement("div"); lg.className = "ch-legend"; el.appendChild(lg);
  chart.subscribeCrosshairMove((p) => {
    const c = p && p.time ? p.seriesData.get(S.candle) : null;
    if (!c) { lg.innerHTML = ""; return; }
    const v = (k) => { const x = S[k] && p.seriesData.get(S[k]); return x && x.value != null ? x.value : null; };
    const bits = [["VWAP", v("vwap")], ["EMA9", v("ema9")], ["EMA21", v("ema21")], ["RSI", v("rsi")]].filter((x) => x[1] != null)
      .map(([n, x]) => `<span>${n} <b>${fx(x)}</b></span>`).join("");
    const dtm = new Date((p.time - 19800) * 1000);
    const t = intra ? dtm.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })
      : dtm.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
    const ev = ((CH.data && CH.data.markers) || []).filter((m) => m.time === p.time && m.title).map((m) => `<div class="lg-ev" style="color:${m.color}">${esc(m.title)}</div>`).join("");
    lg.innerHTML = `<div><span>${t}</span><span>O <b>${fx(c.open)}</b></span><span>H <b>${fx(c.high)}</b></span><span>L <b>${fx(c.low)}</b></span><span>C <b class="${c.close >= c.open ? "up" : "down"}">${fx(c.close)}</b></span>${bits}</div>${ev}`;
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r && r.from < 30) loadOlder(); });
  CH.chart = chart; CH.series = S; CH.lines = []; CH.sig = ""; CH.patSeries = []; CH.psig = null;
}

/* candles and the indicators computed from them (all loaded history) */
function drawBars(first) {
  const S = CH.series; if (!CH.chart || !S.candle) return;
  const B = CH.bars; S.candle.setData(B);
  const c = B.map((b) => b.close), h = B.map((b) => b.high), l = B.map((b) => b.low);
  const put = (ser, arr, from = 0) => ser && ser.setData(B.map((b, i) => (arr[i] == null || i < from ? null : { time: b.time, value: arr[i] })).filter(Boolean));
  if (S.ema9) { put(S.ema9, IND.ema(c, 9), 8); put(S.ema21, IND.ema(c, 21), 20); put(S.ema50, IND.ema(c, 50), 49); }
  if (S.st_up) { const st = IND.st(h, l, c); put(S.st_up, st.line.map((x, i) => (st.dir[i] > 0 ? x : null)), 10); put(S.st_dn, st.line.map((x, i) => (st.dir[i] < 0 ? x : null)), 10); }
  if (S.vwap) { const w = IND.vwap(B, CH.vol); put(S.vwap, w.v); put(S.u1, w.u1); put(S.d1, w.d1); put(S.u2, w.u2); put(S.d2, w.d2); }
  if (S.rsi) put(S.rsi, IND.rsi(c), 14);
  if (S.macd) { const m = IND.macd(c); put(S.macd, m.m, 26); put(S.macd_sig, m.s, 34); S.macd_hist.setData(B.map((b, i) => (i < 34 ? null : { time: b.time, value: m.h[i], color: m.h[i] >= 0 ? "rgba(30,138,90,.5)" : "rgba(194,59,50,.5)" })).filter(Boolean)); }
  if (S.vol) S.vol.setData(CH.vol || []);
  if (first && B.length) {                       // zoom that suits the timeframe: about one screen of recent bars
    const n = B.length, want = CH_VIEW[CH.tf] || 130;
    const ahead = chIntraday() && chOn("scen") && ["NIFTY", "BANKNIFTY"].includes(CH.sym) ? Math.ceil(60 / CH.tfMin) + 3 : 5;
    CH.chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, n - want), to: n + ahead });
  }
  const last = B[B.length - 1];
  if (last && !CH.data) $("#ch-price").innerHTML = `<b>${fx(last.close)}</b> <span class="muted small">${esc(CH.src || "")}</span>`;
}

/* the bar a time falls in (alerts and trades happen inside bars of any timeframe) */
function chBarTime(t) {
  const B = CH.bars; if (!B.length || t < B[0].time) return null;
  let lo = 0, hi = B.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (B[mid].time <= t) lo = mid; else hi = mid - 1; }
  return B[lo].time;
}

function scenarioCard(sc, sym) {
  if (!["NIFTY", "BANKNIFTY"].includes(sym)) return `<p class="muted small">Available for Nifty and Bank Nifty (they have years of 1-minute history).</p>`;
  if (!sc) return `<p class="muted small">Loading…</p>`;
  if (!sc.ready) return `<p class="muted small">${esc(sc.why || "Not ready yet.")}</p>`;
  const pc = (x) => `${Math.round(x * 100)}%`;
  const pts = (x) => `${x > 0 ? "+" : ""}${Math.round(x)}`;
  const rows = ["5", "15", "60"].map((h) => { const x = sc.horizons[h]; if (!x) return "";
    const top = Math.max(x.up, x.side, x.down);
    const c = (v, cls) => `<td class="r ${v === top ? "b " + cls : ""}">${pc(v)}</td>`;
    return `<tr><td class="nw">${h === "60" ? "1 hr" : h + "m"}</td>${c(x.up, "up")}${c(x.side, "")}${c(x.down, "down")}<td class="r small nw">${pts(x.lo_pts)} … ${pts(x.hi_pts)}</td></tr>`; }).join("");
  const ev = sc.eval && sc.eval.horizons ? sc.eval.horizons["15"] : null;
  const evh = sc.eval && sc.eval.horizons ? sc.eval.horizons["60"] : null;
  const skill = ev && ev.skill_pct != null ? ev.skill_pct : null;
  const honest = ev ? `<p class="small" style="margin:8px 0 0"><b>Track record</b> (last ${sc.eval.days} sessions, replayed without peeking): the most likely call came true ${ev.hit_pct}% of the time at 15 min (base rate ${ev.hit_base_pct}%)${evh ? `, ${evh.hit_pct}% at 1 hour (base ${evh.hit_base_pct}%)` : ""}. The range held ${ev.range_inside_pct}% of the time (aim 80%).
      ${skill != null && skill <= 1 ? `<br><b class="amber">Direction has no proven edge yet: trust the range (how far it can go), not the arrow.</b>` : ""}</p>` : `<p class="small muted" style="margin:8px 0 0">Track record appears after the first test run (needs the 1-minute history).</p>`;
  const sims = (sc.similar || []).slice(0, 5).map((x) => `<li><b>${new Date(x.date + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" })}</b> <span class="muted small">${x.similarity}% alike</span> · next hour ${fpc(x.next60_pct)} · to close ${fpc(x.close_pct)}</li>`).join("");
  return `<table class="mini"><thead><tr><th></th><th class="r up">▲ Up</th><th class="r">↔ Side</th><th class="r down">▼ Down</th><th class="r">80% range (pts)</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="small muted" style="margin:6px 0 0">From the ${sc.k} past sessions most like today so far, rescaled to today's volatility (${sc.sigma_day_pct}% a day). Sideways = move under ±${Math.round(sc.horizons["15"] ? sc.horizons["15"].side_band_pts : 0)} pts in 15 min.</p>
    ${sims ? `<h4 style="margin:10px 0 4px">Most similar past days</h4><ul class="plain small">${sims}</ul>` : ""}${honest}`;
}

function drawChartSignals(d) {
  const el = $("#ch-sigs"); if (!el) return;
  const sig = (d.markers || []).filter((m) => (m.text === "BUY" || m.text === "SELL") && m.id).slice(-4).reverse();
  const html = sig.length ? `<ul class="plain small">${sig.map((m) => `<li><span class="pill ${m.text === "BUY" ? "p-up" : "p-down"}">${m.text}</span> ${new Date((m.time - 19800) * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })} ${esc(m.title || "")}
      <br><button class="link-btn" data-paper="${m.id}">Paper trade this signal</button></li>`).join("")}</ul>` : `<p class="muted small">No Focus signal on ${esc(d.sym || CH.sym)} in this range. The Paper BUY / SELL buttons above trade the at-the-money option anyway.</p>`;
  if (el.dataset.h !== html) { el.dataset.h = html; el.innerHTML = html; bindAlertButtons(el); }
}

function drawChart(d, first) {
  if (!d || !CH.chart || !CH.series.candle) return;
  drawChartSignals(d);
  const S = CH.series;
  const intra = chIntraday();
  const sc = d.scenario;
  if (S.sc_up) {
    const P = (sc && sc.ready && sc.paths) || {};
    [["up", S.sc_up, "▲ Up"], ["side", S.sc_side, "↔ Side"], ["down", S.sc_down, "▼ Down"]].forEach(([k, ser, lab]) => {
      const x = P[k]; ser.setData(x ? x.line : []); ser.applyOptions({ title: x ? `${lab} ${Math.round(x.p * 100)}%` : "", lineWidth: x ? Math.max(1, Math.round(1 + x.p * 4)) : 1 });
    });
    S.sc_lo.setData(sc && sc.ready ? sc.band.lo : []); S.sc_hi.setData(sc && sc.ready ? sc.band.hi : []);
  }
  // price lines for levels (rebuild only when they change)
  const groups = { prev: "prev", cpr: "cpr", or: "or", oi: "oi", gamma: "gamma", em: "em" };
  const want = d.levels.filter((l) => chOn(groups[l.group] || l.group) && (intra || !["or", "em", "gamma"].includes(l.group)));
  const sig = JSON.stringify(want.map((l) => [l.name, l.price]));
  if (sig !== CH.sig) {
    CH.lines.forEach((pl) => S.candle.removePriceLine(pl));
    CH.lines = want.map((l) => S.candle.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.style, axisLabelVisible: true, title: l.name }));
    CH.sig = sig;
  }
  const mk = [];
  const at = (m) => { const t = chBarTime(m.time); if (t != null) mk.push({ ...m, time: t }); };
  if (chOn("marks") && intra) d.markers.forEach(({ time, position, color, shape, text }) => at({ time, position, color, shape, text }));
  if (chOn("pat") && intra) (d.patterns || []).forEach((p) => at({ time: p.time, position: p.direction === "up" ? "belowBar" : "aboveBar",
    color: p.grade === "edge" ? "#1E8A5A" : p.grade === "losing" ? "#9AA5B1" : "#3D2FA8", shape: p.direction === "up" ? "arrowUp" : "arrowDown", text: PAT_SHORT[p.pattern] || "" }));
  if (chOn("swing") && intra) (d.swings || []).forEach((w) => at({ time: w.time, position: w.type === "H" ? "aboveBar" : "belowBar", color: "#8A94A0", shape: "circle", size: 0.3, text: w.label }));
  if (CH.overlayMarks) CH.overlayMarks.forEach(at);
  const seen = new Set(); const mk2 = mk.filter((m) => { const k = m.time + "|" + m.text + "|" + m.position; if (seen.has(k)) return false; seen.add(k); return true; });
  mk2.sort((a, b) => a.time - b.time);
  mk.length = 0; mk.push(...mk2);
  S.markers.setMarkers(mk);
  // pattern lines (rebuilt only when the patterns change)
  const psig = chOn("pat") ? JSON.stringify((d.patterns || []).map((p) => [p.pattern, p.time])) : "";
  if (psig !== CH.psig) {
    (CH.patSeries || []).forEach((x) => { try { CH.chart.removeSeries(x); } catch (_) {} });
    CH.patSeries = [];
    if (chOn("pat") && intra) (d.patterns || []).forEach((p) => p.lines.forEach((ln) => {
      const pts = []; ln.forEach((q) => { if (!pts.length || q.time > pts[pts.length - 1].time) pts.push(q); });
      if (pts.length < 2) return;
      const ser = CH.chart.addSeries(window.LightweightCharts.LineSeries, { color: p.direction === "up" ? "rgba(30,138,90,.85)" : "rgba(194,59,50,.85)", lineWidth: 2, lineStyle: 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
      ser.setData(pts); CH.patSeries.push(ser);
    }));
    CH.psig = psig;
  }
  $("#ch-price").innerHTML = `<b>${fx(d.spot)}</b> ${fpc(d.chg_pct)} ${d.regime && d.regime.code !== "opening" ? `<span class="pill p-sig">${esc(d.regime.label)}</span>` : ""}${d.offline ? ` <span class="pill p-amber" title="${esc(d.offline.source)}">Last session · ${esc(d.offline.as_of)}</span>` : ""}`;
  const pats = (d.patterns || []).slice().reverse();
  $("#ch-pats").innerHTML = (d.structure ? `<p class="small" style="margin:0 0 6px">Structure: <b class="${d.structure === "uptrend" ? "up" : d.structure === "downtrend" ? "down" : ""}">${esc(d.structure)}</b> <span class="muted">(${(d.swings || []).slice(-4).map((w) => w.label).join(" → ")})</span></p>` : "")
    + (pats.length ? `<ul class="plain">${pats.slice(0, 6).map((p) => `<li>${hm(p.time - 19800)} ${arrow(p.direction)} <b>${esc(p.name)}</b> @ ${fx(p.entry)}<br><small>${esc(p.why)}. Stop ${fx(p.stop)} · target ${fx(p.target)} · R:R ${p.rr}</small>
      ${p.history ? `<br><small class="${p.grade === "edge" ? "up" : p.grade === "losing" ? "down" : "muted"}">${esc(p.history)}</small>` : ""}</li>`).join("")}</ul>` : `<p class="muted small">No completed pattern on these bars yet.</p>`);
  CH.psig = CH.psig || "";
  $("#ch-read").innerHTML = d.read.length ? `<ul class="plain">${d.read.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">Builds after the first 15 minutes.</p>`;
  $("#ch-setups").innerHTML = d.setups.length ? `<ul class="plain">${d.setups.slice().reverse().map((x) => `<li>${hm(x.ts)} ${arrow(x.direction)} <b>${esc(x.name)}</b> @ ${fx(x.entry)}<br><small>stop ${fx(x.stop)} · target ${fx(x.target)} · R:R ${x.rr}</small>
      <span class="pill ${x.state === "target hit" ? "p-up" : x.state === "stopped" ? "p-down" : "p-sig"}">${esc(x.state)}${x.state === "active" ? ` ${x.r_now > 0 ? "+" : ""}${x.r_now}R` : ""}</span></li>`).join("")}</ul>` : `<p class="muted small">No setup yet today. The scanner checks every closed 5-minute bar.</p>`;
  $("#ch-scen").innerHTML = scenarioCard(d.scenario, d.sym);
  const spot = d.spot;
  $("#ch-levels").innerHTML = `<ul class="plain lv">${d.levels.slice().sort((a, b) => b.price - a.price).map((l) => `<li class="${l.price > spot ? "abv" : "blw"}"><span><i style="background:${l.color}"></i>${esc(l.name)}</span><b>${fx(l.price)}</b><small>${((l.price / spot - 1) * 100).toFixed(2)}%</small></li>`).join("")}</ul>`;
}


/* ---------------- page: World */
async function pageWorld() {
  stopPageTimer();
  const tick = async (first) => {
    if (state.page !== "world") return;
    try { drawWorld(await api("/api/world"), first); } catch (e) { if (first) main.innerHTML = `<div class="empty"><h2>World</h2><p>${esc(e.message)}</p></div>`; }
    L.pageTimer = setTimeout(() => tick(false), 10000);
  };
  tick(true);
}

function brainCard(note, opts = {}) {
  if (!note) return `<div class="panel brain"><h3>AI market brain</h3><p class="muted small">The first note appears a few minutes after the market opens (or click Run now).</p>${opts.button ? `<button class="link-btn" data-brain-run>Run now</button>` : ""}</div>`;
  const bias = note.bias || "neutral";
  return `<div class="panel brain b-${esc(bias)}"><div class="toolbar" style="margin:0 0 6px"><h3 style="margin:0">AI market brain <span class="muted small">${esc(note.time || "")} · ${note.by === "AI" ? "AI" : "rules"}</span></h3>
      ${opts.button ? `<button class="link-btn" data-brain-run>Run now</button>` : ""}</div>
    <p class="brain-head"><span class="pill ${bias === "bullish" ? "p-up" : bias === "bearish" ? "p-down" : ""}">${esc(bias)} ${note.confidence != null ? note.confidence + "%" : ""}</span> <b>${esc(note.headline || "")}</b></p>
    <ul class="plain">${(note.summary || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    ${note.plan ? `<p class="small"><b>Plan:</b> ${esc(note.plan)}</p>` : ""}
    ${(note.changed || []).length ? `<p class="small muted"><b>Changed:</b> ${note.changed.map(esc).join(" · ")}</p>` : ""}
    ${(note.risks || []).length ? `<p class="small"><b class="down">Risks:</b> ${note.risks.map(esc).join(" · ")}</p>` : ""}
    ${(note.watchlist || []).length ? `<div class="chips">${note.watchlist.map((w) => `<a class="chip static" href="#/chart?s=${encodeURIComponent(w.sym)}" title="${esc(w.why || "")}">${esc(w.sym)}${w.level ? " " + fx(Number(w.level)) : ""}</a>`).join("")}</div>` : ""}
  </div>`;
}

function drawWorld(w, first) {
  const p = w.pressure || {};
  const imp = (v) => (v == null ? "–" : `<span class="${v > 0.02 ? "up" : v < -0.02 ? "down" : "muted"}">${v > 0 ? "+" : ""}${v.toFixed(2)}%</span>`);
  const spark = (xs, pc) => {
    // today's line against the previous close (dashed): colour = above / below yesterday's close, like the % change
    if (!xs || xs.length < 2) return "";
    const all = pc ? [...xs, pc] : xs;
    const lo = Math.min(...all), hi = Math.max(...all), W = 84, H = 22, y = (v) => (H - 1 - ((v - lo) / ((hi - lo) || 1)) * (H - 2)).toFixed(1);
    const pts = xs.map((x, i) => `${((i / (xs.length - 1)) * W).toFixed(1)},${y(x)}`).join(" ");
    const up = pc ? xs[xs.length - 1] >= pc : xs[xs.length - 1] >= xs[0];
    return `<svg width="${W}" height="${H}" class="spark" aria-hidden="true">${pc ? `<line x1="0" x2="${W}" y1="${y(pc)}" y2="${y(pc)}" stroke="#9AA5B1" stroke-dasharray="2 2" stroke-width="1"/>` : ""}<polyline points="${pts}" fill="none" stroke="${up ? "#1E8A5A" : "#C23B32"}" stroke-width="1.5"/></svg>`;
  };
  const prevLast = L.worldLast || {};
  const nowS = w.server_ts || Date.now() / 1000;
  const age = (ts) => { if (!ts) return ""; const m = Math.round((nowS - ts) / 60); return m < 1 ? "now" : m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : "closed"; };
  const flash = (r) => { const o = prevLast[r.ticker]; return o == null || o === r.last ? "" : r.last > o ? "flash-up" : "flash-down"; };
  L.worldLast = Object.fromEntries(w.groups.flatMap((g) => g.rows.map((r) => [r.ticker, r.last])));
  const liveAge = w.updated && w.updated.live ? Math.round(nowS - w.updated.live) : null;
  const groups = w.groups.map((g) => `<div class="panel tight"><h3>${esc(g.group)}</h3><table class="mini"><thead><tr><th>Market</th><th class="r">Last</th><th class="r">Day</th><th></th><th class="r" title="Past correlation with Nifty (next day for US/Europe, same day for Asia)">Corr</th><th class="r" title="What this move has meant for Nifty in the past">Nifty impact</th></tr></thead>
    <tbody>${g.rows.map((r) => `<tr><td>${esc(r.name)}${r.live_ts ? ` <small class="muted" title="Time since the last price">${age(r.live_ts)}</small>` : ""}</td><td class="r ${flash(r)}">${fx(r.last)}</td><td class="r">${fpc(r.chg_pct)}</td><td title="Today vs previous close (dashed)">${r.spark_day && r.spark_day.length > 2 ? spark(r.spark_day, r.spark_pc || r.prev) : spark(r.spark)}</td><td class="r muted">${r.corr != null ? r.corr.toFixed(2) : "–"}</td><td class="r">${imp(r.nifty_impact)}</td></tr>`).join("")}</tbody></table></div>`).join("");
  const now = Date.now();
  const cal = w.calendar.map((e) => { const t = new Date(e.time); const past = t.getTime() < now - 30 * 60000;
    return `<tr class="${past ? "past" : ""}"><td>${t.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })}</td><td><b>${esc(e.country)}</b></td><td>${esc(e.title)}</td>
      <td><span class="pill ${e.impact === "High" ? "p-down" : "p-amber"}">${esc(e.impact)}</span></td><td class="r muted">${esc(e.forecast || "")}</td><td class="r muted">${esc(e.previous || "")}</td></tr>`; }).join("");
  const res = {};
  w.results.forEach((r) => { (res[r.date] = res[r.date] || []).push(r.sym); });
  const news = w.news.filter((n) => LS.get("w_imp", "all") === "all" || n.impact === "high" || (LS.get("w_imp", "all") === "medium" && n.impact === "medium")).slice(0, 50);
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">World</h1><p class="lede" style="margin-bottom:0">Global markets, currencies, commodities, rates, crypto, Indian ADRs, economic events, results and global news: what it means for Nifty.</p></div>
      <div style="text-align:right"><button class="btn" id="w-refresh">Refresh now</button>
        <p class="small muted" style="margin:6px 0 0">${liveAge != null ? `<span class="dot-live"></span> Prices ${liveAge < 90 ? "updated " + liveAge + "s ago" : "updated " + Math.round(liveAge / 60) + " min ago"} · auto every 30 s` : "Loading prices…"}</p></div></div>
    <div class="kpis" style="margin-top:16px"><div class="kpi"><b class="${(p.implied_pct || 0) >= 0 ? "up" : "down"}">${p.implied_pct != null ? (p.implied_pct > 0 ? "+" : "") + p.implied_pct.toFixed(2) + "%" : "–"}</b><span>Global pressure on Nifty: ${esc(p.label || "not ready")}</span></div>
      <div class="kpi"><b>${w.nifty ? fx(w.nifty.last) : "–"}</b><span>${w.nifty && w.nifty.live ? "Nifty live" : "Nifty"} ${w.nifty ? fpc(w.nifty.chg_pct) : ""}</span></div>
      <div class="kpi"><b>${w.upcoming.filter((e) => e.impact === "High" && e.mins >= 0).length}</b><span>High-impact events in the next 2 hours</span></div>
      <div class="kpi"><b>${(res[new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })] || []).length}</b><span>F&amp;O results today</span></div></div>
    ${p.drivers ? `<p class="note">Biggest drivers: ${p.drivers.map((d) => `${esc(d.name)} ${d.move > 0 ? "+" : ""}${d.move.toFixed(2)}% → ${d.impact > 0 ? "+" : ""}${d.impact.toFixed(2)}%`).join(" · ")}. Indicative: based on the last 9 months' relation, not a forecast.</p>` : ""}
    <div class="world-grid">${groups}</div>
    ${w.adr.length ? `<h2>Indian ADRs vs NSE price</h2><div class="table-wrap"><table><thead><tr><th>ADR</th><th class="r">ADR ($)</th><th class="r">ADR day</th><th class="r">Implied ₹</th><th class="r">NSE ₹</th><th class="r">Premium</th></tr></thead><tbody>
      ${w.adr.map((a) => `<tr><td><b>${esc(a.nse)}</b> <span class="muted small">${esc(a.adr)}</span></td><td class="r">${fx(a.adr_usd)}</td><td class="r">${fpc(a.adr_chg)}</td><td class="r">${fx(a.implied_inr)}</td><td class="r">${fx(a.nse_inr)}</td><td class="r">${fpc(a.premium_pct)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">A big change in the ADR overnight often shows up in the stock at the next open.</p>` : ""}
    <div class="split" style="margin-top:22px">
      <div><h2 style="margin-top:0">Economic calendar <span class="muted small">this week, IST</span></h2><div class="table-wrap cal"><table><thead><tr><th>When</th><th></th><th>Event</th><th>Impact</th><th class="r">Forecast</th><th class="r">Previous</th></tr></thead><tbody>${cal || `<tr><td colspan="6" class="muted">Loading…</td></tr>`}</tbody></table></div></div>
      <div><h2 style="margin-top:0">Results calendar <span class="muted small">F&amp;O stocks, next 10 days</span></h2><div class="panel tight">${Object.entries(res).map(([d, xs]) => `<p class="small" style="margin:0 0 8px"><b>${new Date(d + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}</b>: ${xs.slice(0, 40).map((x) => `<a href="#/chart?s=${encodeURIComponent(x)}">${esc(x)}</a>`).join(", ")}${xs.length > 40 ? ` +${xs.length - 40}` : ""}</p>`).join("") || `<p class="muted">Loading…</p>`}</div></div></div>
    <div class="toolbar" style="margin-top:22px"><h2 style="margin:0">Global news</h2><div class="seg">${[["all", "All"], ["medium", "Matters for India"], ["high", "High impact"]].map(([k, l]) => `<button data-wimp="${k}" aria-pressed="${LS.get("w_imp", "all") === k}">${l}</button>`).join("")}</div></div>
    <ul class="alerts filings">${news.map((n) => `<li>${n.impact ? `<span class="pill ${n.impact === "high" ? "p-down" : n.impact === "medium" ? "p-amber" : ""}">${esc(n.impact)}</span>` : ""} ${n.direction > 0 ? '<span class="up">▲</span>' : n.direction < 0 ? '<span class="down">▼</span>' : ""}
      <a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a><br><small class="muted">${esc(n.source)} · ${hm(n.ts)}${n.why ? " · " + esc(n.why) : ""}${n.by === "AI" ? " · AI" : ""}</small></li>`).join("") || `<li class="muted" style="padding:14px">Loading…</li>`}</ul>`;
  $("#w-refresh").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "Refreshing…"; try { drawWorld(await post("/api/world/refresh", {})); } catch (err) { toast(err.message); } };
  main.querySelectorAll("[data-wimp]").forEach((b) => b.onclick = () => { LS.set("w_imp", b.dataset.wimp); drawWorld(w); });
}

/* ---------------- page: Insider trades */
const cr = (v) => (v == null ? "–" : `₹${(v / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 1 })} cr`);
async function pageInsiders() {
  stopPageTimer();
  const days = Number(LS.get("ins_days", 60));
  const all = LS.get("ins_all", false);
  const v = await api(`/api/insiders?days=${days}&all=${all ? 1 : 0}`);
  const net = v.net.map((b) => `<tr class="click" data-sym="${esc(b.sym)}"><td><b>${esc(b.sym)}</b> <span class="muted small">${esc(b.company || "")}</span></td><td class="r up">${b.buy ? cr(b.buy) : "–"}</td><td class="r down">${b.sell ? cr(b.sell) : "–"}</td><td class="r"><b class="${b.net >= 0 ? "up" : "down"}">${b.net >= 0 ? "+" : "−"}${cr(Math.abs(b.net))}</b></td><td class="r">${b.n}</td><td class="r">${b.people}</td></tr>`).join("");
  const rows = v.rows.map((x) => `<tr><td>${esc((x.filed || "").slice(0, 10))}</td><td><a href="#/chart?s=${encodeURIComponent(x.sym)}"><b>${esc(x.sym)}</b></a></td><td>${esc(x.person)}<br><small class="muted">${esc(x.category)}</small></td>
    <td class="${x.side === "Buy" ? "up" : "down"}"><b>${esc(x.side)}</b><br><small class="muted">${esc(x.mode)}</small></td><td class="r">${cr(x.value)}</td><td class="r small">${x.before_pct?.toFixed(2)}% → ${x.after_pct?.toFixed(2)}%</td><td class="r small">${esc(x.traded || "")}${x.days_to_file != null ? `<br><span class="muted">+${x.days_to_file}d to file</span>` : ""}</td></tr>`).join("");
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Insider trades</h1><p class="lede" style="margin-bottom:0">Promoters, directors and key managers buying or selling their own company's shares in the market (F&amp;O stocks, ₹50 lakh+). ${esc(v.note)}</p></div>
      <div style="text-align:right"><button class="btn" id="ins-refresh">Refresh now</button><p class="small muted" style="margin:6px 0 0">${v.running ? esc(v.progress || "Reading NSE disclosures") + "…" : v.updated ? "Updated " + hm(v.updated) + " · every 3 hours" : "First read: about 10 minutes (one year of filings)"}</p></div></div>
    <div class="toolbar" style="margin-top:12px"><div class="seg">${[30, 60, 180, 365].map((d) => `<button data-days="${d}" aria-pressed="${d === days}">${d} days</button>`).join("")}</div>
      <label class="check"><input type="checkbox" id="ins-all" ${all ? "checked" : ""}><span>Show all disclosures (off-market, ESOPs, small)</span></label></div>
    <h2>Net by stock</h2><div class="table-wrap"><table><thead><tr><th>Stock</th><th class="r">Bought</th><th class="r">Sold</th><th class="r">Net</th><th class="r">Trades</th><th class="r">People</th></tr></thead><tbody>${net || `<tr><td colspan="6" class="muted">Nothing yet.</td></tr>`}</tbody></table></div>
    <h2>Every disclosure</h2><div class="table-wrap"><table><thead><tr><th>Filed</th><th>Stock</th><th>Who</th><th>Side</th><th class="r">Value</th><th class="r">Holding</th><th class="r">Traded</th></tr></thead><tbody>${rows || `<tr><td colspan="7" class="muted">Nothing yet.</td></tr>`}</tbody></table></div>
    <p class="note">Insiders sell for many reasons (taxes, diversification, pledges); buying with their own money in the market is the stronger signal. Disclosures arrive days after the trade.</p>`;
  main.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { LS.set("ins_days", Number(b.dataset.days)); pageInsiders(); });
  $("#ins-all").onchange = (e) => { LS.set("ins_all", e.target.checked); pageInsiders(); };
  main.querySelectorAll("tr[data-sym]").forEach((r) => r.onclick = () => { location.hash = `#/chart?s=${encodeURIComponent(r.dataset.sym)}`; });
  $("#ins-refresh").onclick = async (e) => { e.target.disabled = true; try { await post("/api/insiders/refresh", {}); toast("Reading NSE disclosures in the background"); setTimeout(pageInsiders, 3000); } catch (err) { toast(err.message); } };
  if (!v.updated && !v.running && !L.insAuto) {        // first visit: start the first read by itself
    L.insAuto = true;
    post("/api/insiders/refresh", {}).then(() => setTimeout(() => state.page === "insiders" && pageInsiders(), 2500)).catch(() => {});
  }
  L.pageTimer = setTimeout(() => state.page === "insiders" && pageInsiders(), v.running ? 10000 : 120000);
}

function companyCard(c) {
  const r = c.results || {};
  if (r.error) return `<p class="muted small">${esc(r.error)}</p>`;
  const q = (r.quarters || []).slice(0, 5);
  const qt = q.length ? `<table class="mini"><thead><tr><th>Quarter</th><th class="r">Revenue</th><th class="r">Profit</th><th class="r">Margin</th><th class="r">EPS</th></tr></thead><tbody>${q.map((x) => `<tr><td>${new Date(x.quarter_end + "T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "2-digit" })}</td><td class="r">${x.revenue_cr != null ? fk(Math.round(x.revenue_cr)) : "–"}</td><td class="r">${x.profit_cr != null ? fk(Math.round(x.profit_cr)) : "–"}</td><td class="r">${x.margin_pct ?? "–"}%</td><td class="r">${x.eps ?? "–"}</td></tr>`).join("")}</tbody></table>
    <p class="small muted" style="margin:4px 0">₹ crore, ${esc(q[0].basis)}${r.yoy ? ` · latest vs a year ago: revenue ${fpc(r.yoy.revenue_pct)}, profit ${r.yoy.profit_pct != null ? fpc(r.yoy.profit_pct) : "–"}` : ""}</p>` : `<p class="muted small">No results found.</p>`;
  const ins = (c.insiders || []).filter((x) => x.market).slice(0, 4);
  const it = ins.length ? `<h4 style="margin:10px 0 4px">Insider trades</h4><ul class="plain small">${ins.map((x) => `<li>${esc((x.filed || "").slice(0, 10))} <b class="${x.side === "Buy" ? "up" : "down"}">${esc(x.side)}</b> ${cr(x.value)} · ${esc(x.person)} <span class="muted">(${esc(x.category)})</span></li>`).join("")}</ul>` : "";
  return qt + it;
}

/* ---------------- Claude reviews (Test & research) and the "Powered by Claude" line */
L.ai = null;
async function aiStatus(force = false) {
  if (!force && L.ai && Date.now() - L.ai._at < 60000) return L.ai;
  try { L.ai = { ...(await api("/api/ai/status")), _at: Date.now() }; } catch (_) { L.ai = L.ai || { enabled: false, _at: Date.now() }; }
  railAI();
  return L.ai;
}
const isClaude = (a) => a && String(a.provider || "").startsWith("claude");
function railAI() {
  const el = $("#rail-ai"); if (!el || !L.ai) return;
  const a = L.ai, mdl = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" }[a.model] || "";
  el.innerHTML = !a.enabled ? `<a href="#/connect" class="muted">AI: not connected</a>`
    : isClaude(a) ? `<span class="ai-by">✦ Powered by Claude${mdl ? " " + mdl : ""}</span><small>${a.provider === "claude_pro" ? "your Pro plan" : "Claude API"}</small>`
    : `<span class="ai-by">AI: ${esc(a.label)}</span>`;
}
function aiReviewBox(topic, title) {
  return `<div class="panel ai-review" data-topic="${topic}"><div class="toolbar" style="margin:0"><div><h3 style="margin:0">${esc(title)}</h3>
      <p class="small muted" style="margin:2px 0 0">A sceptical research read of these numbers: what they say, how far to trust them, what to test next. Uses your connected AI.</p></div>
      <button class="btn" type="button" data-ai-review="${topic}">✦ Ask Claude</button></div><div class="ai-review-out"></div></div>`;
}
function bindAIReview(root, getData) {
  aiStatus().then((a) => root.querySelectorAll("[data-ai-review]").forEach((b) => { b.textContent = isClaude(a) ? "✦ Ask Claude" : a.enabled ? "AI review" : "Connect AI first"; }));
  root.querySelectorAll("[data-ai-review]").forEach((b) => b.onclick = async () => {
    const box = b.closest(".ai-review"), out = box.querySelector(".ai-review-out");
    const a = await aiStatus();
    if (!a.enabled) { location.hash = "#/connect"; return; }
    b.disabled = true; const t = b.textContent; b.textContent = "Thinking…";
    out.innerHTML = `<p class="muted small">Claude is reading the numbers… (Opus can take up to a minute)</p>`;
    try {
      const r = await post("/api/ai/review", { topic: b.dataset.aiReview, data: getData ? getData() : null });
      out.innerHTML = `<div class="ai-text">${fmtAI(r.text)}</div><p class="small muted" style="margin:6px 0 0">${esc(r.by)} · ${r.secs}s · a review, not advice. Test before trusting.</p>`;
    } catch (e) { out.innerHTML = `<p class="down small">${esc(e.message)}</p>`; }
    b.disabled = false; b.textContent = t;
  });
}

/* ---------------- Time Machine: test a trade plan before taking it */
const TM = { setup: null, last: null };
const tmTime = (bar) => { const m = 555 + 5 * bar; return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };

async function pageTimeMachine() {
  stopPageTimer();
  const qp = new URLSearchParams(location.hash.split("?")[1] || "");
  let setup;
  try { setup = await api("/api/timemachine/setup"); } catch (e) { setup = { symbols: [{ sym: "NIFTY" }, { sym: "BANKNIFTY" }], alerts: [] }; }
  TM.setup = setup;
  const keep = LS.get("tm_form", {}) || {};
  const sym = (qp.get("s") || keep.sym || "NIFTY").toUpperCase();
  const opts = setup.symbols.map((x) => `<option value="${esc(x.sym)}" ${x.sym === sym ? "selected" : ""}>${esc(x.sym)}${x.ltp ? " · " + fx(x.ltp) : ""}</option>`).join("");
  const r5 = (t) => { const [h, m] = t.split(":").map(Number); const x = Math.ceil((h * 60 + m) / 5) * 5; return `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };
  const nowHM = setup.now && setup.now >= "09:20" && setup.now < "15:10" ? r5(setup.now) : "";
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Time Machine</h1>
      <p class="lede" style="margin-bottom:0">Test a trade before you take it. The rest of the day is replayed 10,000 times from this stock's own last 60 sessions (same time of day), and the same plan is checked on each real past session.</p></div></div>
    <div class="tm-grid">
      <form class="panel tm-form" id="tm-form" autocomplete="off" novalidate>
        <label class="field">Stock or index<select id="tm-sym">${opts}</select></label>
        <div class="field-group"><span class="field-label">Direction</span>
          <div class="seg" role="group" aria-label="Direction"><button type="button" data-dir="up" aria-pressed="true">▲ Long (price up)</button><button type="button" data-dir="down" aria-pressed="false">▼ Short (price down)</button></div></div>
        <div class="tm-three">
          <label class="field">Entry<input type="text" id="tm-entry" inputmode="decimal"></label>
          <label class="field">Stop<input type="text" id="tm-stop" inputmode="decimal"></label>
          <label class="field">Target<input type="text" id="tm-target" inputmode="decimal"></label>
        </div>
        <p class="small muted tm-quick">Quick fill: stop <select id="tm-qs" aria-label="Stop distance">${[0.3, 0.5, 0.75, 1, 1.5].map((v) => `<option value="${v}" ${v === 0.5 ? "selected" : ""}>${v}%</option>`).join("")}</select> away, target <select id="tm-qr" aria-label="Target in R">${[1, 1.5, 2, 3].map((v) => `<option value="${v}" ${v === 2 ? "selected" : ""}>${v}R</option>`).join("")}</select> <button type="button" class="link-btn" id="tm-fill">Fill</button></p>
        <label class="field">Entry time<input type="time" id="tm-start" min="09:20" max="15:10" step="300" value="${esc(qp.get("t") || nowHM || keep.start || "09:30")}"><small>Bars after this time are replayed up to the 15:15 square-off.</small></label>
        <button class="btn primary" type="submit" id="tm-run">Run 10,000 replays</button>
        <button class="btn" type="button" id="tm-scan">Find a better stop &amp; target</button>
        <p class="small muted" id="tm-plan-note" style="margin:0"></p>
      </form>
      <section id="tm-result" class="tm-result" aria-live="polite">${tmEmpty()}</section>
    </div>
    <h2>Test today's alerts <span class="muted small">alerts with a stop and a target</span></h2>
    <div class="table-wrap"><table><thead><tr><th>Time</th><th>Stock</th><th>Alert</th><th class="r">Entry</th><th class="r">Stop</th><th class="r">Target</th><th class="r">R:R</th><th></th></tr></thead>
      <tbody>${setup.alerts.map((a) => `<tr><td>${hm(a.ts)}</td><td><b>${esc(a.sym)}</b></td><td>${arrow(a.direction)} ${esc(a.title)}</td><td class="r">${fx(a.entry)}</td><td class="r">${fx(a.stop)}</td><td class="r">${fx(a.target)}</td><td class="r">${a.rr ?? "–"}</td><td class="r"><button class="link-btn" data-tma="${a.id}">Test</button></td></tr>`).join("") || `<tr><td colspan="8" class="muted">No alert with a stop and target yet today. Start the scanner (Live), or type a plan above.</td></tr>`}</tbody></table></div>
    <h2>Your recent tests</h2><div id="tm-hist"></div>
    <p class="note">How to read it: a plan with a 2R target only has to reach the target first 33% of the time to break even, so a low win rate is not bad on its own. The replays assume the next hours behave like the last 60 sessions: results days, news and events can do things those sessions never did. A test, not a prediction.</p>`;

  let dir = keep.dir === "down" ? "down" : "up";
  const segBtns = main.querySelectorAll("[data-dir]");
  const setDir = (d) => { dir = d; segBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.dir === d))); planNote(); };
  segBtns.forEach((b) => b.onclick = () => setDir(b.dataset.dir));
  const ltpOf = (s2) => (TM.setup.symbols.find((x) => x.sym === s2) || {}).ltp;
  const priceOf = async (s2) => {          // live price, else the last close (scanner off / market closed)
    const l = ltpOf(s2); if (l) return l;
    try { const r = await api(`/api/price/${encodeURIComponent(s2)}`); const x = TM.setup.symbols.find((y) => y.sym === s2); if (x) x.ltp = r.price; return r.price; } catch (_) { return null; }
  };
  const num = (id) => Number(String($(id).value).replace(/[^0-9.]/g, ""));
  const fill = () => {
    const e = num("#tm-entry") || ltpOf($("#tm-sym").value);
    if (!e) { toast("Type an entry price first"); return; }
    const sp = Number($("#tm-qs").value) / 100, r = Number($("#tm-qr").value), dd = dir === "up" ? 1 : -1;
    const dec = e >= 1000 ? 1 : 2;
    $("#tm-entry").value = e.toFixed(dec); $("#tm-stop").value = (e * (1 - dd * sp)).toFixed(dec); $("#tm-target").value = (e * (1 + dd * sp * r)).toFixed(dec);
    planNote();
  };
  function planNote() {
    const e = num("#tm-entry"), st = num("#tm-stop"), tg = num("#tm-target"), el = $("#tm-plan-note");
    if (!el) return;
    if (!e || !st || !tg) { el.textContent = ""; return; }
    const ok = dir === "up" ? st < e && e < tg : tg < e && e < st;
    if (!ok) { el.innerHTML = `<span class="down">For a ${dir === "up" ? "long" : "short"}, the stop must be ${dir === "up" ? "below" : "above"} the entry and the target ${dir === "up" ? "above" : "below"} it.</span>`; return; }
    const rr = Math.abs(tg - e) / Math.abs(e - st);
    el.innerHTML = `Risk ${(Math.abs(e - st) / e * 100).toFixed(2)}% · reward ${(Math.abs(tg - e) / e * 100).toFixed(2)}% · reward:risk <b>${rr.toFixed(2)}</b> · break-even needs the target first ${(100 / (1 + rr)).toFixed(0)}% of the time`;
  }
  ["#tm-entry", "#tm-stop", "#tm-target"].forEach((id) => $(id).addEventListener("input", planNote));
  $("#tm-fill").onclick = fill;
  $("#tm-sym").onchange = async () => { $("#tm-entry").value = ""; $("#tm-stop").value = ""; $("#tm-target").value = "";
    const l = await priceOf($("#tm-sym").value); if (l) { $("#tm-entry").value = l; fill(); } };
  const run = async () => {
    const body = { sym: $("#tm-sym").value, direction: dir, entry: num("#tm-entry"), stop: num("#tm-stop"), target: num("#tm-target"), start: $("#tm-start").value };
    if (!body.entry || !body.stop || !body.target) { toast("Fill entry, stop and target (or use Fill)"); return; }
    LS.set("tm_form", { sym: body.sym, dir, start: body.start });
    const btn = $("#tm-run"); btn.disabled = true; btn.textContent = "Replaying…";
    $("#tm-result").innerHTML = `<div class="panel"><p class="muted">Loading ${esc(body.sym)}'s last 60 sessions and replaying 10,000 times… (the first test of a stock each day takes a few seconds)</p></div>`;
    try {
      const r = await post("/api/timemachine", body);
      TM.last = r;
      $("#tm-result").innerHTML = tmResult(r) + aiReviewBox("timemachine", "Claude's read on this test");
      tmFan(r);
      bindAIReview($("#tm-result"), () => TM.last);
      const h = (LS.get("tm_hist", []) || []).filter((x) => !(x.sym === r.sym && x.entry === body.entry && x.stop === body.stop && x.target === body.target && x.start === r.start));
      h.unshift({ sym: r.sym, direction: dir, entry: body.entry, stop: body.stop, target: body.target, start: r.start, tf: r.mc.target_first, sf: r.mc.stop_first, avg: r.mc.avg_r, grade: r.verdict.grade, at: Date.now() });
      LS.set("tm_hist", h.slice(0, 10)); renderHist();
    } catch (e) { $("#tm-result").innerHTML = `<div class="panel"><p class="down"><b>Couldn't run this test.</b> ${esc(e.message)}</p></div>`; }
    btn.disabled = false; btn.textContent = "Run 10,000 replays";
  };
  $("#tm-form").onsubmit = (e) => { e.preventDefault(); run(); };
  $("#tm-scan").onclick = async () => {
    const body = { sym: $("#tm-sym").value, direction: dir, entry: num("#tm-entry") || (await priceOf($("#tm-sym").value)), start: $("#tm-start").value };
    if (!body.entry) { toast("Type an entry price first"); return; }
    const btn = $("#tm-scan"); btn.disabled = true; btn.textContent = "Testing 24 plans…";
    $("#tm-result").innerHTML = `<div class="panel"><p class="muted">Testing 24 stop/target combinations for ${esc(body.sym)} (${dir === "up" ? "long" : "short"} at ${esc(body.start)}), each 4,000 replays plus the real past…</p></div>`;
    try {
      const r = await post("/api/timemachine/scan", body);
      $("#tm-result").innerHTML = tmScan(r);
      $("#tm-result").querySelectorAll("[data-use]").forEach((b) => b.onclick = () => { const x = r.rows[Number(b.dataset.use)];
        load({ sym: r.sym, direction: r.direction, entry: r.entry, stop: x.stop, target: x.target, start: r.start }, true); });
    } catch (e) { $("#tm-result").innerHTML = `<div class="panel"><p class="down"><b>Couldn't test.</b> ${esc(e.message)}</p></div>`; }
    btn.disabled = false; btn.innerHTML = "Find a better stop &amp; target";
  };
  const load = (p, go) => {
    if ([...$("#tm-sym").options].some((o) => o.value === p.sym)) $("#tm-sym").value = p.sym;
    else { $("#tm-sym").insertAdjacentHTML("afterbegin", `<option value="${esc(p.sym)}">${esc(p.sym)}</option>`); $("#tm-sym").value = p.sym; }
    setDir(p.direction); $("#tm-entry").value = p.entry; $("#tm-stop").value = p.stop; $("#tm-target").value = p.target;
    if (p.start) $("#tm-start").value = p.start;
    planNote(); window.scrollTo({ top: 0, behavior: "smooth" }); if (go) run();
  };
  const alertPlan = (id) => { const a = TM.setup.alerts.find((x) => String(x.id) === String(id)); if (!a) return null;
    const t = hm(a.ts); return { ...a, start: t >= "09:20" && t < "15:10" ? t : "09:30" }; };
  main.querySelectorAll("[data-tma]").forEach((b) => b.onclick = () => { const p = alertPlan(b.dataset.tma); if (p) load(p, true); });
  function renderHist() {
    const h = LS.get("tm_hist", []) || [];
    const el = $("#tm-hist"); if (!el) return;
    el.innerHTML = h.length ? `<div class="table-wrap"><table><thead><tr><th>Stock</th><th>Plan</th><th>From</th><th class="r">Target first</th><th class="r">Stop first</th><th class="r">Avg</th><th></th></tr></thead><tbody>${h.map((x, i) => `<tr><td><b>${esc(x.sym)}</b></td><td>${arrow(x.direction)} ${fx(x.entry)} · stop ${fx(x.stop)} · target ${fx(x.target)}</td><td>${esc(x.start || "")}</td><td class="r up">${x.tf}%</td><td class="r down">${x.sf}%</td><td class="r"><b class="${x.avg > 0 ? "up" : x.avg < 0 ? "down" : ""}">${x.avg > 0 ? "+" : ""}${x.avg}R</b></td><td class="r"><button class="link-btn" data-tmh="${i}">Run again</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted small">Your tests appear here (kept in this browser).</p>`;
    el.querySelectorAll("[data-tmh]").forEach((b) => b.onclick = () => load(h[Number(b.dataset.tmh)], true));
  }
  renderHist();
  // start state: an alert from the Alerts page, a symbol from the chart, or the last form
  const aid = qp.get("alert");
  const ap = aid ? alertPlan(aid) : null;
  if (ap) load(ap, true);
  else {
    if (keep.dir) setDir(keep.dir);
    priceOf($("#tm-sym").value).then((l) => { if (l && !$("#tm-entry").value) { $("#tm-entry").value = l; fill(); } });
    if (aid) toast("That alert is no longer in today's list: type its plan here.");
  }
}

function tmEmpty() {
  return `<div class="panel tm-empty"><h3>What you get</h3><ul class="plain small">
    <li><b>Target first / stop first / neither</b> by 15:15, out of 10,000 replays.</li>
    <li><b>Break-even check</b>: how often the target must come first for this reward:risk, against how often it did.</li>
    <li><b>Price fan</b>: where the price went in 90% of replays, bar by bar until the square-off.</li>
    <li><b>The real past</b>: the same plan on each of the last 60 sessions, at the same time of day.</li></ul>
    <p class="small muted" style="margin:8px 0 0">History: Upstox 5-minute bars when you're logged in, otherwise Yahoo Finance (free). Works for indices and every F&amp;O stock, market open or closed.</p></div>`;
}

function tmResult(r) {
  const m = r.mc, h = r.history, v = r.verdict, pl = r.plan;
  const cls = v.grade === "edge" ? "up" : v.grade === "against" ? "down" : "amber";
  const word = v.grade === "edge" ? "Leans to the target" : v.grade === "against" ? "Leans to the stop" : "No clear edge";
  return `<div class="panel tm-panel">
    <div class="tm-head"><div><b>${esc(r.sym)}</b> ${arrow(pl.direction)} ${pl.direction === "up" ? "long" : "short"} from ${fx(pl.entry)} · stop ${fx(pl.stop)} · target ${fx(pl.target)} · <b>${pl.r}R</b>
      <div class="small muted">Entry at ${esc(r.start || tmTime(pl.start_bar))} · ${m.replays.toLocaleString("en-IN")} replays to 15:15 from ${h.sessions} sessions (${esc(h.from || "")} → ${esc(h.to || "")}) · seed ${m.seed}</div></div>
      <span class="pill ${v.grade === "edge" ? "p-up" : v.grade === "against" ? "p-down" : "p-amber"}">${word}</span></div>
    <div class="tm-stack" role="img" aria-label="Target first ${m.target_first}%, neither ${m.neither}%, stop first ${m.stop_first}%">
      <i class="t" style="width:${m.target_first}%"></i><i class="n" style="width:${m.neither}%"></i><i class="s" style="width:${m.stop_first}%"></i></div>
    <div class="kpis tm-kpis"><div class="kpi"><b class="up">${m.target_first}%</b><span>Target first</span></div><div class="kpi"><b class="down">${m.stop_first}%</b><span>Stop first</span></div>
      <div class="kpi"><b>${m.neither}%</b><span>Neither by 15:15</span></div><div class="kpi"><b class="${m.avg_r > 0 ? "up" : m.avg_r < 0 ? "down" : ""}">${m.avg_r > 0 ? "+" : ""}${m.avg_r}R</b><span>Average result</span></div></div>
    <p class="tm-do ${cls}"><b>What to do:</b> ${v.grade === "edge" ? (h.avg_r > 0 ? "worth a paper trade: the replays and the real past agree." : "mixed: the replays like it but the real past doesn't. Paper-trade small, or skip.")
      : v.grade === "against" ? "skip it as planned, or tap <b>Find a better stop &amp; target</b>." : "no clear edge. Skip unless you have another reason."}</p>
    <p class="${cls}" style="margin:8px 0 4px">${esc(v.line)}</p>
    ${(v.notes || []).map((n) => `<p class="small" style="margin:2px 0">${esc(n)}</p>`).join("")}
    <p class="small" style="margin:6px 0">Break-even for ${pl.r}R: the target must come first in <b>${v.needed_pct}%</b> of decided replays; it did in <b>${v.got_pct}%</b>.</p>
    ${tmTypical(r.typical, pl.entry)}
    <div id="tm-fan" class="tm-fan"></div>
    <p class="small" style="margin:6px 0">15:15 price: median ${fx(m.close_pct[2])}, 90% of replays between ${fx(m.close_pct[0])} and ${fx(m.close_pct[4])}.</p>
    <p class="small" style="margin:6px 0"><b>Real past</b> (same time of day, same distances, ${h.sessions} sessions): ${h.target} target, ${h.stop} stop, ${h.neither} neither · average ${h.avg_r > 0 ? "+" : ""}${h.avg_r}R.</p></div>`;
}

function tmTypical(t, entry) {
  if (!t) return "";
  const pts = (p) => entry ? ` (${fx(entry * p / 100, 0)} pts)` : "";
  return `<div class="tm-typ"><b>How far it usually moves after this time</b> <span class="muted small">(median of the last 60 sessions)</span>
    <div class="tm-typ-row">${Object.entries(t).map(([k, x]) => `<span><small>${esc(k)}</small> <span class="up">+${x.up_med}%</span>${pts(x.up_med)} / <span class="down">−${x.down_med}%</span></span>`).join("")}</div>
    <p class="small muted" style="margin:4px 0 0">A target much further than the "to 15:15" figure rarely gets hit; a stop inside the 30-minute figure gets hit by noise.</p></div>`;
}

function tmScan(r) {
  const b = r.best, dir = r.direction === "up" ? "long" : "short";
  const head = r.edge
    ? `<p class="tm-do up"><b>Best plan found:</b> stop ${b.stop_pct}% away (${fx(b.stop)}), target ${b.r}R (${fx(b.target)}). Replays ${b.avg_r > 0 ? "+" : ""}${b.avg_r}R, real past ${b.past_avg_r > 0 ? "+" : ""}${b.past_avg_r}R per trade.</p>`
    : `<p class="tm-do down"><b>No stop/target combination had an edge</b> for a ${dir} on ${esc(r.sym)} at ${esc(r.start)} in the last ${r.sessions} sessions. Skip it, or try the other direction or a different time.</p>`;
  const rows = r.rows.slice(0, 10).map((x, i) => `<tr class="${i === 0 && r.edge ? "best" : ""}"><td>${x.stop_pct}% <span class="muted small">${fx(x.stop)}</span></td><td>${x.r}R <span class="muted small">${fx(x.target)}</span></td>
    <td class="r up">${x.target_first}%</td><td class="r down">${x.stop_first}%</td><td class="r"><b class="${x.avg_r > 0 ? "up" : "down"}">${x.avg_r > 0 ? "+" : ""}${x.avg_r}R</b></td>
    <td class="r ${x.past_avg_r > 0 ? "up" : "down"}">${x.past_avg_r > 0 ? "+" : ""}${x.past_avg_r}R</td><td class="r"><button class="link-btn" data-use="${r.rows.indexOf(x)}">Use</button></td></tr>`).join("");
  return `<div class="panel tm-panel"><div class="tm-head"><div><b>${esc(r.sym)}</b> ${arrow(r.direction)} ${dir} from ${fx(r.entry)} at ${esc(r.start)} · 24 plans tested</div></div>
    ${head}${tmTypical(r.typical, r.entry)}
    <div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Stop</th><th>Target</th><th class="r">Target first</th><th class="r">Stop first</th><th class="r">Replays avg</th><th class="r">Real past avg</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin:8px 0 0">${esc(r.note)} Ranked by the weaker of the two averages. "Use" runs the full 10,000-replay test on that plan.</p></div>`;
}

function tmFan(r) {
  const el = document.getElementById("tm-fan");
  if (!el || !window.Plotly || !r.mc.fan) return;
  const f = r.mc.fan, pl = r.plan, n = f.p50.length;
  const x = Array.from({ length: n }, (_, i) => tmTime(pl.start_bar + i + 1));
  const line = (y, color, name, dash) => ({ x: [x[0], x[n - 1]], y: [y, y], mode: "lines", line: { color, width: 1.2, dash }, name, hoverinfo: "name+y" });
  Plotly.newPlot(el, [
    { x, y: f.p95, mode: "lines", line: { width: 0 }, hoverinfo: "skip", name: "95%" },
    { x, y: f.p5, mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(61,47,168,.12)", name: "90% of replays", hovertemplate: "%{y:.2f}" },
    { x, y: f.p50, mode: "lines", line: { color: C.signal || "#3D2FA8", width: 2 }, name: "Median" },
    line(pl.target, "#1E8A5A", "Target", "dash"), line(pl.stop, "#C23B32", "Stop", "dash"), line(pl.entry, "#5E6B78", "Entry", "dot"),
  ], plotLayout({ height: 260, margin: { l: 70, r: 16, t: 8, b: 36 }, hovermode: "x unified", xaxis: { gridcolor: "#EEF1F4", nticks: 8 },
    yaxis: { gridcolor: "#EEF1F4", tickformat: pl.entry >= 1000 ? ",.0f" : ",.2f" } }), plotCfg);
  plotFit(el);
}

/* ---------------- Ask Stockway: questions answered from the app's live data */
const ASK_SAMPLES = ["Bank Nifty aaj kis taraf lag raha hai, aur key levels kya hain?", "Abhi sabse strong setup kaunsa hai? Entry, stop, target ke saath.",
  "FII positioning kya bol rahi hai?", "RELIANCE mein aaj kya ho raha hai?", "Meri paper trading aaj kaisi chal rahi hai?", "Kal ke liye kya dhyan rakhna hai?"];

async function pageAsk() {
  stopPageTimer();
  let st = {};
  try { st = await api("/api/ai/status"); } catch (_) {}
  const hist = () => LS.get("ask_hist", []) || [];
  const fmt = (t) => fmtAI(t);
  const bubble = (h) => `<div class="ask-q">${esc(h.q)}</div><div class="ask-a">${h.a ? fmt(h.a) : `<p class="${h.err ? "down" : "muted"}">${esc(h.err || "Thinking…")}</p>`}${h.by ? `<p class="small muted ask-by">${esc(h.by)}${h.secs ? ` · ${h.secs}s` : ""}</p>` : ""}</div>`;
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Ask Stockway</h1>
      <p class="lede" style="margin-bottom:0">Ask anything about the market right now, in English or Hinglish. The answer comes from the app's own live data: indices, option chains, FII positioning, alerts, your paper trades, insider trades and world cues.</p></div>
      <div style="text-align:right"><span class="pill ${st.enabled ? "p-up" : "p-amber"}">${st.enabled ? esc(st.label) : "No AI connected"}</span>${st.enabled ? `<p class="small muted" style="margin:6px 0 0">${st.used || 0} used today</p>` : ""}</div></div>
    ${st.enabled ? "" : `<div class="banner" style="margin-top:12px">Connect an AI first: <a href="#/connect">Live setup → 4. AI</a>. Your Claude Pro plan works there (through Claude Code on this Mac), no extra payment.</div>`}
    <div class="ask-wrap">
      <div class="ask-log" id="ask-log">${hist().map(bubble).join("") || `<p class="muted">Try one of these:</p>`}</div>
      <div class="chips ask-chips" id="ask-chips">${ASK_SAMPLES.map((q) => `<button class="chip" type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
      <form class="ask-form" id="ask-form" autocomplete="off"><input type="text" id="ask-q" placeholder="e.g. Nifty 25,300 todega kya aaj?" maxlength="800" aria-label="Your question">
        <button class="btn primary" id="ask-send">Ask</button></form>
      <p class="small muted" style="margin:6px 0 0">${hist().length ? `<button class="link-btn small" id="ask-clear" type="button">Clear chat</button> · ` : ""}Answers use the data in the app at this moment. Stockway never places orders: ideas come as alerts, paper trades or Time Machine tests.</p>
    </div>`;
  const log = $("#ask-log");
  log.scrollTop = log.scrollHeight;
  const send = async (q) => {
    q = (q || "").trim(); if (!q) return;
    const h = hist(); const item = { q }; h.push(item);
    log.innerHTML = h.map(bubble).join(""); log.scrollTop = log.scrollHeight;
    $("#ask-q").value = ""; $("#ask-send").disabled = true;
    try {
      const r = await post("/api/ask", { q, history: h.slice(-4, -1).filter((x) => x.a).map((x) => ({ q: x.q, a: x.a })) });
      Object.assign(item, { a: r.answer, by: r.by, secs: r.secs });
    } catch (e) { item.err = e.message; }
    LS.set("ask_hist", h.slice(-20));
    if (state.page !== "ask") return;
    log.innerHTML = h.map(bubble).join(""); log.scrollTop = log.scrollHeight; $("#ask-send").disabled = false; $("#ask-q").focus();
  };
  $("#ask-form").onsubmit = (e) => { e.preventDefault(); send($("#ask-q").value); };
  main.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => send(b.dataset.q));
  if ($("#ask-clear")) $("#ask-clear").onclick = () => { LS.set("ask_hist", []); pageAsk(); };
  const pre = new URLSearchParams(location.hash.split("?")[1] || "").get("q");
  if (pre) send(pre);
}

/* ---------------- page: Watchlist (yours + smart picks) */
async function pageWatchlist() {
  stopPageTimer();
  const syms = await symbolList().catch(() => ({ indices: [], stocks: [] }));
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Watchlist</h1><p class="lede" style="margin-bottom:0">Your symbols and the system's smart picks, live on one screen. Click a name for the chart; B / S = paper buy of the at-the-money call / put.</p></div>
      <form id="wl-add" class="btn-row" style="margin:0"><input id="wl-sym" list="wl-syms" placeholder="Add symbol, e.g. RELIANCE" autocomplete="off" style="min-width:220px">
        <datalist id="wl-syms">${[...(syms.indices || []), ...(syms.stocks || [])].map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
        <button class="btn primary">Add</button></form></div>
    <div id="wl-body"><p class="muted">Loading…</p></div>`;
  $("#wl-add").onsubmit = async (e) => { e.preventDefault(); const v = $("#wl-sym").value.trim().toUpperCase(); if (!v) return;
    try { await post("/api/watchlist", { add: v }); $("#wl-sym").value = ""; toast(`${v} added`); tick(); } catch (err) { toast(err.message); } };
  $("#wl-body").addEventListener("click", async (ev) => {
    const t = ev.target.closest("[data-wl]"); if (!t) return;
    ev.preventDefault(); const sym = t.dataset.sym, act = t.dataset.wl; t.disabled = true;
    try {
      if (act === "add" || act === "remove") { await post("/api/watchlist", { [act]: sym }); toast(act === "add" ? `${sym} added` : `${sym} removed`); }
      else { const r = await post("/api/live/paper/open", { sym, side: act }); toast(`Paper ${act === "buy" ? "BUY" : "SELL"}: ${r.label || sym} · see Paper trades`); navPnl(); }
    } catch (err) { toast(err.message); }
    t.disabled = false; tick();
  });
  const tick = async () => {
    if (state.page !== "watchlist") return;
    try { drawWatchlist(await api("/api/watchlist")); } catch (e) { $("#wl-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    clearTimeout(L.pageTimer); L.pageTimer = setTimeout(tick, 3000);
  };
  tick();
}

function drawWatchlist(w) {
  const el = $("#wl-body"); if (!el) return;
  if (el.contains(document.activeElement) && document.activeElement.tagName === "BUTTON") return;
  const pc = (v, d = 2) => v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${v.toFixed(d)}%</span>`;
  const row = (r, pick) => {
    const a = r.last_alert;
    const alert = a ? `<span class="pill ${a.direction === "up" ? "p-up" : a.direction === "down" ? "p-down" : ""}">${a.direction === "up" ? "BUY" : a.direction === "down" ? "SELL" : "INFO"}</span> <small>${hm(a.ts)} ${esc(a.title)}${a.risk ? ` · risk ${esc(a.risk)}` : ""}</small>` : `<small class="muted">no alert today</small>`;
    const act = pick ? `<button class="link-btn" data-wl="add" data-sym="${esc(r.sym)}">+ Watch</button>` : `<button class="link-btn muted" data-wl="remove" data-sym="${esc(r.sym)}" title="Remove">✕</button>`;
    if (!r.live) return `<tr><td><a href="#/chart?s=${encodeURIComponent(r.sym)}"><b>${esc(r.sym)}</b></a>${pick ? `<br><small class="muted">${esc(r.why || "")}</small>` : ""}</td><td colspan="8" class="muted small">No live data (scanner off, or not in today's scan).</td><td>${act}</td></tr>`;
    return `<tr><td><a href="#/chart?s=${encodeURIComponent(r.sym)}"><b>${esc(r.sym)}</b></a> ${r.paper_open ? `<span class="pill p-amber" title="Open paper position">paper</span>` : ""}<br><small class="muted">${pick ? esc(r.why || "") : esc((r.reasons || []).join(" · "))}</small></td>
      <td class="r"><b>${fx(r.spot)}</b><br>${pc(r.chg_pct)}</td>
      <td class="r">${pc(r.ret_5m)}<br><small class="muted">15m ${pc(r.ret_15m)}</small></td>
      <td class="r">${pc(r.vs_vwap_pct)}<br><small class="muted">vs VWAP</small></td>
      <td class="r">${r.rel_vol ? r.rel_vol.toFixed(1) + "×" : "–"}<br><small class="muted">volume</small></td>
      <td>${r.buildup ? `<span class="pill ${r.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(r.buildup)}</span>` : "<small class='muted'>–</small>"}<br><small class="muted">IV ${r.atm_iv ? r.atm_iv.toFixed(1) : "–"} · PCR ${r.pcr != null ? r.pcr.toFixed(2) : "–"}</small></td>
      <td class="r small">${fx(r.day_l)} – ${fx(r.day_h)}<br><span class="up">${r.support ? fk(r.support) : "–"}</span> / <span class="down">${r.resistance ? fk(r.resistance) : "–"}</span></td>
      <td>${biasMeter(r.score)}</td>
      <td>${alert}</td>
      <td class="nw"><span class="bs"><button class="b" data-wl="buy" data-sym="${esc(r.sym)}" title="Paper: buy ATM call">B</button><button class="s" data-wl="sell" data-sym="${esc(r.sym)}" title="Paper: buy ATM put">S</button></span> <a class="small" href="#/sym?s=${encodeURIComponent(r.sym)}">Chain</a> ${act}</td></tr>`;
  };
  const head = `<thead><tr><th>Symbol</th><th class="r">Price</th><th class="r">5 min</th><th class="r">VWAP</th><th class="r">Vol</th><th>Futures · options</th><th class="r">Range · S / R</th><th>Bias</th><th>Last alert</th><th></th></tr></thead>`;
  el.innerHTML = `${w.running ? "" : `<div class="banner">Live scanner is off: prices fill in when it runs (it starts by itself before every NSE session).</div>`}
    <h2>Your watchlist <span class="muted small">${w.manual.length}</span></h2>
    <div class="table-wrap"><table class="wl">${head}<tbody>${w.manual.map((r) => row(r, false)).join("") || `<tr><td colspan="10" class="muted">Add symbols above. They stay saved.</td></tr>`}</tbody></table></div>
    <h2>Smart picks <span class="muted small">Focus alerts in the last 90 min, then the most active F&amp;O stocks</span></h2>
    <div class="table-wrap"><table class="wl">${head}<tbody>${w.picks.map((r) => row(r, true)).join("") || `<tr><td colspan="10" class="muted">Picks appear while the scanner runs.</td></tr>`}</tbody></table></div>
    <p class="note">Bias = the scanner's combined read (trend, VWAP, flow, options). Support / resistance = biggest put / call open interest. Paper only.</p>`;
}

/* ---------------- page: Strategy Lab (lab/factory.py) */
async function pageLab() {
  stopPageTimer();
  const draw = async () => {
    let f;
    try { f = await api("/api/factory"); } catch (e) { main.innerHTML = `<div class="empty"><h2>Strategy Lab</h2><p>${esc(e.message)}</p></div>`; return; }
    const by = f.by_stage || {}, prom = by.promoted || 0;
    const rs = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
    const st = (x) => x && x.n ? `${x.n} · ${rs(x.avg)} · t ${x.t} · ${x.green_pct}% green` : `<span class="muted">–</span>`;
    const stage = { promoted: ["p-up", "Promoted: passed all 3"], duplicate: ["", "Same trades as another rule"], "failed holdout": ["p-down", "Failed holdout"], "failed validation": ["", "Failed validation"], "failed train": ["", "Failed train"] };
    const src = (s) => s === "generated" || s === "mutation" ? "search" : s.startsWith("pack:") ? "🗂 " + esc(s.slice(5)) : s.startsWith("fusion:") ? "🔗 " + esc(s.slice(7)) : s.startsWith("ebook:") ? "📕 " + esc(s.slice(6)) : s.startsWith("library:") ? "📚 " + esc(s.slice(8)) : s.startsWith("book:") ? "📗 " + esc(s.slice(5)) : s === "claude" ? "Claude" : esc(s);
    const fits = (r) => r.dir !== 0 || String(r.exit || "").startsWith("fly");
    const only40 = LS.get("lab40", false);
    const rows = (f.rules || []).filter((r) => r.stage !== "failed train" && r.stage !== "duplicate" && (!only40 || fits(r))).slice(0, 60).map((r) => `<tr class="click" data-rexp="${esc(r.id)}" title="Show every stock / index this rule traded">
        <td><span class="pill ${stage[r.stage] ? stage[r.stage][0] : ""}">${stage[r.stage] ? stage[r.stage][1] : esc(r.stage)}</span>${r.stage === "promoted" ? ` <span class="pill ${r.evidence === "strong" ? "p-up" : r.evidence === "moderate" ? "p-amber" : ""}" title="Holdout t-stat: strong ≥ 2, moderate ≥ 1">evidence: ${esc(r.evidence || "weak")}</span>` : ""}<br><small class="muted">${r.id} · ${src(r.source || "")}</small></td>
        <td class="small">${esc(r.rule)}${r.note ? `<br><span class="muted">${esc(r.note)}</span>` : ""}${r.live_record ? `<br><b>Live paper:</b> ${r.live_record.n} trades, ${rs(r.live_record.net)}` : ""}</td>
        <td class="small">${st(r.train)}</td><td class="small">${st(r.valid)}</td><td class="small"><b>${st(r.hold)}</b>${r.hold && r.hold.n ? `<br>worst day ${rs(r.hold.worst_day)}` : ""}</td>
        <td>${r.stage === "promoted" ? `<label class="check small"><input type="checkbox" data-rlive="${r.id}" ${r.is_live ? "checked" : ""}><span>Live paper</span></label>` : ""}<small class="muted">stocks ▸</small></td></tr>
        <tr class="exp" data-rrow="${esc(r.id)}" hidden><td colspan="6"></td></tr>`).join("");
    const books = (f.books || []).map((b) => `<tr><td>📕 ${esc(b.name)} <small class="muted">${b.mb} MB</small></td>
        <td class="small">${b.error ? `<span class="down">${esc(b.error)}</span>` : b.done ? `${b.rules} rules found (${esc(b.at)})${b.cannot.length ? `<br><span class="muted">Can't test yet: ${b.cannot.slice(0, 4).map((c) => esc(c.idea || "") + (c.needs ? ` (needs ${esc(c.needs)})` : "")).join("; ")}</span>` : ""}` : `<span class="muted">not read yet</span>`}</td>
        <td><button class="btn" data-read="${esc(b.name)}" ${f.ai ? "" : "disabled title='Connect Claude in Live setup first'"}>${b.done ? "Read again" : "Read with Claude"}</button></td></tr>`).join("");
    const runs = (f.runs || []).slice().reverse().map((r) => `<tr><td>${esc((r.at || "").replace("T", " "))}</td><td class="r">${r.tried.toLocaleString("en-IN")}</td><td class="r">${r.train_pass}</td><td class="r"><b>${r.promoted}</b></td><td class="r">${r.luck_expected}</td><td class="r">${r.secs}s</td></tr>`).join("");
    main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Strategy Lab</h1><p class="lede" style="margin-bottom:0">Thousands of intraday option rules tested at once on 2 years of real 1-minute option prices, from a search, your ebooks and Claude. A rule must win on three separate periods before it trades live (on paper). Every evening it runs again by itself.</p></div>
        <div class="btn-row" style="margin:0"><button class="btn primary" id="lab-run" ${f.has_history ? "" : "disabled"}>Run research now</button><button class="btn" id="lab-lib">Test the strategy library</button><button class="btn" id="lab-ideas" ${f.ai ? "" : "disabled title='Connect Claude in Live setup first'"}>Ask Claude for new ideas</button></div></div>
      ${f.has_history ? "" : `<div class="banner">Download the 1-minute option history first: Strategy desk → Download history (Upstox Plus).</div>`}
      <div class="panel tight lab-guide" style="margin-top:14px"><h3 style="margin-top:0">Start here</h3>
        <ol class="small">
          <li><b>What it does:</b> tests trading rules on years of real 1-minute option prices, with real charges. Nothing here trades real money.</li>
          <li><b>Three exams:</b> <b>Train</b> is where a rule is found, <b>Validation</b> checks it on later days, <b>Holdout</b> is days never used to choose anything: the honest test.</li>
          <li><b>Green "Passed all 3"</b> = good enough to paper-trade live. Most rules fail; that is normal and saves money.</li>
          <li><b>Click any strategy or rule</b> to see every stock and index it was tested on, with its result in each exam.</li>
          <li><b>The numbers:</b> trades · average ₹ per trade after charges · <b>t</b> = how sure (2+ strong, 1–2 some, below 1 could be luck) · green = % of days that ended in profit.</li></ol></div>
      <div class="panel tight" id="lab-packs" style="margin-top:14px"></div>
      <div class="kpis kpi6" style="margin-top:16px">
        <div class="kpi"><b>${(f.tried_total || 0).toLocaleString("en-IN")}</b><span>Rules tried so far</span></div>
        <div class="kpi"><b class="${prom ? "up" : ""}">${prom}</b><span>Passed all 3 periods (promoted)</span></div>
        <div class="kpi"><b>≤ ${f.luck_expected}</b><span>Would pass by pure luck (upper estimate)</span></div>
        <div class="kpi"><b>${(by["failed validation"] || 0) + (by["failed holdout"] || 0)}</b><span>Looked good in train, then failed</span></div>
        <div class="kpi"><b>${(f.live_rules || []).length}</b><span>Rules live on paper now</span></div>
        <div class="kpi"><b>${f.n_days || "–"}</b><span>Days of history</span></div></div>
      <div class="panel tight" style="margin-top:14px"><h3>How to read this</h3><ul class="small">
        <li><b>Train</b> ${f.period ? `(${esc(f.period.train[0])} → ${esc(f.period.train[1])})` : ""} finds a rule; <b>Validation</b> ${f.period ? `(→ ${esc(f.period.valid[1])})` : ""} confirms it; <b>Holdout</b> ${f.period ? `(${esc(f.period.hold[0])} → ${esc(f.period.hold[1])})` : ""} was never used to choose anything: it is the honest test.</li>
        <li>Each trade buys 1 lot of the at-the-money call or put, after real charges and slippage. A day stops after a losing trade (one loss ends the day), so "green" = % of trading days that ended in profit.</li>
        <li>If promoted ≈ the luck number, nothing real was found yet. ${prom > f.luck_expected ? "" : `<b>That is the case right now</b>: ${prom} promoted vs up to ${f.luck_expected} by luck, so even the promoted rules may be luck. They trade on paper only to build a live record, and switch off by themselves if they lose over 15 trades. The search keeps going every evening, with your ebooks and Claude's ideas.`}</li></ul></div>
      <div class="toolbar" style="margin-top:28px"><h2 style="margin:0">Rules <span class="muted small">promoted first, then the closest misses</span></h2>
        <label class="check small"><input type="checkbox" id="lab-40" ${only40 ? "checked" : ""}><span>Only option buying and iron fly (needs the least money)</span></label></div>
      <div class="table-wrap"><table class="lab"><thead><tr><th>Result</th><th>Rule</th><th>Train<br><small class="muted">trades · avg · t · green</small></th><th>Validation</th><th>Holdout</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">Run research to fill this.</td></tr>`}</tbody></table></div>
      <details class="lab-adv" id="lab-data" style="margin-top:20px"><summary><b>Data on your Mac</b> <span class="muted small">stock options (1 year) and the 5-year archive: download status</span></summary>
        <div class="panel tight" id="lab-stocks" style="margin-top:10px"></div>
        <div class="panel tight" id="lab-5y" style="margin-top:14px"></div></details>
      <details class="lab-adv" style="margin-top:10px"><summary><b>Advanced</b> <span class="muted small">exit styles, ML models, strategy library, ebooks, research runs</span></summary>
      ${labExits(f)}${labML(f)}
      ${(f.library || []).length ? `<h2>Strategy library <span class="muted small">famous setups from top traders and books (incl. your basics books, marked "book"), every exit and indicator setting tested</span></h2>
      <div class="table-wrap"><table class="lab"><thead><tr><th>Setup</th><th>Best version</th><th>Train</th><th>Validation</th><th>Holdout</th></tr></thead><tbody>
        ${f.library.map((x) => { const r = x.best; return `<tr><td><b>${esc(x.name)}</b><br><span class="pill ${stage[r.stage] ? stage[r.stage][0] : ""}">${stage[r.stage] ? stage[r.stage][1] : esc(r.stage)}</span> <small class="muted">${x.n_rules} versions</small></td>
          <td class="small">${esc(r.rule)}<br><span class="muted">${esc((r.note || "").split(". ").slice(1).join(". "))}</span></td><td class="small">${st(r.train)}</td><td class="small">${st(r.valid)}</td><td class="small"><b>${st(r.hold)}</b></td></tr>`; }).join("")}</tbody></table></div>` : ""}

      <div class="split" style="margin-top:24px">
        <div><h2 style="margin-top:0">Your ebooks</h2>
          <p class="small muted">Upload trading-strategy books (PDF, EPUB, TXT). Claude reads the rule-heavy passages, turns each concrete rule into a testable one, and lists what it can't test yet. They face the same 3-period test. The book stays on your Mac.</p>
          <div class="btn-row"><input type="file" id="lab-file" accept=".pdf,.epub,.txt,.md" multiple><button class="btn" id="lab-up">Upload</button></div>
          <div class="table-wrap" style="margin-top:10px"><table><tbody>${books || `<tr><td class="muted">No books yet.</td></tr>`}</tbody></table></div>
          ${f.pending_ideas ? `<p class="note">${f.pending_ideas} rules from books / Claude are waiting: they are tested on the next run.</p>` : ""}</div>
        <div><h2 style="margin-top:0">Research runs</h2><div class="table-wrap"><table class="mini"><thead><tr><th>When</th><th class="r">Tried</th><th class="r">Train pass</th><th class="r">Promoted</th><th class="r">Luck</th><th class="r">Time</th></tr></thead><tbody>${runs || `<tr><td colspan="6" class="muted">None yet.</td></tr>`}</tbody></table></div></div></div></details>
      <p class="note">Paper only. Promoted rules with moderate or strong holdout evidence (or ones you tick) fire as "Strategy Lab" alerts and are paper-traded with their own exit (straddle sells only when the paper account is ₹1.5 lakh or more); a rule that loses money over 15 live paper trades is switched off automatically.</p>`;
    const job = async (path, body = {}) => { try { await post(path, body); state.jobSince = 0; $("#job-log").textContent = ""; watchJob(); } catch (e) { toast(e.message); } };
    $("#lab-run").onclick = () => job("/api/factory/run", { n: 8000 });
    $("#lab-40").onchange = (e) => { LS.set("lab40", e.target.checked); draw(); };
    $("#lab-lib").onclick = async () => { try { const r = await post("/api/factory/library", {}); toast(`${r.queued} library rules queued: running the test now`); job("/api/factory/run", { n: 4000 }); } catch (e) { toast(e.message); } };
    $("#lab-ideas").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "Claude is thinking…";
      try { const r = await post("/api/factory/ideas", {}); toast(`Claude proposed ${r.queued} testable rules${r.rejected ? ` (${r.rejected} not usable)` : ""}: tested on the next run`); draw(); } catch (err) { toast(err.message); e.target.disabled = false; e.target.textContent = "Ask Claude for new ideas"; } };
    $("#lab-up").onclick = async () => {
      const files = [...($("#lab-file").files || [])]; if (!files.length) { toast("Choose a file first"); return; }
      for (const fl of files) { try { const r = await fetch(`/api/factory/ebook?name=${encodeURIComponent(fl.name)}`, { method: "POST", body: fl }); if (!r.ok) throw new Error((await r.json()).detail); toast(`${fl.name} uploaded`); } catch (e) { toast(e.message); } }
      draw(); };
    main.querySelectorAll("[data-read]").forEach((b) => b.onclick = () => job("/api/factory/ebook/read", { name: b.dataset.read }));
    drawStocks();
    draw5y();
    drawPacks();
    main.querySelectorAll("[data-rexp]").forEach((tr) => tr.onclick = (ev) => { if (ev.target.closest("input,label,a,button")) return;
      const row = main.querySelector(`[data-rrow="${CSS.escape(tr.dataset.rexp)}"]`); if (!row) return;
      row.hidden = !row.hidden; if (!row.hidden && !row.dataset.done) { row.dataset.done = "1"; labRuleDetail(row.firstElementChild, [tr.dataset.rexp], ["1y"]); } });
    main.querySelectorAll("[data-rlive]").forEach((c) => c.onchange = async () => { try { await post(`/api/factory/rule/${c.dataset.rlive}`, { on: c.checked }); toast(c.checked ? "Rule live on paper" : "Rule switched off"); } catch (e) { toast(e.message); } });
  };
  let t5 = null;
  const drawPacks = async () => {                // 2.43: strategy files (lab/packs.py)
    const el = $("#lab-packs"); if (!el) return;
    let x; try { x = await api("/api/packs"); } catch (e) { el.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; return; }
    const rs = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
    const res = (L) => !L || !L.tested ? `<span class="muted">not tested yet</span>`
      : `<span class="pill ${L.stage === "promoted" ? "p-up" : L.stage === "failed holdout" ? "p-down" : ""}">${L.stage === "promoted" ? "passed all 3" : esc(L.stage || "")}</span>
         <br><small>${L.passed}/${L.of} versions passed · ${L.stage === "promoted" ? "best" : "best on train"}: train ${L.train && L.train.n ? `${L.train.n} trades, avg ${rs(L.train.avg)}` : "no trades"} · holdout ${L.hold && L.hold.n ? `${L.hold.n} trades, avg ${rs(L.hold.avg)}, t ${L.hold.t}` : "no trades"}</small>`;
    const openPk = LS.get("packOpen", {});            // 2.53: each file opens / closes; remembered on this browser
    const labSum = (p, lab) => {                       // one line per Lab for a closed card
      const ok = (p.strategies || []).filter((s) => s.status === "ok"), t = ok.filter((s) => ((s.labs || {})[lab] || {}).tested);
      if (!t.length) return `not tested yet`;
      const pass = t.filter((s) => s.labs[lab].stage === "promoted").length;
      return `<b class="${pass ? "up" : ""}">${pass}</b> of ${t.length} passed all 3`;
    };
    const packs = (x.packs || []).map((p) => { const op = !!openPk[p.id], ss = p.strategies || [];
      const nOk = ss.filter((s) => s.status === "ok").length, nNo = ss.length - nOk;
      return `<div class="pack-card${p.on ? "" : " off"}${op ? " open" : ""}" data-pk="${esc(p.id)}">
        <div class="toolbar pack-head" style="margin:0"><div class="pack-tg" role="button" tabindex="0" aria-expanded="${op}" title="${op ? "Close" : "Open"} this file">
          <span class="pack-car" aria-hidden="true">▸</span> <b>${esc(p.name || p.id)}</b> <small class="muted">v${esc(String(p.version || 1))} · ${esc(p.id)} · ${p.n_rules} Lab rules${p.made ? " · made " + esc(p.made) : ""}</small>
          ${p.source ? `<br><small class="muted">${esc(p.source)}</small>` : ""}
          <div class="small pack-sum">${ss.length} strateg${ss.length === 1 ? "y" : "ies"}${nNo ? ` (${nNo} can't be tested yet)` : ""} · 1-year Lab: ${labSum(p, "1y")} · 5-year Lab: ${labSum(p, "5y")}${p.fatal ? ` · <span class="down">problem: open to see</span>` : ""}</div></div>
          <label class="check small"><input type="checkbox" data-pack="${esc(p.id)}" ${p.on ? "checked" : ""}><span>On (Lab + test bench)</span></label></div>
        <div class="pack-body"${op ? "" : " hidden"}>
        ${p.fatal ? `<p class="small down">${esc(p.fatal)}</p>` : ""}
        <div class="table-wrap" style="margin-top:6px"><table class="mini"><thead><tr><th>Strategy</th><th>1-year Lab</th><th>5-year Lab</th></tr></thead><tbody>
          ${(p.strategies || []).map((s) => `<tr${s.status === "ok" && (s.ids || []).length ? ` class="click" data-sexp="${esc((s.ids || []).join(","))}" title="Show every version and every stock / index it was tested on"` : ""}><td class="small"><b>${esc(s.name)}</b>${s.status === "ok" ? ` <small class="muted">▸</small>` : ""}${s.status !== "ok" ? `<br><span class="${s.status === "cannot" ? "muted" : "down"}">${s.status === "cannot" ? "Can't test yet: " : ""}${esc(s.why)}</span>` : `<br><small class="muted">${s.rules} version(s)</small>`}</td>
            <td class="small">${s.status === "ok" ? res((s.labs || {})["1y"]) : "–"}</td><td class="small">${s.status === "ok" ? res((s.labs || {})["5y"]) : "–"}</td></tr>${s.status === "ok" ? `<tr class="exp" hidden><td colspan="3"></td></tr>` : ""}`).join("")}
        </tbody></table></div>
        ${(p.cannot_test || []).length ? `<p class="small muted" style="margin:6px 0 0">Can't test yet: ${p.cannot_test.slice(0, 6).map((c) => esc(c.idea) + (c.needs ? ` (needs ${esc(c.needs)})` : "")).join("; ")}</p>` : ""}
        ${(p.problems || []).length ? `<details class="small" style="margin-top:4px"><summary>${p.problems.length} note(s) from the check</summary><ul>${p.problems.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></details>` : ""}
      </div></div>`; }).join("");
    const nPk = (x.packs || []).length, allOpen = nPk && (x.packs || []).every((p) => openPk[p.id]);
    el.innerHTML = `<div class="toolbar" style="margin:0"><div><h3 style="margin:0">Strategy files <span class="pill">no app update needed</span></h3>
        <p class="small muted" style="margin:4px 0 0">Upload a strategy file (.json) made from a book, video, transcript or script. The app checks it against the facts and exits it knows, keeps every upload, tests each strategy in the 1-year and 5-year Lab on the next run (train / validation / holdout) and paper-tests it live on the test bench. It never goes on live paper or in your alerts by itself. Data only: nothing in a file is ever run as code, and there is no field for orders.</p></div>
        <div class="btn-row" style="margin:0"><input type="file" id="pack-file" accept=".json,application/json"><button class="btn primary" id="pack-up">Upload</button></div></div>
      ${nPk > 1 ? `<div class="toolbar" style="margin:10px 0 0"><span class="small muted">${nPk} files · click a file to open or close it</span>
        <button class="link-btn" id="pack-all" type="button">${allOpen ? "Close all" : "Open all"}</button></div>` : ""}
      <div style="margin-top:8px">${packs || `<p class="small muted">No strategy files yet.</p>`}</div>`;
    const setOpen = (card, op) => { const id = card.dataset.pk, st = LS.get("packOpen", {});
      if (op) st[id] = 1; else delete st[id]; LS.set("packOpen", st);
      card.classList.toggle("open", op); card.querySelector(".pack-body").hidden = !op;
      const tg = card.querySelector(".pack-tg"); tg.setAttribute("aria-expanded", String(op)); tg.title = `${op ? "Close" : "Open"} this file`;
      const all = $("#pack-all"); if (all) all.textContent = [...el.querySelectorAll(".pack-card")].every((c) => c.classList.contains("open")) ? "Close all" : "Open all"; };
    el.querySelectorAll(".pack-card").forEach((card) => { const tg = card.querySelector(".pack-tg");
      tg.onclick = (ev) => { if (ev.target.closest("a")) return; setOpen(card, !card.classList.contains("open")); };
      tg.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setOpen(card, !card.classList.contains("open")); } }; });
    const all = $("#pack-all");
    if (all) all.onclick = () => { const op = all.textContent === "Open all"; el.querySelectorAll(".pack-card").forEach((c) => setOpen(c, op)); };
    $("#pack-up").onclick = async () => {
      const fl = ($("#pack-file").files || [])[0]; if (!fl) { toast("Choose a .json strategy file first"); return; }
      try { const r = await fetch(`/api/packs/upload?name=${encodeURIComponent(fl.name)}`, { method: "POST", body: fl });
        const j = await r.json(); if (!r.ok) throw new Error(j.detail || "Upload failed");
        if (j.id) { const st = LS.get("packOpen", {}); st[j.id] = 1; LS.set("packOpen", st); }   // the file you just added opens
        toast(j.already ? `${j.name}: this exact file is already here` : `${j.name} v${j.version}: ${j.rules} Lab rules queued for the next run${(j.cannot_test || []).length ? `, ${j.cannot_test.length} can't be tested yet` : ""}`);
      } catch (e) { toast(e.message); }
      drawPacks(); };
    el.querySelectorAll("[data-sexp]").forEach((tr) => tr.onclick = () => { const row = tr.nextElementSibling; if (!row || !row.classList.contains("exp")) return;
      row.hidden = !row.hidden; if (!row.hidden && !row.dataset.done) { row.dataset.done = "1"; labRuleDetail(row.firstElementChild, tr.dataset.sexp.split(","), ["1y", "5y"]); } });
    el.querySelectorAll("[data-pack]").forEach((c) => c.onchange = async () => { try { await post(`/api/packs/${c.dataset.pack}/on`, { on: c.checked }); toast(c.checked ? "Strategy file on" : "Strategy file off (results kept)"); drawPacks(); } catch (e) { toast(e.message); } });
  };
  const draw5y = async () => {
    const el = $("#lab-5y"); if (!el) return;
    let x; try { x = await api("/api/factory/lab5y"); } catch (e) { el.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; return; }
    const ms = x.months || {}, nm = Object.values(ms).reduce((a, b) => a + b, 0);
    const ov = x.overview || {}, tk = x.token || {}, f30 = ov.first30 || {};
    const pct = ov.pct || 0;
    const c = x.compare || {}, A = c.current || {}, B = c.five_year || {};
    const row = (k, l) => `<tr><td>${l}</td><td class="r">${A[k] ?? "–"}</td><td class="r">${B[k] ?? "–"}</td></tr>`;
    const n = (v) => (v ?? 0).toLocaleString("en-IN");
    const why = tk.dead ? `<span class="down"><b>Dhan token expired${tk.expires ? " (" + esc(tk.expires) + ")" : ""}.</b> <a href="/dhan/login" target="_blank">Log in with Dhan</a> (if your API key is saved), or paste a new token in Strategy desk → Dhan keys. The download resumes by itself.</span>`
      : x.running ? `Downloading · ${esc((x.log || []).slice(-1)[0] || "starting…")}`
      : x.user_stop ? `Stopped by you. Press <b>Update</b> to continue (it also resumes by itself tomorrow).`
      : x.status === "budget" ? `Today's Dhan budget is used. It continues by itself after midnight.`
      : x.status === "disk" ? `<span class="down">Less than 10 GB free on the Mac.</span> Free some space and press Update.`
      : x.status === "failed" ? `<span class="down">${esc(x.error || "failed")}</span> · retries by itself in a few minutes`
      : ov.months_pending === 0 && ov.months_total ? `Complete up to yesterday. Each new day is added by itself.`
      : nm ? `Waiting · it resumes by itself while the app is open` : "";
    el.innerHTML = `<div class="toolbar" style="margin:0"><div><h3 style="margin:0">5-year Lab <span class="pill p-amber">challenger</span> <span class="muted small">${ov.months_total ? `${n(ov.months_complete)} of ${n(ov.months_total)} months complete (${pct}%) · ${ov.symbols} symbols` : nm ? `${nm} months from Dhan on your Mac` : "no Dhan data yet"}</span></h3>
        <p class="small muted" style="margin:4px 0 0">Dhan's archive of 1-minute options from 2021: Nifty and Bank Nifty (ATM ±10) and every NSE stock with options (the 30 most liquid first; top 80 by turnover ATM ±3, the rest ATM ±1), joined with the Upstox history and tested in its own folder. Dhan serves one trading day per call and allows 100,000 calls a day, so the speed is set by that limit. Every missing day is fetched, also after the app was closed, the Mac slept or the internet dropped. The current Lab and its live rules are not touched; switching is your call. Market data only.</p></div>
        <div class="btn-row" style="margin:0">${x.running ? `<button class="btn" id="d5-stop">Stop</button>` : `<button class="btn ${nm ? "" : "primary"}" id="d5-go" ${x.dhan && !tk.dead ? "" : "disabled title='Add a valid Dhan token on Strategy desk → Dhan keys'"}>${nm ? "Update" : "Download 5 years"}</button>`}<button class="btn ${nm ? "primary" : ""}" id="d5-run" ${nm && !x.running ? "" : "disabled"}>Run 5-year research</button></div></div>
      ${ov.months_total ? `<div style="margin-top:8px"><div class="bar"><i style="width:${pct}%"></i></div>
        <small>${why}</small>
        <div class="small muted" style="margin-top:4px;line-height:1.6">
          Months: <b>${n(ov.months_complete)}</b> complete · <b>${n(ov.months_partial)}</b> partly on disk · <b>${n(ov.months_pending)}</b> pending (${n(ov.days_missing)} trading days, about ${n(ov.calls_left)} calls)<br>
          Nifty, Bank Nifty and the first 30 stocks: ${n(f30.complete)} / ${n(f30.total)} months<br>
          Dhan calls today: <b>${n(x.calls_today)}</b> / ${n(ov.cap)} for the download (Dhan's limit ${n(ov.dhan_limit)}) · about ${ov.eta_days ?? "–"} day(s) of calls left · disk free ${ov.free_gb ?? "–"} GB<br>
          Token: ${tk.dead ? `<span class="down">expired</span>` : tk.expires ? `valid till ${esc(tk.expires)} (${tk.hours_left} h), renews by itself${tk.renewed ? " · last renewed " + esc(tk.renewed) : ""}` : "–"}
        </div></div>` : why ? `<p class="small" style="margin:8px 0 0">${why}</p>` : ""}
      ${B.updated ? `<div class="table-wrap" style="margin-top:8px"><table class="mini"><thead><tr><th></th><th class="r">Current (2 years)</th><th class="r">5-year</th></tr></thead><tbody>
        ${row("days", "Days of history")}${row("tried", "Rules tried")}${row("promoted", "Passed all 3 periods")}${row("luck", "Would pass by luck")}${row("strong", "Moderate / strong evidence")}${row("buy_strong", "…of which option buying")}</tbody></table></div>` : ""}`;
    const go = $("#d5-go"), stp = $("#d5-stop"), run = $("#d5-run");
    if (go) go.onclick = async () => { try { await post("/api/factory/lab5y/download", {}); toast("5-year download started (Dhan)"); draw5y(); } catch (e) { toast(e.message); } };
    if (stp) stp.onclick = async () => { await post("/api/factory/lab5y/stop", {}); toast("Stopping: finished months are kept"); draw5y(); };
    if (run) run.onclick = async () => { try { await post("/api/factory/lab5y/run", { n: 8000 }); state.jobSince = 0; $("#job-log").textContent = ""; watchJob(); } catch (e) { toast(e.message); } };
    clearTimeout(t5);
    t5 = setTimeout(() => { if (document.getElementById("lab-5y")) draw5y(); }, x.running ? 5000 : 30000);
  };
  let stTimer = null;
  const drawStocks = async () => {
    const el = $("#lab-stocks"); if (!el) return;
    let x; try { x = await api("/api/factory/stocks"); } catch (e) { el.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; return; }
    const pct = x.total ? Math.round(x.done / x.total * 100) : 0;
    const have = (x.stocks_on_disk || []).length;
    el.innerHTML = `<div class="toolbar" style="margin:0"><div><h3 style="margin:0">Stock options data <span class="muted small">${have ? `${have} stocks · ${x.expiries_on_disk} monthly expiries on your Mac` : "not downloaded yet"}</span></h3>
        <p class="small muted" style="margin:4px 0 0">1-minute option prices of ${(x.default || []).length} liquid F&O stocks (Reliance, HDFC Bank, ICICI, SBI, Infosys…) from Upstox Plus, newest month first. With it the Lab tests rules on stock options too ("any F&O stock", one trade at a time) and the conviction model learns faster. About 6–8 hours for 12 months (Upstox's speed limit), runs in the background, resumes if stopped, adds each new month by itself after the close. Market data only, no orders.</p></div>
        <div class="btn-row" style="margin:0">${x.running ? `<button class="btn" id="st-stop">Stop</button>` : `<button class="btn primary" id="st-go">${have ? "Update / continue" : "Download 12 months"}</button>`}</div></div>
      ${x.running || x.status === "failed" ? `<div style="margin-top:8px"><div class="bar"><i style="width:${pct}%"></i></div><small class="muted">${x.running ? `${pct}% · ` : ""}${x.error ? `<span class="down">${esc(x.error)}</span> · ` : ""}${esc((x.log || []).slice(-1)[0] || "starting…")}</small></div>` : ""}
      ${have ? `<p class="small" style="margin:6px 0 0">${have && !x.running ? `Done downloading? Click <b>Run research now</b>: the bar table is rebuilt with stocks (takes a few minutes the first time).` : ""}</p>` : ""}`;
    const go = $("#st-go"), stop = $("#st-stop");
    if (go) go.onclick = async () => { try { await post("/api/factory/stocks/download", { months: 12 }); toast("Stock option history download started"); drawStocks(); } catch (e) { toast(e.message); } };
    if (stop) stop.onclick = async () => { await post("/api/factory/stocks/stop", {}); toast("Stopping: finished months are kept"); drawStocks(); };
    clearTimeout(stTimer);
    if (x.running) stTimer = setTimeout(() => { if (document.getElementById("lab-stocks")) drawStocks(); }, 5000);
  };
  draw();
}

/* 2.49 (Mandeep, 10 Nov #12): a strategy / rule opened: every version, and every stock / index it was tested on, in
   each exam (train, validation, holdout). Read from the Lab's saved results (no re-run). */
async function labRuleDetail(el, ids, labs) {
  el.innerHTML = `<p class="small muted">Loading…</p>`;
  const got = await Promise.all(labs.map((l) => api(`/api/factory/rules_detail?lab=${l}&ids=${encodeURIComponent(ids.slice(0, 60).join(","))}`).catch(() => null)));
  const rs = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
  const ex = (x) => x && x.n ? `${x.n} · ${rs(x.avg)}${x.t != null ? ` · t ${x.t}` : ""}` : `<span class="muted">no trades</span>`;
  const cell = (c) => c && c[0] ? `${c[0]} · ${rs(c[1])}` : `<span class="muted">–</span>`;
  const stg = { promoted: ["p-up", "Passed all 3"], "failed holdout": ["p-down", "Failed holdout"], "failed validation": ["", "Failed validation"], "failed train": ["", "Failed train"], duplicate: ["", "Same as another rule"] };
  const parts = [];
  labs.forEach((lab, i) => {
    const d = got[i]; if (!d) return;
    const rules = Object.values(d.rules || {}).filter((r) => !r.missing);
    if (!rules.length) { parts.push(`<p class="small muted">${lab === "5y" ? "5-year" : "1-year"} Lab: not tested yet (on the next run).</p>`); return; }
    const per = d.period ? ` <span class="muted small">train ${esc(d.period.train[0])} → ${esc(d.period.train[1])} · holdout ${esc(d.period.hold[0])} → ${esc(d.period.hold[1])}</span>` : "";
    parts.push(`<h4 style="margin:10px 0 4px">${lab === "5y" ? "5-year Lab" : "1-year Lab"}${per}</h4>` + rules.map((r) => {
      const bs = Object.entries(r.by_sym || {}).sort((a, b) => (b[1][2][1] || 0) - (a[1][2][1] || 0));
      const pos = bs.filter(([, v]) => v[0][1] > 0 && v[1][1] > 0 && v[2][1] > 0).length;
      const symT = bs.length ? `<details style="margin:4px 0 8px"><summary class="small">${bs.length} stock${bs.length === 1 ? "" : "s"} / indices · positive in all three exams: <b>${pos}</b></summary>
        <div class="table-wrap"><table class="mini"><thead><tr><th>Stock / index</th><th class="r">Train<br><small class="muted">trades · ₹ total</small></th><th class="r">Validation</th><th class="r">Holdout</th><th>All 3 positive</th></tr></thead><tbody>
        ${bs.map(([sym, v]) => `<tr><td><b>${esc(sym)}</b></td><td class="r">${cell(v[0])}</td><td class="r">${cell(v[1])}</td><td class="r"><b>${cell(v[2])}</b></td><td>${v[0][1] > 0 && v[1][1] > 0 && v[2][1] > 0 ? `<span class="up">yes</span>` : `<span class="muted">no</span>`}</td></tr>`).join("")}</tbody></table></div></details>`
        : `<p class="small muted" style="margin:2px 0 8px">Stock-by-stock results appear after the next Lab run.</p>`;
      return `<div class="lab-ver"><span class="pill ${(stg[r.stage] || [""])[0]}">${(stg[r.stage] || ["", esc(r.stage || "")])[1]}</span> <small>${esc(r.rule || r.id)}</small>
        <div class="small" style="margin-top:2px">Train ${ex(r.train)} · Validation ${ex(r.valid)} · <b>Holdout ${ex(r.hold)}</b></div>${symT}</div>`;
    }).join(""));
  });
  el.innerHTML = parts.join("") || `<p class="small muted">No Lab results yet.</p>`;
}

const rsL = (v) => v == null ? "–" : `<span class="nw ${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}</span>`;
const stL = (x) => x && x.n ? `${x.n} · ${rsL(x.avg)} · t ${x.t} · ${x.green_pct}% green` : `<span class="muted">–</span>`;

function labExits(f) {
  const e = f.exit_board;
  if (!e) return "";
  const rows = e.board.map((b) => `<tr><td><b>${esc(b.exit)}</b> <span class="pill ${b.kind === "chart level" ? "p-amber" : ""}">${esc(b.kind)}</span><br><small class="muted">${esc(b.who)}: ${esc(b.words)}</small></td>
    <td class="r">${b.random_hold && b.random_hold.avg != null ? rsL(b.random_hold.avg) : "–"}</td><td class="r">${rsL(b.rules_valid_avg)}</td><td class="r"><b>${rsL(b.rules_hold_avg)}</b></td><td class="r">${b.best_on_valid}</td></tr>`).join("");
  return `<div class="panel tight" style="margin-top:14px"><h3>Stop-loss &amp; target: which way works <span class="muted small">current % exits vs pro chart-level styles</span></h3>
    <p class="small">${e.style_beats_current ? `<b class="up">The chart-level style "${esc(e.best_style)}" beats the current exits on validation and holdout.</b> It still runs in shadow on live alerts before it replaces anything.` : `<b>The current % exits are still the champion.</b> Best chart-level style: "${esc(e.best_style)}" (shown as the Pro plan on alerts, testing).`} Compared on the ${e.rules_compared} best rules, each re-run with every exit.</p>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Exit</th><th class="r" title="Average over every possible entry in the holdout">Any entry (holdout)</th><th class="r">Best rules: validation</th><th class="r">Best rules: holdout</th><th class="r" title="How many rules did best with this exit on validation">Best for</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function labML(f) {
  const m = f.ml;
  if (!m || !m.best) return "";
  const rows = m.best.slice(0, 8).map((r) => `<tr><td><b>${esc(r.model)}</b> · ${r.dir > 0 ? "calls" : "puts"} · exit ${esc(r.exit)} · top ${Math.round((1 - r.q) * 100)}%<br><span class="pill ${r.stage === "promoted" ? "p-up" : ""}">${esc(r.stage)}</span> <small class="muted">${esc(r.evidence || "")}</small></td>
    <td class="small">${stL(r.valid)}</td><td class="small"><b>${stL(r.hold)}</b></td></tr>`).join("");
  return `<div class="panel tight" style="margin-top:14px"><h3>Model strategies <span class="muted small">3 machine-learning models read all bar facts at once</span></h3>
    <p class="small">${esc(m.note)} ${m.promoted && m.promoted.length ? `<b>${m.promoted.length} passed</b>; those with moderate/strong evidence fire live on paper.` : "<b>None passed yet.</b>"}</p>
    <div class="table-wrap"><table class="lab"><thead><tr><th>Model</th><th>Validation</th><th>Holdout</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

/* ---------------- register */
Object.assign(PAGES, { live: pageLive, scanner: pageScanner, sym: pageSym, alerts: pageAlerts, paper: pagePaper, connect: pageConnect,
  context: pageContext, scorecard: pageScorecard, briefs: pageBriefs, desk: pageDesk, chart: pageChart, world: pageWorld, insiders: pageInsiders,
  timemachine: pageTimeMachine, ask: pageAsk, watchlist: pageWatchlist, lab: pageLab });
window.addEventListener("hashchange", () => { stopPageTimer(); if (!location.hash.startsWith("#/chart") && CH.chart) { CH.chart.remove(); CH.chart = null; } main.classList.toggle("wide", location.hash.startsWith("#/chart")); });
pollSnapshot();
aiStatus(true); setInterval(() => aiStatus(true), 5 * 60000);

/* side bar: today's paper P&L (booked + open), every 5 s */
async function navPnl() {
  const el = $("#nav-pnl"); if (!el) return;
  try {
    const m = await api("/api/live/paper/mini");
    if (!m.running) { el.textContent = ""; el.className = "nav-pnl"; return; }
    const v = Math.round(m.today || 0);
    el.textContent = `${v >= 0 ? "+" : "−"}₹${Math.abs(v).toLocaleString("en-IN")}`;
    el.className = `nav-pnl ${v > 0 ? "pos" : v < 0 ? "neg" : ""}`;
    el.title = `Today: ${el.textContent} (booked + ${m.open} open) · account ${rupees(m.equity)} · free ${rupees(m.free)}`;
  } catch (_) {}
}
navPnl(); setInterval(navPnl, 5000);

/* ---------------- System & logins: every connection, its job, its health, all logins, and the no-clash checks */
async function pageSystem() {
  stopPageTimer();
  const pill = (s) => s === "ok" ? `<span class="pill p-up">OK</span>` : s === "warn" ? `<span class="pill p-amber">Check</span>`
    : s === "bad" ? `<span class="pill p-down">Fix</span>` : `<span class="pill">Off</span>`;
  const mark = (ok) => ok === true ? `<span class="up">✓</span>` : ok === false ? `<span class="down">✗</span>` : `<span class="muted">–</span>`;
  let live = {};
  const draw = async () => {
    let x;
    try { x = await api("/api/system"); } catch (e) { main.innerHTML = `<div class="empty"><h2>System status unavailable</h2><p>${esc(e.message)}</p></div>`; return; }
    if (state.page !== "system") return;
    const S = Object.fromEntries(x.services.map((s) => [s.id, s]));
    const up = S.upstox, dh = S.dhan, cl = S.claude, tg = S.telegram, tk = dh.token || {}, au = dh.auto || {};
    const clash = x.checks.filter((c) => c.ok === false);
    const ready = [
      ["Upstox logged in (live prices)", up.logged_in ? true : false],
      ["Dhan token valid (history, order book, news)", tk.dead ? (au.on && !au.blocked ? null : false) : dh.status === "ok" ? true : false],
      ["Dhan automatic login (TOTP)", au.on ? !au.blocked : null],
      ["Claude ready (notes, Ask, Lab ideas)", cl.status === "ok" ? true : cl.status === "off" ? null : false],
      ["Telegram set (alerts on your phone)", tg.status === "ok" ? true : null],
      [`Disk free ${x.free_gb} GB (10 GB+)`, x.free_gb >= 10],
      [`No clashes (${x.checks.filter((c) => c.ok === true).length} checks pass)`, clash.length ? false : true],
    ];
    const lv = (id) => live[id] ? `<div class="small" style="margin-top:6px">Live check: ${mark(live[id].ok)} ${esc(live[id].detail || "")}</div>` : "";
    const roles = (s) => `<ul class="small" style="margin:6px 0 0;padding-left:18px">${s.roles.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
    const card = (s, body = "", actions = "") => `<div class="panel tight"><h3 style="display:flex;gap:8px;align-items:center;justify-content:space-between">${esc(s.name)} ${pill(s.status)}</h3>
      <div class="small">${esc(s.detail || "")}</div>${body}${lv(s.id)}<div class="small muted" style="margin-top:8px"><b>Used for</b></div>${roles(s)}
      ${actions ? `<div class="btn-row" style="margin-top:10px">${actions}</div>` : ""}</div>`;
    const depth = Object.entries(dh.depth || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ") || "off";
    main.innerHTML = `<div class="toolbar"><div><h1 class="page-title" style="margin:0">System &amp; logins ${pill(x.overall)}</h1>
        <p class="lede" style="margin:4px 0 0">Every connection, what it is for and whether it works, all logins in one place, and the checks that nothing collides. Version ${esc((x.version || {}).name || "")} · running ${esc(x.up_since || "")}.</p></div>
        <div class="btn-row" style="margin:0"><button class="btn primary" id="sy-check">Run live check</button><button class="btn" id="sy-refresh">Refresh</button></div></div>

      <div class="panel tight" style="margin-top:12px"><h3>Before the market opens</h3>
        <table class="mini"><tbody>${ready.map(([t, ok]) => `<tr><td style="width:28px">${mark(ok)}</td><td>${t}</td></tr>`).join("")}</tbody></table>
        ${clash.length ? `<p class="small down" style="margin:8px 0 0">Clash: ${clash.map((c) => esc(c.name + " — " + c.detail)).join("; ")}</p>` : ""}</div>

      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-top:12px">
        ${card(up, `<div class="small muted" style="margin-top:6px">Scanner: ${esc(up.scanner || "")}${up.last_tick ? " · last price " + esc(up.last_tick) : ""}${up.streaming ? " · " + up.streaming + " options streaming" : ""}</div>`,
          `<a class="btn primary" href="/upstox/login">Log in with Upstox</a><button class="btn" id="sy-uplogout">Log out</button><a class="btn" href="#/connect">API key &amp; secret</a>`)}
        ${card(dh, `<div class="small muted" style="margin-top:6px">Automatic login: ${au.on ? (au.blocked ? `<span class="down">stopped</span> (${esc(au.last_error || "")})` : `on${au.last_login ? " · last " + esc(au.last_login) : ""}`) : "off"} · calls today ${(dh.calls_today || 0).toLocaleString("en-IN")} / ${(dh.cap || 0).toLocaleString("en-IN")}<br>Order book: ${depth} · news &amp; movers: ${esc(dh.news_movers || "off")}${dh.movers_at ? " (movers " + esc(dh.movers_at) + ")" : ""}</div>
          <div class="form-grid" style="margin-top:10px"><label class="field">Client ID<input id="sy-cid" value="${esc(dh.client_id || "")}" autocomplete="off"></label>
            <label class="field">Paste a token (optional)<input id="sy-tok" type="password" placeholder="only if not using TOTP" autocomplete="off"></label>
            <label class="field">Dhan PIN (for TOTP)<input id="sy-pin" type="password" inputmode="numeric" autocomplete="off"></label>
            <label class="field">TOTP key (text under Dhan's QR)<input id="sy-totp" type="password" autocomplete="off"></label></div>`,
          `<button class="btn" id="sy-dsave">Save</button><button class="btn primary" id="sy-auto">${au.on ? "Save new PIN / key" : "Turn on automatic login"}</button>${au.on ? `<button class="btn" id="sy-auto-off">Turn off TOTP</button>` : ""}<button class="btn" id="sy-dtest">Test Dhan</button><button class="btn" id="sy-renew">Renew now</button>`)}
        ${card(cl, `<div class="small muted" style="margin-top:6px">Used today: ${cl.used || 0}. If it says "not logged in": open Terminal, type <code>claude</code>, log in.</div>`,
          `<button class="btn" id="sy-ai">Test Claude (uses 1 message)</button><a class="btn" href="#/data">AI settings</a>`)}
        ${card(tg, "", `<button class="btn" id="sy-tg">Send test message</button><a class="btn" href="#/data">Telegram settings</a>`)}
        ${card(S.yahoo)}
        ${card(S.nse)}
        ${S.official ? card(S.official, (S.official.sources || []).length ? `<table class="mini" style="margin-top:6px"><tbody>${S.official.sources.map((x) => `<tr><td>${mark(x.ok)}</td><td class="small">${esc(x.name)}</td><td class="small muted r">${x.ok ? `${x.n} items` : esc(x.error || "")}</td></tr>`).join("")}</tbody></table>` : "") : ""}
      </div>

      ${healthPanel(x.health)}
      ${speedPanel(x.perf)}
      ${qualityPanel(x.vault && x.vault.quality)}
      ${vaultPanel(x.vault)}

      <div class="panel tight" style="margin-top:12px"><h3>Which source does what (and its limits)</h3>
        <div class="table-wrap"><table class="mini"><thead><tr><th>Source</th><th>Used for</th><th>Limits</th><th>Login</th></tr></thead><tbody>
          <tr><td>Upstox</td><td>Live prices of indices, stocks and options; option chains (and the data vault's stock chains every 30 min, spare calls only); instrument list</td><td>2,000 live options (app uses ≤1,900); REST calls paced under its 30-minute limit</td><td>Daily (Upstox ends it at 3:30 AM)</td></tr>
          <tr><td>Dhan Pro</td><td>5-year history, 200-level order book, news, market movers</td><td>5 calls/s and 100,000/day shared by all parts (download ≤92,000)</td><td>Automatic (TOTP), token 24 h</td></tr>
          <tr><td>Claude</td><td>Notes, Ask Stockway, Lab ideas, ebook / transcript reading</td><td>Your Claude plan's usage</td><td>Claude Code on this Mac</td></tr>
          <tr><td>Telegram</td><td>Alerts and notices on your phone</td><td>–</td><td>Bot token (once)</td></tr>
          <tr><td>Yahoo Finance</td><td>World markets, VIX history</td><td>Free, unofficial</td><td>None</td></tr>
          <tr><td>NSE</td><td>Filings, insider trades, ban list, end-of-day files</td><td>Free</td><td>None</td></tr>
          <tr><td>RBI, SEBI, PIB, NSE / BSE circulars</td><td>Official news and orders: event risk on alerts, Telegram for high-impact ones</td><td>Free RSS, read every 2 min in market hours</td><td>None</td></tr>
        </tbody></table></div>
        <p class="small muted" style="margin:6px 0 0">Live prices come only from Upstox; Dhan is never a second price feed, so the two can't disagree in the scanner. Market data only: no orders anywhere.</p></div>

      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-top:12px">
        <div class="panel tight"><h3>Background jobs</h3><table class="mini"><tbody>${x.jobs.map((j) => `<tr><td>${esc(j.name)}</td><td>${esc(j.state || "")}</td><td class="small muted">${esc(j.detail || "")}</td></tr>`).join("")}</tbody></table></div>
        <div class="panel tight"><h3>No-clash checks</h3><table class="mini"><tbody>${x.checks.map((c) => `<tr><td style="width:24px">${mark(c.ok)}</td><td>${esc(c.name)}<div class="small muted">${esc(c.detail || "")}</div></td></tr>`).join("")}</tbody></table></div>
      </div>
      <details class="panel tight" style="margin-top:12px"><summary><b>Bullets: every alert, setup, pattern and strategy kind</b> <span class="small muted">(${(x.bullets || []).length}, from live/registry.py)</span></summary>
        <div class="table-wrap" style="margin-top:8px"><table class="mini"><thead><tr><th>Kind</th><th>Family</th><th>Label</th><th class="r">Quiet for</th><th>Auto paper</th></tr></thead><tbody>
        ${(x.bullets || []).map((b) => `<tr><td><code>${esc(b.kind)}</code></td><td>${esc(b.family)}</td><td>${esc(b.label)}</td><td class="r">${b.cooldown != null ? (b.cooldown >= 999 ? "once a day" : b.cooldown + " min") : "default"}</td><td>${b.paper ? "yes" : ""}</td></tr>`).join("")}</tbody></table></div>
        <p class="small muted" style="margin:6px 0 0">Rules files (dated facts): ${(x.rules || []).map((r) => `${r.ok ? "✓" : "✗"} ${esc(r.name)}`).join(" · ")}</p></details>
      <div class="panel tight" style="margin-top:12px"><h3>Problems in the last hour</h3>${x.problems.length ? `<table class="mini"><tbody>${x.problems.map((p) => `<tr><td class="small">${esc(p.last)}</td><td class="r small">${p.n}×</td><td class="small">${esc(p.text)}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted" style="margin:0">None.</p>`}</div>`;
    const act = (id, fn) => { const b = $(id); if (b) b.onclick = async () => { try { await fn(b); } catch (e) { toast(e.message); } }; };
    act("#sy-refresh", () => draw());
    act("#sy-check", async (b) => { b.disabled = true; b.textContent = "Checking…"; const r = await post("/api/system/check", {}); live = Object.fromEntries(r.checks.map((c) => [c.id, c])); toast(`Live check done at ${r.at}`); draw(); });
    act("#sy-uplogout", async () => { await post("/api/live/logout", {}); toast("Logged out of Upstox"); draw(); });
    const saveDhan = async () => { const body = { "lab.dhan_client_id": $("#sy-cid").value.trim() }; const t = $("#sy-tok").value.trim(); if (t) body["lab.dhan_token"] = t; await post("/api/settings", body); };
    act("#sy-dsave", async () => { await saveDhan(); toast("Saved"); draw(); });
    act("#sy-auto", async (b) => { await saveDhan(); const pin = $("#sy-pin").value.trim(), secret = $("#sy-totp").value.trim(); if (!pin || !secret) { toast("Enter the Dhan PIN and the TOTP key"); return; }
      b.disabled = true; b.textContent = "Checking with Dhan…"; try { const r = await post("/api/lab/dhan/auto", { pin, secret }); toast(`Automatic login on: token valid till ${(r.token && r.token.expires) || "tomorrow"}`); } finally { draw(); } });
    act("#sy-auto-off", async () => { await fetch("/api/lab/dhan/auto", { method: "DELETE" }); toast("Automatic login off: PIN and key removed"); draw(); });
    act("#sy-dtest", async () => { await saveDhan(); const r = await post("/api/lab/dhan/test", {}); toast(`Dhan connected (${r.profile.dhanClientId || "ok"}${r.profile.dataPlan ? ", plan " + r.profile.dataPlan : ""})`); draw(); });
    act("#sy-renew", async () => { const r = await post("/api/lab/dhan/renew", {}); toast(`Token renewed: valid till ${r.token.expires}`); draw(); });
    act("#sy-ai", async () => { const r = await post("/api/ai/test", {}); toast(`${r.by} answered: ${r.text}`); draw(); });
    act("#sy-tg", async () => { await post("/api/telegram/test", {}); toast("Test message sent"); });
    stopPageTimer();
    const tick = () => {                   // refresh every 15 s, but never while you are typing a PIN / key / token
      if (state.page !== "system") return;
      const typing = ["#sy-pin", "#sy-totp", "#sy-tok", "#sy-cid"].some((id) => { const el = $(id); return el && (el.value && id !== "#sy-cid" || document.activeElement === el); });
      if (typing) { L.pageTimer = setTimeout(tick, 15000); return; }
      draw();
    };
    L.pageTimer = setTimeout(tick, 15000);
  };
  main.innerHTML = `<p class="muted">Checking every connection…</p>`;
  await draw();
}
/* 2.38: how quickly the app answers (every /api call timed), the Lab's own process, the Dhan download giving way */
function speedPanel(p) {
  if (!p || p.error) return "";
  const t = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "–";
  const slow = (p.slow || []).slice(0, 8).map((x) => `<tr><td class="small">${t(x.at)}</td><td class="small"><code>${esc(x.path.length > 60 ? x.path.slice(0, 60) + "…" : x.path)}</code></td><td class="r small"><b class="down">${(x.ms / 1000).toFixed(1)} s</b></td></tr>`).join("");
  const top = (p.slowest || []).slice(0, 6).map((x) => `<tr><td class="small"><code>${esc(x.call)}</code></td><td class="r small">${x.avg_ms} ms</td><td class="r small muted">max ${x.max_ms} ms · ${x.n}×</td></tr>`).join("");
  const lab = p.lab_last;
  return `<div class="panel tight" style="margin-top:12px"><h3>Speed <span class="pill ${p.slow_last_hour ? "p-down" : "p-up"}">${p.slow_last_hour ? `${p.slow_last_hour} slow in the last hour` : "quick"}</span></h3>
    <p class="small muted" style="margin:0 0 6px">Every screen's data call is timed; over ${(p.slow_ms / 1000).toFixed(1)} s counts as slow. ${(p.calls || 0).toLocaleString("en-IN")} calls since ${t(p.since)}.
      The Strategy Lab runs in its own low-priority process${lab ? ` (last: ${esc(lab.job)}, ${esc(lab.how)}, ${Math.round(lab.secs / 60)} min, ended ${t(lab.at)})` : ""}; the 5-year Dhan download gives way to screens, news and movers${p.dhan_gave_way ? ` (${p.dhan_gave_way.toLocaleString("en-IN")} times so far)` : ""}.</p>
    ${slow ? `<div class="table-wrap"><table class="mini"><thead><tr><th>When</th><th>Slow call</th><th class="r">Took</th></tr></thead><tbody>${slow}</tbody></table></div>` : ""}
    ${top ? `<details><summary class="small muted">Slowest kinds of call (average)</summary><div class="table-wrap"><table class="mini"><tbody>${top}</tbody></table></div></details>` : ""}</div>`;
}

/* 2.33 health monitor: is the app really collecting data right now? */
function healthPanel(h) {
  if (!h) return "";
  const st = h.state || {};
  const row = (k, v) => v == null || v === "" ? "" : `<tr><td>${k}</td><td class="r">${esc(String(v))}</td></tr>`;
  const gaps = (h.gaps_today || []).filter((g) => g.market_mins >= 1);
  return `<div class="panel tight" style="margin-top:12px"><h3>Health monitor <span class="muted small">${h.last_check_s != null ? `checked ${h.last_check_s} s ago` : "starting"}</span></h3>
    <div class="vault-grid">
      <div><table class="mini"><tbody>
        ${row("Trading day", st.trading_day === false ? "no (weekend / holiday)" : st.trading_day ? "yes" : null)}
        ${row("Market", st.market_open ? "open" : "closed")}
        ${row("Live scanner", st.scanner)}
        ${row("Today's recording", st.recording)}
        ${row("Keep the Mac awake (market hours)", st.keep_awake || (h.keep_awake ? `on ${h.window}` : "off"))}
        ${row("Power", st.power)}
        ${row("Disk free", st.disk_free_gb != null ? st.disk_free_gb + " GB" : null)}
        ${row("Problems in the last 10 min", st.errors_10min)}</tbody></table>
        <p class="small muted" style="margin:6px 0 0">Telegram tells you if the Mac sleeps, the scanner or the recording stops, the disk fills up or the Mac runs on battery during the market, plus one summary after the close. Closing a MacBook's lid still puts it to sleep: keep it open and charging.</p></div>
      <div><b class="small">Gaps today</b>${gaps.length ? `<table class="mini"><tbody>${gaps.map((g) => `<tr><td>${esc(g.from_hm)}–${esc(g.to_hm)}</td><td class="r down">${g.market_mins} market min</td></tr>`).join("")}</tbody></table>` : `<p class="small muted" style="margin:4px 0">None: every market minute so far was recorded.</p>`}
        <b class="small" style="display:block;margin-top:8px">Latest notices</b>${(h.notices || []).length ? `<table class="mini"><tbody>${h.notices.slice(0, 6).map((n) => `<tr><td class="small">${esc(n.at)}</td><td class="small">${esc(n.text)}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted" style="margin:4px 0">None.</p>`}</div>
    </div></div>`;
}

/* 2.33 data quality guard: suspect / stale prices, bad prints, alerts held */
function qualityPanel(q) {
  if (!q) return "";
  const st = q.state || {}, c = q.counts || {}, n = (x) => (x || 0).toLocaleString("en-IN");
  const tm = (ts) => ts ? hm(ts) : "";
  const list = (xs, f) => xs.length ? `<div class="table-wrap"><table class="mini"><tbody>${xs.map(f).join("")}</tbody></table></div>` : `<p class="small muted" style="margin:4px 0">None.</p>`;
  return `<div class="panel tight" style="margin-top:12px"><h3>Data quality guard <span class="muted small">mode: ${esc(q.mode || "")} · ${n(st.checked)} prices checked every few seconds</span></h3>
    <div class="vault-grid">
      <div><table class="mini"><tbody>
        <tr><td>Suspect right now</td><td class="r">${n(st.suspect)}</td></tr>
        <tr><td>Indices / stocks / futures with no update for minutes</td><td class="r">${n(st.stale)} of ${n(st.liquid)}</td></tr>
        <tr><td>Bad prints caught today</td><td class="r">${n(c.bad_prints)}</td></tr>
        <tr><td>Big moves checked and confirmed</td><td class="r">${n(c.real_moves)}</td></tr>
        <tr><td>Alerts held on suspect data</td><td class="r">${n(c.alerts_held)}</td></tr></tbody></table>
        <p class="small muted" style="margin:6px 0 0">A sudden jump is held for a few seconds: if it snaps back it was a bad print and is never used; if it stays it was a real move. Test trades never fill on an old or suspect quote. Everything is saved with the day's data.</p></div>
      <div><b class="small">Latest findings</b>${list((q.events || []).slice(0, 8), (e) => `<tr><td class="small">${tm(e.ts)}</td><td class="small" style="word-break:break-all">${esc(e.sym || e.key)} <span class="muted">${esc(e.kind)}</span></td><td class="small">${esc(e.what)}${e.a != null && e.b != null ? ` (${fx(e.a)} → ${fx(e.b)})` : ""}</td></tr>`)}
        <b class="small" style="display:block;margin-top:8px">Alerts held</b>${list((q.held || []).slice(0, 6), (h) => `<tr><td class="small">${tm(h.ts)}</td><td class="small">${esc(h.title || h.sym)}</td><td class="small muted">${esc(h.why)}</td></tr>`)}</div>
    </div></div>`;
}

/* 2.31 data vault: what is recorded for backtests, the bench styles and the mover sweep */
function vaultPanel(v) {
  if (!v || v.error) return v && v.error ? `<div class="panel tight" style="margin-top:12px"><h3>Data vault</h3><p class="small down">${esc(v.error)}</p></div>` : "";
  const t = v.today, n = (x) => (x || 0).toLocaleString("en-IN");
  const sw = v.chain_sweep, mv = v.movers, bn = v.bench || {};
  const famL = { buy: "Only buying", mixed: "Buying + selling", sell: "Only selling" };
  const styles = Object.entries(bn.styles || {}).map(([k, s]) => `<tr><td>${esc(s.label)}</td><td class="small muted">${famL[s.family] || s.family}</td><td class="r">${n(s.opened)}</td><td class="r small muted">${n(s.skipped)}</td></tr>`).join("");
  const days = (v.days || []).slice(0, 8).map((d) => `<tr><td>${esc(d.day)}</td><td class="r">${d.mb} MB</td><td class="small muted">${Object.keys(d.parts).join(", ")}</td></tr>`).join("");
  return `<div class="panel tight" style="margin-top:12px"><h3>Data vault <span class="muted small">recorded on this Mac for backtests: ${n(v.n_days)} day${v.n_days === 1 ? "" : "s"}, ${v.total_mb} MB</span></h3>
    <p class="small muted" style="margin:0 0 8px">${esc(v.note)}</p>
    <div class="vault-grid">
      <div><b class="small">Today</b>${t ? `<table class="mini"><tbody>
        <tr><td>Minutes recorded</td><td class="r">${n(t.minutes)}</td></tr>
        <tr><td>Instruments (prices each minute)</td><td class="r">${n(t.instruments)}</td></tr>
        <tr><td>Full option chains</td><td class="r">${n(t.chain_snaps)} <small class="muted">(${n(t.chain_syms)} symbols)</small></td></tr>
        <tr><td>Headlines</td><td class="r">${n(t.news)}</td></tr>
        <tr><td>Market context snapshots</td><td class="r">${n(t.context)}</td></tr>
        <tr><td>Candle patterns (every timeframe)</td><td class="r">${n(t.candles)} <small class="muted">(${n(t.candle_tfs)} timeframes)</small></td></tr></tbody></table>` : `<p class="small muted">Starts with the scanner.</p>`}
        ${v.candles && v.candles.last && Object.keys(v.candles.last).length ? `<p class="small muted" style="margin:6px 0 0">Candle scanner: ${Object.entries(v.candles.last).map(([tf, x]) => `${esc(tf)} ${esc(x.at)} · ${x.patterns} patterns, ${x.bench} tested`).join(" · ")}</p>` : ""}
        <p class="small muted" style="margin:6px 0 0">Stock chains: every ${v.cfg.stock_chains_every_min} min with spare Upstox calls${sw ? ` · last sweep ${esc(sw.at)}: ${sw.stocks} of ${sw.of} stocks in ${sw.secs} s${sw.waits ? `, waited ${sw.waits}× for spare calls` : ""}` : ""}.</p></div>
      <div><b class="small">Test bench styles (this session)</b>${styles ? `<table class="mini"><thead><tr><th>Style</th><th></th><th class="r">Trades</th><th class="r" title="Signals where a leg had no live price">No price</th></tr></thead><tbody>${styles}</tbody></table>` : `<p class="small muted">Starts with the scanner.</p>`}
        ${(bn.errors || []).length ? `<p class="small down">Skipped: ${bn.errors.map(esc).join("; ")}</p>` : ""}</div>
      <div><b class="small">Mover sweep</b>${mv ? `<p class="small" style="margin:4px 0">${esc(mv.at)}: ${(mv.picks || []).slice(0, 10).map((p) => `${esc(p.sym)} ${p.direction === "up" ? "▲" : "▼"}`).join(" · ") || "nothing moving enough"} · ${n(mv.opened)} test trades</p>
        <p class="small muted" style="margin:0">In play today (library rules run on them too): ${(mv.in_play || []).map(esc).join(", ") || "none yet"}</p>` : `<p class="small muted">Every 15 minutes from 9:30 while the scanner runs.</p>`}
        ${days ? `<b class="small" style="display:block;margin-top:8px">Past days</b><table class="mini"><tbody>${days}</tbody></table>` : ""}</div>
    </div></div>`;
}
Object.assign(PAGES, { system: pageSystem });

/* ---------------- Options lens (live/lens.py): straddle, premium decay, IV, PCR, OI flow, OI by strike, market breadth,
   buildup heatmap, IV grid, sectors, contributors, FII / DII. All from the app's own minute recording. */
const LZ = { charts: {}, sym: null };
async function pageLens() {
  stopPageTimer();
  Object.values(LZ.charts).forEach((c) => { try { c.remove(); } catch (_) {} }); LZ.charts = {};
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  LZ.sym = (params.get("s") || LS.get("lensSym", "NIFTY")).toUpperCase(); LS.set("lensSym", LZ.sym);
  const IDX = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
  let stocks = []; try { stocks = (await symbolList()).stocks.filter((x) => !IDX.includes(x)); } catch (_) {}
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title" style="margin:0">Options lens</h1>
      <p class="lede" style="margin:4px 0 0">Straddle, premium decay, IV, PCR and who is adding positions, through the day, plus the whole market's breadth. Built from the app's own minute-by-minute recording; every value here is recorded, so the Lab can test it later.</p></div>
      <select id="lz-sym" aria-label="Symbol"><optgroup label="Indices">${IDX.map((x) => `<option ${x === LZ.sym ? "selected" : ""}>${x}</option>`).join("")}</optgroup><optgroup label="F&amp;O stocks">${stocks.map((x) => `<option ${x === LZ.sym ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup></select></div>
    <div id="lz-kpis" class="kpis kpi6" style="margin-top:10px"></div>
    <div class="mtf-grid" style="margin-top:12px">
      <div class="mtf-cell"><div class="mtf-head"><b>ATM straddle</b> <span class="small muted">call + put at the strike nearest the price, and its average</span></div><div class="mtf-chart" id="lz-strad"></div></div>
      <div class="mtf-cell"><div class="mtf-head"><b>Call vs put premium</b> <span class="small muted">ATM; which side is decaying</span></div><div class="mtf-chart" id="lz-cepe"></div></div>
      <div class="mtf-cell"><div class="mtf-head"><b>Put OI − call OI</b> <span class="small muted">change since the open, lakh; above 0 = puts being written (support)</span></div><div class="mtf-chart" id="lz-oi"></div></div>
      <div class="mtf-cell"><div class="mtf-head"><b>ATM IV and PCR</b> <span class="small muted">IV (left), put-call ratio (right)</span></div><div class="mtf-chart" id="lz-iv"></div></div>
    </div>
    <div class="panel tight" style="margin-top:12px"><h3>OI by strike <span class="muted small">now, and change since the open (lakh), near the price</span></h3><div id="lz-strikes"></div></div>
    <h2 style="margin:20px 0 8px">Whole market</h2>
    <div class="mtf-grid"><div class="mtf-cell"><div class="mtf-head"><b>Breadth through the day</b> <span class="small muted">F&amp;O stocks up / down, % above VWAP</span></div><div class="mtf-chart" id="lz-breadth"></div></div>
      <div class="panel tight"><h3>Futures buildup</h3><div id="lz-build"></div></div></div>
    <div class="mtf-grid" style="margin-top:12px"><div class="panel tight"><h3>Sectors</h3><div id="lz-sec"></div></div>
      <div class="panel tight"><h3>Who is moving the index</h3><div id="lz-contrib"></div></div>
      <div class="panel tight"><h3>FII / DII and participants</h3><div id="lz-flows"></div></div></div>
    <div class="panel tight" style="margin-top:12px"><h3>IV grid <span class="muted small">every F&amp;O stock, highest IV first</span></h3><div id="lz-iv-grid"></div></div>`;
  $("#lz-sym").onchange = (e) => { location.hash = `#/lens?s=${encodeURIComponent(e.target.value)}`; };
  const LW = window.LightweightCharts;
  const mk = (id, opts = {}) => {
    if (LZ.charts[id]) return LZ.charts[id];
    const el = $("#" + id); if (!el || !LW) return null;
    const c = LW.createChart(el, { autoSize: true, layout: { background: { color: "#ffffff" }, textColor: "#16212C", fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: "#F4F6F8" }, horzLines: { color: "#F4F6F8" } }, rightPriceScale: { borderColor: "#D5DCE3" }, leftPriceScale: { visible: !!opts.left, borderColor: "#D5DCE3" },
      timeScale: { borderColor: "#D5DCE3", timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 } });
    c._s = {}; LZ.charts[id] = c; return c;
  };
  const put = (c, name, type, data, o = {}) => { if (!c) return; if (!c._s[name]) c._s[name] = c.addSeries(LW[type], { priceLineVisible: false, lastValueVisible: true, ...o }); c._s[name].setData(data || []); };
  const n2 = (v) => (v == null ? "–" : fk(v));
  const draw = async () => {
    if (state.page !== "lens") return;
    let x, m;
    try { [x, m] = await Promise.all([api(`/api/live/lens/${encodeURIComponent(LZ.sym)}`), api("/api/live/lens_market")]); }
    catch (e) { $("#lz-kpis").innerHTML = `<p class="muted">${esc(e.message)}</p>`; LZ.timer = setTimeout(draw, 30000); return; }
    const nw = x.now || {}, S = x.series || {};
    $("#lz-kpis").innerHTML = x.empty ? `<div class="kpi" style="grid-column:1/-1"><span>${esc(x.empty)}</span></div>` : [
      ["Straddle", n2(nw.straddle), nw.straddle_decay_pct != null ? `${nw.straddle_decay_pct > 0 ? "+" : ""}${nw.straddle_decay_pct}% since the first minute` : "ATM call + put"],
      ["ATM IV", n2(nw.atm_iv), nw.iv_chg_15m != null ? `${nw.iv_chg_15m > 0 ? "+" : ""}${nw.iv_chg_15m} in 15 min` : ""],
      ["PCR", n2(nw.pcr), "puts ÷ calls (OI)"], ["Max pain", n2(nw.max_pain), x.expiry ? "expiry " + esc(x.expiry) : ""],
      ["Call wall", n2(nw.resistance), "biggest call OI"], ["Put wall", n2(nw.support), "biggest put OI"],
    ].map(([a, b, c]) => `<div class="kpi"><b>${b}</b><span>${a}${c ? ` · <small>${c}</small>` : ""}</span></div>`).join("") + (x.session && x.session !== "today" ? `<p class="small muted" style="grid-column:1/-1;margin:6px 12px">Showing the last recorded session (${esc(x.session)}).</p>` : "");
    const a = mk("lz-strad"); put(a, "s", "LineSeries", S.straddle, { color: "#3D2FA8", lineWidth: 2, title: "Straddle" }); put(a, "v", "LineSeries", S.straddle_vwap, { color: "#9A6A12", lineWidth: 1, lineStyle: 2, title: "Average" });
    const b = mk("lz-cepe"); put(b, "c", "LineSeries", S.ce, { color: "#C23B32", lineWidth: 2, title: "Call" }); put(b, "p", "LineSeries", S.pe, { color: "#1E8A5A", lineWidth: 2, title: "Put" });
    const c = mk("lz-oi"); put(c, "d", "HistogramSeries", (S.pe_minus_ce_oi_lakh || []).map((p) => ({ time: p.time, value: p.value, color: p.value >= 0 ? "rgba(30,138,90,.6)" : "rgba(194,59,50,.6)" })), { title: "PE − CE" });
    const d = mk("lz-iv", { left: true }); put(d, "iv", "LineSeries", S.atm_iv, { color: "#B04ACF", lineWidth: 2, priceScaleId: "left", title: "IV" }); put(d, "pcr", "LineSeries", S.pcr, { color: "#0F7C8C", lineWidth: 2, title: "PCR" });
    const st = x.strikes || [], mx = Math.max(1, ...st.map((r) => Math.max(r.ce_oi || 0, r.pe_oi || 0)));
    const bar = (v, col) => `<span style="display:inline-block;height:9px;width:${Math.round((v || 0) / mx * 100)}%;max-width:100%;background:${col};border-radius:2px"></span>`;
    $("#lz-strikes").innerHTML = st.length ? `<div class="table-wrap"><table class="mini"><thead><tr><th class="r">Call OI chg</th><th>Call OI</th><th class="r">Strike</th><th>Put OI</th><th>Put OI chg</th></tr></thead><tbody>
      ${st.map((r) => `<tr${nw.spot && Math.abs(r.strike - nw.spot) === Math.min(...st.map((q) => Math.abs(q.strike - nw.spot))) ? ` style="background:#FFF8E6"` : ""}><td class="r ${(r.ce_chg || 0) > 0 ? "down" : "up"}">${n2(r.ce_chg)}</td><td style="width:30%;text-align:right">${bar(r.ce_oi, "rgba(194,59,50,.55)")} <small>${n2(r.ce_oi)}</small></td><td class="r"><b>${fk(r.strike)}</b></td><td style="width:30%"><small>${n2(r.pe_oi)}</small> ${bar(r.pe_oi, "rgba(30,138,90,.55)")}</td><td class="${(r.pe_chg || 0) > 0 ? "up" : "down"}">${n2(r.pe_chg)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="small muted" style="margin:0">Strike OI shows while the scanner runs (options streaming).</p>`;
    const br = mk("lz-breadth", { left: true });
    put(br, "a", "LineSeries", (m.breadth || []).map((p) => ({ time: p.time, value: p.adv })), { color: "#1E8A5A", lineWidth: 2, title: "Up" });
    put(br, "d", "LineSeries", (m.breadth || []).map((p) => ({ time: p.time, value: p.dec })), { color: "#C23B32", lineWidth: 2, title: "Down" });
    put(br, "v", "LineSeries", (m.breadth || []).filter((p) => p.above_vwap_pct != null).map((p) => ({ time: p.time, value: p.above_vwap_pct })), { color: "#5E6B78", lineWidth: 1, lineStyle: 2, priceScaleId: "left", title: "% > VWAP" });
    const BL = { long_buildup: ["Long buildup", "price ↑ OI ↑", "up"], short_covering: ["Short covering", "price ↑ OI ↓", "up"], short_buildup: ["Short buildup", "price ↓ OI ↑", "down"], long_unwinding: ["Long unwinding", "price ↓ OI ↓", "down"] };
    const bkey = (k) => Object.keys(BL).find((b) => (k || "").toLowerCase().replace(/ /g, "_").includes(b)) || k;
    const bu = {}; Object.entries(m.buildup || {}).forEach(([k, v]) => { const kk = bkey(k); (bu[kk] = bu[kk] || []).push(...v); });
    $("#lz-build").innerHTML = `<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px">${Object.entries(BL).map(([k, [t, h, cls]]) => `<div><b class="${cls}">${t}</b> <span class="small muted">${h} · ${(bu[k] || []).length}</span><div class="small" style="margin-top:4px;line-height:1.7">${(bu[k] || []).slice(0, 8).map((r) => `<a href="#/lens?s=${encodeURIComponent(r.sym)}">${esc(r.sym)}</a> <span class="${(r.chg_pct || 0) >= 0 ? "up" : "down"}">${r.chg_pct ?? "–"}%</span>`).join(" · ") || "–"}</div></div>`).join("")}</div>`;
    $("#lz-sec").innerHTML = (m.sectors || []).length ? `<table class="mini"><tbody>${m.sectors.slice(0, 12).map((s) => `<tr><td>${esc(s.sector)}</td><td class="r ${s.chg >= 0 ? "up" : "down"}">${s.chg > 0 ? "+" : ""}${s.chg}%</td><td class="small muted">${s.up}/${s.n} up</td></tr>`).join("")}</tbody></table>` : `<p class="small muted" style="margin:0">While the scanner runs.</p>`;
    const cb = m.contributors || {};
    $("#lz-contrib").innerHTML = Object.keys(cb).length ? Object.entries(cb).slice(0, 2).map(([idx, v]) => `<div class="small"><b>${esc(idx)}</b> ${v.actual > 0 ? "+" : ""}${v.actual} pts (explained ${v.explained})</div><table class="mini" style="margin-bottom:6px"><tbody>${(v.rows || []).slice(0, 6).map((r) => `<tr><td>${esc(r.sym)}</td><td class="r ${r.points >= 0 ? "up" : "down"}">${r.points > 0 ? "+" : ""}${r.points}</td></tr>`).join("")}</tbody></table>`).join("") : `<p class="small muted" style="margin:0">While the scanner runs.</p>`;
    const fl = m.flows || {}, fd = fl.fii_dii || {}, pr = fl.participants || [];
    const cash = (w) => fd[w] && fd[w].net != null ? `${fd[w].net > 0 ? "+" : ""}${fk(fd[w].net)} cr` : "–";
    $("#lz-flows").innerHTML = `${fd.FII || fd.DII ? `<p class="small" style="margin:0 0 6px">${esc((fd.FII || fd.DII).date || "")}: FII cash <b>${cash("FII")}</b> · DII cash <b>${cash("DII")}</b></p>` : ""}
      ${pr.length ? `<table class="mini"><thead><tr><th>Who</th><th class="r">Index fut long %</th><th class="r">Calls net</th><th class="r">Puts net</th></tr></thead><tbody>${pr.map((r) => `<tr><td>${esc(r.who)}</td><td class="r">${r.fut_long_pct ?? "–"}</td><td class="r">${fk(r.call_net)}</td><td class="r">${fk(r.put_net)}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted" style="margin:0">From NSE after the close (Market context).</p>`}`;
    const ig = m.iv_grid || [];
    $("#lz-iv-grid").innerHTML = ig.length ? `<div class="table-wrap" style="max-height:360px;overflow:auto"><table class="mini"><thead><tr><th>Stock</th><th class="r">ATM IV</th><th class="r">IV 15-min chg</th><th class="r">IV ÷ HV</th><th class="r">Price chg</th><th class="r">PCR</th></tr></thead><tbody>${ig.slice(0, 60).map((r) => `<tr><td><a href="#/lens?s=${encodeURIComponent(r.sym)}">${esc(r.sym)}</a></td><td class="r">${n2(r.atm_iv)}</td><td class="r ${(r.iv_chg_15m || 0) > 0 ? "up" : (r.iv_chg_15m || 0) < 0 ? "down" : ""}">${n2(r.iv_chg_15m)}</td><td class="r">${n2(r.iv_hv)}</td><td class="r ${(r.chg_pct || 0) >= 0 ? "up" : "down"}">${n2(r.chg_pct)}%</td><td class="r">${n2(r.pcr)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="small muted" style="margin:0">While the scanner runs.</p>`;
    Object.values(LZ.charts).forEach((ch) => { if (!ch._fit) { ch.timeScale().fitContent(); ch._fit = true; } });
    stopPageTimer(); L.pageTimer = setTimeout(draw, 60000);
  };
  draw();
}
Object.assign(PAGES, { lens: pageLens, board: pageBoard });

/* ---------------- 2.30: amounts the way people write them ("40k", "1.5 lakh", "2,00,000") */
function parseAmount(text) {
  const t = String(text ?? "").trim().toLowerCase().replace(/₹/g, "").replace(/^(rs\.?|inr)\s*/, "").replace(/[,_\s]/g, "");
  const m = t.match(/^(\d+(?:\.\d+)?)([a-z]*)$/);
  if (!m) return { error: `Couldn't read "${text}". Try 200000, 2 lakh, 1.5L or 1 cr.` };
  const U = { "": 1, k: 1e3, thousand: 1e3, l: 1e5, lac: 1e5, lacs: 1e5, lakh: 1e5, lakhs: 1e5, cr: 1e7, crore: 1e7, crores: 1e7, m: 1e6, mn: 1e6, million: 1e6 };
  if (!(m[2] in U)) return { error: `Unknown unit "${m[2]}". Use k, lakh (L) or crore (cr).` };
  const v = Number(m[1]) * U[m[2]];
  if (v < 10000) return { error: "Capital must be at least ₹10,000." };
  if (v > 1e8) return { error: "That looks too big: check the zeros (most ₹10 crore)." };
  return { value: Math.round(v) };
}

/* the capital box: type any amount, see what it means, save it for the whole app */
function capitalBox(cur) {
  return `<div class="panel tight capbox"><h3>Capital <span class="muted small">one amount for paper trading, the risk check, the Lab and the Strategy board</span></h3>
    <div class="btn-row" style="margin:0;align-items:center"><input type="text" id="cap-in" value="${esc(fk(Math.round(cur || 0)))}" aria-label="Capital" placeholder="2 lakh, 1.5L, 5,00,000, 1 cr" style="max-width:200px">
      <button class="btn primary" id="cap-save">Save</button><span id="cap-prev" class="small"></span></div>
    <p class="small muted" style="margin:6px 0 0">Saving also applies the account rules for that amount (lots, trades a day, daily loss, profit lock) from rules/capital.json. Paper trades never use more than the account's free money. You can change any rule in Data &amp; settings.</p></div>`;
}

function bindCapitalBox(after) {
  const inp = $("#cap-in"), prev = $("#cap-prev"); if (!inp) return;
  let t = null;
  const show = () => { const r = parseAmount(inp.value); if (r.error) { prev.innerHTML = `<span class="down">${esc(r.error)}</span>`; return; }
    clearTimeout(t); t = setTimeout(async () => { try { const x = await api(`/api/live/capital?amount=${encodeURIComponent(inp.value)}`);
      const s = x.settings || {}; prev.innerHTML = `= <b>${rupees(x.capital)}</b> · ${esc(x.tier.name)}: ${s.max_lots} lot${s.max_lots > 1 ? "s" : ""}, ${s.max_trades_day} trades a day, risk ${s.risk_pct}% (${rupees(x.capital * s.risk_pct / 100)}) a trade, stop at −${s.max_daily_loss_pct}% a day${s.profit_lock ? `, lock +${rupees(s.profit_lock)}` : ""}`; }
      catch (e) { prev.innerHTML = `<span class="down">${esc(e.message)}</span>`; } }, 250); };
  inp.oninput = show; show();
  $("#cap-save").onclick = async () => { const r = parseAmount(inp.value); if (r.error) { toast(r.error); return; }
    try { const x = await post("/api/live/capital", { amount: inp.value }); toast(`Capital ${rupees(x.capital)} saved (${x.tier.name})`); if (after) after(); } catch (e) { toast(e.message); } };
}

/* prop-firm challenge + your trading patterns (live/coach.py) */
function coachBox(c) {
  if (!c) return "";
  const ch = c.challenge || {}, r = ch.rules || {}, pt = c.patterns || {};
  const st = { "in progress": ["p-amber", "In progress"], passed: ["p-up", "Passed"], failed: ["p-down", "Failed"], "not started": ["", "Not started"] }[ch.state] || ["", ch.state];
  const chal = `<div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3 style="margin:0">Prop-firm challenge <span class="pill ${st[0]}">${esc(st[1])}</span></h3><button class="btn" id="ch-new">Start a new one today</button></div>
    <p class="small" style="margin:0">Make <b>+${r.profit_target_pct}%</b> without losing more than ${r.daily_loss_pct}% in a day or ${r.max_drawdown_pct}% from the best point, over at least ${r.min_days} trading days (max ${r.max_days}). Pass it on paper before thinking about real money.</p>
    ${ch.days != null ? `<div class="kpis ch-kpis" style="margin-top:8px"><div class="kpi"><b class="${ch.net >= 0 ? "up" : "down"}">${rupees(ch.net)}</b><span>Net since ${esc(ch.start)} (target ${rupees(ch.target)})</span></div>
      <div class="kpi"><b>${ch.days}</b><span>Trading days${ch.days_left ? ` (${ch.days_left} more needed)` : ""}</span></div>
      <div class="kpi"><b class="down">${rupees(ch.worst_day)}</b><span>Worst day</span></div><div class="kpi"><b class="down">${rupees(ch.max_drawdown)}</b><span>Biggest drop</span></div></div>
      <div class="meter"><i class="${ch.progress_pct >= 100 ? "on" : ""}" style="width:${Math.min(100, ch.progress_pct || 0)}%"></i></div>` : ""}
    ${ch.why ? `<p class="small down" style="margin:6px 0 0">${esc(ch.why)}</p>` : ""}</div>`;
  const pat = (pt.lines || []).length ? `<div class="panel tight"><h3>Your patterns <span class="muted small">from ${pt.n} paper trades</span></h3><ul class="small">${pt.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    <p class="small muted" style="margin:0">After two losses in a row the risk check now reminds you (a warning, never a block).</p></div>` : "";
  return `<div class="split" style="margin-top:14px"><div>${chal}</div><div>${pat}</div></div>`;
}

/* ---------------- page: Strategy board (2.30) */
const REGIMES = [["auto", "Today's day type"], ["all", "All days"], ["trend_up", "Trend up"], ["trend_down", "Trend down"], ["range", "Range"], ["quiet", "Quiet range"], ["volatile", "Volatile"]];
/* 2.45: trade journeys (live/paths.py): every bench signal held to 15:15, peaks, givebacks, bottoms, recoveries */
async function drawJourneys() {
  const el = $("#bd-paths"); if (!el) return;
  let j; try { j = await api("/api/paths"); } catch (e) { el.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; return; }
  const pc = (v) => v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${v}%</span>`;
  const mn = (v) => v == null ? "" : ` <small class="muted">@${Math.round(v)} min</small>`;
  const bx = (b) => !b ? `<span class="muted small">needs more days</span>` : `<small>${b.target ? "+" + b.target + "%" : "no target"} / ${b.stop ? "−" + b.stop + "%" : "no stop"} / ${b.time === "eod" ? "15:15" : b.time + " min"}<br>older days ${pc(b.train_avg)} · newer ${pc(b.hold_avg)} <span class="muted">(${b.hold_n})</span></small>`;
  const ctx = (c, what) => !c ? "" : `<li>${what} (${c.n}): underlying ${c.und_avg >= 0 ? "+" : ""}${c.und_avg}% with the trade since entry · ${c.vwap_avg >= 0 ? "+" : ""}${c.vwap_avg}% from VWAP (wrong side ${c.wrong_side_vwap}%) · last 5 min against ${c.last5_against}% · option IV ${c.iv_avg >= 0 ? "+" : ""}${c.iv_avg} (falling ${c.iv_falling}%) · Nifty against ${c.nifty_against}%</li>`;
  const a = j.all || {};
  const rows = (j.groups || []).slice(0, 25).map((g) => `<tr><td><b>${esc(g.name)}</b><br><small class="muted">${esc(g.kind)}</small></td><td class="r">${g.n}</td>
      <td class="r">${g.p20_first ?? "–"}% / ${g.m20_first ?? "–"}%</td><td class="r">${pc(g.mfe)}${mn(g.mfe_min)}</td><td class="r">${pc(g.giveback != null ? -g.giveback : null)}</td>
      <td class="r">${pc(g.mae)}${mn(g.mae_min)}</td><td class="r">${g.recovered != null ? g.recovered + "%" : "–"} <small class="muted">${g.deep ? "of " + g.deep : ""}</small></td>
      <td class="r">${pc(g.end)}</td><td>${bx(g.best_exit)}</td></tr>`).join("");
  el.innerHTML = `<h3 style="margin-top:0">Trade journeys <span class="muted small">every bench signal's option held to 15:15 with no exit: when it peaked, how much it gave back, how deep it went, whether it came back</span></h3>
    ${!j.ready || !a.n ? `<p class="small muted">Built after each close from the day's recording (also for earlier recorded days). Nothing yet.</p>` : `
    <p class="small">${a.n.toLocaleString("en-IN")} signals: +20% came first ${a.p20_first}%, −20% first ${a.m20_first}% · average max profit ${pc(a.mfe)}${mn(a.mfe_min)}, then gave back ${pc(a.giveback != null ? -a.giveback : null)} · average max loss ${pc(a.mae)} · of ${a.deep} that fell 20%+, ${a.recovered ?? "–"}% came back to entry · held to 15:15 ${pc(a.end)}. Option %, entry at the ask, valued at the bid, before charges.</p>
    <ul class="small">${ctx(j.at_peak, "At the peak (just before profit started dropping)")}${ctx(j.at_drop, "When the drop began (gave back a third)")}${ctx(j.at_bottom, "At the bottom (just before a loss turned back)")}${ctx(j.at_turn, "When it had won back half the loss")}</ul>
    <div class="table-wrap"><table class="mini"><thead><tr><th>Strategy</th><th class="r">Signals</th><th class="r">+20% / −20% first</th><th class="r">Max profit</th><th class="r">Gave back</th><th class="r">Max loss</th><th class="r">Back from −20%</th><th class="r">At 15:15</th><th>Best fixed exit (chosen on older days, judged on newer)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">Recorded only: nothing here changes alerts, the paper account or the bench's exits. A better exit becomes a bench style only when it wins on days it never saw, on your say-so.</p>`}`;
}

async function pageBoard() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const reg = params.get("regime") || LS.get("board_reg", "auto");
  const fitOnly = params.has("fit") ? params.get("fit") === "1" : LS.get("board_fit", true);
  const fam = params.get("fam") || LS.get("board_fam", "all");
  let style = params.get("style") || LS.get("board_style", "all");
  LS.set("board_reg", reg); LS.set("board_fit", fitOnly); LS.set("board_fam", fam); LS.set("board_style", style);
  const [b, cap, acc] = await Promise.all([api(`/api/live/board?regime=${reg}${fam !== "all" ? `&family=${fam}` : ""}`), api("/api/live/capital"),
    api("/api/live/accounts").catch(() => null)]);
  if (state.page !== "board") return;
  const styles = (b.styles || []).filter((x) => fam === "all" || x.family === fam);
  if (style !== "all" && !styles.some((x) => x.style === style)) style = "all";
  const go = (p) => { const q = new URLSearchParams({ regime: reg, fit: fitOnly ? "1" : "0", fam, style, ...p }); location.hash = `#/board?${q}`; };
  const S = (x) => x && x.n ? `<b class="${x.avg >= 0 ? "up" : "down"}">${rupees(x.avg)}</b> <small class="muted">× ${x.n}</small><br><small class="muted">${x.win_pct}% won${x.pf != null ? ` · PF ${x.pf}` : ""}</small>` : `<span class="muted small">–</span>`;
  const rowsAll = b.rows.filter((r) => (!fitOnly || r.fits) && (style === "all" || r.style === style));
  // 2.50 (Mandeep #6): candle patterns are chart reading, not strategies of their own: kept apart, folded
  const isCdl = (r) => String(r.kind || "").startsWith("candle_");
  const rows = rowsAll.filter((r) => !isCdl(r)), cdlRows = rowsAll.filter(isCdl);
  const keyed = reg !== "all" && b.regime;
  const needs = (r) => r.family === "buy" || r.need_lot == null ? `${rupees(r.cost_lot)}` : `${rupees(r.need_lot)} <span class="muted">blocked</span>`;
  const when = (ts) => ts ? new Date(ts * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  const rowHtml = (list) => list.slice(0, 150).map((r) => `<tr class="click" data-bk="${esc(r.kind)}" data-bv="${esc(r.variant || "")}" data-bs="${esc(r.style)}" title="Show this strategy's trades"><td><b>${esc(r.label)}</b> <small class="muted">▸</small><br><small class="muted">${esc(r.style_label)} · ${esc((b.family_labels || {})[r.family] || "")} · ${r.days} day${r.days === 1 ? "" : "s"}${r.fits ? "" : ` · <span class="down">needs more capital</span>`}</small>${r.last_ts ? `<br><small class="muted">first ${when(r.first_ts)} · last ${when(r.last_ts)}</small>` : ""}</td>
      <td class="r">${S(r.all)}</td>${keyed ? `<td class="r">${S(r.regime)}</td>` : ""}<td class="r">${S(r.today)}</td>
      <td class="r small">${needs(r)}<br><span class="muted">risk ${rupees(r.risk_lot)}</span></td>
      <td class="r">${(keyed ? r.regime.rank : r.all.rank) != null ? `<b class="${(keyed ? r.regime.rank : r.all.rank) > 0 ? "up" : "down"}">${rupees(keyed ? r.regime.rank : r.all.rank)}</b>` : `<span class="muted small">collecting</span>`}</td></tr>`).join("");
  const tr = rowHtml(rows), cdlTr = rowHtml(cdlRows);
  const cdlTot = cdlRows.reduce((a, r) => ({ n: a.n + (r.all.n || 0), s: a.s + (r.all.total || 0) }), { n: 0, s: 0 });
  const best = (b.best || []).map((r) => `<li><b>${esc(r.label)}</b> <span class="muted small">${esc(r.style_label)}</span> · <b class="up">${rupees((keyed ? r.regime : r.all).avg)}</b> a trade over ${(keyed ? r.regime : r.all).n} trades, ${(keyed ? r.regime : r.all).win_pct}% won</li>`).join("");
  const famChips = [["all", "All ways"], ["buy", "Only buying"], ["mixed", "Buying + selling"], ["sell", "Only selling"]]
    .map(([v, l]) => `<button class="chip" data-fam="${v}" aria-pressed="${fam === v}">${l}</button>`).join("");
  main.innerHTML = `<div class="toolbar"><h1 class="page-title" style="margin:0">Strategy board</h1>
      <div class="btn-row" style="margin:0"><a class="btn" href="/api/live/export.xlsx" download title="Alert check (working / not working), daily validation, every strategy on the test bench, exit styles, Strategy Lab results and the paper accounts">Download Excel</a></div></div>
    <p class="lede">Every strategy paper-traded on every signal in ${(b.styles || []).length} styles (only buying, buying + selling, only selling), 1 lot each, like the real paper account (ask in, bid out, all charges), ranked for ${keyed ? `<b>${esc((REGIMES.find((x) => x[0] === b.regime) || [0, b.regime])[1])}</b> days` : "all days"} and your capital ${rupees(b.capital)}.
      ${b.day_type ? `Today so far: <b>${esc(b.day_type)}</b>.` : ""}</p>
    ${capitalBox(cap.capital)}
    ${accountsPanel(acc)}
    <div class="panel tight"><h3>Best for you right now <span class="muted small">fits ${rupees(b.capital)}, positive after costs, enough trades</span></h3>
      ${best ? `<ol class="small">${best}</ol>` : `<p class="small muted">Nothing has earned it yet: ${b.n_closed.toLocaleString("en-IN")} bench trades closed so far${b.n_open ? `, ${b.n_open} open` : ""}. A strategy needs at least ${b.min_n} closed trades and a positive average after its uncertainty. The record grows every market day.</p>`}</div>
    <div class="panel tight" id="bd-paths"></div>
    <div class="toolbar"><div class="chips" role="group" aria-label="Way of trading">${famChips}</div></div>
    <div class="toolbar"><div class="btn-row">
      <label class="field inline">Day type <select id="bd-reg">${REGIMES.map(([v, l]) => `<option value="${v}" ${v === reg ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field inline">Style <select id="bd-style"><option value="all">All styles</option>${styles.map((x) => `<option value="${esc(x.style)}" ${x.style === style ? "selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>
      <label class="check small"><input type="checkbox" id="bd-fit" ${fitOnly ? "checked" : ""}><span>Only what fits my capital</span></label></div></div>
    <div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="r">All days<br><small class="muted">₹ a trade × trades</small></th>${keyed ? `<th class="r">This day type</th>` : ""}<th class="r">Today</th><th class="r">1 lot needs</th><th class="r" title="Average minus its uncertainty: what you can count on">Rank</th></tr></thead>
      <tbody>${tr || `<tr><td colspan="6" class="muted">No bench trades ${fam !== "all" ? "in this way of trading " : ""}yet: they start with the next scanner session (every directional alert, every idea on trial, every Lab library / book setup and every mover test).</td></tr>`}</tbody></table></div>
    ${cdlRows.length ? `<details class="lab-adv" style="margin-top:14px"><summary><b>Chart reading: candle patterns</b> <span class="muted small">${cdlRows.length} rows · ${cdlTot.n.toLocaleString("en-IN")} trades · ${rupees(cdlTot.n ? cdlTot.s / cdlTot.n : null)} a trade</span></summary>
      <p class="small muted">Candles read the chart: their job is to confirm (or warn against) other strategies, so they are not in the list above. Each one is still paper-tested on its own here to measure it. Whether a candle that agrees makes other signals better: Scorecard → Credibility check.</p>
      <div class="table-wrap"><table><thead><tr><th>Candle pattern</th><th class="r">All days</th>${keyed ? `<th class="r">This day type</th>` : ""}<th class="r">Today</th><th class="r">1 lot needs</th><th class="r">Rank</th></tr></thead><tbody>${cdlTr}</tbody></table></div></details>` : ""}
    <p class="note">${esc(b.note)} Last ${b.days} days.</p>`;
  bindCapitalBox(() => pageBoard());
  drawJourneys();
  main.querySelectorAll("tr[data-bk]").forEach((row) => row.onclick = async () => {      // 2.38: every trade with its date and time
    const nx = row.nextElementSibling;
    if (nx && nx.classList.contains("bd-trades")) { nx.remove(); return; }
    const cols = row.children.length;
    row.insertAdjacentHTML("afterend", `<tr class="bd-trades"><td colspan="${cols}" class="small muted">Loading trades…</td></tr>`);
    const box = row.nextElementSibling;
    try {
      const q = new URLSearchParams({ kind: row.dataset.bk, style: row.dataset.bs, variant: row.dataset.bv, limit: "60" });
      const t = await api(`/api/live/board/trades?${q}`);
      const body = (t.trades || []).map((x) => `<tr><td>${when(x.ts)}</td><td>${arrow(x.direction)} ${esc(x.sym)}</td>
        <td class="r">${fx(x.entry)} → ${x.exit != null ? fx(x.exit) : "<span class='muted'>open</span>"}<br><small class="muted">${x.exit_ts ? "out " + when(x.exit_ts) : ""}${x.reason ? " · " + esc(x.reason) : ""}</small></td>
        <td class="r">${x.lots} lot × ${(x.lot || 0).toLocaleString("en-IN")}<br><small class="muted">${(x.qty || 0).toLocaleString("en-IN")} qty · ${rupees(x.capital)}</small></td>
        <td class="r">${x.net != null ? `<b class="${x.net >= 0 ? "up" : "down"}">${rupees(x.net)}</b>` : "<span class='muted'>–</span>"}</td></tr>`).join("");
      box.firstElementChild.innerHTML = body ? `<div class="table-wrap"><table class="mini"><thead><tr><th>In at</th><th>Symbol</th><th class="r">Price</th><th class="r">Size · money</th><th class="r">Net</th></tr></thead><tbody>${body}</tbody></table></div>` : "No trades yet.";
    } catch (e) { box.firstElementChild.textContent = e.message; }
  });
  $("#bd-reg").onchange = (e) => go({ regime: e.target.value });
  $("#bd-style").onchange = (e) => go({ style: e.target.value });
  $("#bd-fit").onchange = (e) => go({ fit: e.target.checked ? "1" : "0" });
  main.querySelectorAll("[data-fam]").forEach((x) => x.onclick = () => go({ fam: x.dataset.fam, style: "all" }));
  L.pageTimer = setTimeout(() => { if (state.page === "board") pageBoard(); }, acc && acc.refreshing && !(acc.cells || []).length ? 6000 : 60000);
}

/* 2.31 capital test: virtual accounts at 4 capitals x 3 ways of trading x 2 choosing rules */
function accountsPanel(g) {
  if (!g) return "";
  if (!g.cells || !g.cells.length) return g.refreshing ? `<div class="panel tight"><h3>Capital test</h3><p class="small muted" style="margin:0">Working out the ${(g.levels || []).length || 4} capital accounts from the test bench… (refreshes by itself)</p></div>` : "";
  const find = (lv, f, p) => g.cells.find((c) => c.capital === lv && c.family === f && c.policy === p);
  const line = (c) => !c ? "" : c.n ? `<b class="${c.net >= 0 ? "up" : "down"}">${c.net >= 0 ? "+" : ""}${rupees(c.net)}</b> <small class="muted">(${c.return_pct >= 0 ? "+" : ""}${c.return_pct}%) · ${c.n} trade${c.n === 1 ? "" : "s"} · ${c.win_pct}% won · deepest fall ${c.max_dd_pct}%</small>`
    : `<small class="muted">no trade yet${c.candidates ? ` (${c.candidates.toLocaleString("en-IN")} signals, none fitted)` : ""}</small>`;
  const cards = g.levels.map((lv) => { const t = find(lv, "buy", "all");
    return `<div class="capcard"><h4>${rupees(lv)} <small class="muted">${esc((t && t.tier) || "")}</small></h4>
      <table class="mini"><tbody>${g.families.map((f) => `<tr><td><b>${esc(f.label)}</b><div>All signals: ${line(find(lv, f.family, "all"))}</div><div>Proven only: ${line(find(lv, f.family, "proven"))}</div></td></tr>`).join("")}</tbody></table></div>`; }).join("");
  return `<div class="panel tight"><h3>Capital test <span class="muted small">${g.levels.length} capitals × 3 ways of trading, paper, last ${g.days} days</span></h3>
    <p class="small muted" style="margin:0 0 8px">Each account replays the test bench with its own rules (lots, risk a trade, trades a day, daily loss stop, money for premium and margin) and grows or shrinks with its profit. <b>All signals</b> = every signal that fits, first come. <b>Proven only</b> = only strategies already positive after costs on earlier days (${g.proven_min_n}+ trades)${g.proven_today ? `: ${g.proven_today} qualify today` : ": none qualify yet"}.</p>
    <div class="capgrid">${cards}</div>
    <p class="small muted" style="margin:6px 0 0">${g.n_trades.toLocaleString("en-IN")} closed bench trades. ${esc(g.note)}</p></div>`;
}

/* 2.35 Live page: today's events (scheduled days, world events, fresh official news) */
async function loadEventsMini() {
  let v; try { v = await api("/api/live/events"); } catch (_) { return; }
  v.fetched = Date.now() / 1000;
  L.eventsMini = v; const el = $("#lv-events"); if (el) el.outerHTML = eventsMini(v);
}
function eventsMini(v) {
  if (!v) return `<div id="lv-events"></div>`;
  const r = v.today || {}, items = r.items || [];
  const fresh = (v.items || []).filter((x) => (x.impact === "high" || (x.impact === "medium" && (x.syms || []).length)) && x.ago_min <= 24 * 60).slice(0, 4);
  if (!items.length && !fresh.length) return `<div id="lv-events"></div>`;
  const col = (l) => l === "high" ? "down" : l === "medium" ? "amber" : "muted";
  return `<div id="lv-events" class="panel tight"><h3 style="margin:0 0 4px">Events today <span class="muted small">information only</span> ${asOf(v.fetched)}</h3>
    ${items.length ? `<ul class="plain small" style="margin:0">${items.map((i) => `<li><span class="${col(i.level)}">●</span> ${esc(i.what)}</li>`).join("")}</ul>` : ""}
    ${fresh.length ? `<p class="small muted" style="margin:6px 0 2px">Latest official news</p><ul class="plain small" style="margin:0">${fresh.map((x) => `<li><span class="${col(x.impact)}">●</span> <b>${esc(x.who)}</b> ${x.link ? `<a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <span class="muted">${x.ago_min < 60 ? x.ago_min + " min" : Math.round(x.ago_min / 60) + " h"} ago${(x.syms || []).length ? " · " + x.syms.map(esc).join(", ") : ""}</span></li>`).join("")}</ul>` : ""}</div>`;
}

/* Live page: what works today (top of the Strategy board) */
async function loadBoardMini() {
  let b; try { b = await api("/api/live/board?regime=auto"); } catch (_) { return; }
  b.fetched = Date.now() / 1000;
  L.boardMini = b; const el = $("#lv-board"); if (el) el.outerHTML = boardMini(b);
}
function boardMini(b) {
  if (!b) return `<div id="lv-board"></div>`;
  const keyed = !!b.regime;
  const best = (b.best || []).slice(0, 3).map((r) => { const x = keyed ? r.regime : r.all;
    return `<li><b>${esc(r.label)}</b> <span class="muted small">${esc(r.style_label)}</span> <b class="up">${rupees(x.avg)}</b><small class="muted"> × ${x.n}</small></li>`; }).join("");
  return `<div id="lv-board" class="panel tight"><div class="toolbar" style="margin:0 0 4px"><h3 style="margin:0">What works ${keyed ? `on ${esc(b.day_type || "days like today")}` : "so far"} ${asOf(b.fetched)}</h3><a class="small" href="#/board">Strategy board →</a></div>
    ${best ? `<ol class="small" style="margin:4px 0 0">${best}</ol>` : `<p class="small muted" style="margin:4px 0 0">Collecting: ${b.n_closed.toLocaleString("en-IN")} honest bench trades so far. Nothing fits ${rupees(b.capital)} with a positive record yet.</p>`}</div>`;
}
