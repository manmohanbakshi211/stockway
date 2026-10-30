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

/* ---------------- polling + notifications */
function livePage() { return ["live", "chart", "world", "desk", "scanner", "sym", "alerts", "paper", "connect", "context", "scorecard", "briefs", "insiders"].includes(state.page); }

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
  const track = tr && tr.decided ? `<span class="track ${tr.hit_rate >= 60 ? "good" : tr.hit_rate < 45 ? "bad" : ""}" title="Option +20% before −20% within an hour, among ${tr.decided} decided of ${tr.n} alerts">Worked ${tr.hit_rate}% of ${tr.decided} decided</span>` :
    tr && tr.n && tr.decided === undefined ? `<span class="track muted">Worked ${tr.hit_rate}% of ${tr.n} times (old count)</span>` :
    (a.direction === "up" || a.direction === "down") ? `<span class="track muted">New signal type: no track record yet</span>` : "";
  const ev = (d.evidence || []).map((e) => `<span class="ev ${e.agree ? "yes" : "no"}">${e.agree ? "✓" : "✗"} ${esc(e.label)}</span>`).join("");
  const plan = lv.stop ? `<div class="plan"><span>Target <b>${fx(lv.target)}</b> <small>(${lv.target_pct}%)</small></span><span>Wrong ${a.direction === "up" ? "below" : "above"} <b>${fx(lv.stop)}</b> <small>(${lv.stop_pct}%)</small></span>${lv.rr ? `<span>Reward:risk <b>${lv.rr}</b></span>` : ""}</div>` : "";
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
  const fits = d.fits_small ? `<span class="pill" title="One lot costs ₹10,000 or less: tradable from a ₹40K account">₹40K ✓</span>` : "";
  return `<li class="alert sev${a.severity}" data-aid="${a.id}">
    <div class="al-time">${hms(a.ts)}<span class="sevtag s${a.severity}">${SEV[a.severity]}</span>${score}</div>
    <div class="al-body"><div class="al-title">${arrow(a.direction)} <a href="${a.sym === "GLOBAL" ? "#/world" : `#/chart?s=${encodeURIComponent(a.sym)}`}">${esc(a.title)}</a></div>
      <p>${esc(a.detail)}${link} <span class="muted small">${esc(KIND[a.kind] || a.kind)}</span> ${track}</p>${live ? `<p class="small" style="margin:2px 0 6px">${live}</p>` : ""}
      ${d.muted ? `<p class="muted small">⤓ ${esc(d.muted)}</p>` : ""}
      ${ev || ctxChips || conv || fits ? `<div class="evs">${conv}${fits}${ev}${ctxChips}</div>` : ""}${plan}${trade}${riskLine(d.risk)}
      ${a.ai ? `<div class="ai-text">${fmtAI(a.ai)}</div>` : ""}${extra}</div></li>`;
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

function extraIdx(x) {
  // Sensex (Upstox live) and GIFT Nifty (NSE IX near-month future) beside the main index cards
  if (!x || !x.length) return "";
  return `<div class="xcards">${x.map((c) => `<div class="xcard"><span>${esc(c.name)}</span>
      <b>${c.spot != null ? fx(c.spot) : "–"}</b> ${c.chg_pct != null ? `<em class="${c.chg_pct >= 0 ? "up" : "down"}">${c.chg_pct >= 0 ? "+" : ""}${c.chg_pct.toFixed(2)}%</em>` : ""}
      ${c.day_l && c.day_h ? `<small class="muted">${fx(c.day_l)} – ${fx(c.day_h)}</small>` : ""}<small class="muted">${esc(c.src || "")}${c.expiry ? ` · ${esc(c.expiry)}` : ""}</small></div>`).join("")}</div>`;
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
  if (!s.status.running && !s.indices.length) {
    body.innerHTML = extraIdx(s.extra) + watchStrip(s.watch) + (s.status.logged_in ? `<div class="empty" style="max-width:none"><h2>${esc(s.status.message || "Scanner is off")}</h2><p>It starts by itself at 9:00 AM on market days. Start now to scan immediately.</p><div class="btn-row"><button class="btn primary" data-live-start="upstox">Start live</button><button class="btn" data-live-start="sim">Run simulator</button></div></div>` : setupCard());
    bindLiveButtons(body); return;
  }
  const pulse = s.pulse || {};
  const pulseHTML = pulse.ai ? fmtAI(pulse.ai) + `<p class="muted small">AI pulse at ${hm(pulse.ts)}</p>` : (pulse.lines || []).map((l) => `<p>${esc(l)}</p>`).join("") || `<p class="muted">Building the picture…</p>`;
  const small = LS.get("only40k", false);
  const alerts = L.alerts.filter((a) => sevPass(a, L.sevFilter) && (!small || (a.data && a.data.fits_small) || !a.option)).slice(0, 60);
  const nFocus = L.alerts.filter(isFocus).length;
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
    ${extraIdx(s.extra)}
    <div class="icards">${s.indices.slice(0, 4).map(indexCard).join("")}</div>
    ${watchStrip(s.watch)}
    <div class="live-grid">
      <section>
        <div class="toolbar"><h2 style="margin:0">Alerts <span class="muted small">${nFocus} in focus · ${L.alerts.length} in all</span></h2>
          <div class="btn-row" style="margin:0"><label class="check small" title="Only ideas where one lot costs ₹10,000 or less"><input type="checkbox" id="only40k" ${small ? "checked" : ""}><span>₹40K only</span></label>
          <div class="seg" role="group" aria-label="Which alerts">${SEVSEG.map(([v, l]) => `<button data-sev="${v}" aria-pressed="${L.sevFilter === v}" ${v === 0 ? `title="Act-now alerts that are not demoted, at most one per symbol per hour"` : ""}>${l}</button>`).join("")}</div></div></div>
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
  const o4 = body.querySelector("#only40k"); if (o4) o4.onchange = (e) => { LS.set("only40k", e.target.checked); renderLive(true); };
  body.querySelectorAll("[data-sev]").forEach((b) => b.onclick = () => { L.sevFilter = Number(b.dataset.sev); LS.set("sevFilter2", L.sevFilter); renderLive(true); });
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
        <td class="r"><b>${fx(m.spot)}</b><br><small class="${(m.chg_pct || 0) >= 0 ? "up" : "down"}">${(m.chg_pct || 0) >= 0 ? "+" : ""}${(m.chg_pct || 0).toFixed(2)}%</small></td>
        <td>${scanRead(m.score)}</td><td class="small">${esc(scanWords(m)) || "<span class='muted'>quiet</span>"}</td>
        <td class="nw small"><a href="#/chart?s=${encodeURIComponent(m.sym)}">Chart</a> · <a href="#/sym?s=${encodeURIComponent(m.sym)}">Chain</a> · <a href="#" data-watch="${esc(m.sym)}">+ Watch</a></td></tr>`).join("")
    : rows.map((m) => `<tr class="click" data-sym="${esc(m.sym)}">${SCAN_COLS.map(([k, , fn, r]) => `<td class="${r ? "r" : ""}">${fn(m)}</td>`).join("")}</tr>`).join("")) ||
    `<tr><td colspan="${cols}" class="muted">${L.scanner.length ? "Nothing matches this filter right now." : "Start the live scanner to fill this table."}</td></tr>`;
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
      drawSym(d, first);
    } catch (e) {
      if (first) $("#sym-body").innerHTML = `<div class="empty"><h2>${esc(sym)}: option chain needs the live feed</h2><p>${esc(e.message)} The live scanner starts by itself at 9:00 AM on market days (or start it on the Live page). Meanwhile the chart shows the last session.</p><div class="btn-row"><a class="btn primary" href="#/chart?s=${encodeURIComponent(sym)}">Open last chart</a><a class="btn" href="#/live">Go to Live</a></div></div>`;
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
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const day = params.get("day") || "latest", sev = Number(params.get("sev") ?? 0), sym = params.get("sym") || "";
  const d = await api(`/api/live/alerts?day=${day}&min_sev=${Math.max(1, sev)}${sev === 0 ? "&focus=true" : ""}${sym ? `&sym=${encodeURIComponent(sym)}` : ""}`);
  const nAll = d.total ?? d.alerts.length;
  const byKind = {};
  d.alerts.forEach((a) => { byKind[a.kind] = (byKind[a.kind] || 0) + 1; });
  const go = (p) => { const q = new URLSearchParams({ day: d.day || "", sev, sym, ...p }); location.hash = `#/alerts?${q}`; };
  main.innerHTML = `<h1 class="page-title">Alerts</h1><p class="lede">Every alert, saved by day. ${d.day ? `${d.alerts.length}${sev === 0 ? ` in focus (of ${nAll})` : ""} on ${new Date(d.day + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}.` : "None yet: they appear here as the scanner finds them."}</p>
    <div class="toolbar"><div class="btn-row">
      <label class="field inline">Day <select id="al-day">${d.days.map((x) => `<option value="${x.day}" ${x.day === d.day ? "selected" : ""}>${x.day} (${x.n})</option>`).join("") || `<option>No alerts yet</option>`}</select></label>
      <div class="seg">${SEVSEG.map(([v, l]) => `<button data-s="${v}" aria-pressed="${sev === v}">${l}</button>`).join("")}</div>
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
    return `<tr><td>${sideTag(t)} <b>${esc(t.label)}</b> × ${t.lots}<br><small class="muted">${esc(KIND[t.kind] || t.kind)}${mode(t)}</small><br>${links(t)}</td><td>${stockNow(t)}</td><td>${hm(t.entry_ts)}</td><td class="r">${fx(t.entry)}</td><td class="r">${fx(t.last)} ${fpc(chg, 1)}</td>
      <td class="r">${fx(t.target)} / ${fx(t.stop)}</td><td class="r ${t.unreal >= 0 ? "up" : "down"}"><b>${rupees(t.unreal)}</b></td><td><button class="link-btn" data-close="${t.id}">Exit</button></td></tr>`;
  }).join("");
  const closed = p.closed.map((t) => `<tr><td>${sideTag(t)} <b>${esc(t.label)}</b><br><small class="muted">${esc(KIND[t.kind] || t.kind)}${mode(t)}</small><br>${links(t)}</td><td>${stockClosed(t)}</td><td>${t.day}<br><small class="muted">${hm(t.entry_ts)}–${hm(t.exit_ts)}</small></td><td class="r">${fx(t.entry)} → ${fx(t.exit)}</td><td>${esc(t.reason)}</td><td class="r muted">${rupees(t.costs)}</td><td class="r ${t.net >= 0 ? "up" : "down"}"><b>${rupees(t.net)}</b></td></tr>`).join("");
  const kinds = p.by_kind.map((b) => `<tr><td>${esc(KIND[b.kind] || b.kind)}</td><td class="r">${b.n}</td><td class="r">${b.win_rate}%</td><td class="r ${b.avg >= 0 ? "up" : "down"}">${rupees(b.avg)}</td><td class="r ${b.net >= 0 ? "up" : "down"}"><b>${rupees(b.net)}</b></td></tr>`).join("");
  const cls = (v) => v >= 0 ? "up" : "down";
  main.innerHTML = `<div class="toolbar"><h1 class="page-title" style="margin:0">Paper trading</h1>
      <div class="btn-row" style="margin:0">${p.open.length ? `<button class="btn danger" id="pp-closeall">Close all (${p.open.length})</button>` : ""}
        <button class="btn" id="pp-preset" title="${(A.capital || 0) <= 60000 ? "Back to ₹5 lakh, normal limits" : "₹40K: 1 lot, max 2 trades a day, the day stops after one loss, a good day is locked in at +₹1,200"}">${(A.capital || 0) <= 60000 ? "Normal account (₹5L)" : "Small account (₹40K)"}</button>
        <a class="btn" href="#/briefs">Day report</a></div></div>
    ${(A.capital || 0) <= 60000 ? `<div class="note">Small-account mode: 1 lot, 1 position at a time, max 2 trades a day, no new trade after a loss, and the day is locked once +₹${fk(s.profit_lock || 0)} is booked. Fewer, cleaner trades; it can't make every day green, but it keeps red days small.</div>` : ""}
    <div class="kpis kpi6 acct">
      <div class="kpi"><b>${rupees(A.equity)}</b><span>Account value now <small class="${cls(A.return_pct || 0)}">${A.return_pct >= 0 ? "+" : ""}${(A.return_pct || 0).toFixed(2)}%</small></span></div>
      <div class="kpi"><b class="${cls(A.today || 0)}">${rupees(A.today)}</b><span>Today: booked ${rupees(A.today_booked)} + open ${rupees(A.unrealised)}</span></div>
      <div class="kpi"><b>${rupees(A.free)}</b><span>Free to use</span></div>
      <div class="kpi"><b>${rupees(A.used)}</b><span>In open positions (${p.open.length})</span></div>
      <div class="kpi"><b class="${cls(A.realised || 0)}">${rupees(A.realised)}</b><span>All booked P&amp;L (${p.n} trades, ${p.win_rate != null ? p.win_rate + "% won" : "–"})</span></div>
      <div class="kpi"><b>${rupees(A.capital)}</b><span>Starting capital</span></div></div>
    <div class="panel tight agg ${p.aggressive ? "on" : ""}" style="margin-top:14px"><label class="check"><input type="checkbox" id="pp-agg" ${p.aggressive ? "checked" : ""}>
      <span><b>Aggressive auto trading</b> ${p.aggressive ? `<span class="pill p-amber">ON</span>` : ""}<small>Paper only. The system takes every up/down idea automatically (Important and Act-now), even when the risk check says BLOCK; up to ${s.aggressive_max_open || 15} open at once, between 09:16 and ${esc(s.exit_time || "15:15")}. Tagged "aggressive" so the Day report shows what it earns apart. Day 1 data: trades against the risk check lost money, so watch this record before trusting it.</small></span></label></div>
    ${p.guard ? `<div class="${p.guard.blocked ? "banner" : "note"}" style="${p.guard.blocked ? "margin-top:14px" : ""}">${p.guard.blocked ? "⛔ " + esc(p.guard.why) + (p.aggressive ? " (aggressive mode keeps trading)" : "") : `Risk guard: ₹${fk(p.guard.risk_per_trade)} at risk per trade, stops new trades after a ₹${fk(Math.abs(p.guard.loss_limit))} loss or ${s.max_trades_day} trades in a day. Today: ${p.guard.trades_today} trades.`}</div>` : ""}
    <h2>Open positions</h2>
    <div class="table-wrap"><table><thead><tr><th>Option</th><th>Stock</th><th>In at</th><th class="r">Entry</th><th class="r">Now</th><th class="r">Target / stop</th><th class="r">P&amp;L</th><th></th></tr></thead><tbody>${open || `<tr><td colspan="8" class="muted">No open paper trades. Buy from an alert, the option chain or the chart.</td></tr>`}</tbody></table></div>
    <div class="split" style="margin-top:28px"><div><h2 style="margin-top:0">Which alerts make money</h2>
      <div class="table-wrap"><table><thead><tr><th>Alert type</th><th class="r">Trades</th><th class="r">Win</th><th class="r">Avg</th><th class="r">Total</th></tr></thead><tbody>${kinds || `<tr><td colspan="5" class="muted">Fills in as trades close.</td></tr>`}</tbody></table></div></div>
      <div><h2 style="margin-top:0">Equity</h2><div class="chart" id="eq-chart" style="height:240px"></div></div></div>
    <h2>Closed trades</h2>
    <div class="table-wrap"><table><thead><tr><th>Option</th><th>Stock</th><th>When</th><th class="r">Price</th><th>Exit</th><th class="r">Charges</th><th class="r">Net</th></tr></thead><tbody>${closed || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table></div>
    <p class="note">Paper only: no order ever reaches a broker. Charges are real (brokerage, STT, exchange, GST, stamp). Target +${s.target_pct}% / stop −${s.stop_pct}% on the option; alert trades exit after ${s.max_hold_min} min, all by ${esc(s.exit_time)}. ${p.source === "sim" ? "<b>Showing simulator trades.</b>" : ""}</p>`;
  main.querySelectorAll("[data-close]").forEach((b) => b.onclick = async () => { try { await post(`/api/live/paper/${b.dataset.close}/close`, {}); toast("Closed"); pagePaper(); } catch (e) { toast(e.message); } });
  $("#pp-preset").onclick = async () => { const small = !((A.capital || 0) <= 60000);
    if (!window.confirm(small ? "Switch paper trading to the ₹40K small account (1 lot, max 2 trades a day, stop after one loss, lock +₹1,200)?" : "Switch back to the ₹5 lakh account with normal limits?")) return;
    try { await post("/api/live/paper/preset", { name: small ? "small" : "normal" }); toast(small ? "Small account (₹40K) on" : "Normal account on"); pagePaper(); } catch (e) { toast(e.message); } };
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
async function pageConnect() {
  stopPageTimer();
  const [info, s] = await Promise.all([api("/api/live/info"), api("/api/settings")]);
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
    "live.auto_start": $("#s-auto").checked, "live.source": $("#s-src").value, "live.news": $("#s-news").checked,
    "live.sim_speed": Number($("#s-speed").value), "live.telegram_min_severity": Number($("#s-tsev").value),
    "ai.provider": $("#s-aip").value, "ai.api_key": $("#s-aik").value.trim(), "ai.explain_min_severity": Number($("#s-aisev").value),
    "ai.pro_model": $("#s-aim").value, "ai.pro_auto_per_day": Number($("#s-aiauto").value), "ai.language": $("#s-ailang").value,
    "ai.pulse_every_min": Number($("#s-aipulse").value), "live.paper.auto": $("#s-pauto").checked, "live.paper.lots": Number($("#s-plots").value),
    "live.paper.target_pct": Number($("#s-ptgt").value), "live.paper.stop_pct": Number($("#s-pstop").value),
    "live.paper.capital": Number(String($("#s-pcap").value).replace(/[^0-9.]/g, "")) || 500000, "live.paper.risk_pct": Number($("#s-prisk").value),
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

async function pageScorecard() {
  stopPageTimer();
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const sc = await api(`/api/live/scorecard${params.get("src") ? `?source=${params.get("src")}` : ""}`);
  const m = sc.model || {};
  const rows = sc.rows.map((r) => `<tr><td><b>${esc(KIND[r.kind] || r.kind)}</b> <span class="muted small">${r.is_index ? "indices" : "stocks"}</span></td><td class="r">${r.n}</td>
    <td class="r"><b class="${r.hit_rate >= 60 ? "up" : r.hit_rate < 45 ? "down" : ""}">${r.hit_rate ?? "–"}%</b> <small class="muted">${r.decided ?? ""} decided</small><div class="meter thin"><i class="${r.hit_rate >= 50 ? "on" : ""}" style="width:${r.hit_rate || 0}%"></i></div></td>
    <td class="r">${r.recent_rate != null ? r.recent_rate + "%" : "–"} <small class="muted">last ${r.recent_n}</small></td>
    <td class="r">${fpc(r.avg_o15, 1)}</td><td class="r">${fpc(r.avg_o60, 1)}</td><td class="r">${fpc(r.avg_r15, 2)}</td>
    <td>${(r.decided ?? 0) >= 20 && r.hit_rate < 45 || (r.n >= 40 && (r.avg_o60 ?? 0) < -2) ? `<span class="pill p-down">Demoted</span>` : (r.decided ?? 0) < 10 ? `<span class="muted small">too few yet</span>` : r.hit_rate >= 60 ? `<span class="pill p-up">Reliable</span>` : ""}</td></tr>`).join("");
  const recent = sc.recent.slice(0, 80).map((o) => `<tr><td>${o.day.slice(5)} ${hm(o.ts)}</td><td>${arrow(o.direction)} ${esc(o.title || o.sym)}</td>
    <td>${o.result ? `<span class="pill ${o.result === "hit" ? "p-up" : o.result === "miss" ? "p-down" : ""}">${o.result === "hit" ? "Worked" : o.result === "miss" ? "Failed" : "Flat"}</span>` : `<span class="muted small">tracking…</span>`}</td>
    <td class="r">${fpc(o.r5, 2)}</td><td class="r">${fpc(o.r15, 2)}</td><td class="r">${fpc(o.r60, 2)}</td><td class="r">${o.o0 ? fpc(o.omax, 0) + " / " + fpc(o.omin, 0) : "–"}</td></tr>`).join("");
  main.innerHTML = `<h1 class="page-title">Signal scorecard</h1>
    <p class="lede">Every alert is followed for 60 minutes and graded. This is how you know which signals deserve real money. ${sc.source === "sim" ? "<b>Showing simulator results (random market, not evidence).</b>" : ""}
      <a href="#/scorecard?src=${sc.source === "sim" ? "upstox" : "sim"}">Show ${sc.source === "sim" ? "real market" : "simulator"} results</a></p>
    ${scoreBuckets(sc)}
    ${aiReviewBox("scorecard", "Claude's read on your alerts")}
    <div class="kpis"><div class="kpi"><b>${sc.graded}</b><span>Alerts graded (recent)</span></div>
      <div class="kpi"><b>${sc.overall != null ? sc.overall + "%" : "–"}</b><span>Worked overall</span></div>
      <div class="kpi"><b class="${m.trained ? "up" : ""}">${m.auc != null ? m.auc : "–"}</b><span>Conviction model: holdout AUC (0.5 = coin toss, trusted at 0.60+ with 5+ days)</span></div>
      <div class="kpi"><b>${sc.recordings_mb} MB</b><span>Market data recorded on this Mac</span></div></div>
    <div class="panel tight" style="margin-top:14px"><h3>Conviction model <span class="pill ${m.trained ? "p-up" : ""}">${m.trained ? "trusted: it now picks Focus" : "learning"}</span></h3>
      <p class="small">Learns which alerts are worth an option buy from every graded alert: the stock (move, VWAP, futures buildup, order flow, unusual options, IV), the option itself (price per lot, spread), and everything around it (Nifty, breadth, its sector, global markets, VIX, fresh news). Trained on earlier days, judged on the latest days it never saw.</p>
      <p class="small">${m.n ? `${(m.n || 0).toLocaleString("en-IN")} graded alerts over ${m.days || 0} day${m.days === 1 ? "" : "s"}. ` : ""}${m.hold_n ? `Holdout ${m.hold_n.toLocaleString("en-IN")} alerts: top 10% by conviction worked ${m.top10_hit}% vs ${m.base_rate}% for all (option ${m.top10_o60 >= 0 ? "+" : ""}${m.top10_o60}% vs ${m.all_o60 >= 0 ? "+" : ""}${m.all_o60}% after an hour). ` : ""}<b>${esc(m.why || (m.need ? `needs ${m.need} graded alerts` : ""))}</b></p>
      ${(m.drivers || []).length ? `<p class="small muted">What matters most so far: ${m.drivers.map((d) => esc(d.feature)).join(", ")}.</p>` : ""}</div>
    <p class="note">${esc(sc.rule)} Alert types under 45% of decided cases (20+) are demoted automatically: out of Focus, no Telegram, no auto paper trade.</p>
    <h2>By alert type</h2>
    <div class="table-wrap"><table><thead><tr><th>Alert</th><th class="r">Graded</th><th class="r">Worked</th><th class="r">Recently</th><th class="r">Option 15 min</th><th class="r">Option 60 min</th><th class="r" title="Price move in the alert's favour">Price 15 min</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="muted">Nothing graded yet. Run the scanner for a session.</td></tr>`}</tbody></table></div>
    <h2>Latest graded alerts</h2>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Alert</th><th>Result</th><th class="r" title="Price move in the alert's favour">In favour 5m</th><th class="r">15m</th><th class="r">60m</th><th class="r">Option best / worst</th></tr></thead><tbody>${recent || `<tr><td colspan="7" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
  bindAIReview(main);
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
  const stocks = (await symbolList()).stocks.filter((x) => !IDX.includes(x));
  if (!IDX.includes(CH.sym) && !stocks.includes(CH.sym)) stocks.unshift(CH.sym);
  main.classList.add("wide");
  main.innerHTML = `<div class="ch-top">
      <div class="ch-sym"><select id="ch-sym" aria-label="Symbol"><optgroup label="Indices">${IDX.map((x) => `<option value="${x}" ${x === CH.sym ? "selected" : ""}>${x}</option>`).join("")}</optgroup>
        <optgroup label="F&amp;O stocks">${stocks.map((x) => `<option value="${esc(x)}" ${x === CH.sym ? "selected" : ""}>${esc(x)}</option>`).join("")}</optgroup></select>
        <span id="ch-price" class="ch-price"></span></div>
      <div class="seg" role="group" aria-label="Timeframe">${[1, 3, 5, 15].map((t) => `<button data-tf="${t}" aria-pressed="${CH.tf === t}">${t}m</button>`).join("")}</div>
      <div class="seg" role="group" aria-label="Days">${[1, 3, 5, 10].map((d) => `<button data-days="${d}" aria-pressed="${CH.days === d}">${d}D</button>`).join("")}</div>
      <details class="ch-ind"><summary class="btn">Indicators &amp; levels</summary><div class="ch-menu">${CH_TOGGLES.map(([k, l]) => `<label class="check"><input type="checkbox" data-tog="${k}" ${chOn(k) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></details>
      <a class="btn" href="#/sym?s=${encodeURIComponent(CH.sym)}">Option chain</a>
      <a class="btn" href="#/timemachine?s=${encodeURIComponent(CH.sym)}">Time Machine</a>
      <span class="btn-row" style="margin:0"><button class="btn buy" id="ch-buy" title="Paper: buy the at-the-money call, 1 lot">Paper BUY</button><button class="btn sell" id="ch-sell" title="Paper: buy the at-the-money put, 1 lot">Paper SELL</button></span>
    </div>
    <div class="ch-wrap"><div id="ch-chart" class="ch-chart"></div>
      <aside class="ch-side"><div class="panel tight"><h3>Trade signals <span class="muted small">BUY / SELL marks on the chart</span></h3><div id="ch-sigs"><p class="muted small">Focus alerts on this symbol show here.</p></div></div>
        <div class="panel tight"><h3>Chart read</h3><div id="ch-read"><p class="muted">Loading…</p></div>
        <button class="link-btn" id="ch-ai">AI read this chart</button><div id="ch-ai-out"></div></div>
        <div class="panel tight"><h3>Patterns <span class="muted small">on this chart</span></h3><div id="ch-pats"></div></div>
        ${["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"].includes(CH.sym) ? "" : `<div class="panel tight"><h3>Company <span class="muted small">results and insiders</span></h3><div id="ch-company"><p class="muted small">Loading…</p></div></div>`}
        <div class="panel tight" id="ch-scen-wrap"><h3>Scenarios <span class="muted small">next 5 / 15 / 60 min</span></h3><div id="ch-scen"><p class="muted small">Nifty and Bank Nifty.</p></div></div>
        <div class="panel tight"><h3>Setups today</h3><div id="ch-setups"></div></div>
        <div class="panel tight"><h3>Key levels</h3><div id="ch-levels"></div></div></aside></div>
    <p class="note">Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a> Lightweight Charts. ${""}Index VWAP counts each bar equally (indices have no volume).</p>`;
  $("#ch-sym").onchange = (e) => { location.hash = `#/chart?s=${encodeURIComponent(e.target.value)}&tf=${CH.tf}`; };
  main.querySelectorAll("[data-tf]").forEach((b) => b.onclick = () => { LS.set("ch_tf", Number(b.dataset.tf)); location.hash = `#/chart?s=${encodeURIComponent(CH.sym)}&tf=${b.dataset.tf}`; });
  main.querySelectorAll("[data-days]").forEach((b) => b.onclick = () => { LS.set("ch_days", Number(b.dataset.days)); pageChart(); });
  main.querySelectorAll("[data-tog]").forEach((c) => c.onchange = () => { LS.set("ch_" + c.dataset.tog, c.checked); buildChart(CH.hasVol); drawChart(CH.data, true); });
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
  const tick = async (first) => {
    if (state.page !== "chart") return;
    try {
      CH.data = await api(`/api/live/chart/${encodeURIComponent(CH.sym)}?tf=${CH.tf}&days=${CH.days}`);
      if (first || !CH.chart || CH.hasVol !== CH.data.has_volume) { CH.hasVol = CH.data.has_volume; buildChart(CH.hasVol); first = true; }
      drawChart(CH.data, first);
    }
    catch (err) {
      if (first || !CH.chart) $("#ch-chart").innerHTML = `<div class="empty" style="margin:20px"><h2>Loading ${esc(CH.sym)} history…</h2><p class="small muted">${esc(err.message)} Trying again every few seconds.</p></div>`;
      L.pageTimer = setTimeout(() => tick(!CH.chart), 6000); return;
    }
    L.pageTimer = setTimeout(() => tick(false), CH.data && CH.data.offline ? 30000 : 2000);
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

function drawChartSignals(d) {
  const el = $("#ch-sigs"); if (!el) return;
  const sig = (d.markers || []).filter((m) => (m.text === "BUY" || m.text === "SELL") && m.id).slice(-4).reverse();
  const html = sig.length ? `<ul class="plain small">${sig.map((m) => `<li><span class="pill ${m.text === "BUY" ? "p-up" : "p-down"}">${m.text}</span> ${new Date((m.time - 19800) * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })} ${esc(m.title || "")}
      <br><button class="link-btn" data-paper="${m.id}">Paper trade this signal</button></li>`).join("")}</ul>` : `<p class="muted small">No Focus signal on ${esc(d.sym || CH.sym)} in this range. The Paper BUY / SELL buttons above trade the at-the-money option anyway.</p>`;
  if (el.dataset.h !== html) { el.dataset.h = html; el.innerHTML = html; bindAlertButtons(el); }
}

function drawChart(d, first) {
  if (!d || !CH.chart) return;
  drawChartSignals(d);
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
    if (n) CH.chart.timeScale().setVisibleLogicalRange({ from: n - Math.min(n, 500), to: n + ahead });   // show every bar of the chosen days (like a trading app); scroll or zoom for detail
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
  el.innerHTML = `${w.running ? "" : `<div class="banner">Live scanner is off: prices fill in when it runs (it starts by itself at 9:00 on market days, or start it on Live).</div>`}
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
    const src = (s) => s === "generated" || s === "mutation" ? "search" : s.startsWith("ebook:") ? "📕 " + esc(s.slice(6)) : s.startsWith("library:") ? "📚 " + esc(s.slice(8)) : s === "claude" ? "Claude" : esc(s);
    const fits = (r) => r.dir !== 0 || String(r.exit || "").startsWith("fly");
    const only40 = LS.get("lab40", false);
    const rows = (f.rules || []).filter((r) => r.stage !== "failed train" && r.stage !== "duplicate" && (!only40 || fits(r))).slice(0, 60).map((r) => `<tr>
        <td><span class="pill ${stage[r.stage] ? stage[r.stage][0] : ""}">${stage[r.stage] ? stage[r.stage][1] : esc(r.stage)}</span>${r.stage === "promoted" ? ` <span class="pill ${r.evidence === "strong" ? "p-up" : r.evidence === "moderate" ? "p-amber" : ""}" title="Holdout t-stat: strong ≥ 2, moderate ≥ 1">evidence: ${esc(r.evidence || "weak")}</span>` : ""}<br><small class="muted">${r.id} · ${src(r.source || "")}</small></td>
        <td class="small">${esc(r.rule)}${r.note ? `<br><span class="muted">${esc(r.note)}</span>` : ""}${r.live_record ? `<br><b>Live paper:</b> ${r.live_record.n} trades, ${rs(r.live_record.net)}` : ""}</td>
        <td class="small">${st(r.train)}</td><td class="small">${st(r.valid)}</td><td class="small"><b>${st(r.hold)}</b>${r.hold && r.hold.n ? `<br>worst day ${rs(r.hold.worst_day)}` : ""}</td>
        <td>${r.stage === "promoted" ? `<label class="check small"><input type="checkbox" data-rlive="${r.id}" ${r.is_live ? "checked" : ""}><span>Live paper</span></label>` : ""}</td></tr>`).join("");
    const books = (f.books || []).map((b) => `<tr><td>📕 ${esc(b.name)} <small class="muted">${b.mb} MB</small></td>
        <td class="small">${b.error ? `<span class="down">${esc(b.error)}</span>` : b.done ? `${b.rules} rules found (${esc(b.at)})${b.cannot.length ? `<br><span class="muted">Can't test yet: ${b.cannot.slice(0, 4).map((c) => esc(c.idea || "") + (c.needs ? ` (needs ${esc(c.needs)})` : "")).join("; ")}</span>` : ""}` : `<span class="muted">not read yet</span>`}</td>
        <td><button class="btn" data-read="${esc(b.name)}" ${f.ai ? "" : "disabled title='Connect Claude in Live setup first'"}>${b.done ? "Read again" : "Read with Claude"}</button></td></tr>`).join("");
    const runs = (f.runs || []).slice().reverse().map((r) => `<tr><td>${esc((r.at || "").replace("T", " "))}</td><td class="r">${r.tried.toLocaleString("en-IN")}</td><td class="r">${r.train_pass}</td><td class="r"><b>${r.promoted}</b></td><td class="r">${r.luck_expected}</td><td class="r">${r.secs}s</td></tr>`).join("");
    main.innerHTML = `<div class="toolbar"><div><h1 class="page-title">Strategy Lab</h1><p class="lede" style="margin-bottom:0">Thousands of intraday option rules tested at once on 2 years of real 1-minute option prices, from a search, your ebooks and Claude. A rule must win on three separate periods before it trades live (on paper). Every evening it runs again by itself.</p></div>
        <div class="btn-row" style="margin:0"><button class="btn primary" id="lab-run" ${f.has_history ? "" : "disabled"}>Run research now</button><button class="btn" id="lab-lib">Test the strategy library</button><button class="btn" id="lab-ideas" ${f.ai ? "" : "disabled title='Connect Claude in Live setup first'"}>Ask Claude for new ideas</button></div></div>
      ${f.has_history ? "" : `<div class="banner">Download the 1-minute option history first: Strategy desk → Download history (Upstox Plus).</div>`}
      <div class="panel tight" id="lab-stocks" style="margin-top:14px"></div>
      <div class="kpis kpi6" style="margin-top:16px">
        <div class="kpi"><b>${(f.tried_total || 0).toLocaleString("en-IN")}</b><span>Rules tried so far</span></div>
        <div class="kpi"><b class="${prom ? "up" : ""}">${prom}</b><span>Passed all 3 periods (promoted)</span></div>
        <div class="kpi"><b>≤ ${f.luck_expected}</b><span>Would pass by pure luck (upper estimate)</span></div>
        <div class="kpi"><b>${(by["failed validation"] || 0) + (by["failed holdout"] || 0)}</b><span>Looked good in train, then failed</span></div>
        <div class="kpi"><b>${(f.live_rules || []).length}</b><span>Rules live on paper now</span></div>
        <div class="kpi"><b>${f.n_days || "–"}</b><span>Days of history</span></div></div>
      <div class="panel tight" style="margin-top:14px"><h3>How to read this</h3><ul class="small">
        <li><b>Train</b> ${f.period ? `(${esc(f.period.train[0])} → ${esc(f.period.train[1])})` : ""} finds a rule; <b>Validation</b> ${f.period ? `(→ ${esc(f.period.valid[1])})` : ""} confirms it; <b>Holdout</b> ${f.period ? `(${esc(f.period.hold[0])} → ${esc(f.period.hold[1])})` : ""} was never used to choose anything: it is the honest test.</li>
        <li>Each trade buys 1 lot of the at-the-money call or put, after real charges and slippage. A day stops after a losing trade (the small-account way), so "green" = % of trading days that ended in profit.</li>
        <li>If promoted ≈ the luck number, nothing real was found yet. ${prom > f.luck_expected ? "" : `<b>That is the case right now</b>: ${prom} promoted vs up to ${f.luck_expected} by luck, so even the promoted rules may be luck. They trade on paper only to build a live record, and switch off by themselves if they lose over 15 trades. The search keeps going every evening, with your ebooks and Claude's ideas.`}</li></ul></div>
      ${(f.library || []).length ? `<h2>Strategy library <span class="muted small">famous setups from top traders and books, every exit and indicator setting tested</span></h2>
      <div class="table-wrap"><table class="lab"><thead><tr><th>Setup</th><th>Best version</th><th>Train</th><th>Validation</th><th>Holdout</th></tr></thead><tbody>
        ${f.library.map((x) => { const r = x.best; return `<tr><td><b>${esc(x.name)}</b><br><span class="pill ${stage[r.stage] ? stage[r.stage][0] : ""}">${stage[r.stage] ? stage[r.stage][1] : esc(r.stage)}</span> <small class="muted">${x.n_rules} versions</small></td>
          <td class="small">${esc(r.rule)}<br><span class="muted">${esc((r.note || "").split(". ").slice(1).join(". "))}</span></td><td class="small">${st(r.train)}</td><td class="small">${st(r.valid)}</td><td class="small"><b>${st(r.hold)}</b></td></tr>`; }).join("")}</tbody></table></div>` : ""}
      <div class="toolbar" style="margin-top:28px"><h2 style="margin:0">Rules <span class="muted small">promoted first, then the closest misses</span></h2>
        <label class="check small"><input type="checkbox" id="lab-40" ${only40 ? "checked" : ""}><span>Only what a ₹40K account can trade (option buying, iron fly)</span></label></div>
      <div class="table-wrap"><table class="lab"><thead><tr><th>Result</th><th>Rule</th><th>Train<br><small class="muted">trades · avg · t · green</small></th><th>Validation</th><th>Holdout</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="muted">Run research to fill this.</td></tr>`}</tbody></table></div>
      <div class="split" style="margin-top:24px">
        <div><h2 style="margin-top:0">Your ebooks</h2>
          <p class="small muted">Upload trading-strategy books (PDF, EPUB, TXT). Claude reads the rule-heavy passages, turns each concrete rule into a testable one, and lists what it can't test yet. They face the same 3-period test. The book stays on your Mac.</p>
          <div class="btn-row"><input type="file" id="lab-file" accept=".pdf,.epub,.txt,.md" multiple><button class="btn" id="lab-up">Upload</button></div>
          <div class="table-wrap" style="margin-top:10px"><table><tbody>${books || `<tr><td class="muted">No books yet.</td></tr>`}</tbody></table></div>
          ${f.pending_ideas ? `<p class="note">${f.pending_ideas} rules from books / Claude are waiting: they are tested on the next run.</p>` : ""}</div>
        <div><h2 style="margin-top:0">Research runs</h2><div class="table-wrap"><table class="mini"><thead><tr><th>When</th><th class="r">Tried</th><th class="r">Train pass</th><th class="r">Promoted</th><th class="r">Luck</th><th class="r">Time</th></tr></thead><tbody>${runs || `<tr><td colspan="6" class="muted">None yet.</td></tr>`}</tbody></table></div></div></div>
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
    main.querySelectorAll("[data-rlive]").forEach((c) => c.onchange = async () => { try { await post(`/api/factory/rule/${c.dataset.rlive}`, { on: c.checked }); toast(c.checked ? "Rule live on paper" : "Rule switched off"); } catch (e) { toast(e.message); } });
  };
  let stTimer = null;
  const drawStocks = async () => {
    const el = $("#lab-stocks"); if (!el) return;
    let x; try { x = await api("/api/factory/stocks"); } catch (e) { el.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; return; }
    const pct = x.total ? Math.round(x.done / x.total * 100) : 0;
    const have = (x.stocks_on_disk || []).length;
    el.innerHTML = `<div class="toolbar" style="margin:0"><div><h3 style="margin:0">Stock options data <span class="muted small">${have ? `${have} stocks · ${x.expiries_on_disk} monthly expiries on your Mac` : "not downloaded yet"}</span></h3>
        <p class="small muted" style="margin:4px 0 0">1-minute option prices of ${(x.default || []).length} liquid F&O stocks (Reliance, HDFC Bank, ICICI, SBI, Infosys…) from Upstox Plus, newest month first. With it the Lab tests rules on stock options too ("any F&O stock", one trade at a time like a ₹40K account) and the conviction model learns faster. About 6–8 hours for 12 months (Upstox's speed limit), runs in the background, resumes if stopped, adds each new month by itself after the close. Market data only, no orders.</p></div>
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
