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
  vix_jump: "VIX jump", vix_drop: "VIX drop", news: "News",
};

/* ---------------- polling + notifications */
function livePage() { return ["live", "scanner", "sym", "alerts", "paper", "connect"].includes(state.page); }

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
  const msg = st.state === "running" ? `${st.source === "sim" ? "Simulator" : "Upstox"} · ${subs} · ${lag}` : esc(st.message || "");
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
      <div><dt>ATM IV</dt><dd>${o.atm_iv ? o.atm_iv.toFixed(1) + "%" : "–"}${o.iv_chg_15m != null ? ` <small class="${o.iv_chg_15m > 0 ? "down" : "up"}">${o.iv_chg_15m > 0 ? "+" : ""}${o.iv_chg_15m.toFixed(1)}</small>` : ""}</dd></div>
      <div><dt>Exp. move</dt><dd>${o.exp_move_pct ? "±" + o.exp_move_pct.toFixed(2) + "%" : "–"}<small class="muted"> ${o.days_to_expiry != null ? o.days_to_expiry + "d" : ""}</small></dd></div>
    </dl>
    ${biasMeter(m.score)}
    ${m.reasons && m.reasons.length ? `<p class="ic-why">${esc(m.reasons.slice(0, 3).join(" · "))}</p>` : ""}
  </a>`;
}

function alertItem(a, full = false) {
  const o = a.option;
  const idea = o ? `<div class="idea"><span>Idea: <b>${esc(o.label)}</b> @ ₹${fx(o.ltp)} · ${rupees(o.cost_per_lot)}/lot${o.iv ? ` · IV ${o.iv.toFixed(1)}%` : ""}</span>
      <span class="btn-row"><button class="link-btn" data-paper="${a.id}">Paper trade</button><button class="link-btn" data-explain="${a.id}">Why?</button></span></div>` :
    `<div class="idea"><span class="btn-row"><button class="link-btn" data-explain="${a.id}">Why?</button></span></div>`;
  const link = a.data && a.data.link ? ` <a href="${esc(a.data.link)}" target="_blank" rel="noopener">Read</a>` : "";
  return `<li class="alert sev${a.severity}" data-aid="${a.id}">
    <div class="al-time">${hms(a.ts)}<span class="sevtag s${a.severity}">${SEV[a.severity]}</span></div>
    <div class="al-body"><div class="al-title">${arrow(a.direction)} <a href="#/sym?s=${encodeURIComponent(a.sym)}">${esc(a.title)}</a></div>
      <p>${esc(a.detail)}${link} <span class="muted small">${esc(KIND[a.kind] || a.kind)}</span></p>
      ${idea}
      ${a.ai ? `<div class="ai-text">${fmtAI(a.ai)}</div>` : ""}</div></li>`;
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
  body.innerHTML = `
    <div class="icards">${s.indices.slice(0, 4).map(indexCard).join("")}</div>
    <div class="live-grid">
      <section>
        <div class="toolbar"><h2 style="margin:0">Alerts <span class="muted small">${L.alerts.length} today</span></h2>
          <div class="seg" role="group" aria-label="Minimum importance">${[1, 2, 3].map((v) => `<button data-sev="${v}" aria-pressed="${L.sevFilter === v}">${v === 1 ? "All" : v === 2 ? "Important" : "Act now"}</button>`).join("")}</div></div>
        <ul class="alerts">${alerts.map((a) => alertItem(a)).join("") || `<li class="muted" style="padding:18px">No alerts yet. They appear here the moment something happens.</li>`}</ul>
      </section>
      <aside>
        <div class="panel pulse"><h3>Market pulse</h3>${pulseHTML}</div>
        <div class="panel tight"><div class="toolbar" style="margin:0 0 6px"><h3>Movers</h3>
          <div class="seg small-seg">${[["hot", "Most active"], ["gainers", "Gainers"], ["losers", "Losers"]].map(([k, l]) => `<button data-mtab="${k}" aria-pressed="${tab === k}">${l}</button>`).join("")}</div></div>
          <table class="mini"><thead><tr><th>Stock</th><th class="r">Price</th><th class="r">Chg</th><th class="r">Vol</th><th class="r">Heat</th></tr></thead><tbody>${mrows}</tbody></table>
          <a class="small" href="#/scanner">Open full scanner →</a></div>
        ${pp ? `<a class="panel tight paperbox" href="#/paper"><h3>Paper trading</h3><div class="pb-row"><div><span>Booked</span><b class="${pp.net >= 0 ? "up" : "down"}">${rupees(pp.net)}</b></div><div><span>Open</span><b class="${pp.unreal >= 0 ? "up" : "down"}">${rupees(pp.unreal)}</b></div><div><span>Trades</span><b>${pp.n}${pp.open ? ` <small class="muted">+${pp.open} open</small>` : ""}</b></div><div><span>Win rate</span><b>${pp.win_rate != null ? pp.win_rate + "%" : "–"}</b></div></div></a>` : ""}
        <div class="panel tight"><h3>Market news</h3><ul class="news">${news || `<li class="muted">Loading headlines…</li>`}</ul></div>
      </aside>
    </div>`;
  bindLiveButtons(body); bindAlertButtons(body);
  body.querySelectorAll("[data-sev]").forEach((b) => b.onclick = () => { L.sevFilter = Number(b.dataset.sev); LS.set("sevFilter", L.sevFilter); renderLive(true); });
  body.querySelectorAll("[data-mtab]").forEach((b) => b.onclick = () => { LS.set("moversTab", b.dataset.mtab); renderLive(true); });
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
    <div class="sym-head"><div><a class="small" href="#/scanner">← Scanner</a>
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
    <p class="lede">Practice with zero risk. Strong alerts buy the suggested option automatically (${s.lots || 1} lot, target +${s.target_pct}%, stop −${s.stop_pct}%, exit by ${s.exit_time}). Charges are real: brokerage, STT, exchange, GST, stamp. ${p.source === "sim" ? "<b>Showing simulator trades.</b>" : ""}</p>
    <div class="kpis"><div class="kpi"><b class="${p.net >= 0 ? "up" : "down"}">${rupees(p.net)}</b><span>Booked P&amp;L after charges</span></div>
      <div class="kpi"><b>${p.n}</b><span>Closed trades</span></div><div class="kpi"><b>${p.win_rate != null ? p.win_rate + "%" : "–"}</b><span>Winning trades</span></div>
      <div class="kpi"><b class="${p.unreal >= 0 ? "up" : "down"}">${rupees(p.unreal)}</b><span>Open, not booked (${p.open.length})</span></div></div>
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
      <label class="field">Lots per trade<select id="s-plots">${[1, 2, 3, 5].map((v) => `<option ${Number(s["live.paper.lots"] || 1) === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="field">Target<select id="s-ptgt">${[20, 30, 40, 50, 75, 100].map((v) => `<option value="${v}" ${Number(s["live.paper.target_pct"] || 30) === v ? "selected" : ""}>+${v}% on premium</option>`).join("")}</select></label>
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

/* ---------------- register */
Object.assign(PAGES, { live: pageLive, scanner: pageScanner, sym: pageSym, alerts: pageAlerts, paper: pagePaper, connect: pageConnect });
window.addEventListener("hashchange", () => { stopPageTimer(); });
pollSnapshot();
