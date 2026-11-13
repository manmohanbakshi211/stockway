// Tanu UI: four screens. Read-only except the live setup on System (saved on this Mac only). No libraries.
"use strict";

const view = document.getElementById("view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rs = (x) => (x == null ? "–" : (x < 0 ? "−₹" : "₹") + Math.abs(Math.round(x)).toLocaleString("en-IN"));
const pct = (x) => (x == null ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(1) + "%");
const money = (x) => `<span class="${x > 0 ? "up" : x < 0 ? "down" : ""}">${rs(x)}</span>`;
const EXIT = { chand_tight: "tight chart stop", chand_wide: "wide chart stop", chand_wide_nomove: "wide chart stop + out if no move",
  prem_trail_20: "premium trail 20%", prem_trail_30: "premium trail 30%", time_30: "out after 30 min", time_60: "out after 60 min" };
const FILTER = { all_days: "all days", no_expiry_day: "not on expiry day" };
const variant = (id) => { const [, e, f] = String(id).split("__"); return e ? `${EXIT[e] || e}, ${FILTER[f] || f}` : "as written"; };
const plain = (s) => { const x = String(s).replace(/\blearn\b/g, "tuning days").replace(/\bholdout\b/g, "unseen days");
  return x ? x[0].toUpperCase() + x.slice(1) + "." : x; };
const hm = (t) => (t ? new Date(t * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : "–");

async function get(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || `${url} answered ${r.status}`);
  return j;
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-Tanu": "1" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || `${url} answered ${r.status}`);
  return j;
}

const ACCT = { shadow: "Shadow (all setups)", sniper: "Sniper (proven setups)", shadow_sell: "Shadow (all setups)",
  sniper_sell: "Sniper (proven setups)" };
function acctLabel(id) { return ACCT[id] || id; }
function acctShort(id) { return (ACCT[id] || id).split(" (")[0]; }

function fail(e) {
  view.innerHTML = `<h1>This screen could not load</h1><p class="muted">${esc(e.message)}</p>
    <p class="muted">Check that Tanu is running (python -m tanu web), then reload.</p>`;
}

// ---------------------------------------------------------------- Today
function liveBlock(L) {
  if (!L || (!L.switch && !L.fresh)) return "";
  const st = L.status, sup = L.supervisor;
  if (!st || !L.fresh) {
    return `<h2>Live</h2><div class="empty">${esc((sup && sup.why && sup.why[0]) || "Waiting for the market. Live numbers appear here from 09:15.")}</div>`;
  }
  const f = st.feed || {};
  const spots = Object.entries(st.spots || {}).filter(([, v]) => v);
  const sig = (st.signals || []).slice().reverse();
  const opens = st.open || [];
  return `<h2>Live</h2>
    <div class="facts">
      <div><b>${st.done ? "Closed" : `${st.minute}/${st.of}`}</b><span>${st.done ? "day done" : "minutes of the day"}</span></div>
      <div><b class="${f.connected ? "" : "down"}">${f.connected ? "On" : "Off"}</b><span>feed${f.last_msg_ago_s != null ? `, last price ${f.last_msg_ago_s}s ago` : ""}</span></div>
      ${spots.map(([k, v]) => `<div><b>${Number(v).toLocaleString("en-IN")}</b><span>${esc(k)}</span></div>`).join("")}
    </div>
    <h2>Open paper trades</h2>
    ${opens.length ? `<div class="scroll"><table class="ledger"><tbody>${opens.map((p) => `<tr><td>${esc(p.sym)} ${p.strike} ${esc(p.side)}
      <div class="small muted">${esc(p.setup)}</div></td><td class="num">${p.entry != null ? "₹" + p.entry.toFixed(2) : "filling"}<div class="small muted">bought</div></td>
      <td class="num">${p.last != null ? "₹" + Number(p.last).toFixed(2) : "–"}<div class="small muted">now</div></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">None open.</p>`}
    <h2>Signals today</h2>
    ${sig.length ? `<div class="scroll"><table class="ledger"><tbody>${sig.map((x) => `<tr><td class="small">${hm(x.t + 60)}</td>
      <td>${esc(x.sym)} ${x.d > 0 ? "up" : "down"} <span class="status ${esc(x.status)}">${esc(x.status)}</span>
      <div class="small muted">${esc(x.setup)}${x.skipped ? " · " + esc(x.skipped) : ""}</div></td></tr>`).join("")}</tbody></table></div>
      <p class="muted small">Shadow signals are graded only; alerts reach your phone only for setups on trial or live.</p>`
      : `<p class="muted">No signals yet today.</p>`}`;
}

async function today() {
  const [d, L] = await Promise.all([get("/api/today"), get("/api/live").catch(() => null)]);
  document.getElementById("ver").textContent = "v" + d.version;
  const accl = d.account_list || [];
  const st = d.setups || {};
  const acctTable = (kind, title) => {
    const rows = accl.filter((a) => a.kind === kind);
    return `<h3>${title}</h3><div class="scroll"><table class="ledger"><thead><tr><th>Account</th><th class="num">Trades</th>
      <th class="num wide">Won</th><th class="num">P&amp;L</th><th class="num">On capital</th><th class="num wide">Per trade</th></tr></thead><tbody>
      ${rows.map((a) => `<tr><td>${esc(acctShort(a.id))}</td>
        <td class="num">${a.trades}</td><td class="num wide">${a.win_pct ?? "–"}${a.win_pct != null ? "%" : ""}</td>
        <td class="num">${money(a.pnl_rs)}</td><td class="num">${pct(a.return_pct)}</td>
        <td class="num wide">${pct(a.avg_pct)}</td></tr>`).join("")}</tbody></table></div>`;
  };
  view.innerHTML = `
    <h1>${esc(d.headline)}</h1>
    <p class="muted">${d.live ? "Live paper trading." : "Not live yet: the old app still runs the market until the switch-over."}
      ${d.journal_day ? "Latest journal day: " + esc(d.journal_day) + "." : ""}</p>
    ${liveBlock(L)}
    <h2>Paper accounts${d.journal_day ? `, ${esc(d.journal_day)}` : ""}</h2>
    <p class="muted small">Capital ${money((accl[0] || {}).capital || 500000)} in each account. Shadow takes every signal of
      every setup (grading); Sniper only setups that passed the tests.</p>
    ${accl.length ? acctTable("buy", "Option buying") + acctTable("sell", "Option selling")
      : `<div class="empty">No paper accounts set up.</div>`}
    <h2>Setups</h2>
    <div class="facts">
      <div><b>${st.live || 0}</b><span>live</span></div><div><b>${st.trial || 0}</b><span>on trial</span></div>
      <div><b>${st.shadow || 0}</b><span>in shadow</span></div><div><b>${(d.setups_passing || []).length}</b><span>passed the tests</span></div>
    </div>
    <h2>Cheapest options to buy</h2>
    ${d.tradeable.count ? `<p class="muted small">${d.tradeable.count} of ${d.tradeable.of} cheap enough on ${esc(d.tradeable.day)}
      (round trip: spread, charges and an hour of time decay).</p>
      <div class="scroll"><table class="ledger"><tbody>${d.tradeable.cheapest.map((x) =>
        `<tr><td>${esc(x.sym)}</td><td class="num">${x.round_trip_pct.toFixed(2)}%</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">No cost list yet. It is made from recorded option chains (python -m tanu costs &lt;day&gt;).</div>`}
    <h2>Questions from the reviews</h2>
    ${(d.questions || []).length ? `<ul class="plain">${d.questions.map((q) => `<li>${esc(q.day)}: ${esc(q.idea)}
      <span class="muted">(${esc(q.evidence)})</span></li>`).join("")}</ul>` : `<p class="muted">None yet.</p>`}`;
}

// ---------------------------------------------------------------- Setups
async function setups() {
  const [d, st] = await Promise.all([get("/api/setups"), get("/api/structures").catch(() => null)]);
  const num = (label, part) => `<span><span class="muted">${label}</span> <b>${pct(part.avg_pct)}</b>
      <span class="muted small">(${part.n || 0} trades)</span></span>`;
  const part = (label, b, n) => {
    const verdict = !b ? "Not tested yet." : b.passes ? "Passed." : (plain((b.why_not || []).join("; ")) || "Yardstick for the others.");
    return `<div class="version"><span class="vlabel">${label}</span>
      ${b ? `<div class="nums">${num("Tuning days", b.learn)}${num("Unseen days", b.hold)}</div>
        <p class="small">${esc(verdict)} <span class="muted">Best of ${n} version${n === 1 ? "" : "s"}: ${esc(variant(b.variant))}.</span></p>`
        : `<p class="small muted">${esc(verdict)}</p>`}</div>`;
  };
  const row = (s) => `<div class="row">
      <div class="row-head"><b>${esc(s.name)}</b> <span class="status ${esc(s.status)}">${esc(s.status)}</span></div>
      ${part("Buying the option", s.best, s.variants_tested)}
      ${part("Selling the option", s.best_sell, s.sell_variants_tested)}
      <p class="small muted">${esc(s.why)}</p></div>`;
  view.innerHTML = `<h1>Setups and their evidence</h1>
    <p class="muted">A setup trades on paper only after it wins on days it never saw, after costs, beyond luck.
      Ideas tried so far: ${d.ideas_tried}. The more ideas tried, the higher the bar.</p>
    <div class="rows">${d.setups.map(row).join("")}</div>
    ${st && st.structures.length ? `<h2>Multi-leg structures</h2>
      <p class="muted small">Iron condors and straddles, entered by the calendar; judged on return on margin, above zero
        beyond luck${st.t_bar ? ` (t ≥ ${st.t_bar})` : ""}.</p>
      <div class="rows">${st.structures.map((x) => `<div class="row">
        <div class="row-head"><b>${esc(x.name)}</b> <span class="status ${esc(x.status)}">${esc(x.status)}</span></div>
        ${x.learn ? `<div class="nums">${num("Tuning days", x.learn)}${num("Unseen days", x.hold || {})}</div>
          <p class="small">${x.passes ? "Passed." : esc(plain((x.why_not || []).join("; ")))} <span class="muted">₹ a trade: ${x.per_trade != null ? money(x.per_trade) : "–"} (return on margin above).</span></p>`
          : `<p class="small muted">Not tested yet.</p>`}
        <p class="small muted">${esc(x.why)}</p></div>`).join("")}</div>` : ""}
    ${d.bad.length ? `<h2>Setup files with errors</h2><ul class="plain">${d.bad.map((b) => `<li>${esc(b.file)}: <span class="warn">${esc(b.error)}</span></li>`).join("")}</ul>` : ""}`;
}

// ---------------------------------------------------------------- Journal
async function multileg() {
  const d = await get("/api/structures").catch(() => null);
  if (!d || !d.structures.length) return "";
  const legs = (t) => t.legs.map((x) => `${x.action === "sell" ? "S" : "B"} ${x.strike} ${x.side}`).join(" · ");
  return `<h2>Multi-leg selling (iron condor, straddle)</h2>
    <p class="muted small">Entered by the calendar and held to expiry or a time, every structure graded on real option prices. Capital ₹5L.</p>
    <div class="scroll"><table class="ledger"><thead><tr><th>Structure</th><th class="num">Trades</th><th class="num wide">Won</th>
      <th class="num">Total</th><th class="num">Per trade</th></tr></thead><tbody>
      ${d.structures.map((s) => `<tr><td>${esc(s.name)}<div class="small muted">${esc((s.symbols || []).join(", "))} ·
        <span class="status ${esc(s.status)}">${esc(s.status)}</span>${s.passes ? " · passed" : s.learn ? " · not proven" : ""}</div></td>
        <td class="num">${s.trades}</td><td class="num wide">${s.won_pct ?? "–"}${s.won_pct != null ? "%" : ""}</td>
        <td class="num">${money(s.pnl_rs)}</td><td class="num">${s.per_trade != null ? money(s.per_trade) : "–"}</td></tr>`).join("")}
    </tbody></table></div>
    ${d.trades.length ? `<h3>Latest multi-leg trades</h3><div class="scroll"><table class="ledger"><tbody>
      ${d.trades.slice(0, 15).map((t) => `<tr><td class="small">${esc(t.day)}<div class="muted">${esc(t.sym)}</div></td>
        <td class="small">${esc(t.structure)}<div class="muted">${esc(legs(t))}</div></td>
        <td class="num">${money(t.pnl_rs)}<div class="small muted">${esc(t.reason)}</div></td></tr>`).join("")}
      </tbody></table></div>` : ""}`;
}

async function journal(params) {
  const q = new URLSearchParams(params || {});
  const kind = q.get("kind") === "sell" ? "sell" : "buy";
  q.set("kind", kind);
  const d = await get("/api/journal?" + q);
  const tabs = `<div class="seg" role="tablist" aria-label="Paper trading">
      <a role="tab" href="#journal?kind=buy" aria-selected="${kind === "buy"}">Option buying</a>
      <a role="tab" href="#journal?kind=sell" aria-selected="${kind === "sell"}">Option selling</a></div>`;
  const ml = kind === "sell" ? await multileg() : "";
  if (!d.trades || !d.days.length) {
    view.innerHTML = `<h1>Paper trades</h1>${tabs}<div class="empty">${kind === "sell"
      ? "No option-selling trades yet. Every setup has a selling version; its paper trades appear here."
      : "No trades in the journal yet. Every paper trade will appear here with its full footprint."}</div>${ml}`;
    return;
  }
  const acc = q.get("account") || "";
  const rev = d.review && d.review.accounts ? d.review.accounts : {};
  view.innerHTML = `<h1>Paper trades</h1>${tabs}
    <p class="muted small">${kind === "sell" ? "Selling: an option is sold against each signal (a put when the view is up, a call when it is down); profit when its price falls. Capital ₹5L per account; margin is an estimate."
      : "Buying: the option in the signal's direction is bought; profit when its price rises. Capital ₹5L per account."}</p>
    <div class="controls">
      <select id="j-day" aria-label="Day">${d.days.map((x) => `<option ${x === d.day ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
      <select id="j-acc" aria-label="Account"><option value="">All ${kind === "sell" ? "selling" : "buying"} accounts</option>${d.accounts.map((a) => `<option value="${esc(a)}" ${a === acc ? "selected" : ""}>${esc(acctLabel(a))}</option>`).join("")}</select>
    </div>
    ${Object.entries(rev).map(([a, r]) => `<p><b>${esc(acctLabel(a))}</b>: ${r.summary.trades} trades, ${money(r.summary.pnl_rs)};
      losers that were up 10%+ first: ${r.green_then_red} of ${r.losers}; never moved 3%: ${r.never_moved}.</p>`).join("")}
    <div id="detail"></div>
    <div class="scroll"><table class="ledger"><thead><tr><th>In</th><th>Setup</th><th class="num">P&amp;L</th><th class="num wide">Best</th><th class="wide">Exit</th></tr></thead>
    <tbody>${d.trades.map((t, i) => `<tr class="tap" data-i="${i}" tabindex="0"><td>${hm(t.entry_t)}</td>
      <td>${esc(t.setup.replace("@sell", "").split("__")[0])}<br><span class="muted small">${kind === "sell" ? "sold " : ""}${esc(t.sym)} ${t.strike} ${esc(t.side)}, ${esc(acctShort(t.account))}</span></td>
      <td class="num">${money(t.pnl_rs)}<br><span class="muted small">${pct(t.pct)}</span></td>
      <td class="num wide">${pct(t.mfe_pct)}</td><td class="small wide">${esc(t.reason)} ${hm(t.exit_t)}</td></tr>`).join("")}</tbody></table></div>
    ${ml}`;
  const go = () => {
    const p = { day: document.getElementById("j-day").value, kind };
    const a = document.getElementById("j-acc").value;
    if (a) p.account = a;
    journal(p).catch(fail);
  };
  document.getElementById("j-day").onchange = go;
  document.getElementById("j-acc").onchange = go;
  view.querySelectorAll("tr.tap").forEach((tr) => {
    const open = () => showTrade(d.trades[+tr.dataset.i]);
    tr.onclick = open;
    tr.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
}

function chart(points, marks, cls) {
  if (!points.length) return `<p class="muted small">No price data for this part.</p>`;
  const W = 600, H = 160, P = 8;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (x) => P + ((x - x0) / Math.max(1, x1 - x0)) * (W - 2 * P);
  const Y = (y) => H - P - ((y - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * P);
  const path = points.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join("");
  const m = marks.filter((k) => k.t && k.y != null).map((k) =>
    `<circle class="${k.cls}" cx="${X(k.t).toFixed(1)}" cy="${Y(k.y).toFixed(1)}" r="5"><title>${esc(k.label)}</title></circle>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="price chart">
    <path class="${cls}" d="${path}"/>${m}<text x="${P}" y="14">${y1.toFixed(2)}</text><text x="${P}" y="${H - 4}">${y0.toFixed(2)}</text></svg>`;
}

async function showTrade(t) {
  const box = document.getElementById("detail");
  box.innerHTML = `<div class="detail"><p class="muted">Loading the trade…</p></div>`;
  const q = new URLSearchParams({ account: t.account, setup: t.setup, sym: t.sym, day: t.day, d: t.d, entry_t: t.entry_t });
  try {
    const d = await get("/api/trade?" + q);
    const s = d.trade.state || {};
    box.innerHTML = `<div class="detail">
      <p><b>${esc(t.sym)} ${t.strike} ${esc(t.side)}</b>, ${esc(t.setup.replace("@sell", "").split("__")[0])}:
        ${t.kind === "sell" ? `sold ${hm(t.entry_t)} at ₹${t.entry.toFixed(2)}, bought back ${hm(t.exit_t)} at ₹${(t.exit ?? 0).toFixed(2)}`
          : `in ${hm(t.entry_t)} at ₹${t.entry.toFixed(2)}, out ${hm(t.exit_t)} at ₹${(t.exit ?? 0).toFixed(2)}`}
        (${esc(t.reason)}). ${money(t.pnl_rs)} after ₹${Math.round(t.charges || 0)} charges${t.kind === "sell" && t.margin ? `; margin about ${money(t.margin)}` : ""}.</p>
      <p class="muted small">At entry the day was ${esc(s.day || "?")}${s.rel ? `, ${Number(s.rel).toFixed(1)}× its normal range so far` : ""}.
        Best point ${pct(t.mfe_pct)}, worst ${pct(t.mae_pct)}, held ${t.minutes ?? "–"} min.</p>
      <h2>${esc(t.sym)} that day</h2>
      ${chart(d.underlying, [{ t: t.entry_t, y: t.und_entry, cls: "mark-in", label: "in" }, { t: t.exit_t, y: t.und_exit, cls: "mark-out", label: "out" }], "line")}
      <h2>The option around the trade</h2>
      ${chart(d.option, [{ t: t.entry_t, y: t.entry, cls: "mark-in", label: "bought" }, { t: t.exit_t, y: t.exit, cls: "mark-out", label: "sold" }], "opt")}
    </div>`;
    box.scrollIntoView({ block: "nearest" });
  } catch (e) {
    box.innerHTML = `<div class="detail"><p class="warn">${esc(e.message)}</p></div>`;
  }
}

// ---------------------------------------------------------------- System
function setupBlock(u) {
  if (!u) return "";
  const up = u.upstox || {}, tg = u.telegram || {};
  const step = (done, title, body) => `<div class="row"><div class="row-head"><b>${title}</b>
    <span class="status ${done ? "live" : ""}">${done ? "Done" : "To do"}</span></div>${body}</div>`;
  return `<h2>Switch-over setup</h2>
    <p class="muted small">Saved only on this Mac (never shown again, never sent anywhere except Upstox / Telegram).</p>
    <div class="rows">
      ${step(up.app_saved, "Upstox app key and secret", `${up.app_saved ? `<p class="muted small">Saved. To change, enter new ones.</p>` : ""}
        <form class="form" data-form="upstox">
        <label>API key<input name="api_key" type="password" autocomplete="off" required></label>
        <label>API secret<input name="api_secret" type="password" autocomplete="off" required></label>
        <p class="muted small">From the Upstox developer page (your app). Its redirect URL must be ${esc(u.redirect_uri)}</p>
        <button type="submit">Save</button><span class="msg small" role="status"></span></form>`)}
      ${step(tg.saved, "Telegram alerts", `<form class="form" data-form="telegram">
        <label>Bot token<input name="bot_token" type="password" autocomplete="off" required></label>
        <label>Chat id<input name="chat_id" inputmode="numeric" autocomplete="off" required></label>
        <button type="submit">Save and send a test</button><span class="msg small" role="status"></span></form>
        ${tg.last_error ? `<p class="warn small">Last send failed: ${esc(tg.last_error)}</p>` : ""}`)}
      ${step(u.switch, "Live switch", u.switch ? `<p class="small">On. Tanu runs the market; the old app stays closed.</p>`
        : `<p class="muted small">Off until your go. ${u.legacy_running ? "The old app is running now." : ""}</p>`)}
      ${step(up.logged_in, "Daily Upstox login", up.logged_in
        ? `<p class="small">Logged in${up.user ? " as " + esc(up.user) : ""}, valid till ${esc((up.expires_at || "").slice(11, 16))} tomorrow.</p>`
        : u.switch ? `<p><a class="button" href="/upstox/login">Log in with Upstox</a></p>`
        : `<p class="muted small">Opens after the switch-over.</p>`)}
    </div>`;
}

function wireForms() {
  document.querySelectorAll("form[data-form]").forEach((f) => f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = f.querySelector(".msg");
    const body = Object.fromEntries(new FormData(f).entries());
    msg.textContent = "Saving…";
    try {
      const r = await post(`/api/setup/${f.dataset.form}`, body);
      f.reset();
      msg.textContent = r.message || "Saved.";
      setTimeout(route, 1200);
    } catch (e) { msg.textContent = e.message; msg.className = "msg small warn"; }
  }));
}

async function system() {
  const [d, u] = await Promise.all([get("/api/system"), get("/api/setup").catch(() => null)]);
  const mark = { ok: "OK", warn: "Check", fail: "Fix" };
  const mb = (b) => (b / 1e6).toFixed(b < 1e7 ? 1 : 0) + " MB";
  view.innerHTML = `<h1>System</h1>
    <p class="muted">Version ${esc(d.version)}. Free disk ${d.free_gb} GB. Tanu reads market data only and never places an order.</p>
    ${location.hash.includes("login=ok") ? `<p class="up">Logged in with Upstox.</p>` : location.hash.includes("login=failed") ? `<p class="down">Upstox login did not finish. Try again.</p>` : ""}
    ${setupBlock(u)}
    <h2>Checks</h2>
    <div class="scroll"><table class="ledger"><tbody>${d.checks.map((c) => `<tr><td>${esc(c.check)}</td>
      <td class="${c.status === "ok" ? "" : c.status === "warn" ? "warn" : "down"}">${mark[c.status]}</td><td class="small">${esc(c.why)}</td></tr>`).join("")}
    </tbody></table></div>
    <h2>Data</h2>
    <div class="scroll"><table class="ledger"><tbody>${Object.entries(d.sizes).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${mb(v)}</td></tr>`).join("") || "<tr><td class='muted'>No data yet.</td></tr>"}
    </tbody></table></div>
    ${d.import.at ? `<p class="muted small">Old app's data imported ${esc(d.import.at)}: ${Object.entries(d.import.totals || {}).map(([k, v]) => `${esc(k)} ${Number(v).toLocaleString("en-IN")} rows`).join(", ")}.</p>` : ""}
    <h2>Latest log lines</h2>
    ${d.logs.length ? `<div class="scroll"><table class="ledger"><tbody>${d.logs.map((l) => `<tr><td class="small">${esc((l.ts || "").slice(11, 19))}</td>
      <td class="small ${l.level === "ERROR" ? "down" : l.level === "WARNING" ? "warn" : ""}">${esc(l.comp)}: ${esc(l.event)}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">No log lines yet.</p>`}`;
  wireForms();
}

// ---------------------------------------------------------------- router
const screens = { today, setups, journal, system };
function route() {
  const name = (location.hash.slice(1) || "today").split("?")[0];
  const fn = screens[name] || today;
  document.querySelectorAll(".tabs a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.tab === (screens[name] ? name : "today")));
  document.querySelectorAll(".tabs a[aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
  view.innerHTML = `<p class="muted">Loading…</p>`;
  const qs = location.hash.slice(1).split("?")[1] || "";
  fn(Object.fromEntries(new URLSearchParams(qs))).catch(fail);
}
window.addEventListener("hashchange", route);
get("/api/today").then((d) => (document.getElementById("ver").textContent = "v" + d.version)).catch(() => {});
route();
