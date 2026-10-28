/* Bank Nifty Radar - front end (no build step) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const C = {
  ink: "#16212C", muted: "#5E6B78", rule: "#D5DCE3", signal: "#3D2FA8",
  up: "#1E8A5A", down: "#C23B32", amber: "#9A6A12", fog: "#E9EDF1",
};
const state = { status: null, page: null, jobSince: 0, jobId: null, pollTimer: null };

/* ---------------- helpers */
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error((body && body.detail) || `Request failed (${r.status})`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v, d = 1) => (v == null || isNaN(v) ? "–" : `${(v * 100).toFixed(d)}%`);
const pct0 = (v) => pct(v, 0);
const sgn = (v, d = 2) => (v == null || isNaN(v) ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const pts = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-IN"));
const fmtNum = (v) => {
  if (v == null || isNaN(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1000) return Math.round(v).toLocaleString("en-IN");
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(4);
};
const PCT_SIGNED = /(_ret\d|_chg\d|^bn_gap$|^bn_oc$|^bn_dd60$|^bn_dist_|^bn_rel_)/;
const PCT_PLAIN = /^(bn_range|bn_atr14|bn_implied_daily|bn_rv20|bn_bbw)$/;
const LEVEL_CHG = /^(us10y_chg\d|us_vix_chg\d)$/;
function fmtFeature(f, v, colour = true) {
  if (v == null || isNaN(v)) return "–";
  if (LEVEL_CHG.test(f)) return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
  if (PCT_SIGNED.test(f)) return colour ? `<span class="${v >= 0 ? "up" : "down"}">${sgn(v)}</span>` : sgn(v);
  if (PCT_PLAIN.test(f)) return pct(v, 2);
  if (/_rank$/.test(f) || f === "bn_close_loc") return pct0(v);
  if (/^is_|^bn_nr7$|^bn_inside_day$|^bn_big_yesterday$/.test(f)) return v >= 0.5 ? "Yes" : "No";
  if (f === "dow") return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][v] || v;
  return fmtNum(v);
}
const longDate = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const shortDate = (s) => new Date(s + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const dateTime = (s) => s ? new Date(s).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 3200);
}

function badge(outcome) {
  const map = { "Caught": "b-caught", "Missed": "b-missed", "False alarm": "b-false", "Quiet, as expected": "b-quiet", "Pending": "b-pending" };
  return outcome ? `<span class="badge ${map[outcome] || "b-pending"}">${esc(outcome)}</span>` : "";
}

const plotLayout = (extra = {}) => Object.assign({
  font: { family: "Archivo, Helvetica Neue, Arial, sans-serif", size: 12, color: C.ink },
  paper_bgcolor: "#fff", plot_bgcolor: "#fff",
  margin: { l: 56, r: 16, t: 10, b: 36 },
  xaxis: { gridcolor: "#EEF1F4", linecolor: C.rule, zeroline: false },
  yaxis: { gridcolor: "#EEF1F4", linecolor: C.rule, zeroline: false },
  showlegend: false, hovermode: "x unified",
}, extra);
const plotCfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"] };

/* ---------------- status + rail */
async function loadVersion(force = false) {
  const el = $("#rail-version");
  if (!el) return;
  let v;
  try { v = await api(`/api/version${force ? "?force=true" : ""}`); } catch (e) { return; }
  const msg = {
    latest: `<span class="v-ok">✓ Up to date</span>`,
    update: `<span class="v-new">⬆ Update available</span><small>Close the Terminal window, then double-click Start Radar.command again.</small>`,
    restart: `<span class="v-new">⬆ Update downloaded</span><small>Close the Terminal window, then double-click Start Radar.command again.</small>`,
    offline: `<span class="muted">Couldn't check for updates (offline)</span>`,
    unknown: `<span class="muted">Version check not available</span>`,
  }[v.state] || "";
  el.innerHTML = `<b>Stockway ${esc(v.name || "")}</b> <span class="muted">· ${esc(v.commit || "")}${v.date ? ", " + esc(v.date) : ""}</span>
    <div class="v-state">${msg} <button class="link-btn small" id="v-check" type="button">Check now</button></div>`;
  $("#v-check").onclick = async (e) => { e.target.textContent = "Checking…"; await loadVersion(true); };
}

async function loadStatus() {
  try { state.status = await api("/api/status"); } catch (e) { state.status = null; }
  const s = state.status, el = $("#rail-status");
  if (!s) { el.textContent = "Can't reach the app server."; return; }
  if (!s.model) { el.innerHTML = "Daily radar: no model yet"; return; }
  el.innerHTML = `Model trained <b>${dateTime(s.model.trained_at)}</b><br>Data to ${shortDate(s.model.data_end)}` +
    (s.model.demo ? `<br><span class="badge b-demo">Demo data</span>` : "") +
    (s.schedule.enabled ? `<br>Auto forecast ${esc(s.schedule.time)}, Mon–Fri` : "");
  if (s.job && s.job.status === "running" && !state.pollTimer) watchJob();
}

function demoBanner() {
  const m = state.status && state.status.model;
  return m && m.demo ? `<div class="banner">You're looking at <b>demo data</b> (synthetic prices). Train on real data in Data &amp; settings to see real Bank Nifty results.</div>` : "";
}

function noModel(title = "No model yet") {
  return `<div class="empty">
    <h2>${title}</h2>
    <p>Train the radar on 10 years of Bank Nifty, India VIX and global markets. It takes a few minutes. You can add news later from Data &amp; settings.</p>
    <div class="btn-row">
      <button class="btn primary" data-train="real">Train model</button>
      <button class="btn" data-train="demo">Try with demo data</button>
    </div></div>`;
}

/* ---------------- jobs */
async function startJob(payload) {
  state.newsLine = null;
  try {
    await post("/api/jobs", payload);
    state.jobSince = 0;
    $("#job-log").textContent = "";
    watchJob();
  } catch (e) { toast(e.message); }
}

function bindTrainButtons(root = main) {
  root.querySelectorAll("[data-train]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.train;
    startJob({ kind: "train", demo: k === "demo" });
  }));
  root.querySelectorAll("[data-news]").forEach((b) => b.addEventListener("click", () => startJob({ kind: "news" })));
  root.querySelectorAll("[data-forecast]").forEach((b) => b.addEventListener("click", () => startJob({ kind: "forecast" })));
}

function watchJob() {
  clearInterval(state.pollTimer);
  const box = $("#job");
  box.hidden = false; box.classList.remove("failed");
  const tick = async () => {
    let j;
    try { j = await api(`/api/jobs/current?since=${state.jobSince}`); } catch (_) { return; }
    if (!j || !j.id) { box.hidden = true; clearInterval(state.pollTimer); state.pollTimer = null; return; }
    if (state.jobId !== j.id) { state.jobId = j.id; state.jobSince = 0; $("#job-log").textContent = ""; }
    $("#job-label").textContent = j.label;
    const lines = j.lines || [];
    if (lines.length) {
      const log = $("#job-log");
      log.textContent += lines.join("\n") + "\n";
      log.scrollTop = log.scrollHeight;
      state.jobSince = j.total_lines;
    }
    if (lines.length) {
      const nl = [...lines].reverse().find((l) => /news \d+\/\d+:/.test(l));
      if (nl) state.newsLine = nl.trim();
    }
    const m = (j.step || "").match(/Step (\d)\/(\d)/);
    const nm = j.kind === "news" && state.newsLine ? state.newsLine.match(/news (\d+)\/(\d+)/) : null;
    const pm = (j.step || "").match(/(\d+)%/);
    let frac = j.status === "done" ? 1 : m ? (Number(m[1]) - (pm ? 1 - Number(pm[1]) / 100 : 0.5)) / Number(m[2]) : 0.03;
    if (nm && j.status === "running") frac = Math.max(0.03, Number(nm[1]) / Number(nm[2]));
    $("#job-progress").style.width = `${Math.round(frac * 100)}%`;
    const running = j.kind === "news" && state.newsLine ? state.newsLine.replace(/^news /, "") : (j.step || "Starting");
    $("#job-step").textContent = j.status === "running" ? running
      : j.status === "done" ? `Finished ${dateTime(j.finished)}`
      : j.status === "stopped" ? "Stopped. Progress is saved." : "Stopped with an error";
    $("#job-stop").hidden = j.status !== "running";
    if (j.status !== "running") {
      clearInterval(state.pollTimer); state.pollTimer = null;
      if (j.status === "failed") {
        box.classList.add("failed");
        $("#job-step").textContent = j.error || "Stopped with an error";
        $("#job-log").hidden = false; $("#job-toggle").textContent = "Hide log"; $("#job-toggle").setAttribute("aria-expanded", "true");
      } else if (j.status === "stopped") {
        toast("Stopped");
        setTimeout(() => { if (!state.pollTimer) box.hidden = true; }, 6000);
      } else {
        toast(j.kind === "train" ? "Training finished" : j.kind === "news" ? "News download finished" : j.kind === "lab_download" ? "History downloaded. Now run the backtest." : j.kind === "lab_backtest" ? "Backtest finished" : "Forecast updated");
        setTimeout(() => { if (!state.pollTimer) box.hidden = true; }, 6000);
      }
      await loadStatus();
      route();
    }
  };
  tick();
  state.pollTimer = setInterval(tick, 1000);
}
$("#job-stop").addEventListener("click", async () => {
  try { await post("/api/jobs/stop", {}); $("#job-step").textContent = "Stopping…"; } catch (e) { toast(e.message); }
});
$("#job-toggle").addEventListener("click", (e) => {
  const log = $("#job-log"); log.hidden = !log.hidden;
  e.target.textContent = log.hidden ? "Show log" : "Hide log";
  e.target.setAttribute("aria-expanded", String(!log.hidden));
});

/* ---------------- page: tomorrow */
function ladderSVG(fc, width = 1000) {
  const W = Math.max(320, Math.round(width)), H = 150, padX = 34, mid = W / 2, narrow = W < 620;
  const sig = fc.implied_pts, close = fc.last_close;
  const span = 2.6 * sig, x = (p) => mid + ((p - close) / span) * (mid - padX);
  const col = fc.flag ? C.signal : C.ink;
  const ticks = [-2, -1, 0, 1, 2];
  const y0 = 56, bandH = 30;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Expected range for tomorrow: ${pts(close - sig)} to ${pts(close + sig)} points (one sigma)">`;
  s += `<line x1="${padX}" x2="${W - padX}" y1="${y0 + bandH / 2}" y2="${y0 + bandH / 2}" stroke="${C.rule}" stroke-width="1"/>`;
  s += `<rect x="${x(close - 2 * sig)}" y="${y0 + 6}" width="${x(close + 2 * sig) - x(close - 2 * sig)}" height="${bandH - 12}" rx="3" fill="${col}" opacity=".12"/>`;
  s += `<rect x="${x(close - sig)}" y="${y0}" width="${x(close + sig) - x(close - sig)}" height="${bandH}" rx="4" fill="${col}" opacity="${fc.flag ? 0.9 : 0.75}"/>`;
  s += `<line x1="${mid}" x2="${mid}" y1="${y0 - 14}" y2="${y0 + bandH + 14}" stroke="${C.ink}" stroke-width="2"/>`;
  s += `<text x="${mid}" y="${y0 - 22}" text-anchor="middle" font-size="15" font-weight="700" fill="${C.ink}">${pts(close)}</text>`;
  s += `<text x="${mid}" y="${y0 - 40}" text-anchor="middle" font-size="12" fill="${C.muted}">Last close</text>`;
  ticks.filter((t) => t !== 0 && (!narrow || Math.abs(t) === 1)).forEach((t) => {
    const p = close + t * sig, xx = x(p);
    s += `<line x1="${xx}" x2="${xx}" y1="${y0 + bandH + 4}" y2="${y0 + bandH + 12}" stroke="${C.muted}"/>`;
    s += `<text x="${xx}" y="${y0 + bandH + 30}" text-anchor="middle" font-size="14" font-weight="600" fill="${C.ink}">${pts(p)}</text>`;
    s += `<text x="${xx}" y="${y0 + bandH + 48}" text-anchor="middle" font-size="12" fill="${C.muted}">${t > 0 ? "+" : "−"}${Math.abs(t)}σ  ${t > 0 ? "+" : "−"}${pts(Math.abs(t) * sig)}</text>`;
  });
  return s + "</svg>";
}

function driversHTML(drivers) {
  if (!drivers || !drivers.length) return `<p class="muted">No strong drivers.</p>`;
  const max = Math.max(...drivers.map((d) => Math.abs(d.effect)), 0.01);
  const rows = drivers.map((d) => {
    const w = (Math.abs(d.effect) / max) * 100;
    return `<div class="drv">
      <span class="name">${esc(d.label)}</span>
      <span class="val">${fmtFeature(d.feature, d.value, false)}</span>
      <span class="neg">${d.effect < 0 ? `<i style="width:${w}%"></i>` : ""}</span>
      <span class="pos">${d.effect > 0 ? `<i style="width:${w}%"></i>` : ""}</span>
      <span class="eff ${d.effect > 0 ? "" : "muted"}">${d.effect > 0 ? "+" : "−"}${(Math.abs(d.effect) * 100).toFixed(1)} pts</span>
    </div>`;
  }).join("");
  return `<div class="drivers"><div class="drv-legend"><span>Factor</span><span style="text-align:right">Value</span><span>Lowers the chance</span><span>Raises the chance</span><span></span></div>${rows}</div>`;
}

async function pageTomorrow() {
  if (!state.status || !state.status.model) { main.innerHTML = `<h1 class="page-title">Tomorrow</h1>${noModel()}`; bindTrainButtons(); return; }
  const { forecast: fc, calls } = await api("/api/forecast");
  if (!fc) {
    main.innerHTML = `<h1 class="page-title">Tomorrow</h1>${demoBanner()}
      <div class="empty"><h2>No forecast yet</h2><p>The model is trained. Get the forecast for the next Bank Nifty session.</p>
      <button class="btn primary" data-forecast>Update forecast</button></div>`;
    bindTrainButtons(); return;
  }
  const up = fc.prob_up >= 0.5;
  const events = (fc.events || []).map((e) => e.replace(/^is_/, "").replace(/_/g, " ")).map((e) => e[0].toUpperCase() + e.slice(1));
  const verdict = fc.flag ? "Opportunity day. A big move is likely." : "Normal day. No big move expected.";
  const callsRows = calls.map((c) => `<tr class="click" data-day="${c.date}">
      <td>${shortDate(c.date)}</td><td class="r">${pct0(c.prob_big)}</td>
      <td>${c.flag ? "Opportunity" : "Normal"}</td><td class="r">${pct(c.range_pct, 2)}</td><td>${badge(c.outcome)}</td></tr>`).join("");
  main.innerHTML = `${demoBanner()}
    <div class="verdict-meta">
      <div class="when">Forecast for <b>${longDate(fc.for_date)}</b>. Updated ${dateTime(fc.made_at)}.</div>
      <button class="btn" data-forecast>Update forecast</button>
    </div>
    <h1 class="verdict ${fc.flag ? "flagged" : ""}">${verdict}</h1>
    <p class="verdict-sub"><b>${pct0(fc.prob_big)}</b> chance that tomorrow's high–low range is among the biggest fifth of the past year. The model flags days above ${pct0(fc.threshold)}.</p>

    <div class="ladder" id="ladder">${ladderSVG(fc)}
      <p class="cap">Expected range from India VIX: about ±${pts(fc.implied_pts)} points (${pct(fc.implied_pct, 2)}) around the last close on ${shortDate(fc.last_date)}. On a big-move day, Bank Nifty usually travels beyond the solid band.</p>
    </div>

    <div class="facts">
      <div class="fact"><h3>Big-move chance</h3>
        <div class="big">${pct0(fc.prob_big)}</div>
        <div class="meter"><i class="${fc.flag ? "on" : ""}" style="width:${Math.min(100, fc.prob_big * 100)}%"></i><span class="thr" style="left:${fc.threshold * 100}%"></span></div>
        <div class="meter-labels"><span>0%</span><span>Flag at ${pct0(fc.threshold)}</span><span>100%</span></div>
      </div>
      <div class="fact"><h3>Direction lean</h3>
        <div class="big ${up ? "lean-up" : "lean-down"}">${up ? "Up" : "Down"}</div>
        <p>${pct0(fc.prob_up)} chance of an up close. A weak signal: pick your side from your intraday setup.</p>
      </div>
      <div class="fact"><h3>Scheduled events</h3>
        ${events.length ? `<ul class="events">${events.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : `<p style="margin-top:0">Nothing scheduled.</p>`}
      </div>
    </div>

    <h2>Why the model thinks so</h2>
    ${driversHTML(fc.drivers)}
    <p class="note">Each bar shows how many percentage points the factor moved tomorrow's big-move chance, compared with a typical day.</p>

    <h2>Recent calls</h2>
    ${calls.length ? `<div class="table-wrap"><table><thead><tr><th>Day</th><th class="r">Chance</th><th>Call</th><th class="r">Actual range</th><th>Result</th></tr></thead><tbody>${callsRows}</tbody></table></div>` : `<p class="muted">Your forecasts will be graded here once each day has traded.</p>`}`;
  bindTrainButtons();
  main.querySelectorAll("tr[data-day]").forEach((tr) => tr.addEventListener("click", () => openDay(tr.dataset.day)));
  const lad = $("#ladder");
  const drawLadder = () => { const w = lad.clientWidth - 48; lad.querySelector("svg").outerHTML = ladderSVG(fc, w); };
  drawLadder();
  window.onresize = () => { if ($("#ladder")) drawLadder(); };
}

/* ---------------- page: history */
async function pageHistory() {
  if (!state.status || !state.status.model) { main.innerHTML = `<h1 class="page-title">History</h1>${noModel()}`; bindTrainButtons(); return; }
  const h = await api("/api/history");
  main.innerHTML = `<h1 class="page-title">History</h1>
    <p class="lede">Every big-move day in the data. From the first test year on, each day was predicted by a model that had never seen it. Click any point to see what came before it.</p>
    ${demoBanner()}
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Time range">
        <button data-range="365">1 year</button><button data-range="1095" aria-pressed="true">3 years</button><button data-range="all">All</button>
      </div>
      <div class="legend">
        <span style="--c:${C.signal}">Big day, flagged</span><span style="--c:${C.down}">Big day, missed</span><span style="--c:#B9A6F0">Flagged, no big move</span>
      </div>
    </div>
    <div class="chart"><div id="hist-price" style="height:420px"></div></div>
    <h2>Big-move chance, day by day</h2>
    <div class="chart"><div id="hist-prob" style="height:220px"></div></div>`;

  const D = h.dates;
  const pick = (cond) => D.map((d, i) => (cond(i) ? i : -1)).filter((i) => i >= 0);
  const tested = (i) => h.prob[i] != null;
  const caught = pick((i) => tested(i) && h.big[i] === 1 && h.flag[i] === 1);
  const missed = pick((i) => tested(i) && h.big[i] === 1 && h.flag[i] !== 1);
  const falseA = pick((i) => tested(i) && h.big[i] === 0 && h.flag[i] === 1);
  const untested = pick((i) => !tested(i) && h.big[i] === 1);
  const mk = (idx, name, color, symbol, size = 7) => ({
    x: idx.map((i) => D[i]), y: idx.map((i) => h.close[i]), mode: "markers", name, type: "scattergl",
    marker: { color, size, symbol, line: { width: symbol.includes("open") ? 1.5 : 0, color } },
    customdata: idx.map((i) => [pct(h.range[i], 2), h.prob[i] == null ? "not tested" : pct0(h.prob[i])]),
    hovertemplate: `${name}<br>Range %{customdata[0]}<br>Chance %{customdata[1]}<extra></extra>`,
  });
  const traces = [
    { x: D, y: h.close, type: "scattergl", mode: "lines", line: { color: C.ink, width: 1.1 }, name: "Close", hovertemplate: "%{x}<br>%{y:,.0f}<extra></extra>" },
    mk(untested, "Big day (training years)", "#A9B4BF", "circle", 5),
    mk(falseA, "Flagged, no big move", "#B9A6F0", "circle-open", 6),
    mk(missed, "Big day, missed", C.down, "x-thin-open", 8),
    mk(caught, "Big day, flagged", C.signal, "circle", 7),
  ];
  const last = D[D.length - 1];
  const from = (days) => { const t = new Date(last); t.setDate(t.getDate() - days); return t.toISOString().slice(0, 10); };
  const priceLayout = plotLayout({ hovermode: "closest", xaxis: { gridcolor: "#EEF1F4", range: [from(1095), last] }, yaxis: { gridcolor: "#EEF1F4", tickformat: ",.0f" } });
  Plotly.newPlot("hist-price", traces, priceLayout, plotCfg);
  const thr = h.threshold;
  Plotly.newPlot("hist-prob", [
    { x: D, y: h.prob, type: "scattergl", mode: "lines", line: { color: C.signal, width: 1 }, name: "Chance", hovertemplate: "%{x}<br>%{y:.0%}<extra></extra>" },
    { x: D, y: thr, type: "scattergl", mode: "lines", line: { color: C.muted, width: 1, dash: "dot" }, name: "Flag level", hovertemplate: "Flag at %{y:.0%}<extra></extra>" },
  ], plotLayout({ xaxis: { gridcolor: "#EEF1F4", range: [from(1095), last] }, yaxis: { gridcolor: "#EEF1F4", tickformat: ".0%", range: [0, 1] } }), plotCfg);

  const setRange = (r) => {
    main.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.range === r)));
    const range = r === "all" ? [D[0], last] : [from(Number(r)), last];
    Plotly.relayout("hist-price", { "xaxis.range": range, "yaxis.autorange": true });
    Plotly.relayout("hist-prob", { "xaxis.range": range });
  };
  main.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => setRange(b.dataset.range)));
  $("#hist-price").on("plotly_click", (ev) => { if (ev.points && ev.points[0]) openDay(String(ev.points[0].x).slice(0, 10)); });
  $("#hist-price").on("plotly_relayout", (ev) => {
    if (ev["xaxis.range[0]"]) Plotly.relayout("hist-prob", { "xaxis.range": [ev["xaxis.range[0]"], ev["xaxis.range[1]"]] });
  });
}

/* ---------------- page: biggest days */
async function pageBiggest() {
  if (!state.status || !state.status.model) { main.innerHTML = `<h1 class="page-title">Biggest days</h1>${noModel()}`; bindTrainButtons(); return; }
  const rows = await api("/api/biggest");
  const render = (onlyMissed) => {
    const list = rows.filter((r) => !onlyMissed || (r.model_prob != null && r.flagged !== 1));
    const result = (r) => r.model_prob == null ? `<span class="muted small">Training years</span>` : badge(r.flagged === 1 ? "Caught" : "Missed");
    $("#big-body").innerHTML = list.map((r) => `<tr class="click" data-day="${r.date}">
      <td>${shortDate(r.date)}</td>
      <td class="r"><b>${pct(r.range_pct, 2)}</b></td>
      <td class="r ${r.open_to_close >= 0 ? "up" : "down"}">${sgn(r.open_to_close)}</td>
      <td class="r ${r.gap >= 0 ? "up" : "down"}">${sgn(r.gap)}</td>
      <td class="r">${r.model_prob == null ? "–" : pct0(r.model_prob)}</td>
      <td>${result(r)}</td>
      <td class="small muted">${esc(r.top_drivers || "")}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No missed days in the test years.</td></tr>`;
    main.querySelectorAll("tr[data-day]").forEach((tr) => tr.addEventListener("click", () => openDay(tr.dataset.day)));
  };
  const tested = rows.filter((r) => r.model_prob != null);
  const caught = tested.filter((r) => r.flagged === 1).length;
  main.innerHTML = `<h1 class="page-title">Biggest days</h1>
    <p class="lede">The ${rows.length} widest-range Bank Nifty days since ${rows.length ? shortDate(rows.map((r) => r.date).sort()[0]) : "the start"}. In the years the model was tested, it flagged ${caught} of ${tested.length} of them the evening before.</p>
    ${demoBanner()}
    <div class="toolbar"><div class="seg" role="group" aria-label="Filter">
      <button data-f="all" aria-pressed="true">All days</button><button data-f="missed">Missed only</button></div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Day</th><th class="r">Range</th><th class="r">Open to close</th><th class="r">Gap</th><th class="r">Chance</th><th>Result</th><th>What stood out the evening before</th></tr></thead>
      <tbody id="big-body"></tbody></table></div>`;
  main.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => {
    main.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render(b.dataset.f === "missed");
  }));
  render(false);
}

/* ---------------- page: drivers */
async function pageDrivers() {
  if (!state.status || !state.status.model) { main.innerHTML = `<h1 class="page-title">What drives moves</h1>${noModel()}`; bindTrainButtons(); return; }
  const f = await api("/api/factors");
  const maxRate = Math.max(...f.factors.flatMap((r) => [r.high || 0, r.low || 0]), f.base || 0, 0.01);
  const rows = f.factors.map((r) => `<tr>
      <td>${esc(r.label)}</td>
      <td class="r"><b>${pct0(r.high)}</b></td><td class="r">${pct0(r.low)}</td>
      <td><div class="fbar"><span class="base" style="left:${(f.base / maxRate) * 100}%"></span>
        <i class="hi" style="width:${((r.high || 0) / maxRate) * 100}%"></i><i class="lo" style="width:${((r.low || 0) / maxRate) * 100}%"></i></div></td></tr>`).join("");
  const imp = f.importance.filter((i) => i.value > 0);
  const maxImp = Math.max(...imp.map((i) => i.value), 1e-6);
  main.innerHTML = `<h1 class="page-title">What drives moves</h1>
    <p class="lede">How often a big day followed, depending on what the market looked like the evening before. A big day happens ${pct0(f.base)} of the time on average (dashed line).</p>
    ${demoBanner()}
    <div class="table-wrap"><table>
      <thead><tr><th>Known before the open</th><th class="r">Big day next, when high</th><th class="r">When low</th><th style="min-width:200px"><span style="color:${C.signal}">High</span> vs <span>low</span></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="note">"High" means the factor was in its top fifth of the last 10 years; "low", its bottom fifth. For yes/no factors (like an expiry day), high means yes.</p>
    <h2>What the model leans on most</h2>
    <div class="drivers">${imp.map((i) => `<div class="drv" style="grid-template-columns:minmax(180px,1.2fr) 2fr 70px">
      <span class="name">${esc(i.label)}</span>
      <span class="pos" style="border-left:0"><i style="width:${(i.value / maxImp) * 100}%;border-radius:3px"></i></span>
      <span class="eff muted">${(i.value * 100).toFixed(2)}</span></div>`).join("")}</div>
    <p class="note">Measured on test years only: how much prediction quality (AUC, in points ×100) drops when the factor is scrambled.</p>`;
}

/* ---------------- page: model check */
async function pageCheck() {
  if (!state.status || !state.status.model) { main.innerHTML = `<h1 class="page-title">Model check</h1>${noModel()}`; bindTrainButtons(); return; }
  const p = await api("/api/performance");
  const m = p.metrics.big_move, d = p.metrics.up_day;
  const years = Object.keys(m.by_year_auc);
  const btRows = (p.backtest || []).map((r) => `<tr>${Object.entries(r).map(([k, v], i) => `<td class="${i ? "r" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("");
  const btHead = p.backtest && p.backtest[0] ? Object.keys(p.backtest[0]).map((k, i) => `<th class="${i ? "r" : ""}">${esc(k)}</th>`).join("") : "";
  const good = m.auc >= 0.6 && m.lift_vs_random >= 1.5;
  main.innerHTML = `<h1 class="page-title">Model check</h1>
    <p class="lede">Every number here comes from days the model had never seen: it was trained only on earlier years each time. ${good ? "The big-move signal is clearly better than chance." : "The big-move signal is weak; treat flags with caution."}</p>
    ${demoBanner()}
    <div class="kpis">
      <div class="kpi"><b>${m.auc.toFixed(2)}</b><span>Big-move score (AUC). 0.50 is a coin flip.</span></div>
      <div class="kpi"><b>${pct0(m.precision_when_flagged)}</b><span>Flagged days that were big, vs ${pct0(m.base_rate)} on any day</span></div>
      <div class="kpi"><b>${m.lift_vs_random.toFixed(1)}×</b><span>Better odds than picking days at random</span></div>
      <div class="kpi"><b>${pct0(m.recall_of_actual_days)}</b><span>Of all big days, flagged the evening before</span></div>
    </div>
    <h2>Score by test year</h2>
    <div class="chart"><div id="chk-years" style="height:260px"></div></div>
    <p class="note">If recent years drop toward 0.50, the market has changed and the model needs new factors. Direction score: ${d.auc.toFixed(2)}, which is ${d.auc < 0.56 ? "close to a coin flip, as expected" : "better than usual; verify before trusting it"}.</p>
    <h2>Did buying options on flagged days pay?</h2>
    <p class="note" style="margin:0 0 12px">Proxy test: buy an at-the-money straddle at 9:15, sell at 3:30, paying the move India VIX priced in plus costs. Positive totals mean Bank Nifty moved more than options expected on those days. Confirm with real option prices before trading.</p>
    <div class="table-wrap"><table><thead><tr>${btHead}</tr></thead><tbody>${btRows}</tbody></table></div>
    <div class="chart" style="margin-top:14px"><div id="chk-eq" style="height:300px"></div></div>
    <p class="note"><a href="/report" target="_blank" rel="noopener">Open the full printable report</a></p>`;
  Plotly.newPlot("chk-years", [{
    x: years, y: years.map((y) => m.by_year_auc[y]), type: "bar",
    marker: { color: years.map((y) => (m.by_year_auc[y] >= 0.6 ? C.signal : "#A9B4BF")) },
    hovertemplate: "%{x}: %{y:.2f}<extra></extra>",
  }], plotLayout({ yaxis: { range: [0.4, 1], gridcolor: "#EEF1F4" }, shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0.5, y1: 0.5, line: { color: C.muted, dash: "dot", width: 1 } }] }), plotCfg);
  const e = p.equity;
  Plotly.newPlot("chk-eq", [
    { x: e.all_dates, y: e.all, type: "scattergl", mode: "lines", line: { color: "#A9B4BF", width: 1.4 }, name: "Every day" },
    { x: e.flag_dates, y: e.flag, type: "scattergl", mode: "lines", line: { color: C.signal, width: 2 }, name: "Flagged days only" },
  ], plotLayout({ showlegend: true, legend: { orientation: "h", y: 1.1 }, yaxis: { gridcolor: "#EEF1F4", ticksuffix: "%", title: { text: "Cumulative, % of spot" } } }), plotCfg);
}

/* ---------------- page: data & settings */
async function pageData() {
  const [s, st] = await Promise.all([api("/api/settings"), api("/api/status")]);
  state.status = st;
  const tr = st.training;
  const g = st.news.gdelt || { done: 0, total: 0 };
  const names = { banknifty: "Bank Nifty", nifty: "Nifty 50", india_vix: "India VIX", sp500: "S&P 500", nasdaq: "Nasdaq", us_vix: "US VIX", us10y: "US 10-year yield", dxy: "Dollar index", brent: "Brent crude", usdinr: "USD/INR", gold: "Gold", nikkei: "Nikkei 225", hangseng: "Hang Seng" };
  const dataRows = st.data.map((d) => `<tr><td>${names[d.name] || d.name}</td><td class="muted">${esc(d.symbol)}</td><td class="r">${d.days ? d.days.toLocaleString("en-IN") : "Not downloaded"}</td><td>${d.from ? shortDate(d.from) : "–"}</td><td>${d.to ? shortDate(d.to) : "–"}</td></tr>`).join("");
  const q = (v) => (v === "" || v == null ? "" : esc(v));
  main.innerHTML = `<h1 class="page-title">Data &amp; settings</h1>
    <p class="lede">Train the radar, add your own data, and choose when it runs.</p>

    <div class="panel">
      <h2 style="margin-top:0">Train the model</h2>
      <p class="muted" style="margin-top:0">${tr ? `Last trained ${dateTime(tr.finished_at)} on ${tr.days.toLocaleString("en-IN")} trading days (${shortDate(tr.data_start)} to ${shortDate(tr.data_end)}), ${tr.factors} factors, ${tr.news ? "with news" : "without news"}${tr.demo ? ", demo data" : ""}.` : "Not trained yet."}
      Retrain weekly to include new days.</p>
      <div class="btn-row">
        <button class="btn primary" data-train="real">Train model</button>
        <button class="btn" data-train="demo">Train on demo data</button>
      </div>
      <p class="note">Takes a few minutes. Training uses prices plus any news you've already downloaded below.</p>
    </div>

    <h2>Market data</h2>
    <div class="table-wrap"><table><thead><tr><th>Market</th><th>Symbol</th><th class="r">Days</th><th>From</th><th>To</th></tr></thead><tbody>${dataRows}</tbody></table></div>
    <p class="note">Daily prices from Yahoo Finance, downloaded when you train or update the forecast.</p>

    <h2>News and events</h2>
    <div class="panel">
      <div class="upload-row"><div><b>GDELT news tone</b><p>Daily mood and volume of news about RBI, Indian banks, the market, inflation, the Fed and war or terror, from 2017.
        <b>${g.done} of ${g.total}</b> topic-years downloaded${g.last_note ? `. Last run: ${esc(g.last_note)}` : ""}.
        The free GDELT server often limits requests, so this can take several tries. Each try keeps what it downloaded.</p>
        <div class="meter" style="max-width:360px"><i class="on" style="width:${g.total ? (g.done / g.total) * 100 : 0}%"></i></div></div>
        <div class="btn-row"><label class="check"><input type="checkbox" id="s-gdelt" ${s["news.use_gdelt"] ? "checked" : ""}> Use news</label>
        <button class="btn" type="button" data-news ${g.done >= g.total && g.total ? "disabled" : ""}>${g.done ? "Continue download" : "Download news"}</button></div></div>
      <div class="upload-row"><div><b>Headlines file</b><p>Optional. The India News Headlines dataset from Kaggle (columns publish_date, headline_category, headline_text) adds headline sentiment before 2017. ${st.news.headlines_file ? `Loaded, ${st.news.headlines_mb} MB.` : "Not added."}</p></div>
        <label class="btn">Choose CSV<input type="file" accept=".csv" data-upload="headlines" hidden></label></div>
      <div class="upload-row"><div><b>Your events</b><p>Optional CSV with columns date and event, for example <i>2026-12-05, rbi_policy</i>. Each event name becomes a factor. ${st.news.events_file ? `${st.news.events_rows} events loaded.` : "Not added."}</p></div>
        <label class="btn">Choose CSV<input type="file" accept=".csv" data-upload="events" hidden></label></div>
      <p class="note">Retrain after adding files so the model learns from them.</p>
    </div>

    <h2>Settings</h2>
    <form class="panel" id="settings-form">
      <div class="form-grid">
        <label class="check"><input type="checkbox" id="s-sched" ${s["schedule.enabled"] ? "checked" : ""}><span>Update the forecast automatically<small>Monday to Friday, while this app is open.</small></span></label>
        <label class="field">Time<input type="time" id="s-time" value="${q(s["schedule.time"])}"><small>After 3:30 PM, once the day's prices are final. 6:30 PM works well.</small></label>
        <label class="check"><input type="checkbox" id="s-retrain" ${s["schedule.weekly_retrain"] ? "checked" : ""}><span>Retrain every Saturday<small>Keeps the model current with the latest week.</small></span></label>
        <span></span>
        <label class="field">What counts as a big day
          <select id="s-q">${[0.7, 0.75, 0.8, 0.85, 0.9].map((v) => `<option value="${v}" ${Math.abs(s["labels.big_move_quantile"] - v) < 1e-9 ? "selected" : ""}>Range in the top ${Math.round((1 - v) * 100)}% of the past year</option>`).join("")}</select>
          <small>Changes take effect after retraining.</small></label>
        <label class="field">How many days to flag
          <select id="s-flag">${[0.15, 0.2, 0.25, 0.3, 0.4].map((v) => `<option value="${v}" ${Math.abs(s["model.flag_rate"] - v) < 1e-9 ? "selected" : ""}>About ${Math.round(v * 100)}% of days</option>`).join("")}</select>
          <small>Fewer flags means higher-conviction days. Retrain to apply.</small></label>
        <label class="field">Telegram bot token<input type="password" id="s-tok" value="${q(s["telegram.bot_token"])}" autocomplete="off"><small>Create a bot with @BotFather to get a token.</small></label>
        <label class="field">Telegram chat ID<input type="text" id="s-chat" value="${q(s["telegram.chat_id"])}"><small>Your user or group ID. Each forecast is sent here.</small></label>
      </div>
      <div class="btn-row" style="margin-top:20px"><button class="btn primary" type="submit">Save settings</button><button class="btn" type="button" id="tg-test">Send test message</button></div>
    </form>`;
  bindTrainButtons();
  main.querySelectorAll("[data-upload]").forEach((inp) => inp.addEventListener("change", async () => {
    const file = inp.files[0]; if (!file) return;
    toast(`Uploading ${file.name}…`);
    try {
      const r = await api(`/api/upload/${inp.dataset.upload}`, { method: "POST", body: file });
      toast(`Added ${file.name} (${r.mb} MB). Retrain to use it.`); pageData();
    } catch (e) { toast(e.message); }
  }));
  const save = async () => {
    try {
      await post("/api/settings", {
        "schedule.enabled": $("#s-sched").checked, "schedule.time": $("#s-time").value || "18:30",
        "schedule.weekly_retrain": $("#s-retrain").checked, "news.use_gdelt": $("#s-gdelt").checked,
        "labels.big_move_quantile": Number($("#s-q").value), "model.flag_rate": Number($("#s-flag").value),
        "telegram.bot_token": $("#s-tok").value.trim(), "telegram.chat_id": $("#s-chat").value.trim(),
      });
      toast("Settings saved"); loadStatus(); return true;
    } catch (e) { toast(e.message); return false; }
  };
  $("#settings-form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
  $("#s-gdelt").addEventListener("change", save);
  $("#tg-test").addEventListener("click", async () => {
    if (!(await save())) return;
    try { await post("/api/telegram/test", {}); toast("Test message sent"); } catch (e) { toast(e.message); }
  });
}

/* ---------------- day drawer */
async function openDay(date) {
  const drawer = $("#drawer"), body = $("#drawer-body");
  body.innerHTML = `<p class="muted">Loading ${shortDate(date)}…</p>`;
  drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false");
  $(".drawer-close").focus();
  let d;
  try { d = await api(`/api/day/${date}`); } catch (e) { body.innerHTML = `<p>${esc(e.message)}</p>`; return; }
  const kv = (rows, fmt) => rows.length ? `<table class="kv">${rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${fmt(r)}</td></tr>`).join("")}</table>` : `<p class="muted small">Not available for this day.</p>`;
  const cueFmt = (r) => fmtFeature(r.feature, r.value);
  body.innerHTML = `
    <p class="muted small" style="margin:0">${d.big ? "Big-move day" : "Ordinary day"}</p>
    <h1 class="day-title" id="drawer-title">${longDate(d.date)}</h1>
    ${d.outcome ? `<p style="margin:0">${badge(d.outcome)} <span class="muted small">Model chance the evening before: ${pct0(d.prob)}</span></p>` : `<p class="muted small" style="margin:0">Before the first test year, so no out-of-sample prediction.</p>`}
    <div class="stat-row">
      <div><span>Range</span><b>${pct(d.range_pct, 2)}</b></div>
      <div><span>Open to close</span><b class="${d.oc_pct >= 0 ? "up" : "down"}">${sgn(d.oc_pct)}</b></div>
      <div><span>Gap</span><b class="${d.gap_pct >= 0 ? "up" : "down"}">${sgn(d.gap_pct)}</b></div>
      <div><span>Open</span><b>${pts(d.open)}</b></div>
      <div><span>High / low</span><b>${pts(d.high)} / ${pts(d.low)}</b></div>
      <div><span>Big-day bar</span><b>${pct(d.threshold, 2)}</b></div>
    </div>
    <h2>What pushed the chance up or down</h2>
    ${d.drivers.length ? driversHTML(d.drivers.slice(0, 6)) : `<p class="muted small">Not available.</p>`}
    <h2>Market the evening before</h2>
    ${kv(d.cues, cueFmt)}
    <h2>News mood the day before</h2>
    ${kv(d.news, (r) => fmtNum(r.value))}
    ${d.headlines.length ? `<h2>Headlines</h2><ul class="heads">${d.headlines.map((x) => `<li><span class="${x.score > 0 ? "up" : x.score < 0 ? "down" : "muted"}">${x.score > 0 ? "▲" : x.score < 0 ? "▼" : "•"}</span> ${esc(x.text)} <span class="muted small">${shortDate(x.date)}</span></li>`).join("")}</ul>` : ""}`;
  body.querySelectorAll(".drv").forEach((r) => (r.style.paddingLeft = r.style.paddingRight = "0"));
  body.querySelectorAll(".drv-legend").forEach((r) => (r.style.paddingLeft = r.style.paddingRight = "0"));
  body.querySelectorAll(".drivers").forEach((r) => (r.style.border = "0"));
}
function closeDrawer() { const dr = $("#drawer"); dr.classList.remove("open"); dr.setAttribute("aria-hidden", "true"); }
$("#drawer").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

/* ---------------- router */
const PAGES = { tomorrow: pageTomorrow, history: pageHistory, biggest: pageBiggest, drivers: pageDrivers, check: pageCheck, data: pageData };
async function route() {
  const page = (location.hash.replace("#/", "") || "live").split("?")[0];
  const fn = PAGES[page] || PAGES.live || pageTomorrow;
  document.querySelectorAll(".rail a[data-page]").forEach((a) => { if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  if (state.page !== page) { main.innerHTML = `<p class="muted">Loading…</p>`; window.scrollTo(0, 0); }
  state.page = page;
  try { await fn(); } catch (e) { main.innerHTML = `<div class="empty"><h2>This page couldn't load</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">Reload</button></div>`; }
}
window.addEventListener("hashchange", () => { route(); main.focus({ preventScroll: true }); });
document.addEventListener("DOMContentLoaded", async () => { await loadStatus(); loadVersion(); await route(); setInterval(loadStatus, 30000); setInterval(loadVersion, 15 * 60000); });
