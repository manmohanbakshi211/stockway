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
L.sevFilter = LS.get("sevFilter", 1);

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
const arrow = (d) => (d === "up" ? `<span class="up" aria-label="up">▲</span>` : d === "down" ? `<span class="down" aria-label="down">▼</span>` : `<span class="amber" aria-label="watch">●</span>`);
const SEV = { 1: "Watch", 2: "Important", 3: "Act now" };
const KIND = {
  sharp_move: "Sharp move", orb: "Opening-range breakout", day_high: "New day high", day_low: "New day low",
  vwap_cross: "VWAP cross", volume_surge: "Volume surge", oi_buildup: "Futures OI buildup", unusual_options: "Unusual options",
  oi_wall: "OI wall shift", pcr_shift: "PCR shift", iv_spike: "IV spike", beyond_priced: "Bigger than priced",
  vix_jump: "VIX jump", vix_drop: "VIX drop", news: "News", order_flow: "Order flow", absorption: "Absorption",
  gamma_flip: "Gamma flip", big_print: "Big trade", filing: "Company filing", macro: "Global / macro",
  setup_ema_pullback: "Setup: EMA pullback", setup_vwap_reclaim: "Setup: VWAP reclaim", setup_cpr_breakout: "Setup: CPR breakout",
  setup_pdh_break: "Setup: prev-day break", setup_wall_fade: "Setup: OI wall rejection",
  pattern_double_top: "Pattern: double top", pattern_double_bottom: "Pattern: double bottom", pattern_head_shoulders: "Pattern: head & shoulders",
  pattern_inv_hs: "Pattern: inverse H&S", pattern_triangle: "Pattern: triangle", pattern_flag: "Pattern: flag",
  pattern_breakout_retest: "Pattern: breakout + retest", pattern_rsi_divergence: "Pattern: RSI divergence", pattern_candle_at_level: "Pattern: candle at level",
};

/* ---------------- polling + notifications */
function livePage() { return ["live", "chart", "world", "desk", "scanner", "sym", "alerts", "paper", "connect", "context", "scorecard", "briefs"].includes(state.page); }

async function pollSnapshot() {
  try {
    const s = await api(`/api/live/snapshot?since=${L.lastId}`);
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

function notify(a) {
  const min = LS.get("notifySev", 2);
  if (a.severity < min) return;
  if (LS.get("sound", true) && a.severity >= 3) beep();
  if ("Notification" in window && Notification.permission === "granted" && document.visibilityState !== "visible" || (LS.get("notifyAlways", false) && "Notification" in window && Notification.permission === "granted")) {
    const n = new Notification(`${a.direction === "up" ? "▲" : a.direction === "down" ? "▼" : "●"} ${a.title}`, { body: a.detail + (a.option ? `\nIdea: ${a.option.label} @ ₹${a.option.ltp}` : ""), tag: `sw-${a.id}` });
    n.onclick = () => { window.focus(); location.hash = `#/sym?s=${encodeURIComponent(a.sym)}`; };
  } else if (!livePage() || state.page !== "live") {
    toast(a.title);
  }
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
  const msg = st.state === "running" ? `${st.source === "sim" ? "Simulator" : "Upstox"} · ${subs} · ${lag}${speed}` : esc(st.message || "");
  let btns = "";
  if (st.running) btns = `<button class="btn" data-live-stop>Stop</button>`;
  else {
    if (st.logged_in) btns += `<button class="btn primary" data-live-start="upstox">Start live</button>`;
    else btns += `<a class="btn primary" href="/upstox/login">Log in with Upstox</a>`;
    btns += `<button class="btn" data-live-start="sim">Run simulator</button>`;
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

function indexCard(m) {
  const o = m.opt || {}, f = m.fut || {};
  const bu = f.buildup_label ? `<span class="pill ${f.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(f.buildup_label)}</span>` : "";
  return `<a class="icard" href="#/sym?s=${encodeURIComponent(m.sym)}">
    <div class="ic-head"><b>${esc(m.name)}</b>${bu}</div>
    <div class="ic-price">${fx(m.spot)} ${fpc(m.chg_pct)}</div>
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
    ${biasMeter(m.score)}
    ${m.reasons && m.reasons.length ? `<p class="ic-why">${esc(m.reasons.slice(0, 3).join(" · "))}</p>` : ""}
  </a>`;
}

function alertItem(a, full = false, extra = "") {
  const o = a.option, d = a.data || {};
  const lv = d.levels || {}, st = d.structure, tr = d.track;
  const link = d.link ? ` <a href="${esc(d.link)}" target="_blank" rel="noopener">Read</a>` : "";
  const score = d.score != null ? `<span class="score" style="--s:${d.score}" title="Signal score: confluence, track record, market type${d.model_p != null ? " and the learned model" : ""}">${d.score}</span>` : "";
  const track = tr && tr.n ? `<span class="track ${tr.hit_rate >= 50 ? "good" : tr.hit_rate < 35 ? "bad" : ""}">Worked ${tr.hit_rate}% of ${tr.n} times</span>` :
    (a.direction === "up" || a.direction === "down") ? `<span class="track muted">New signal type: no track record yet</span>` : "";
  const ev = (d.evidence || []).map((e) => `<span class="ev ${e.agree ? "yes" : "no"}">${e.agree ? "✓" : "✗"} ${esc(e.label)}</span>`).join("");
  const plan = lv.stop ? `<div class="plan"><span>Target <b>${fx(lv.target)}</b> <small>(${lv.target_pct}%)</small></span><span>Wrong ${a.direction === "up" ? "below" : "above"} <b>${fx(lv.stop)}</b> <small>(${lv.stop_pct}%)</small></span>${lv.rr ? `<span>Reward:risk <b>${lv.rr}</b></span>` : ""}</div>` : "";
  let trade = "";
  if (o) {
    const s2 = st && st.type === "spread" && st.spread ? ` · or spread ${fk(st.spread.buy)}/${fk(st.spread.sell)} for ${rupees(st.spread.cost)}, max ${rupees(st.spread.max_profit)}` : "";
    const tag = st ? `<span class="pill ${st.type === "buy" ? "p-up" : st.type === "spread" ? "p-amber" : "p-down"}">${st.type === "buy" ? "Buy option" : st.type === "spread" ? "Use a spread" : "Skip"}</span>` : "";
    trade = `<div class="idea"><span>${tag} <b>${esc(o.label)}</b> @ ₹${fx(o.ltp)} · ${rupees(o.cost_per_lot)}/lot${s2}${st ? `<br><small class="muted">${esc(st.why)}</small>` : ""}</span>
      <span class="btn-row">${lv.stop ? `<button class="link-btn" data-tm="${a.id}">Time Machine</button>` : ""}<button class="link-btn" data-paper="${a.id}">Paper trade</button><button class="link-btn" data-explain="${a.id}">Why?</button></span></div>`;
  } else trade = `<div class="idea"><span class="muted small">${esc(d.regime ? "Market: " + d.regime : "")}</span><span class="btn-row"><button class="link-btn" data-explain="${a.id}">Why?</button></span></div>`;
  return `<li class="alert sev${a.severity}" data-aid="${a.id}">
    <div class="al-time">${hms(a.ts)}<span class="sevtag s${a.severity}">${SEV[a.severity]}</span>${score}</div>
    <div class="al-body"><div class="al-title">${arrow(a.direction)} <a href="${a.sym === "GLOBAL" ? "#/world" : `#/chart?s=${encodeURIComponent(a.sym)}`}">${esc(a.title)}</a></div>
      <p>${esc(a.detail)}${link} <span class="muted small">${esc(KIND[a.kind] || a.kind)}</span> ${track}</p>
      ${d.muted ? `<p class="muted small">⤓ ${esc(d.muted)}</p>` : ""}
      ${ev ? `<div class="evs">${ev}</div>` : ""}${plan}${trade}${riskLine(d.risk)}<div class="tm-out" id="tm-${a.id}"></div>
      ${a.ai ? `<div class="ai-text">${fmtAI(a.ai)}</div>` : ""}${extra}</div></li>`;
}

function riskLine(r) {
  if (!r) return "";
  if (r.verdict === "PASS") return `<p class="risk pass small"><b>Risk check: PASS</b> · ${r.lots} lot${r.lots > 1 ? "s" : ""} · max loss ${rupees(r.max_loss)} at the stop · uses ${rupees(r.capital_used)}${(r.warnings || []).length ? ` · <span class="amber">${r.warnings.map(esc).join(" ")}</span>` : ""}</p>`;
  return `<p class="risk block small"><b>Risk check: BLOCK</b> · ${(r.reasons || []).map(esc).join(" ")} <span class="muted">No paper trade.</span></p>`;
}

function tmCard(r) {
  const m = r.mc, h = r.history, v = r.verdict;
  const cls = v.grade === "edge" ? "up" : v.grade === "against" ? "down" : "amber";
  return `<div class="tm"><p class="small" style="margin:0 0 4px"><b>Time Machine</b> <span class="muted">· ${m.replays.toLocaleString("en-IN")} replays of the rest of the day from ${esc(r.sym)}'s last ${h.sessions} sessions (seed ${m.seed})</span></p>
    <div class="tm-bars"><span class="up">Target first <b>${m.target_first}%</b></span><span class="down">Stop first <b>${m.stop_first}%</b></span><span>Neither by 15:15 <b>${m.neither}%</b></span><span>Avg <b>${m.avg_r > 0 ? "+" : ""}${m.avg_r}R</b></span></div>
    <p class="small" style="margin:4px 0">15:15 price: median ${fx(m.close_pct[2])}, 90% between ${fx(m.close_pct[0])} and ${fx(m.close_pct[4])}.</p>
    <p class="small" style="margin:4px 0">Real past (same time of day, same stop/target distances, ${h.sessions} sessions): ${h.target} target, ${h.stop} stop, ${h.neither} neither, avg ${h.avg_r > 0 ? "+" : ""}${h.avg_r}R.</p>
    <p class="small ${cls}" style="margin:4px 0"><b>${esc(v.line)}</b>${(v.notes || []).map((n) => `<br>${esc(n)}`).join("")}</p>
    <p class="muted small" style="margin:0">A simulation from recent behaviour, not a prediction. Results, news and events can do things the last 60 days never did.</p></div>`;
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
    try { await post(`/api/live/alerts/${b.dataset.paper}/paper`, {}); toast("Paper trade opened"); } catch (err) { toast(err.message); }
    b.disabled = false;
  });
  root.querySelectorAll("[data-tm]").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); b.disabled = true; const t = b.textContent; b.textContent = "Replaying…";
    try { const r = await post(`/api/live/alerts/${b.dataset.tm}/timemachine`, {}); const el = document.getElementById("tm-" + b.dataset.tm); if (el) el.innerHTML = tmCard(r); }
    catch (err) { toast(err.message); }
    b.disabled = false; b.textContent = t;
  });
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

function stopPageTimer() { clearTimeout(L.pageTimer); L.pageTimer = null; }

/* ---------------- page: Live */
async function pageLive() {
  stopPageTimer();
  if (!L.snap) await pollSnapshot();
  main.innerHTML = `<div class="live-head"><div><h1 class="page-title">Live market</h1>
      <p class="lede" style="margin-bottom:14px">Nifty, Bank Nifty and every F&amp;O stock with its options, checked every second.</p></div></div>
    <div id="lv-bar"></div><div id="lv-body"></div>`;
  renderLive(true);
}

function setupCard() {
  const s = L.snap.status;
  return `<div class="empty" style="max-width:none"><h2>Connect live data</h2>
    <p>Live prices come from your free Upstox account. Set it up once, then log in each morning (Upstox needs a fresh login every day).</p>
    <div class="btn-row"><a class="btn primary" href="#/connect">Set up Upstox</a><button class="btn" data-live-start="sim">Try the simulator first</button></div>
    <p class="note">The simulator plays a made-up trading day with real-looking moves, so you can learn the screens before connecting. ${s.state === "error" ? `<br><b class="down">${esc(s.message)}</b>` : ""}</p></div>`;
}

function renderLive(force = false) {
  const bar = $("#lv-bar"), body = $("#lv-body");
  if (!bar || !body || !L.snap) return;
  const ae = document.activeElement;
  if (!force && ae && body.contains(ae) && ae.tagName === "BUTTON") return;   // don't re-render under a click
  if (!force && body.querySelector("details.story[open]")) return;             // reading a story: hold still
  bar.innerHTML = statusBar(); bindLiveButtons(bar);
  const s = L.snap;
  if (!s.status.running && !s.indices.length) {
    body.innerHTML = s.status.logged_in ? `<div class="empty" style="max-width:none"><h2>${esc(s.status.message || "Scanner is off")}</h2><p>It starts by itself at 9:00 AM on market days. Start now to scan immediately.</p><div class="btn-row"><button class="btn primary" data-live-start="upstox">Start live</button><button class="btn" data-live-start="sim">Run simulator</button></div></div>` : setupCard();
    bindLiveButtons(body); return;
  }
  const pulse = s.pulse || {};
  const pulseHTML = pulse.ai ? fmtAI(pulse.ai) + `<p class="muted small">AI pulse at ${hm(pulse.ts)}</p>` : (pulse.lines || []).map((l) => `<p>${esc(l)}</p>`).join("") || `<p class="muted">Building the picture…</p>`;
  const alerts = L.alerts.filter((a) => a.severity >= L.sevFilter).slice(0, 60);
  const mv = s.movers || { hot: [], gainers: [], losers: [] };
  const tab = LS.get("moversTab", "hot");
  const mrows = (mv[tab] || []).map((m) => `<tr class="click" data-sym="${esc(m.sym)}"><td><b>${esc(m.sym)}</b></td><td class="r">${fx(m.spot)}</td><td class="r">${fpc(m.chg_pct)}</td><td class="r">${m.rel_vol ? m.rel_vol.toFixed(1) + "×" : "–"}</td><td class="r">${m.heat ?? "–"}</td></tr>`).join("");
  const pp = s.paper;
  const news = (s.news || []).slice(0, 8).map((n) => `<li><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a><span class="muted small"> ${esc(n.source)} · ${hm(n.ts)}${n.syms && n.syms.length ? " · " + n.syms.map(esc).join(", ") : ""}</span></li>`).join("");
  const mk = s.market || {}, br = mk.breadth, cx = s.context || {};
  const fii = cx.fii, fd = cx.fii_dii || {};
  const g = Object.fromEntries((cx.global || []).map((x) => [x.name, x]));
  const reg = mk.regime;
  const strip = `<div class="mstrip">
    ${reg ? `<div class="ms-item ms-wide"><span>Day type</span><b>${esc(reg.label)}</b><small>${esc(reg.detail || "")}</small></div>` : ""}
    ${br ? `<div class="ms-item"><span>Breadth</span><b><span class="up">${br.adv}</span> / <span class="down">${br.dec}</span></b>
      <div class="adbar"><i style="width:${(br.adv / Math.max(1, br.adv + br.dec)) * 100}%"></i></div><small>${br.above_vwap} of ${br.n} above VWAP</small></div>
      <div class="ms-item"><span>F&amp;O buildups</span><b><span class="up">${br.long_buildup}</span> long · <span class="down">${br.short_buildup}</span> short</b><small>${br.short_covering} covering · ${br.long_unwinding} unwinding</small></div>` : ""}
    ${fii ? `<div class="ms-item"><span>FII index futures</span><b class="${fii.fut_long_pct >= 50 ? "up" : "down"}">${fii.fut_long_pct}% long</b><small>${fii.fut_net_chg != null ? `${fii.fut_net_chg > 0 ? "+" : ""}${fk(fii.fut_net_chg)} contracts yesterday` : ""}</small></div>` : ""}
    ${fd.FII ? `<div class="ms-item"><span>Cash (${esc(fd.FII.date)})</span><b>FII <span class="${fd.FII.net >= 0 ? "up" : "down"}">${fd.FII.net >= 0 ? "+" : ""}${fk(Math.round(fd.FII.net))}</span> · DII <span class="${(fd.DII || {}).net >= 0 ? "up" : "down"}">${(fd.DII || {}).net >= 0 ? "+" : ""}${fk(Math.round((fd.DII || {}).net || 0))}</span></b><small>₹ crore</small></div>` : ""}
    ${g["US S&P 500 fut"] ? `<div class="ms-item"><span>Global</span><b>US ${fpc(g["US S&P 500 fut"].chg_pct, 1)} · Crude ${fpc((g["Brent crude"] || {}).chg_pct, 1)}</b><small>USD/INR ${g["USD/INR"] ? g["USD/INR"].last.toFixed(2) : "–"}</small></div>` : ""}
    ${cx.ban && cx.ban.length ? `<div class="ms-item"><span>F&amp;O ban</span><b class="down">${cx.ban.slice(0, 4).map(esc).join(", ")}${cx.ban.length > 4 ? "…" : ""}</b><small><a href="#/context">Market context →</a></small></div>` : ""}
  </div>`;
  const secs = (mk.sectors || []).filter((x) => x.n >= 2);
  const secHTML = secs.length ? `<div class="panel tight"><h3>Sectors</h3><div class="sectors">${secs.slice(0, 12).map((x) => `<div class="sec" style="--c:${Math.max(-3, Math.min(3, x.chg))}" title="${x.up} of ${x.n} up · best ${esc(x.best[0])} ${x.best[1].toFixed(1)}% · worst ${esc(x.worst[0])} ${x.worst[1].toFixed(1)}%"><span>${esc(x.sector)}</span><b>${x.chg > 0 ? "+" : ""}${x.chg.toFixed(2)}%</b></div>`).join("")}</div></div>` : "";
  const ctab = LS.get("contribTab", "BANKNIFTY");
  const cb = (mk.contrib || {})[ctab];
  const maxPts = cb ? Math.max(1, ...cb.rows.map((r) => Math.abs(r.points))) : 1;
  const contribHTML = mk.contrib && Object.keys(mk.contrib).length ? `<div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3>Who is moving the index</h3>
      <div class="seg small-seg">${Object.keys(mk.contrib).map((k) => `<button data-ctab="${k}" aria-pressed="${ctab === k}">${k === "BANKNIFTY" ? "Bank Nifty" : "Nifty"}</button>`).join("")}</div></div>
    ${cb ? `<div class="contrib">${cb.rows.slice(0, 8).map((r) => `<div class="cb-row"><span>${esc(r.sym)}</span><div class="cb-bar"><i class="${r.points >= 0 ? "pos" : "neg"}" style="width:${Math.abs(r.points) / maxPts * 50}%;${r.points >= 0 ? "left:50%" : `right:50%`}"></i></div><b class="${r.points >= 0 ? "up" : "down"}">${r.points > 0 ? "+" : ""}${r.points.toFixed(0)}</b></div>`).join("")}</div>
      <p class="muted small" style="margin:6px 0 0">Points from index moves × approximate weights. These explain ${cb.explained > 0 ? "+" : ""}${cb.explained.toFixed(0)} of the ${cb.actual > 0 ? "+" : ""}${cb.actual.toFixed(0)}-point move.</p>` : ""}</div>` : "";
  body.innerHTML = `${strip}
    <div class="icards">${s.indices.slice(0, 4).map(indexCard).join("")}</div>
    <div class="live-grid">
      <section>
        <div class="toolbar"><h2 style="margin:0">Alerts <span class="muted small">${L.alerts.length} today</span></h2>
          <div class="seg" role="group" aria-label="Minimum importance">${[1, 2, 3].map((v) => `<button data-sev="${v}" aria-pressed="${L.sevFilter === v}">${v === 1 ? "All" : v === 2 ? "Important" : "Act now"}</button>`).join("")}</div></div>
        <ul class="alerts">${storyList(alerts) || `<li class="muted" style="padding:18px">No alerts yet. They appear here the moment something happens.</li>`}</ul>
      </section>
      <aside>
        ${brainCard(L.brain, { button: true })}
        <div class="panel pulse"><h3>Market pulse</h3>${pulseHTML}</div>
        ${contribHTML}${secHTML}
        <div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3>Movers</h3>
          <div class="seg small-seg">${[["hot", "Most active"], ["gainers", "Gainers"], ["losers", "Losers"]].map(([k, l]) => `<button data-mtab="${k}" aria-pressed="${tab === k}">${l}</button>`).join("")}</div></div>
          <table class="mini"><thead><tr><th>Stock</th><th class="r">Price</th><th class="r">Chg</th><th class="r">Vol</th><th class="r">Heat</th></tr></thead><tbody>${mrows}</tbody></table>
          <a class="small" href="#/scanner">Open full scanner →</a></div>
        ${pp ? `<a class="panel tight paperbox" href="#/paper"><h3>Paper trading${pp.guard && pp.guard.blocked ? ` <span class="pill p-down">Stopped for today</span>` : ""}</h3><div class="pb-row"><div><span>Booked</span><b class="${pp.net >= 0 ? "up" : "down"}">${rupees(pp.net)}</b></div><div><span>Open</span><b class="${pp.unreal >= 0 ? "up" : "down"}">${rupees(pp.unreal)}</b></div><div><span>Trades</span><b>${pp.n}${pp.open ? ` <small class="muted">+${pp.open} open</small>` : ""}</b></div><div><span>Win rate</span><b>${pp.win_rate != null ? pp.win_rate + "%" : "–"}</b></div></div></a>` : ""}
        <div class="panel tight"><h3>Market news</h3><ul class="news">${news || `<li class="muted">Loading headlines…</li>`}</ul></div>
      </aside>
    </div>`;
  bindLiveButtons(body); bindAlertButtons(body);
  body.querySelectorAll("[data-sev]").forEach((b) => b.onclick = () => { L.sevFilter = Number(b.dataset.sev); LS.set("sevFilter", L.sevFilter); renderLive(true); });
  body.querySelectorAll("[data-mtab]").forEach((b) => b.onclick = () => { LS.set("moversTab", b.dataset.mtab); renderLive(true); });
  body.querySelectorAll("[data-ctab]").forEach((b) => b.onclick = () => { LS.set("contribTab", b.dataset.ctab); renderLive(true); });
  body.querySelectorAll("[data-brain-run]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Thinking…"; try { L.brain = await post("/api/brain/run", {}); renderLive(true); } catch (e) { toast(e.message); b.disabled = false; } });
  body.querySelectorAll("tr[data-sym]").forEach((tr) => tr.onclick = () => { location.hash = `#/sym?s=${encodeURIComponent(tr.dataset.sym)}`; });
}

/* ---------------- page: Scanner */
const SCAN_COLS = [
  ["sym", "Symbol", (m) => `<b>${esc(m.sym)}</b>${m.is_index ? ` <span class="pill p-idx">Index</span>` : ""}`, false],
  ["spot", "Price", (m) => fx(m.spot), true],
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
  main.innerHTML = `<h1 class="page-title">Scanner</h1>
    <p class="lede">Every F&amp;O stock and index, ranked live. Click a row for its option chain.</p>
    <div id="lv-bar"></div>
    <div class="toolbar"><div class="chips">${Object.entries(SCAN_FILTERS).map(([k, [l]]) => `<button class="chip" data-f="${k}" aria-pressed="${L.filter === k}">${l}</button>`).join("")}</div>
      <input type="search" id="scan-q" placeholder="Find a stock" value="${esc(L.q)}" aria-label="Find a stock"></div>
    <div class="table-wrap scan"><table><thead><tr>${SCAN_COLS.map(([k, l, , r]) => `<th class="${r ? "r" : ""} sortable" data-k="${k}" aria-sort="${L.sort.key === k ? (L.sort.dir > 0 ? "ascending" : "descending") : "none"}">${l}</th>`).join("")}</tr></thead><tbody id="scan-body"></tbody></table></div>
    <p class="note">Heat = how unusual activity is right now (volume pace, speed of the move, futures OI change, unusual option trades). Bias = −100 (bearish) to +100 (bullish) from VWAP, buildup, momentum and opening range.</p>`;
  $("#lv-bar").innerHTML = statusBar(); bindLiveButtons($("#lv-bar"));
  main.querySelectorAll("[data-f]").forEach((b) => b.onclick = () => { L.filter = b.dataset.f; main.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x === b)); drawScanner(); });
  main.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
    const k = th.dataset.k; L.sort = { key: k, dir: L.sort.key === k ? -L.sort.dir : (k === "sym" ? 1 : -1) };
    main.querySelectorAll("th[data-k]").forEach((x) => x.setAttribute("aria-sort", x.dataset.k === k ? (L.sort.dir > 0 ? "ascending" : "descending") : "none"));
    drawScanner();
  });
  $("#scan-q").oninput = (e) => { L.q = e.target.value; drawScanner(); };
  const tick = async () => {
    if (state.page !== "scanner") return;
    try { L.scanner = await api("/api/live/scanner"); drawScanner(); } catch (_) {}
    const bar = $("#lv-bar"); if (bar && L.snap) { bar.innerHTML = statusBar(); bindLiveButtons(bar); }
    L.pageTimer = setTimeout(tick, 2000);
  };
  tick();
}

function drawScanner() {
  const tb = $("#scan-body"); if (!tb) return;
  const q = L.q.trim().toUpperCase(), f = SCAN_FILTERS[L.filter][1], { key, dir } = L.sort;
  const rows = L.scanner.filter((m) => f(m) && (!q || m.sym.includes(q) || (m.name || "").toUpperCase().includes(q)))
    .sort((a, b) => { const x = a[key], y = b[key]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
  tb.innerHTML = rows.map((m) => `<tr class="click" data-sym="${esc(m.sym)}">${SCAN_COLS.map(([k, , fn, r]) => `<td class="${r ? "r" : ""}">${fn(m)}</td>`).join("")}</tr>`).join("") ||
    `<tr><td colspan="${SCAN_COLS.length}" class="muted">${L.scanner.length ? "Nothing matches this filter right now." : "Start the live scanner to fill this table."}</td></tr>`;
  tb.querySelectorAll("tr[data-sym]").forEach((tr) => tr.onclick = () => { location.hash = `#/sym?s=${encodeURIComponent(tr.dataset.sym)}`; });
}

/* ---------------- page: Symbol / option chain */
async function pageSym() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const sym = (params.get("s") || LS.get("lastSym", "NIFTY")).toUpperCase();
  if (L.sym !== sym) { L.sym = sym; L.expiry = null; }
  LS.set("lastSym", sym);
  main.innerHTML = `<div id="sym-body"><p class="muted">Loading ${esc(sym)}…</p></div>`;
  const tick = async (first) => {
    if (state.page !== "sym" || L.sym !== sym) return;
    try {
      const d = await api(`/api/live/symbol/${encodeURIComponent(sym)}${L.expiry ? `?expiry=${L.expiry}` : ""}`);
      drawSym(d, first);
    } catch (e) {
      if (first) $("#sym-body").innerHTML = `<div class="empty"><h2>${esc(sym)}</h2><p>${esc(e.message)}</p><a class="btn primary" href="#/live">Go to Live</a></div>`;
    }
    L.pageTimer = setTimeout(() => tick(false), 3000);
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
      `<td class="r ltp">${fx(x.ltp)}</td>`, `<td class="r">${fpc(x.chg_pct, 1)}</td>`, `<td class="r muted">${x.delta != null ? x.delta.toFixed(2) : ""}</td>`];
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
      <div class="sym-price">${fx(m.spot)} ${fpc(m.chg_pct)} ${f.buildup_label ? `<span class="pill ${f.buildup_sign > 0 ? "p-up" : "p-down"}">${esc(f.buildup_label)}</span>` : ""}</div></div>
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
      <label class="field inline">Expiry <select id="exp-sel">${expiries.filter(Boolean).map((e) => `<option value="${e}" ${e === (ch ? ch.expiry : o.expiry) ? "selected" : ""}>${new Date(e + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</option>`).join("")}</select></label></div>
    <div class="chain-sides"><b class="ce-h">← CALLS (CE)</b><b class="pe-h">PUTS (PE) →</b></div>
    <div class="table-wrap chain-wrap"><table class="chain"><thead>
      <tr><th class="r">OI · change</th><th class="r">Volume</th><th class="r">IV</th><th class="r">LTP</th><th class="r">Chg</th><th class="r">Δ</th><th class="strike">Strike</th>
          <th class="r">Δ</th><th class="r">Chg</th><th class="r">LTP</th><th class="r">IV</th><th class="r">Volume</th><th class="r">OI · change</th></tr></thead>
      <tbody>${chainRows}</tbody></table></div>
    <p class="note">Shaded = in the money. Biggest call OI above price acts as resistance, biggest put OI below acts as support. Max pain = the expiry price where option buyers collectively lose the most.</p>`;
  $("#exp-sel").onchange = (e) => { L.expiry = e.target.value; pageSym(); };
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
}

/* ---------------- page: Alerts history */
async function pageAlerts() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const day = params.get("day") || "latest", sev = Number(params.get("sev") || 1), sym = params.get("sym") || "";
  const d = await api(`/api/live/alerts?day=${day}&min_sev=${sev}${sym ? `&sym=${encodeURIComponent(sym)}` : ""}`);
  const byKind = {};
  d.alerts.forEach((a) => { byKind[a.kind] = (byKind[a.kind] || 0) + 1; });
  const go = (p) => { const q = new URLSearchParams({ day: d.day || "", sev, sym, ...p }); location.hash = `#/alerts?${q}`; };
  main.innerHTML = `<h1 class="page-title">Alerts</h1><p class="lede">Every alert, saved by day. ${d.alerts.length} ${d.day ? `on ${new Date(d.day + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}` : ""}.</p>
    <div class="toolbar"><div class="btn-row">
      <label class="field inline">Day <select id="al-day">${d.days.map((x) => `<option value="${x.day}" ${x.day === d.day ? "selected" : ""}>${x.day} (${x.n})</option>`).join("") || `<option>No alerts yet</option>`}</select></label>
      <div class="seg">${[1, 2, 3].map((v) => `<button data-s="${v}" aria-pressed="${sev === v}">${v === 1 ? "All" : v === 2 ? "Important" : "Act now"}</button>`).join("")}</div>
      <input type="search" id="al-sym" placeholder="Symbol" value="${esc(sym)}" aria-label="Filter by symbol"></div>
      <div class="chips small">${Object.entries(byKind).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="chip static">${esc(KIND[k] || k)} ${n}</span>`).join("")}</div></div>
    <ul class="alerts">${d.alerts.map((a) => alertItem(a, true)).join("") || `<li class="muted" style="padding:18px">No alerts for this filter.</li>`}</ul>`;
  $("#al-day").onchange = (e) => go({ day: e.target.value });
  main.querySelectorAll("[data-s]").forEach((b) => b.onclick = () => go({ sev: b.dataset.s }));
  $("#al-sym").onchange = (e) => go({ sym: e.target.value.trim().toUpperCase() });
  bindAlertButtons(main);
}

/* ---------------- page: Paper trades */
async function pagePaper() {
  stopPageTimer();
  const tick = async (first) => {
    if (state.page !== "paper") return;
    try { drawPaper(await api("/api/live/paper"), first); } catch (e) { if (first) main.innerHTML = `<div class="empty"><h2>Paper trading</h2><p>${esc(e.message)}</p></div>`; }
    L.pageTimer = setTimeout(() => tick(false), 3000);
  };
  tick(true);
}

function drawPaper(p, first) {
  const s = p.settings || {};
  const open = p.open.map((t) => {
    const chg = t.last && t.entry ? (t.last / t.entry - 1) * 100 : null;
    return `<tr><td><b>${esc(t.label)}</b><br><small class="muted">${esc(KIND[t.kind] || t.kind)}${t.manual ? " · by you" : ""}</small></td><td>${hm(t.entry_ts)}</td><td class="r">${fx(t.entry)}</td><td class="r">${fx(t.last)} ${fpc(chg, 1)}</td>
      <td class="r">${fx(t.target)} / ${fx(t.stop)}</td><td class="r ${t.unreal >= 0 ? "up" : "down"}">${rupees(t.unreal)}</td><td><button class="link-btn" data-close="${t.id}">Exit</button></td></tr>`;
  }).join("");
  const closed = p.closed.map((t) => `<tr><td><b>${esc(t.label)}</b><br><small class="muted">${esc(KIND[t.kind] || t.kind)}</small></td><td>${t.day}<br><small class="muted">${hm(t.entry_ts)}–${hm(t.exit_ts)}</small></td><td class="r">${fx(t.entry)} → ${fx(t.exit)}</td><td>${esc(t.reason)}</td><td class="r muted">${rupees(t.costs)}</td><td class="r ${t.net >= 0 ? "up" : "down"}"><b>${rupees(t.net)}</b></td></tr>`).join("");
  const kinds = p.by_kind.map((b) => `<tr><td>${esc(KIND[b.kind] || b.kind)}</td><td class="r">${b.n}</td><td class="r">${b.win_rate}%</td><td class="r ${b.avg >= 0 ? "up" : "down"}">${rupees(b.avg)}</td><td class="r ${b.net >= 0 ? "up" : "down"}"><b>${rupees(b.net)}</b></td></tr>`).join("");
  main.innerHTML = `<h1 class="page-title">Paper trading</h1>
    <p class="lede">Practice with zero risk. Strong alerts buy the suggested option automatically (lots sized to your risk, target +${s.target_pct}%, stop −${s.stop_pct}%, exit by ${s.exit_time}). Charges are real: brokerage, STT, exchange, GST, stamp. ${p.source === "sim" ? "<b>Showing simulator trades.</b>" : ""}</p>
    <div class="kpis"><div class="kpi"><b class="${p.net >= 0 ? "up" : "down"}">${rupees(p.net)}</b><span>Booked P&amp;L after charges</span></div>
      <div class="kpi"><b>${p.n}</b><span>Closed trades</span></div><div class="kpi"><b>${p.win_rate != null ? p.win_rate + "%" : "–"}</b><span>Winning trades</span></div>
      <div class="kpi"><b class="${p.unreal >= 0 ? "up" : "down"}">${rupees(p.unreal)}</b><span>Open, not booked (${p.open.length})</span></div></div>
    ${p.guard ? `<div class="${p.guard.blocked ? "banner" : "note"}" style="${p.guard.blocked ? "margin-top:18px" : ""}">${p.guard.blocked ? "⛔ " + esc(p.guard.why) : `Risk guard: ₹${fk(p.guard.risk_per_trade)} at risk per trade (lots sized to it), stops new trades after a ₹${fk(Math.abs(p.guard.loss_limit))} loss or ${s.max_trades_day} trades in a day. Today: ${p.guard.trades_today} trades, ${rupees(p.guard.booked_today)} booked.`}</div>` : ""}
    <h2>Open positions</h2>
    <div class="table-wrap"><table><thead><tr><th>Option</th><th>In at</th><th class="r">Entry</th><th class="r">Now</th><th class="r">Target / stop</th><th class="r">P&amp;L</th><th></th></tr></thead><tbody>${open || `<tr><td colspan="7" class="muted">No open paper trades.</td></tr>`}</tbody></table></div>
    <div class="split" style="margin-top:28px"><div><h2 style="margin-top:0">Which alerts make money</h2>
      <div class="table-wrap"><table><thead><tr><th>Alert type</th><th class="r">Trades</th><th class="r">Win</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${kinds || `<tr><td colspan="5" class="muted">Fills in as trades close.</td></tr>`}</tbody></table></div>
      <p class="note">After a few weeks this tells you which signals are worth real money, and which to ignore.</p></div>
      <div><h2 style="margin-top:0">Equity</h2><div class="chart" id="eq-chart" style="height:240px"></div></div></div>
    <h2>Closed trades</h2>
    <div class="table-wrap"><table><thead><tr><th>Option</th><th>When</th><th class="r">Price</th><th>Exit</th><th class="r">Charges</th><th class="r">Net</th></tr></thead><tbody>${closed || `<tr><td colspan="6" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
  main.querySelectorAll("[data-close]").forEach((b) => b.onclick = async () => { try { await post(`/api/live/paper/${b.dataset.close}/close`, {}); toast("Closed"); pagePaper(); } catch (e) { toast(e.message); } });
  const el = $("#eq-chart");
  if (p.equity.length && window.Plotly) {
    const first = p.closed.length ? Math.min(...p.closed.map((t) => t.entry_ts)) : p.equity[0].ts - 60;
    const eq = [{ ts: first, equity: 0 }, ...p.equity];
    Plotly.react(el, [{ x: eq.map((e) => new Date(e.ts * 1000)), y: eq.map((e) => e.equity), type: "scatter", mode: "lines", line: { color: C.signal, width: 2, shape: "hv" }, fill: "tozeroy", fillcolor: "rgba(61,47,168,.08)" }],
      plotLayout({ margin: { l: 60, r: 12, t: 8, b: 30 }, xaxis: { gridcolor: "#EEF1F4", type: "date", tickformat: "%d %b %H:%M" }, yaxis: { gridcolor: "#EEF1F4", tickprefix: "₹", zeroline: true, zerolinecolor: C.rule } }), plotCfg);
  } else el.innerHTML = `<p class="muted" style="padding:16px">Your running P&amp;L appears here.</p>`;
}

/* ---------------- page: Connect / live settings */
async function pageConnect() {
  stopPageTimer();
  const [info, s] = await Promise.all([api("/api/live/info"), api("/api/settings")]);
  const q = (v) => (v == null ? "" : esc(v));
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  main.innerHTML = `<h1 class="page-title">Live setup</h1>
    <p class="lede">Connect Upstox for live data, add free AI for explanations, and choose how you get alerts.</p>
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

    <div class="panel" style="margin-top:18px"><h2 style="margin-top:0">4. AI explanations <span class="muted small">optional</span></h2>
      <p class="muted" style="margin-top:0">Adds a written market pulse every 15 minutes and a "Why?" answer on any alert. Google Gemini has a free tier: get a key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>. Only prices and public headlines are sent.</p>
      <div class="form-grid">
        <label class="field">Provider<select id="s-aip"><option value="gemini" ${s["ai.provider"] !== "claude" ? "selected" : ""}>Google Gemini (free tier)</option><option value="claude" ${s["ai.provider"] === "claude" ? "selected" : ""}>Claude (paid, about ₹300–600/month)</option></select></label>
        <label class="field">API key<input type="password" id="s-aik" value="${q(s["ai.api_key"])}" autocomplete="off"></label>
        <label class="field">Explain automatically<select id="s-aisev">${[[2, "Important and act-now alerts"], [3, "Only act-now alerts"], [9, "Never (only when I click Why?)"]].map(([v, l]) => `<option value="${v}" ${Number(s["ai.explain_min_severity"] || 3) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field">Market pulse every<select id="s-aipulse">${[5, 10, 15, 30, 60].map((v) => `<option value="${v}" ${Number(s["ai.pulse_every_min"] || 15) === v ? "selected" : ""}>${v} minutes</option>`).join("")}</select></label>
      </div></div>

    <div class="panel" style="margin-top:18px"><h2 style="margin-top:0">5. Paper trading</h2><div class="form-grid">
      <label class="check"><input type="checkbox" id="s-pauto" ${s["live.paper.auto"] !== false ? "checked" : ""}><span>Auto paper-trade strong alerts<small>Buys the suggested option on important breakouts, surges, OI buildups and unusual option buying.</small></span></label>
      <input type="hidden" id="s-plots" value="${q(s["live.paper.lots"] ?? 1)}">
      <label class="field">Target<select id="s-ptgt">${[20, 30, 40, 50, 75, 100].map((v) => `<option value="${v}" ${Number(s["live.paper.target_pct"] || 30) === v ? "selected" : ""}>+${v}% on premium</option>`).join("")}</select></label>
      <label class="field">Capital for sizing (₹)<input type="text" id="s-pcap" value="${q(s["live.paper.capital"] ?? 200000)}" inputmode="numeric"><small>Lots are sized so a stop-loss costs about the risk % below.</small></label>
      <label class="field">Risk per trade<select id="s-prisk">${[0.5, 1, 1.5, 2].map((v) => `<option value="${v}" ${Number(s["live.paper.risk_pct"] ?? 1) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select></label>
      <label class="field">Stop trading for the day after losing<select id="s-pdl">${[2, 3, 4, 5].map((v) => `<option value="${v}" ${Number(s["live.paper.max_daily_loss_pct"] ?? 3) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select></label>
      <label class="field">Max trades per day<select id="s-pmt">${[5, 8, 12, 20].map((v) => `<option ${Number(s["live.paper.max_trades_day"] ?? 12) === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field">Stop loss<select id="s-pstop">${[10, 15, 20, 25, 30, 40].map((v) => `<option value="${v}" ${Number(s["live.paper.stop_pct"] || 20) === v ? "selected" : ""}>−${v}% on premium</option>`).join("")}</select></label>
    </div></div>
    <div class="btn-row" style="margin-top:18px"><button class="btn primary" id="all-save" type="button">Save all</button></div>`;
  const collect = () => ({
    "live.upstox_api_key": $("#s-ukey").value.trim(), "live.upstox_api_secret": $("#s-usec").value.trim(),
    "live.auto_start": $("#s-auto").checked, "live.source": $("#s-src").value, "live.news": $("#s-news").checked,
    "live.sim_speed": Number($("#s-speed").value), "live.telegram_min_severity": Number($("#s-tsev").value),
    "ai.provider": $("#s-aip").value, "ai.api_key": $("#s-aik").value.trim(), "ai.explain_min_severity": Number($("#s-aisev").value),
    "ai.pulse_every_min": Number($("#s-aipulse").value), "live.paper.auto": $("#s-pauto").checked, "live.paper.lots": Number($("#s-plots").value),
    "live.paper.target_pct": Number($("#s-ptgt").value), "live.paper.stop_pct": Number($("#s-pstop").value),
    "live.paper.capital": Number(String($("#s-pcap").value).replace(/[^0-9.]/g, "")) || 200000, "live.paper.risk_pct": Number($("#s-prisk").value),
    "live.paper.max_daily_loss_pct": Number($("#s-pdl").value), "live.paper.max_trades_day": Number($("#s-pmt").value),
    "live.filings": $("#s-fil").checked, "live.briefs": $("#s-brief").checked,
  });
  const save = async () => {
    LS.set("notifySev", Number($("#s-nsev").value)); LS.set("sound", $("#s-sound").checked);
    try { await post("/api/settings", collect()); toast("Saved"); return true; } catch (e) { toast(e.message); return false; }
  };
  $("#all-save").onclick = save;
  $("#up-save").onclick = async () => { if (await save()) pageConnect(); };
  $("#copy-redir").onclick = async () => { try { await navigator.clipboard.writeText(info.redirect_uri); toast("Copied"); } catch (_) { toast(info.redirect_uri); } };
  if ($("#up-logout")) $("#up-logout").onclick = async () => { await post("/api/live/logout", {}); toast("Logged out"); pageConnect(); };
  $("#np-btn").onclick = async () => { const r = await Notification.requestPermission(); toast(r === "granted" ? "Pop-up alerts on" : "Not allowed"); pageConnect(); };
  ["#s-nsev", "#s-sound"].forEach((id) => $(id).addEventListener("change", () => { LS.set("notifySev", Number($("#s-nsev").value)); LS.set("sound", $("#s-sound").checked); }));
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
async function pageScorecard() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const sc = await api(`/api/live/scorecard${params.get("src") ? `?source=${params.get("src")}` : ""}`);
  const m = sc.model || {};
  const rows = sc.rows.map((r) => `<tr><td><b>${esc(KIND[r.kind] || r.kind)}</b> <span class="muted small">${r.is_index ? "indices" : "stocks"}</span></td><td class="r">${r.n}</td>
    <td class="r"><b class="${r.hit_rate >= 50 ? "up" : r.hit_rate < 35 ? "down" : ""}">${r.hit_rate ?? "–"}%</b><div class="meter thin"><i class="${r.hit_rate >= 50 ? "on" : ""}" style="width:${r.hit_rate || 0}%"></i></div></td>
    <td class="r">${r.recent_rate != null ? r.recent_rate + "%" : "–"} <small class="muted">last ${r.recent_n}</small></td>
    <td class="r">${fpc(r.avg_o15, 1)}</td><td class="r">${fpc(r.avg_o60, 1)}</td><td class="r">${fpc(r.avg_r15, 2)}</td>
    <td>${r.n >= 30 && r.hit_rate < 35 ? `<span class="pill p-down">Demoted</span>` : r.n < 10 ? `<span class="muted small">too few yet</span>` : r.hit_rate >= 50 ? `<span class="pill p-up">Reliable</span>` : ""}</td></tr>`).join("");
  const recent = sc.recent.slice(0, 80).map((o) => `<tr><td>${o.day.slice(5)} ${hm(o.ts)}</td><td>${arrow(o.direction)} ${esc(o.title || o.sym)}</td>
    <td>${o.result ? `<span class="pill ${o.result === "hit" ? "p-up" : o.result === "miss" ? "p-down" : ""}">${o.result === "hit" ? "Worked" : o.result === "miss" ? "Failed" : "Flat"}</span>` : `<span class="muted small">tracking…</span>`}</td>
    <td class="r">${fpc(o.r5, 2)}</td><td class="r">${fpc(o.r15, 2)}</td><td class="r">${fpc(o.r60, 2)}</td><td class="r">${o.o0 ? fpc(o.omax, 0) + " / " + fpc(o.omin, 0) : "–"}</td></tr>`).join("");
  main.innerHTML = `<h1 class="page-title">Signal scorecard</h1>
    <p class="lede">Every alert is followed for 60 minutes and graded. This is how you know which signals deserve real money. ${sc.source === "sim" ? "<b>Showing simulator results (random market, not evidence).</b>" : ""}
      <a href="#/scorecard?src=${sc.source === "sim" ? "upstox" : "sim"}">Show ${sc.source === "sim" ? "real market" : "simulator"} results</a></p>
    <div class="kpis"><div class="kpi"><b>${sc.graded}</b><span>Alerts graded (recent)</span></div>
      <div class="kpi"><b>${sc.overall != null ? sc.overall + "%" : "–"}</b><span>Worked overall</span></div>
      <div class="kpi"><b>${m.trained ? `${m.auc}` : `${m.n || 0}/${m.need || 120}`}</b><span>${m.trained ? `Learning model accuracy (AUC; 0.5 = coin toss), ${m.n} alerts` : "Graded alerts needed before the learning model switches on"}</span></div>
      <div class="kpi"><b>${sc.recordings_mb} MB</b><span>Market data recorded on this Mac</span></div></div>
    <p class="note">${esc(sc.rule)} Alert types that work under 35% of the time (30+ cases) are demoted automatically: no Telegram, no auto paper trade.</p>
    <h2>By alert type</h2>
    <div class="table-wrap"><table><thead><tr><th>Alert</th><th class="r">Graded</th><th class="r">Worked</th><th class="r">Recently</th><th class="r">Option 15 min</th><th class="r">Option 60 min</th><th class="r" title="Price move in the alert's favour">Price 15 min</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="muted">Nothing graded yet. Run the scanner for a session.</td></tr>`}</tbody></table></div>
    <h2>Latest graded alerts</h2>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Alert</th><th>Result</th><th class="r" title="Price move in the alert's favour">In favour 5m</th><th class="r">15m</th><th class="r">60m</th><th class="r">Option best / worst</th></tr></thead><tbody>${recent || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
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
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Briefs</h1><p class="lede" style="margin-bottom:0">Your morning plan and evening review, written from the data. Add an AI key in Live setup for a written analysis.</p></div>
      ${b.days.length ? `<label class="field inline">Day <select id="br-day">${b.days.map((d) => `<option ${d === b.day ? "selected" : ""}>${d}</option>`).join("")}</select></label>` : ""}</div>
    <div class="split" style="margin-top:18px">${card(b.pre, "🌅 Pre-market brief", "pre")}${card(b.post, "🌇 Post-market review", "post")}</div>`;
  if ($("#br-day")) $("#br-day").onchange = (e) => { location.hash = `#/briefs?day=${e.target.value}`; };
  main.querySelectorAll("[data-mk]").forEach((btn) => btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "Writing…";
    try { await post(`/api/live/briefs/${btn.dataset.mk}`, {}); pageBriefs(); } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Make now"; }
  });
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
    <p class="lede">Institution-style intraday strategies on Nifty and Bank Nifty: hedged option selling on calm days, debit spreads on trending days. Every strategy is tested on years of real 1-minute option prices, and only out-of-sample results count.</p>
    ${st.synthetic ? `<div class="banner">⚠️ You are looking at a <b>demo on synthetic (fake) data</b>. It shows how the desk works, not whether it makes money. Download real history below (Upstox Plus).</div>` : ""}
    <div id="desk-live"></div>
    <div id="desk-edge"></div>
    <div id="desk-eod"></div>
    ${S ? deskResults(res) : `<div class="empty" style="max-width:none"><h2>No backtest yet</h2><p>Log in to Upstox (Plus plan), download history, then run the backtest. Or see a quick demo on synthetic data first.</p>
      <div class="btn-row"><button class="btn" id="lab-demo">See a demo (synthetic data)</button></div></div>`}
    <h2>Data and settings</h2>
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
        <details style="margin-top:12px"><summary class="muted small">Or use Dhan instead (needs a Dhan account)</summary>
        <div class="form-grid" style="margin-top:10px">
          <label class="field">Dhan client ID<input type="text" id="l-cid" value="${q(settings["lab.dhan_client_id"])}" autocomplete="off"></label>
          <label class="field">Access token<input type="password" id="l-tok" value="${q(settings["lab.dhan_token"])}" autocomplete="off"><small>web.dhan.co → Profile → Access DhanHQ APIs (Data APIs ₹499 / 30 days).</small></label>
        </div>
        <div class="btn-row" style="margin-top:10px"><button class="btn" id="l-save">Save</button><button class="btn" id="l-test">Test Dhan</button>
          <button class="btn" id="l-dl-dhan" ${st.dhan ? "" : "disabled"}>Download from Dhan</button></div></details>
        <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Index</th><th class="r">Months</th><th>Range</th><th class="r">Size</th></tr></thead><tbody>${dataRows}</tbody></table></div>
        <div class="btn-row" style="margin-top:12px"><button class="btn primary" id="l-bt" ${Object.values(st.data).some((v) => v.months) ? "" : "disabled"}>Run backtest</button>
          ${!st.synthetic && !Object.values(st.data).some((v) => v.months) ? `<button class="btn" id="lab-demo2">Demo on synthetic data</button>` : ""}</div>
        <p class="note">Re-run the backtest once a month (download first): the plan picks strategies on the latest 6 months.</p></div>
      <div class="panel"><h3>2. Money and risk</h3><div class="form-grid">
        <label class="field">Capital (₹)<input type="text" id="l-cap" value="${q(settings["lab.capital"] ?? 500000)}" inputmode="numeric"></label>
        <label class="field">Risk per trade<select id="l-risk">${[0.5, 1, 1.5, 2].map((v) => `<option value="${v}" ${Number(settings["lab.risk_pct"] ?? 1.5) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select><small>Loss if the stops hit, plus a 50% buffer.</small></label>
        <label class="field">Daily loss limit<select id="l-stop">${[1, 1.5, 2, 3].map((v) => `<option value="${v}" ${Number(settings["lab.daily_stop_pct"] ?? 2) === v ? "selected" : ""}>${v}% of capital</option>`).join("")}</select><small>Everything closes for the day at this loss.</small></label>
        <label class="check"><input type="checkbox" id="l-auto" ${settings["lab.auto_trade"] !== false ? "checked" : ""}><span>Paper-trade the plan automatically<small>On live or simulator prices, every market day.</small></span></label>
      </div><div class="btn-row" style="margin-top:14px"><button class="btn" id="l-save2">Save</button></div>
      <p class="note">Changing capital or risk: run the backtest again to re-size.</p></div>
    </div>`;
  const save = async () => {
    try {
      await post("/api/settings", { "lab.dhan_client_id": $("#l-cid").value.trim(), "lab.dhan_token": $("#l-tok").value.trim(),
        "lab.years": Number($("#l-years").value), "lab.capital": Number(String($("#l-cap").value).replace(/[^0-9.]/g, "")) || 500000,
        "lab.risk_pct": Number($("#l-risk").value), "lab.daily_stop_pct": Number($("#l-stop").value), "lab.auto_trade": $("#l-auto").checked });
      toast("Saved"); return true;
    } catch (e) { toast(e.message); return false; }
  };
  const job = async (path, body = {}) => { try { await post(path, body); state.jobSince = 0; $("#job-log").textContent = ""; watchJob(); } catch (e) { toast(e.message); } };
  $("#l-save").onclick = async () => { if (await save()) pageDesk(); };
  $("#l-save2").onclick = save;
  $("#l-test").onclick = async () => { if (!(await save())) return; try { const r = await post("/api/lab/dhan/test", {}); toast(`Dhan connected (${r.profile.dhanClientId || "ok"}${r.profile.dataPlan ? ", data plan: " + r.profile.dataPlan : ""})`); pageDesk(); } catch (e) { toast(e.message); } };
  $("#l-dl").onclick = async () => { if (await save()) job("/api/lab/download", { source: "upstox" }); };
  $("#l-dl-dhan").onclick = async () => { if (await save()) job("/api/lab/download", { source: "dhan" }); };
  $("#l-uptest").onclick = async () => {
    try { const r = await post("/api/lab/upstox/test", {}); const e = r.expiries;
      toast(`Upstox Plus OK: Nifty ${e.NIFTY.n} expiries (from ${e.NIFTY.first}), Bank Nifty ${e.BANKNIFTY.n} (from ${e.BANKNIFTY.first})`);
    } catch (err) { toast(err.message); } };
  $("#l-bt").onclick = async () => { if (await save()) job("/api/lab/backtest"); };
  ["#lab-demo", "#lab-demo2"].forEach((id) => { if ($(id)) $(id).onclick = () => job("/api/lab/demo"); });
  if (res) drawDeskCharts(res);
  api("/api/lab/edge").then((e) => { const el = $("#desk-edge"); if (el) el.innerHTML = edgeCard(e); }).catch(() => {});
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
  const open = d.today.map((t) => `<tr><td><b>${esc(SNAME[t.family] || t.family)}</b> ${esc(t.sym)}<br><small class="muted">${t.lots} lot${t.lots > 1 ? "s" : ""} · max loss ₹${fk(Math.round(t.max_loss))} · margin ~₹${fk(Math.round(t.margin))}</small></td>
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
const CH = { chart: null, sym: null, tf: 5, days: 3, series: {}, lines: [], markers: null, sig: "", data: null };
const CH_TOGGLES = [["vwap", "VWAP + bands"], ["ema", "EMA 9/21/50"], ["st", "Supertrend"], ["prev", "Yesterday H/L/C"], ["cpr", "CPR + pivots"],
  ["or", "Opening range"], ["oi", "OI walls + max pain"], ["gamma", "Gamma levels"], ["em", "Expected move"], ["marks", "Alerts + trades"],
  ["vol", "Volume"], ["rsi", "RSI"], ["macd", "MACD"], ["scen", "Scenarios: up / sideways / down"], ["pat", "Chart patterns"],
  ["swing", "Swing labels (HH / HL / LH / LL)"]];
const PAT_SHORT = { double_top: "DT", double_bottom: "DB", head_shoulders: "H&S", inv_hs: "iH&S", triangle: "Tri", flag: "Flag",
  breakout_retest: "Retest", rsi_divergence: "Div", candle_at_level: "Candle" };
function chOn(k) { return LS.get("ch_" + k, !["macd", "em"].includes(k)); }

async function pageChart() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  CH.sym = (params.get("s") || LS.get("lastSym", "NIFTY")).toUpperCase();
  CH.tf = Number(params.get("tf") || LS.get("ch_tf", 5));
  CH.days = Number(LS.get("ch_days", 3));
  LS.set("lastSym", CH.sym);
  const IDX = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
  const stocks = (L.scanner && L.scanner.length ? L.scanner : await api("/api/live/scanner").catch(() => [])).map((m) => m.sym).filter((x) => !IDX.includes(x)).sort();
  if (!IDX.includes(CH.sym) && !stocks.includes(CH.sym)) stocks.unshift(CH.sym);
  main.classList.add("wide");
  main.innerHTML = `<div class="ch-top">
      <div class="ch-sym"><select id="ch-sym" aria-label="Symbol"><optgroup label="Indices">${IDX.map((x) => `<option value="${x}" ${x === CH.sym ? "selected" : ""}>${x}</option>`).join("")}</optgroup>
        <optgroup label="F&amp;O stocks">${stocks.map((x) => `<option value="${esc(x)}" ${x === CH.sym ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup></select>
        <span id="ch-price" class="ch-price"></span></div>
      <div class="seg" role="group" aria-label="Timeframe">${[1, 3, 5, 15].map((t) => `<button data-tf="${t}" aria-pressed="${CH.tf === t}">${t}m</button>`).join("")}</div>
      <div class="seg" role="group" aria-label="Days">${[1, 3, 5].map((d) => `<button data-days="${d}" aria-pressed="${CH.days === d}">${d}D</button>`).join("")}</div>
      <details class="ch-ind"><summary class="btn">Indicators &amp; levels</summary><div class="ch-menu">${CH_TOGGLES.map(([k, l]) => `<label class="check"><input type="checkbox" data-tog="${k}" ${chOn(k) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></details>
      <a class="btn" href="#/sym?s=${encodeURIComponent(CH.sym)}">Option chain</a>
    </div>
    <div class="ch-wrap"><div id="ch-chart" class="ch-chart"></div>
      <aside class="ch-side"><div class="panel tight"><h3>Chart read</h3><div id="ch-read"><p class="muted">Loading…</p></div>
        <button class="link-btn" id="ch-ai">AI read this chart</button><div id="ch-ai-out"></div></div>
        <div class="panel tight"><h3>Patterns <span class="muted small">on this chart</span></h3><div id="ch-pats"></div></div>
        <div class="panel tight" id="ch-scen-wrap"><h3>Scenarios <span class="muted small">next 5 / 15 / 60 min</span></h3><div id="ch-scen"><p class="muted small">Nifty and Bank Nifty.</p></div></div>
        <div class="panel tight"><h3>Setups today</h3><div id="ch-setups"></div></div>
        <div class="panel tight"><h3>Key levels</h3><div id="ch-levels"></div></div></aside></div>
    <p class="note">Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a> Lightweight Charts. ${""}Index VWAP counts each bar equally (indices have no volume).</p>`;
  $("#ch-sym").onchange = (e) => { location.hash = `#/chart?s=${encodeURIComponent(e.target.value)}&tf=${CH.tf}`; };
  main.querySelectorAll("[data-tf]").forEach((b) => b.onclick = () => { LS.set("ch_tf", Number(b.dataset.tf)); location.hash = `#/chart?s=${encodeURIComponent(CH.sym)}&tf=${b.dataset.tf}`; });
  main.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { LS.set("ch_days", Number(b.dataset.days)); pageChart(); });
  main.querySelectorAll("[data-tog]").forEach((c) => c.onchange = () => { LS.set("ch_" + c.dataset.tog, c.checked); buildChart(CH.hasVol); drawChart(CH.data, true); });
  $("#ch-ai").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Reading…";
    try { const r = await post(`/api/live/chart/${encodeURIComponent(CH.sym)}/ai`, {}); $("#ch-ai-out").innerHTML = `<div class="ai-text">${fmtAI(r.text)}</div>`; } catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "AI read this chart";
  };
  const tick = async (first) => {
    if (state.page !== "chart") return;
    try {
      CH.data = await api(`/api/live/chart/${encodeURIComponent(CH.sym)}?tf=${CH.tf}&days=${CH.days}`);
      if (first || !CH.chart || CH.hasVol !== CH.data.has_volume) { CH.hasVol = CH.data.has_volume; buildChart(CH.hasVol); first = true; }
      drawChart(CH.data, first);
    }
    catch (err) { if (first) $("#ch-chart").innerHTML = `<div class="empty" style="margin:20px">${esc(err.message)}<br><br><a class="btn primary" href="#/live">Go to Live</a></div>`; }
    L.pageTimer = setTimeout(() => tick(false), 2000);
  };
  tick(true);
}

function buildChart(hasVolume = true) {
  const el = $("#ch-chart"); if (!el || !window.LightweightCharts) return;
  if (CH.chart) { CH.chart.remove(); CH.chart = null; }
  const LW = window.LightweightCharts;
  const chart = LW.createChart(el, { autoSize: true, layout: { background: { color: "#ffffff" }, textColor: "#16212C", fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif", fontSize: 12, panes: { separatorColor: "#D5DCE3" } },
    grid: { vertLines: { color: "#F1F4F7" }, horzLines: { color: "#F1F4F7" } }, rightPriceScale: { borderColor: "#D5DCE3" },
    timeScale: { borderColor: "#D5DCE3", timeVisible: true, secondsVisible: false, rightOffset: 8 }, crosshair: { mode: 0 } });
  const S = {};
  S.candle = chart.addSeries(LW.CandlestickSeries, { upColor: "#1E8A5A", downColor: "#C23B32", borderVisible: false, wickUpColor: "#1E8A5A", wickDownColor: "#C23B32" });
  const ln = (color, width = 1, style = 0, pane = 0, extra = {}) => chart.addSeries(LW.LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra }, pane);
  if (chOn("vwap")) { S.vwap = ln("#3D2FA8", 2); S.u1 = ln("rgba(61,47,168,.45)", 1, 2); S.d1 = ln("rgba(61,47,168,.45)", 1, 2); S.u2 = ln("rgba(61,47,168,.3)", 1, 1); S.d2 = ln("rgba(61,47,168,.3)", 1, 1); }
  if (chOn("ema")) { S.ema9 = ln("#E07B00", 1); S.ema21 = ln("#0A84C1", 1); S.ema50 = ln("#5E6B78", 1, 2); }
  if (chOn("st")) { S.st_up = ln("#1E8A5A", 2, 0, 0, { lineType: 1 }); S.st_dn = ln("#C23B32", 2, 0, 0, { lineType: 1 }); }
  if (chOn("scen")) {
    const sl = (color, style, w = 2) => chart.addSeries(LW.LineSeries, { color, lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false }, 0);
    S.sc_up = sl("rgba(30,138,90,.9)", 2); S.sc_side = sl("rgba(94,107,120,.9)", 2); S.sc_down = sl("rgba(194,59,50,.9)", 2);
    S.sc_lo = sl("rgba(94,107,120,.45)", 1, 1); S.sc_hi = sl("rgba(94,107,120,.45)", 1, 1);
    S.sc_lo.applyOptions({ lastValueVisible: false }); S.sc_hi.applyOptions({ lastValueVisible: false });
  }
  let pane = 1;
  if (chOn("vol") && hasVolume) { S.vol = chart.addSeries(LW.HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, pane); S.volPane = pane++; }
  if (chOn("rsi")) { S.rsi = ln("#3D2FA8", 1, 0, pane, { lastValueVisible: true }); S.rsi.createPriceLine({ price: 70, color: "#C23B32", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }); S.rsi.createPriceLine({ price: 30, color: "#1E8A5A", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }); S.rsiPane = pane++; }
  if (chOn("macd")) { S.macd_hist = chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane); S.macd = ln("#0A84C1", 1, 0, pane); S.macd_sig = ln("#E07B00", 1, 0, pane); S.macdPane = pane++; }
  const panes = chart.panes();
  panes.forEach((p, i) => p.setStretchFactor(i === 0 ? 4 : 1));
  S.markers = LW.createSeriesMarkers(S.candle, []);
  const lg = document.createElement("div"); lg.className = "ch-legend"; el.appendChild(lg);
  chart.subscribeCrosshairMove((p) => {
    const c = p && p.time ? p.seriesData.get(S.candle) : null;
    if (!c) { lg.innerHTML = ""; return; }
    const v = (k) => { const x = S[k] && p.seriesData.get(S[k]); return x && x.value != null ? x.value : null; };
    const bits = [["VWAP", v("vwap")], ["EMA9", v("ema9")], ["EMA21", v("ema21")], ["RSI", v("rsi")]].filter((x) => x[1] != null)
      .map(([n, x]) => `<span>${n} <b>${fx(x)}</b></span>`).join("");
    const t = new Date((p.time - 19800) * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
    const ev = ((CH.data && CH.data.markers) || []).filter((m) => m.time === p.time && m.title).map((m) => `<div class="lg-ev" style="color:${m.color}">${esc(m.title)}</div>`).join("");
    lg.innerHTML = `<div><span>${t}</span><span>O <b>${fx(c.open)}</b></span><span>H <b>${fx(c.high)}</b></span><span>L <b>${fx(c.low)}</b></span><span>C <b class="${c.close >= c.open ? "up" : "down"}">${fx(c.close)}</b></span>${bits}</div>${ev}`;
  });
  CH.chart = chart; CH.series = S; CH.lines = []; CH.sig = ""; CH.patSeries = []; CH.psig = null;
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

function drawChart(d, first) {
  if (!d || !CH.chart) return;
  const S = CH.series;
  S.candle.setData(d.candles);
  for (const k of ["vwap", "u1", "d1", "u2", "d2", "ema9", "ema21", "ema50", "st_up", "st_dn", "rsi", "macd", "macd_sig", "macd_hist"]) if (S[k]) S[k].setData(d.series[k] || []);
  if (S.vol) S.vol.setData(d.has_volume ? d.volume : []);
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
  const want = d.levels.filter((l) => chOn(groups[l.group] || l.group));
  const sig = JSON.stringify(want.map((l) => [l.name, l.price]));
  if (sig !== CH.sig) {
    CH.lines.forEach((pl) => S.candle.removePriceLine(pl));
    CH.lines = want.map((l) => S.candle.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.style, axisLabelVisible: true, title: l.name }));
    CH.sig = sig;
  }
  const mk = chOn("marks") ? d.markers.map(({ time, position, color, shape, text }) => ({ time, position, color, shape, text })) : [];
  if (chOn("pat")) (d.patterns || []).forEach((p) => mk.push({ time: p.time, position: p.direction === "up" ? "belowBar" : "aboveBar",
    color: p.grade === "edge" ? "#1E8A5A" : p.grade === "losing" ? "#9AA5B1" : "#3D2FA8", shape: p.direction === "up" ? "arrowUp" : "arrowDown", text: PAT_SHORT[p.pattern] || "" }));
  if (chOn("swing")) (d.swings || []).forEach((w) => mk.push({ time: w.time, position: w.type === "H" ? "aboveBar" : "belowBar", color: "#8A94A0", shape: "circle", size: 0.3, text: w.label }));
  mk.sort((a, b) => a.time - b.time);
  S.markers.setMarkers(mk);
  // pattern lines (rebuilt only when the patterns change)
  const psig = chOn("pat") ? JSON.stringify((d.patterns || []).map((p) => [p.pattern, p.time])) : "";
  if (psig !== CH.psig) {
    (CH.patSeries || []).forEach((x) => { try { CH.chart.removeSeries(x); } catch (_) {} });
    CH.patSeries = [];
    if (chOn("pat")) (d.patterns || []).forEach((p) => p.lines.forEach((ln) => {
      const pts = []; ln.forEach((q) => { if (!pts.length || q.time > pts[pts.length - 1].time) pts.push(q); });
      if (pts.length < 2) return;
      const ser = CH.chart.addSeries(window.LightweightCharts.LineSeries, { color: p.direction === "up" ? "rgba(30,138,90,.85)" : "rgba(194,59,50,.85)", lineWidth: 2, lineStyle: 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
      ser.setData(pts); CH.patSeries.push(ser);
    }));
    CH.psig = psig;
  }
  if (first) {                       // show the last ~120 bars (at least 40 slots so a young session isn't stretched)
    const n = d.candles.length;
    const ahead = d.scenario && d.scenario.ready && chOn("scen") ? Math.ceil((d.scenario.horizon_max || 60) / d.tf) + 3 : 6;
    if (n) CH.chart.timeScale().setVisibleLogicalRange({ from: n - Math.max(40, Math.min(n, 120)), to: n + ahead });
  }
  $("#ch-price").innerHTML = `<b>${fx(d.spot)}</b> ${fpc(d.chg_pct)} ${d.regime && d.regime.code !== "opening" ? `<span class="pill p-sig">${esc(d.regime.label)}</span>` : ""}`;
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
    L.pageTimer = setTimeout(() => tick(false), 15000);
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
  const spark = (xs) => {
    if (!xs || xs.length < 2) return "";
    const lo = Math.min(...xs), hi = Math.max(...xs), W = 60, H = 18;
    const pts = xs.map((x, i) => `${(i / (xs.length - 1)) * W},${H - ((x - lo) / ((hi - lo) || 1)) * H}`).join(" ");
    return `<svg width="${W}" height="${H}" class="spark"><polyline points="${pts}" fill="none" stroke="${xs[xs.length - 1] >= xs[0] ? "#1E8A5A" : "#C23B32"}" stroke-width="1.5"/></svg>`;
  };
  const prevLast = L.worldLast || {};
  const nowS = w.server_ts || Date.now() / 1000;
  const age = (ts) => { if (!ts) return ""; const m = Math.round((nowS - ts) / 60); return m < 1 ? "now" : m < 60 ? m + "m" : m < 1440 ? Math.round(m / 60) + "h" : "closed"; };
  const flash = (r) => { const o = prevLast[r.ticker]; return o == null || o === r.last ? "" : r.last > o ? "flash-up" : "flash-down"; };
  L.worldLast = Object.fromEntries(w.groups.flatMap((g) => g.rows.map((r) => [r.ticker, r.last])));
  const liveAge = w.updated && w.updated.live ? Math.round(nowS - w.updated.live) : null;
  const groups = w.groups.map((g) => `<div class="panel tight"><h3>${esc(g.group)}</h3><table class="mini"><thead><tr><th>Market</th><th class="r">Last</th><th class="r">Day</th><th></th><th class="r" title="Past correlation with Nifty (next day for US/Europe, same day for Asia)">Corr</th><th class="r" title="What this move has meant for Nifty in the past">Nifty impact</th></tr></thead>
    <tbody>${g.rows.map((r) => `<tr><td>${esc(r.name)}${r.live_ts ? ` <small class="muted" title="Time since the last price">${age(r.live_ts)}</small>` : ""}</td><td class="r ${flash(r)}">${fx(r.last)}</td><td class="r">${fpc(r.chg_pct)}</td><td>${spark(r.spark_day && r.spark_day.length > 2 ? r.spark_day : r.spark)}</td><td class="r muted">${r.corr != null ? r.corr.toFixed(2) : "–"}</td><td class="r">${imp(r.nifty_impact)}</td></tr>`).join("")}</tbody></table></div>`).join("");
  const now = Date.now();
  const cal = w.calendar.map((e) => { const t = new Date(e.time); const past = t.getTime() < now - 30 * 60000;
    return `<tr class="${past ? "past" : ""}"><td>${t.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })}</td><td><b>${esc(e.country)}</b></td><td>${esc(e.title)}</td>
      <td><span class="pill ${e.impact === "High" ? "p-down" : "p-amber"}">${esc(e.impact)}</span></td><td class="r muted">${esc(e.forecast || "")}</td><td class="r muted">${esc(e.previous || "")}</td></tr>`; }).join("");
  const res = {};
  w.results.forEach((r) => { (res[r.date] = res[r.date] || []).push(r.sym); });
  const news = w.news.filter((n) => LS.get("w_imp", "all") === "all" || n.impact === "high" || (LS.get("w_imp", "all") === "medium" && n.impact === "medium")).slice(0, 50);
  main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">World</h1><p class="lede" style="margin-bottom:0">Global markets, currencies, commodities, rates, crypto, Indian ADRs, economic events, results and global news: what it means for Nifty.</p></div>
      <div style="text-align:right"><button class="btn" id="w-refresh">Refresh now</button>
        <p class="small muted" style="margin:6px 0 0">${liveAge != null ? `<span class="dot-live"></span> Prices ${liveAge < 90 ? "updated " + liveAge + "s ago" : "updated " + Math.round(liveAge / 60) + " min ago"} · auto every minute` : "Loading prices…"}</p></div></div>
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

/* ---------------- register */
Object.assign(PAGES, { live: pageLive, scanner: pageScanner, sym: pageSym, alerts: pageAlerts, paper: pagePaper, connect: pageConnect,
  context: pageContext, scorecard: pageScorecard, briefs: pageBriefs, desk: pageDesk, chart: pageChart, world: pageWorld });
window.addEventListener("hashchange", () => { stopPageTimer(); if (!location.hash.startsWith("#/chart") && CH.chart) { CH.chart.remove(); CH.chart = null; } main.classList.toggle("wide", location.hash.startsWith("#/chart")); });
pollSnapshot();
