// Tanu UI: five screens. Read-only except the live setup on System (saved on this Mac only). No libraries.
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

// ---------------------------------------------------------------- Ask Tanu (AI, read-only)
function md(text) {        // the AI's text: escaped first, then bullets and **bold** only
  const lines = esc(text).split(/\n/);
  let html = "", list = false;
  for (const raw of lines) {
    const ln = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    const m = ln.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (m) { if (!list) { html += "<ul class=\"plain\">"; list = true; } html += `<li>${m[1]}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (ln.trim()) html += `<p>${ln.replace(/^#+\s*/, "")}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function askBlock(ai) {
  if (!ai) return "";
  const notes = ai.notes || [];
  return `<h2>Ask Tanu</h2>
    ${ai.available ? `<form class="inline-form ask" id="ask-form"><input name="q" maxlength="1000" autocomplete="off"
        placeholder="e.g. aaj Bank Nifty mein kya chal raha hai?" aria-label="Question"><button type="submit">Ask</button></form>
      <p class="muted small">Answers come only from Tanu's own data. AI: ${esc(ai.label)}. Paper only: it never places a trade.</p>`
      : `<div class="empty">AI is not set up yet. On the System page choose Claude (your plan on this Mac) or save a Claude API key.</div>`}
    <div id="ask-out"></div>
    <h2>Notes</h2>
    ${notes.length ? notes.slice(0, 2).map((n) => `<div class="detail note"><p class="muted small">${esc(n.kind === "morning" ? "Morning note" : "Evening note")},
      ${esc(n.day)}${n.at ? ", written " + esc(String(n.at).slice(11, 16)) : ""}</p>${md(n.text)}</div>`).join("")
      : `<p class="muted">No notes yet.${ai.available ? "" : " They start once AI is set up."}</p>`}
    ${ai.available ? `<div class="controls"><button type="button" class="ghost" data-note="morning">Write the morning note now</button>
      <button type="button" class="ghost" data-note="evening">Write the evening note now</button><span class="msg small" id="note-msg" role="status"></span></div>` : ""}`;
}

function wireAsk() {
  const f = document.getElementById("ask-form"), out = document.getElementById("ask-out");
  if (f) f.onsubmit = async (ev) => {
    ev.preventDefault();
    const q = (new FormData(f).get("q") || "").toString().trim();
    if (!q) return;
    const btn = f.querySelector("button");
    btn.disabled = true;
    out.innerHTML = `<div class="detail"><p class="muted">Thinking… (up to a couple of minutes)</p></div>`;
    try {
      const r = await post("/api/ai/ask", { question: q });
      out.innerHTML = `<div class="detail note"><p class="muted small">${esc(r.question)}</p>${md(r.answer)}
        <p class="muted small">${esc(r.model)}, ${r.secs} s</p></div>`;
    } catch (e) { out.innerHTML = `<div class="detail"><p class="warn">${esc(e.message)}</p></div>`; }
    btn.disabled = false;
  };
  view.querySelectorAll("[data-note]").forEach((b) => (b.onclick = async () => {
    const msg = document.getElementById("note-msg");
    msg.textContent = "Writing… (up to a few minutes)";
    view.querySelectorAll("[data-note]").forEach((x) => (x.disabled = true));
    try { await post("/api/ai/note", { kind: b.dataset.note }); route(); }
    catch (e) { msg.textContent = e.message; msg.className = "msg small warn"; view.querySelectorAll("[data-note]").forEach((x) => (x.disabled = false)); }
  }));
}

async function today() {
  const [d, L, ai] = await Promise.all([get("/api/today"), get("/api/live").catch(() => null), get("/api/ai").catch(() => null)]);
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
    ${askBlock(ai)}
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
  wireAsk();
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

// ---------------------------------------------------------------- Market (public information, display only)
const MTABS = [["context", "Context"], ["world", "World"], ["insiders", "Insiders"], ["watchlist", "Watchlist"],
  ["news", "News"], ["alerts", "Alerts"]];
const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
const chg = (x) => (x == null ? "–" : `<span class="${cls(x)}">${x > 0 ? "+" : ""}${Number(x).toFixed(2)}%</span>`);
const num2 = (x, d = 2) => (x == null ? "–" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const cr = (x) => (x == null ? "–" : `<span class="${cls(x)}">${x < 0 ? "−" : ""}₹${Math.abs(x).toLocaleString("en-IN", { maximumFractionDigits: Math.abs(x) < 100 ? 2 : 0 })} Cr</span>`);
const ago = (s) => (s == null ? "never" : s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago");
const link = (href, text) => (/^https?:\/\//.test(href || "") ? `<a class="news-title" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text));
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }) : "");
let marketTimer = null;

function spark(line, up) {
  if (!line || line.length < 3) return "";
  const lo = Math.min(...line), hi = Math.max(...line), W = 72, H = 22;
  const pts = line.map((v, i) => `${(i / (line.length - 1) * W).toFixed(1)},${(H - 2 - (v - lo) / Math.max(hi - lo, 1e-9) * (H - 4)).toFixed(1)}`).join(" ");
  return `<svg class="spark ${up ? "up" : "down"}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}

function sourcesNote(src) {
  const bad = Object.values(src || {}).filter((s) => s.ok === false || s.stale || s.ok == null);
  if (!bad.length) return "";
  return `<ul class="plain sources">${bad.map((s) => `<li>${esc(s.name)}: ${s.ok == null ? (s.busy ? "reading now…" : "not read yet")
    : s.ok === false ? `last read failed (${esc(s.error || "")})${s.age_s != null ? `; showing what was read ${ago(s.age_s)}` : ""}`
    : `from ${ago(s.age_s)}`}</li>`).join("")}</ul>`;
}

function fiiBlock(f) {
  if (!f || !f.FII) return `<div class="empty">FII / DII figures not read yet (NSE publishes them after the close).</div>`;
  return `<div class="facts"><div><b>${cr(f.FII.net)}</b><span>FII / FPI net, ${esc(f.FII.date)}</span></div>
    <div><b>${cr((f.DII || {}).net)}</b><span>DII net</span></div></div>`;
}

function poiBlock(p) {
  if (!p || !(p.rows || []).length) return `<div class="empty">Participant positions not read yet.</div>`;
  const k = (x) => (x == null ? "–" : Math.round(x).toLocaleString("en-IN"));
  return `<p class="muted small">Index futures and options, contracts, ${esc(p.date)} (change on the day).</p>
    <div class="scroll"><table class="ledger"><thead><tr><th>Who</th><th class="num">Futures long</th><th class="num">Futures net</th>
    <th class="num wide">Calls net</th><th class="num wide">Puts net</th></tr></thead><tbody>${p.rows.map((r) => `<tr><td>${esc(r.who)}</td>
    <td class="num">${r.fut_long_pct ?? "–"}%</td><td class="num"><span class="${cls(r.fut_net)}">${k(r.fut_net)}</span>
    <div class="small muted">${r.fut_net_chg != null ? (r.fut_net_chg > 0 ? "+" : "") + k(r.fut_net_chg) : ""}</div></td>
    <td class="num wide">${k(r.call_net)}</td><td class="num wide">${k(r.put_net)}</td></tr>`).join("")}</tbody></table></div>`;
}

const M = {
  context(d) {
    const g = d.gift_nifty, b = d.ban;
    return `<h2>Before and during the day</h2>
      <div class="facts">${g ? `<div><b>${num2(g.last, 1)}</b><span>GIFT Nifty ${chg(g.chg_pct)}</span></div>` : ""}
        ${(d.cues || []).map((c) => `<div><b>${num2(c.last)}</b><span>${esc(c.name)} ${chg(c.chg_pct)}</span></div>`).join("")
          || `<div><b>–</b><span>World prices not read yet</span></div>`}</div>
      <h2>FII and DII cash</h2>${fiiBlock(d.fii_dii)}
      <h2>Who holds index futures and options</h2>${poiBlock(d.participant_oi)}
      <h2>F&amp;O ban</h2>${b ? `<p>${b.ban.length ? b.ban.map(esc).join(", ") : "No stock in the ban"} <span class="muted small">(${esc(b.date)})</span></p>
        ${b.near.length ? `<p class="muted small">Near the limit: ${b.near.map(([s, p]) => `${esc(s)} ${p}%`).join(", ")}</p>` : ""}` : `<div class="empty">Not read yet.</div>`}
      <h2>High-impact events, next 24 hours</h2>
      ${(d.calendar_soon || []).length ? `<div class="scroll"><table class="ledger"><tbody>${d.calendar_soon.map((e) => `<tr>
        <td class="small nowrap">${esc(e.time.slice(5, 16).replace("T", " "))}</td><td>${esc(e.country)} ${esc(e.title)}
        <div class="small muted">${e.forecast ? `forecast ${esc(e.forecast)}, ` : ""}${e.previous ? `previous ${esc(e.previous)}` : ""}</div></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted">None.</p>`}
      <h2>Results this week (F&amp;O stocks)</h2>
      ${(d.results_week || []).length ? `<div class="scroll"><table class="ledger"><tbody>${d.results_week.map((e) => `<tr>
        <td class="small nowrap">${esc(e.date.slice(5))}</td><td><b>${esc(e.sym)}</b> <span class="small muted">${esc(e.purpose)}</span></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted">None listed.</p>`}
      <h2>Company filings that matter</h2>${filingsTable(d.filings)}`;
  },
  world(d) {
    return `${(d.groups || []).map((g) => `<h2>${esc(g.group)}</h2><div class="scroll"><table class="ledger"><tbody>
      ${g.rows.map((r) => `<tr><td>${esc(r.name)}${r.stale ? ` <span class="small muted">(old)</span>` : ""}</td>
        <td class="num">${num2(r.last)}</td><td class="num">${chg(r.chg_pct)}</td><td class="wide">${spark(r.line, r.chg_pct >= 0)}</td></tr>`).join("")}
      </tbody></table></div>`).join("") || `<div class="empty">World prices not read yet.</div>`}
      ${(d.adr || []).length ? `<h2>Indian ADRs vs NSE</h2><div class="scroll"><table class="ledger"><tbody>${d.adr.map((a) => `<tr>
        <td>${esc(a.nse)}</td><td class="num">₹${num2(a.implied_inr)}<div class="small muted">ADR in ₹</div></td>
        <td class="num">₹${num2(a.nse_inr)}<div class="small muted">NSE</div></td><td class="num">${chg(a.premium_pct)}<div class="small muted">premium</div></td></tr>`).join("")}
        </tbody></table></div>` : ""}
      <h2>Economic calendar this week</h2>
      ${(d.calendar || []).length ? `<div class="scroll"><table class="ledger"><tbody>${d.calendar.map((e) => `<tr>
        <td class="small nowrap">${esc(e.time.slice(5, 16).replace("T", " "))}</td><td>${esc(e.country)} ${esc(e.title)}
        <div class="small muted">${esc(e.impact)}${e.forecast ? `, forecast ${esc(e.forecast)}` : ""}${e.previous ? `, previous ${esc(e.previous)}` : ""}</div></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted">Not read yet.</p>`}
      <h2>World news</h2>${newsList(d.news)}`;
  },
  insiders(d) {
    const f = d.fii_dii;
    return `<h2>FII and DII cash</h2>${fiiBlock(f)}
      ${f && (f.history || []).length > 1 ? `<div class="scroll"><table class="ledger"><thead><tr><th>Day</th><th class="num">FII</th><th class="num">DII</th></tr></thead><tbody>
        ${f.history.slice(0, 10).map((h) => `<tr><td class="small nowrap">${esc(h.date)}</td><td class="num">${cr(h.fii)}</td><td class="num">${cr(h.dii)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <h2>Who holds index futures and options</h2>${poiBlock(d.participant_oi)}
      <h2>Insider trades, last ${d.insider_days} days</h2>
      <p class="muted small">Promoters, directors and key managers buying or selling in the market, ₹50 lakh and more (SEBI disclosures on NSE).
        Public information filed after the trade.${d.insider_left ? ` ${d.insider_left} more filings still to read.` : ""}</p>
      ${(d.insider_net || []).length ? `<h3>Net by stock</h3><div class="scroll"><table class="ledger"><tbody>${d.insider_net.map((r) => `<tr>
        <td><b>${esc(r.sym)}</b> <span class="small muted">${r.trades} trade${r.trades === 1 ? "" : "s"}</span></td><td class="num">${cr(r.net_cr)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${(d.insiders || []).length ? `<h3>Latest</h3><div class="scroll"><table class="ledger"><tbody>${d.insiders.slice(0, 60).map((x) => `<tr>
        <td class="small nowrap">${esc((x.traded || x.filed || "").slice(5, 10))}</td>
        <td><b>${esc(x.sym)}</b> <span class="${x.side === "Buy" ? "up" : "down"}">${esc(x.side)}</span>
          <div class="small muted">${esc(x.person)}, ${esc(x.category)}${x.after_pct != null ? `, holds ${num2(x.after_pct)}%` : ""}</div></td>
        <td class="num">₹${num2(x.value / 1e7)} Cr</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">No insider trades read yet.</div>`}
      <h2>Bulk and block deals</h2>
      ${(d.deals || []).length ? `<div class="scroll"><table class="ledger"><tbody>${d.deals.slice(0, 40).map((x) => `<tr>
        <td><b>${esc(x.sym)}</b> <span class="${/^b/i.test(x.side || "") ? "up" : "down"}">${esc(x.side)}</span>
          <div class="small muted">${esc(x.client)}, ${esc(x.type)}, ${esc(x.date)}</div></td>
        <td class="num">₹${num2(x.value_cr)} Cr<div class="small muted">at ₹${num2(x.price)}</div></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted">None read today.</p>`}`;
  },
  watchlist(d) {
    return `<form class="inline-form" id="w-add"><input name="sym" placeholder="Add a symbol, e.g. TATAMOTORS" aria-label="Symbol"
        autocomplete="off" autocapitalize="characters" maxlength="20"><button type="submit">Add</button></form>
      <div class="chips">${d.symbols.map((s) => `<span class="chip">${esc(s)}<button type="button" data-rm="${esc(s)}" aria-label="Remove ${esc(s)}">×</button></span>`).join("")}</div>
      <div class="scroll"><table class="ledger"><tbody>${d.rows.map((r) => `<tr>
        <td><b>${esc(r.sym)}</b>${r.ban ? ` <span class="down small">F&amp;O ban</span>` : r.mwpl_pct >= 80 ? ` <span class="warn small">${r.mwpl_pct}% of limit</span>` : ""}
          <div class="small muted">${r.next_event ? `${esc(r.next_event.date.slice(5))} ${esc(r.next_event.purpose)}` : ""}
          ${r.insider_net_cr != null ? ` insiders ${cr(r.insider_net_cr)} in 30 days` : ""}</div>
          ${r.filing ? `<div class="small">${esc(r.filing.label)}: ${link(r.filing.link, (r.filing.desc || "").slice(0, 80))}</div>` : ""}
          ${(r.news || []).map((n) => `<div class="small">${link(n.link, n.title)}</div>`).join("")}</td>
        <td class="num">${r.last != null ? num2(r.last) : "–"}<div class="small">${chg(r.chg_pct)}</div></td>
        <td class="wide">${spark(r.line, r.chg_pct >= 0)}</td></tr>`).join("")}</tbody></table></div>`;
  },
  news(d) {
    return `<h2>Indian market news</h2>${newsList(d.items)}
      <h2>Company filings that matter</h2>${filingsTable(d.filings)}`;
  },
};

function newsList(items) {
  if (!(items || []).length) return `<p class="muted">Not read yet.</p>`;
  const tone = { 1: `<span class="up small">positive words</span>`, "-1": `<span class="down small">negative words</span>` };
  return `<div class="scroll"><table class="ledger"><tbody>${items.slice(0, 60).map((n) => `<tr><td class="small nowrap">${esc(when(n.ts))}</td>
    <td>${link(n.link, n.title)}<div class="small muted">${esc(n.source)}${n.impact === "high" ? ", high impact" : ""} ${tone[n.tone] || ""}</div></td></tr>`).join("")}
    </tbody></table></div>`;
}

function filingsTable(items) {
  if (!(items || []).length) return `<p class="muted">None read yet.</p>`;
  return `<div class="scroll"><table class="ledger"><tbody>${items.slice(0, 40).map((x) => `<tr><td class="small nowrap">${esc((x.time || "").slice(0, 17))}</td>
    <td><b>${esc(x.sym)}</b> <span class="${cls(x.tone)}">${esc(x.label)}</span><div class="small">${link(x.link, (x.desc || "").slice(0, 120))}</div></td></tr>`).join("")}
    </tbody></table></div>`;
}

async function alertsTab(params) {
  const q = new URLSearchParams({ tab: "alerts", min_sev: params.min_sev || "2" });
  ["day", "sym", "kind"].forEach((k) => params[k] && q.set(k, params[k]));
  const d = await get("/api/market?" + q);
  const kinds = Object.entries(d.counts.by_kind || {}).sort((a, b) => b[1] - a[1]);
  const sevName = { 1: "worth a look", 2: "important", 3: "act-now level" };
  const body = !d.day ? `<div class="empty">No alerts yet. The detectors (sharp move, unusual options, OI build-up, IV spike, VWAP,
      opening range, candles, patterns) run on Tanu's live day after the switch-over. News, filings, insider trades, FII / DII and big
      world moves appear here once the Market page has read them twice.</div>`
    : `<div class="controls">
        <select id="a-day" aria-label="Day">${d.days.slice().reverse().map((x) => `<option ${x === d.day ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <select id="a-sev" aria-label="Which alerts"><option value="3" ${params.min_sev === "3" ? "selected" : ""}>Act-now level only</option>
          <option value="2" ${!params.min_sev || params.min_sev === "2" ? "selected" : ""}>Important and up</option>
          <option value="1" ${params.min_sev === "1" ? "selected" : ""}>All</option></select>
        <select id="a-kind" aria-label="Kind"><option value="">Every kind</option>${kinds.map(([k, n]) => `<option value="${esc(k)}" ${k === params.kind ? "selected" : ""}>${esc(k.replace(/_/g, " "))} (${n})</option>`).join("")}</select>
      </div>
      <p class="muted small">${d.counts.all.toLocaleString("en-IN")} alerts on ${d.counts.symbols} symbols; showing ${d.shown.toLocaleString("en-IN")}.
        Shown only: alerts never place or change a paper trade.${d.detect && Object.keys(d.detect.failed || {}).length ? ` Switched off after an error today: ${esc(Object.keys(d.detect.failed).join(", "))}.` : ""}</p>
      ${d.alerts.length ? `<div class="scroll"><table class="ledger"><tbody>${d.alerts.map((a) => `<tr><td class="small nowrap">${esc(a.at.slice(11, 16))}</td>
        <td><span class="sev s${a.severity}" title="${esc(sevName[a.severity])}">${a.severity}</span> <span class="${a.direction === "up" ? "up" : a.direction === "down" ? "down" : ""}">${esc(a.title)}</span>
          <div class="small muted">${esc(a.detail)}${a.data && a.data.link ? " " + link(a.data.link, "open") : ""}</div></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="muted">None at this level.</p>`}`;
  return { html: body, wire: () => {
    const go = () => {
      const p = { tab: "alerts", day: document.getElementById("a-day").value, min_sev: document.getElementById("a-sev").value };
      const k = document.getElementById("a-kind").value;
      if (k) p.kind = k;
      location.hash = "market?" + new URLSearchParams(p);
    };
    ["a-day", "a-sev", "a-kind"].forEach((id) => { const el = document.getElementById(id); if (el) el.onchange = go; });
  } };
}

async function market(params) {
  clearTimeout(marketTimer);
  const tab = MTABS.some(([k]) => k === params.tab) ? params.tab : "context";
  const nav = `<div class="subtabs" role="tablist" aria-label="Market">${MTABS.map(([k, label]) =>
    `<a role="tab" href="#market?tab=${k}" aria-selected="${k === tab}">${label}</a>`).join("")}</div>`;
  let html, wire = () => {}, reading = [];
  if (tab === "alerts") {
    const a = await alertsTab(params);
    html = a.html;
    wire = a.wire;
  } else {
    const d = await get("/api/market?tab=" + tab);
    reading = d.reading || [];
    html = M[tab](d) + sourcesNote(d.sources);
    if (tab === "watchlist") wire = () => wireWatch(d.symbols);
  }
  if ((location.hash.slice(1) || "").split("?")[0] !== "market") return;
  view.innerHTML = `<h1>Market</h1>
    <p class="muted small">Public information (NSE, NSE IX, Yahoo Finance, ForexFactory, news feeds), shown only: nothing here places or changes a trade.</p>
    ${nav}${html}`;
  wire();
  const sel = view.querySelector('.subtabs a[aria-selected="true"]');
  if (sel) sel.scrollIntoView({ block: "nearest", inline: "center" });
  const again = reading.length ? 5000 : 60000;              // fresh data a few seconds after a background read
  marketTimer = setTimeout(() => {
    const busy = document.activeElement && document.activeElement.tagName === "INPUT";
    if ((location.hash.slice(1) || "").startsWith("market") && !busy) market(params).catch(() => {});
  }, again);
}

function wireWatch(symbols) {
  const save = async (list) => { await post("/api/market/watchlist", { symbols: list }); setTimeout(route, 2500); };
  const f = document.getElementById("w-add");
  if (f) f.onsubmit = (ev) => {
    ev.preventDefault();
    const s = (new FormData(f).get("sym") || "").toString().trim().toUpperCase();
    if (s) save([...symbols, s]).catch((e) => { f.insertAdjacentHTML("beforeend", `<span class="warn small">${esc(e.message)}</span>`); });
  };
  view.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => save(symbols.filter((x) => x !== b.dataset.rm)).catch(() => {})));
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

function aiSetup(ai) {
  if (!ai) return "";
  return `<h2>AI (Ask Tanu and the notes)</h2>
    <div class="rows"><div class="row"><div class="row-head"><b>${esc(ai.label)}</b>
      <span class="status ${ai.available ? "live" : ""}">${ai.available ? "Ready" : "To do"}</span></div>
      <p class="muted small">Claude Code on this Mac: ${ai.claude_code_found ? "found (it uses your Claude plan's login)" : "not found (install it, then type claude in Terminal once to log in)"}.
        Claude API key: ${ai.api_key_saved ? "saved" : "not saved"}. Today: ${Object.entries((ai.usage || {}).tiers || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "not used yet"}.</p>
      <form class="form" data-form="ai">
        <label>Use<select name="provider"><option value="claude_code" ${ai.provider === "claude_code" ? "selected" : ""}>Claude, your plan (Claude Code on this Mac)</option>
          <option value="api" ${ai.provider === "api" ? "selected" : ""}>Claude API key</option><option value="off" ${ai.provider === "off" ? "selected" : ""}>Off</option></select></label>
        <label>Claude API key (only for "Claude API key")<input name="api_key" type="password" autocomplete="off"></label>
        <p class="muted small">The AI sees only Tanu's market data, paper trades and test results: never a key, a file or your accounts.</p>
        <button type="submit">Save</button><span class="msg small" role="status"></span></form></div></div>`;
}

async function system() {
  const [d, u, ai] = await Promise.all([get("/api/system"), get("/api/setup").catch(() => null), get("/api/ai").catch(() => null)]);
  const mark = { ok: "OK", warn: "Check", fail: "Fix" };
  const mb = (b) => (b / 1e6).toFixed(b < 1e7 ? 1 : 0) + " MB";
  view.innerHTML = `<h1>System</h1>
    <p class="muted">Version ${esc(d.version)}. Free disk ${d.free_gb} GB. Tanu reads market data only and never places an order.</p>
    ${location.hash.includes("login=ok") ? `<p class="up">Logged in with Upstox.</p>` : location.hash.includes("login=failed") ? `<p class="down">Upstox login did not finish. Try again.</p>` : ""}
    ${setupBlock(u)}
    ${aiSetup(ai)}
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
const screens = { today, market, setups, journal, system };
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
