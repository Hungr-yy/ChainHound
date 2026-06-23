"use strict";
/* ChainHound investigation canvas.
 *
 * An analyst-driven, incremental graph-building UI over the query API: search a
 * seed txid to plot a trace, click nodes to triage / peel / measure exposure,
 * mark up the graph (color / hide / note) for hygiene, and save / load the work
 * as a case. Everything is vanilla JS + Cytoscape — no build step.
 */

const $ = (sel) => document.querySelector(sel);
const BANDS = ["Near Certainty", "High", "Moderate", "Low"];
const BAND_VAR = {
  "Near Certainty": "--b-near",
  High: "--b-high",
  Moderate: "--b-mod",
  Low: "--b-low",
};

// --- tiny API client ---------------------------------------------------------
async function api(path, opts) {
  const resp = await fetch(path, opts);
  let body = null;
  try {
    body = await resp.json();
  } catch (_) {
    /* empty body (e.g. 204) */
  }
  if (!resp.ok) {
    const detail = (body && body.detail) || resp.statusText;
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return body;
}

function setStatus(msg, isErr) {
  const el = $("#status");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}

function looksLikeTxid(s) {
  return /^(0x)?[0-9a-fA-F]{64}$/.test(s.trim());
}

// --- graph state -------------------------------------------------------------
let cy;
let currentCaseId = null;
const transfers = []; // {from, to, value, role, confidence}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function bandColor(band) {
  return cssVar(BAND_VAR[band] || "--b-pay") || "#888";
}

function initCy() {
  cy = cytoscape({
    container: $("#cy"),
    wheelSensitivity: 0.2,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)",
          "font-size": 9,
          color: "#cfd6e0",
          "text-valign": "bottom",
          "text-margin-y": 3,
          width: 26,
          height: 26,
        },
      },
      // node kind shapes; hygiene color (data.color) overrides the default fill.
      { selector: 'node[kind="tx"]', style: { shape: "round-rectangle", "background-color": "#7d8590" } },
      { selector: 'node[kind="address"]', style: { shape: "ellipse", "background-color": "#39506e" } },
      { selector: "node[color]", style: { "background-color": "data(color)" } },
      {
        selector: "edge",
        style: {
          width: 2,
          "line-color": "data(color)",
          "target-arrow-color": "data(color)",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "arrow-scale": 0.8,
        },
      },
      { selector: 'edge[role="payment"]', style: { "line-style": "dashed" } },
      { selector: ".sel", style: { "border-width": 3, "border-color": "#e6e9ee" } },
      { selector: ".hidden", style: { display: "none" } },
    ],
  });

  cy.on("tap", "node", (evt) => selectElement(evt.target));
  cy.on("tap", "edge", (evt) => selectElement(evt.target));
  cy.on("dbltap", 'node[kind="tx"]', (evt) => traceTxid(evt.target.id(), hopsValue()));
  cy.on("tap", (evt) => {
    if (evt.target === cy) clearSelection();
  });
}

function hopsValue() {
  return Math.max(0, parseInt($("#hops").value, 10) || 0);
}

function relayout() {
  cy.layout({ name: "cose", animate: false, padding: 30, nodeRepulsion: 8000 }).run();
}

// --- trace -> graph ----------------------------------------------------------
async function traceTxid(txid, hops) {
  setStatus("tracing…");
  try {
    const g = await api(`/trace?txid=${encodeURIComponent(txid)}&hops=${hops}`);
    mergeGraph(g);
    setStatus(`traced ${txid.slice(0, 10)}… (${g.nodes.length} nodes)`);
  } catch (e) {
    setStatus(e.message, true);
  }
}

function mergeGraph(g) {
  cy.batch(() => {
    for (const n of g.nodes) {
      if (cy.getElementById(n.id).empty()) {
        cy.add({ group: "nodes", data: { id: n.id, label: n.label || n.id.slice(0, 8), kind: n.kind } });
      }
    }
    for (const e of g.edges) {
      const id = `${e.src}->${e.dst}#${e.role}`;
      if (cy.getElementById(id).empty()) {
        const color = e.role === "change" ? bandColor(e.confidence) : bandColor("payment");
        cy.add({
          group: "edges",
          data: { id, source: e.src, target: e.dst, role: e.role, color, value: e.value, confidence: e.confidence || "" },
        });
        transfers.push({ from: e.src, to: e.dst, value: e.value, role: e.role, confidence: e.confidence || "" });
      }
    }
  });
  renderTransfers();
  relayout();
}

// --- transfer table ----------------------------------------------------------
function renderTransfers() {
  const q = $("#tx-filter").value.toLowerCase();
  const rows = transfers
    .filter((t) =>
      !q ||
      `${t.from} ${t.to} ${t.value} ${t.role} ${t.confidence}`.toLowerCase().includes(q)
    )
    .map(
      (t) =>
        `<tr><td class="mono">${t.from}</td><td class="mono">${t.to}</td>` +
        `<td>${t.value}</td><td>${t.role}</td>` +
        `<td class="band" data-band="${t.confidence}">${t.confidence || "—"}</td></tr>`
    );
  $("#tx-table tbody").innerHTML = rows.join("") || '<tr><td colspan="5" class="muted">no transfers yet</td></tr>';
}

// --- selection + details -----------------------------------------------------
let selected = null;

function clearSelection() {
  cy.elements().removeClass("sel");
  selected = null;
  $("#hygiene").classList.add("hidden");
}

function selectElement(ele) {
  cy.elements().removeClass("sel");
  ele.addClass("sel");
  selected = ele;
  loadHygieneControls(ele);
  if (ele.isNode()) {
    const kind = ele.data("kind");
    if (kind === "address") showAddressDetails(ele.id());
    else showTxDetails(ele.id());
  } else {
    showEdgeDetails(ele);
  }
}

function kv(pairs) {
  return (
    '<dl class="kv">' +
    pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("") +
    "</dl>"
  );
}

function btn(label, id) {
  return `<button data-act="${id}">${label}</button>`;
}

function showTxDetails(txid) {
  const d = $("#details");
  d.innerHTML =
    "<h2>Transaction</h2>" +
    `<p class="mono">${txid}</p>` +
    `<div class="actions">${btn("Trace deeper", "trace")}${btn("Peel chain", "peel")}</div>` +
    '<div id="extra"></div>';
  d.querySelector('[data-act="trace"]').onclick = () => traceTxid(txid, hopsValue());
  d.querySelector('[data-act="peel"]').onclick = () => showPeel(txid);
}

async function showAddressDetails(address) {
  const d = $("#details");
  d.innerHTML = `<h2>Address</h2><p class="mono">${address}</p><p class="muted">triaging…</p>`;
  try {
    const t = await api(`/triage?address=${encodeURIComponent(address)}&chain=bitcoin`);
    const flags = (t.service_flags || []).map((f) => `<span class="flag">${f}</span>`).join("") || "—";
    d.innerHTML =
      "<h2>Address</h2>" +
      `<p class="mono">${address}</p>` +
      kv([
        ["type", t.address_type || "—"],
        ["balance", t.balance],
        ["received", t.total_received],
        ["sent", t.total_sent],
        ["tx count", t.tx_count],
        ["first seen", t.first_seen || "—"],
        ["last seen", t.last_seen || "—"],
        ["labels", (t.labels || []).join(", ") || "—"],
        ["flags", flags],
      ]) +
      `<div class="actions">${btn("Exposure rings", "expo")}${btn("Watch", "watch")}</div>` +
      '<div id="extra"></div>';
    d.querySelector('[data-act="expo"]').onclick = () => showExposure(address);
    d.querySelector('[data-act="watch"]').onclick = () => watchAddress(address);
  } catch (e) {
    if (e.status === 404) d.querySelector(".muted").textContent = "address not found on-chain";
    else setStatus(e.message, true);
  }
}

function showEdgeDetails(edge) {
  $("#details").innerHTML =
    "<h2>Transfer</h2>" +
    kv([
      ["from", `<span class="mono">${edge.data("source")}</span>`],
      ["to", `<span class="mono">${edge.data("target")}</span>`],
      ["value", edge.data("value")],
      ["role", edge.data("role")],
      [
        "confidence",
        `<span class="band" data-band="${edge.data("confidence")}">${edge.data("confidence") || "—"}</span>`,
      ],
    ]);
}

async function showPeel(txid) {
  const extra = $("#extra");
  if (extra) extra.innerHTML = '<p class="muted">following the peel chain…</p>';
  try {
    const p = await api(`/peel?txid=${encodeURIComponent(txid)}`);
    const cash = p.cash_out ? `${p.cash_out.address || "?"} (${p.cash_out.value})` : "—";
    if (extra)
      extra.innerHTML =
        "<h2>Peel chain</h2>" +
        kv([
          ["is peel chain", p.is_peel_chain],
          ["length", p.length],
          ["cash-out", `<span class="mono">${cash}</span>`],
        ]);
  } catch (e) {
    setStatus(e.message, true);
  }
}

async function showExposure(address) {
  const extra = $("#extra");
  if (extra) extra.innerHTML = '<p class="muted">computing exposure…</p>';
  try {
    const r = await api(`/exposure?address=${encodeURIComponent(address)}&chain=bitcoin&hops=${hopsValue() || 1}`);
    const rings = (r.rings || [])
      .map(
        (ring) =>
          `<div class="ring"><span>${ring.category} · ${ring.direction}</span>` +
          `<span class="band" data-band="${ring.confidence}">${ring.value}</span></div>`
      )
      .join("");
    if (extra)
      extra.innerHTML =
        "<h2>Exposure rings</h2>" + (rings || '<p class="muted">no labeled exposure found</p>');
  } catch (e) {
    if (e.status === 503 && extra)
      extra.innerHTML = '<p class="muted">exposure needs the label corpus (DATABASE_URL).</p>';
    else setStatus(e.message, true);
  }
}

async function watchAddress(address) {
  try {
    const w = await api("/watches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chain: "bitcoin", address, case_id: currentCaseId }),
    });
    setStatus(`watching ${address.slice(0, 10)}… (watch #${w.id})`);
  } catch (e) {
    if (e.status === 503) setStatus("monitoring needs a database (DATABASE_URL)", true);
    else setStatus(e.message, true);
  }
}

// --- graph hygiene -----------------------------------------------------------
function loadHygieneControls(ele) {
  $("#hygiene").classList.remove("hidden");
  $("#hygiene .sel-id").textContent = ele.id();
  $("#hy-color").value = toHex(ele.data("color")) || "#888888";
  $("#hy-hidden").checked = ele.hasClass("hidden");
  $("#hy-note").value = ele.data("note") || "";
}

function toHex(v) {
  if (!v) return null;
  if (v.startsWith("#")) return v;
  return null;
}

function applyHygiene() {
  if (!selected) return;
  const color = $("#hy-color").value;
  const hidden = $("#hy-hidden").checked;
  const note = $("#hy-note").value;
  selected.data("color", color);
  selected.data("note", note);
  selected.toggleClass("hidden", hidden);
  setStatus(`hygiene set on ${selected.id().slice(0, 10)}…`);
}

// --- cases (save / load) -----------------------------------------------------
async function refreshCases() {
  try {
    const cases = await api("/cases");
    const sel = $("#case-select");
    sel.innerHTML =
      '<option value="">— cases —</option>' +
      cases.map((c) => `<option value="${c.case_id}">#${c.case_id} ${escapeHtml(c.name)}</option>`).join("");
    if (currentCaseId) sel.value = String(currentCaseId);
  } catch (e) {
    // 503 without a database is expected; persistence is simply unavailable.
    if (e.status === 503) setStatus("cases unavailable (no DATABASE_URL)");
  }
}

async function newCase() {
  const name = prompt("Case name:");
  if (!name) return;
  try {
    const c = await api("/cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    currentCaseId = c.case_id;
    setActiveCase(c);
    await refreshCases();
    setStatus(`case #${c.case_id} created`);
  } catch (e) {
    setStatus(e.message, true);
  }
}

function setActiveCase(c) {
  $("#case-label").textContent = c ? `case #${c.case_id}: ${c.name}` : "";
  $("#save-case").disabled = !c;
  $("#export-case").disabled = !c;
}

function exportCase() {
  if (!currentCaseId) return;
  // Streams the attachment (raw on-chain only) straight to a download.
  window.open(`/cases/${currentCaseId}/export`, "_blank");
}

async function saveCase() {
  if (!currentCaseId) return;
  setStatus("saving hygiene…");
  try {
    const marked = cy.elements().filter((e) => e.data("color") || e.data("note") || e.hasClass("hidden"));
    for (const ele of marked) {
      await api(`/cases/${currentCaseId}/elements`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          element_id: ele.id(),
          color: ele.data("color") || null,
          hidden: ele.hasClass("hidden"),
          note: ele.data("note") || null,
        }),
      });
    }
    setStatus(`saved ${marked.length} element(s) to case #${currentCaseId}`);
  } catch (e) {
    setStatus(e.message, true);
  }
}

async function loadCase(caseId) {
  if (!caseId) return;
  try {
    const c = await api(`/cases/${caseId}`);
    currentCaseId = c.case_id;
    setActiveCase(c);
    cy.batch(() => {
      for (const el of c.elements || []) {
        const node = cy.getElementById(el.element_id);
        if (node.empty()) continue; // hygiene applies to elements already on the canvas
        if (el.color) node.data("color", el.color);
        if (el.note) node.data("note", el.note);
        node.toggleClass("hidden", !!el.hidden);
      }
    });
    setStatus(`loaded case #${c.case_id} (${(c.elements || []).length} hygiene rows)`);
  } catch (e) {
    setStatus(e.message, true);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// --- wire up -----------------------------------------------------------------
function search() {
  const v = $("#search").value.trim();
  if (!v) return;
  if (looksLikeTxid(v)) traceTxid(v, hopsValue());
  else showAddressNode(v);
}

function showAddressNode(address) {
  if (cy.getElementById(address).empty()) {
    cy.add({ group: "nodes", data: { id: address, label: address.slice(0, 10), kind: "address" } });
    relayout();
  }
  selectElement(cy.getElementById(address));
}

window.addEventListener("DOMContentLoaded", () => {
  initCy();
  $("#go").onclick = search;
  $("#search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") search();
  });
  $("#tx-filter").addEventListener("input", renderTransfers);
  $("#hy-apply").onclick = applyHygiene;
  $("#new-case").onclick = newCase;
  $("#save-case").onclick = saveCase;
  $("#export-case").onclick = exportCase;
  $("#case-select").addEventListener("change", (e) => loadCase(e.target.value));
  renderTransfers();
  refreshCases();
});
