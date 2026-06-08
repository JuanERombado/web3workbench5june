const state = {
  runs: [],
  hypotheses: [],
  executions: [],
  profiles: {},
  statuses: [],
  known_sources: [],
  known_source_types: [],
  known_matches: [],
  run_overview: {},
  last_packet: {},
};

const $ = (selector) => document.querySelector(selector);
const currentRun = $("#current-run");
const messages = $("#messages");

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

async function load() {
  const query = currentRun.value ? `?run=${encodeURIComponent(currentRun.value)}` : "";
  const payload = await api(`/api/bootstrap${query}`);
  Object.assign(state, payload);
  render();
}

function render() {
  renderRuns();
  renderOverview();
  renderProfiles();
  renderStatuses();
  renderKnownTypes();
  renderExecutions();
  renderHypotheses();
  renderKnownSources();
  renderKnownResults();
}

function renderRuns() {
  $("#runs-body").innerHTML = state.runs.map((run) => `
    <tr>
      <td>${escapeHtml(run.target_name)}</td>
      <td><a href="${escapeAttr(run.program_url)}" target="_blank" rel="noreferrer">${escapeHtml(run.program_url)}</a></td>
      <td>${escapeHtml(run.created_at)}</td>
      <td>${run.hypothesis_count}</td>
      <td>${escapeHtml(run.latest_status)}</td>
      <td class="actions">
        <button type="button" data-select-run="${escapeAttr(run.run_path)}">Open Run</button>
        <button type="button" data-prepare-run="${escapeAttr(run.run_path)}">Prepare Packet</button>
      </td>
    </tr>
  `).join("");
  document.querySelectorAll("[data-select-run]").forEach((button) => {
    button.addEventListener("click", async () => {
      currentRun.value = button.dataset.selectRun;
      await load();
      showTab("dashboard");
    });
  });
  document.querySelectorAll("[data-prepare-run]").forEach((button) => {
    button.addEventListener("click", async () => {
      currentRun.value = button.dataset.prepareRun;
      await prepareIntel();
    });
  });
}

function renderOverview() {
  const overview = state.run_overview || {};
  if (!overview.run_path) {
    $("#overview-body").innerHTML = `<div class="muted-box">Select a run to see the overview.</div>`;
    return;
  }
  const statuses = Object.entries(overview.status_counts || {})
    .map(([status, count]) => `${escapeHtml(status)}: ${count}`)
    .join("<br>") || "None";
  $("#overview-body").innerHTML = `
    <div><strong>Target</strong><span>${escapeHtml(overview.target_name)}</span></div>
    <div><strong>Program URL</strong><span>${escapeHtml(overview.program_url)}</span></div>
    <div><strong>Run Path</strong><span>${escapeHtml(overview.run_path)}</span></div>
    <div><strong>Hypotheses</strong><span>${overview.hypothesis_count || 0}<br>${statuses}</span></div>
    <div><strong>Known Sources</strong><span>${overview.known_source_count || 0}</span></div>
    <div><strong>Latest Scan</strong><span>${escapeHtml(overview.latest_scan_summary || "No scans recorded.")}</span></div>
  `;
}

function renderProfiles() {
  const names = Object.keys(state.profiles || {});
  $("#profile-select").innerHTML = `<option value="">default/generic</option>${names.map((name) => `<option>${escapeHtml(name)}</option>`).join("")}`;
}

function renderStatuses() {
  $("#status-select").innerHTML = state.statuses.map((status) => `<option>${escapeHtml(status)}</option>`).join("");
}

function renderExecutions() {
  $("#executions-body").innerHTML = state.executions.map((row) => `
    <tr>
      <td>${escapeHtml(row.tool)}</td>
      <td>${escapeHtml(row.command)}</td>
      <td>${row.exit_code}</td>
      <td>${escapeHtml(row.parsed_summary)}</td>
      <td><button type="button" data-view-file="${escapeAttr(row.stdout_path)}">stdout</button></td>
      <td><button type="button" data-view-file="${escapeAttr(row.stderr_path)}">stderr</button></td>
      <td><button type="button" data-view-file="${escapeAttr(executionJsonPath(row.stdout_path))}">json</button></td>
      <td>${escapeHtml(row.start_time)}</td>
    </tr>
  `).join("");
  bindFileButtons();
}

function executionJsonPath(stdoutPath) {
  const normalized = String(stdoutPath || "").replaceAll("\\", "/");
  const folder = normalized.split("/").slice(0, -1).join("/");
  return `${folder}/execution.json`;
}

function renderHypotheses() {
  $("#hypotheses-body").innerHTML = state.hypotheses.map((row) => `
    <tr>
      <td>${escapeHtml(row.id)}</td>
      <td>${escapeHtml(row.title)}</td>
      <td>${escapeHtml(row.status)}</td>
      <td>${escapeHtml(row.poc_status)}</td>
      <td>${escapeHtml(row.validation_status)}</td>
      <td>${escapeHtml(row.gate_decision)}</td>
      <td>${escapeHtml(row.next_action)}</td>
      <td class="actions">
        <button type="button" data-gate="${escapeAttr(row.id)}">Gate</button>
        <button type="button" data-close="${escapeAttr(row.id)}">Close</button>
        <button type="button" data-check-known="${escapeAttr(row.id)}">Check Known</button>
        <button type="button" data-view-file="${escapeAttr(currentRun.value + "/hypotheses/" + row.id + ".md")}">MD</button>
      </td>
    </tr>
  `).join("");
  document.querySelectorAll("[data-gate]").forEach((button) => {
    button.addEventListener("click", () => gateHypothesis(button.dataset.gate));
  });
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => closeHypothesis(button.dataset.close));
  });
  document.querySelectorAll("[data-check-known]").forEach((button) => {
    button.addEventListener("click", () => checkKnown(button.dataset.checkKnown));
  });
  bindFileButtons();
}

function renderKnownTypes() {
  document.querySelectorAll(".known-type-select").forEach((select) => {
    select.innerHTML = state.known_source_types.map((kind) => `<option>${escapeHtml(kind)}</option>`).join("");
  });
}

function renderKnownSources() {
  $("#known-sources-body").innerHTML = state.known_sources.map((source) => `
    <tr>
      <td>${source.id}</td>
      <td>${escapeHtml(source.title)}</td>
      <td>${escapeHtml(source.source_type)}</td>
      <td>${escapeHtml(source.fetch_status || "")}</td>
      <td>${escapeHtml(source.url || source.file_path || "")}</td>
      <td>${source.chunk_count || 0}</td>
      <td>${escapeHtml(source.fetched_at)}</td>
    </tr>
  `).join("");
}

function renderKnownResults() {
  $("#known-results-body").innerHTML = state.known_matches.map((match) => `
    <tr>
      <td>${escapeHtml(match.title)}</td>
      <td>${escapeHtml(match.source_type)}</td>
      <td>${escapeHtml(match.confidence)}</td>
      <td>${escapeHtml(match.recommendation)}</td>
      <td>${escapeHtml(match.snippet)}</td>
      <td class="actions">
        <button type="button" data-link-known="${match.source_id}">Link</button>
      </td>
    </tr>
  `).join("");
  document.querySelectorAll("[data-link-known]").forEach((button) => {
    button.addEventListener("click", () => linkKnown(Number(button.dataset.linkKnown)));
  });
}

function bindFileButtons() {
  document.querySelectorAll("[data-view-file]").forEach((button) => {
    button.addEventListener("click", () => viewFile(button.dataset.viewFile));
  });
}

async function viewFile(path) {
  const text = await api(`/api/file?path=${encodeURIComponent(path)}`);
  $("#file-title").textContent = path;
  $("#file-content").textContent = text;
  $("#file-dialog").showModal();
}

async function postJson(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function requireRun() {
  const run = currentRun.value.trim();
  if (!run) throw new Error("Select or create a run first.");
  return run;
}

function say(message) {
  messages.textContent = typeof message === "string" ? message : JSON.stringify(message, null, 2);
}

function fail(error) {
  messages.textContent = error.message || String(error);
}

function showTab(id) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.id === id));
  document.querySelectorAll(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.tab === id));
}

document.querySelectorAll(".nav button").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});
document.querySelectorAll("[data-tab-jump]").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tabJump));
});

$("#refresh-all").addEventListener("click", () => load().catch(fail));
$("#dashboard-refresh").addEventListener("click", () => load().catch(fail));
$("#dashboard-prepare-intel").addEventListener("click", () => prepareIntel().catch(fail));
$("#dashboard-open-folder").addEventListener("click", async () => {
  try {
    await postJson("/api/open-path", { path: requireRun() });
  } catch (error) {
    fail(error);
  }
});

$("#new-target-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = new FormData(event.currentTarget);
    const payload = await api("/api/runs", { method: "POST", body: form });
    currentRun.value = payload.run;
    say(payload);
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#ingest-run").addEventListener("click", async () => {
  try {
    say(await postJson("/api/ingest", { run: requireRun() }));
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#scope-run").addEventListener("click", async () => {
  try {
    say(await postJson("/api/scope", { run: requireRun() }));
    await loadScope();
  } catch (error) {
    fail(error);
  }
});

$("#run-doctor").addEventListener("click", async () => {
  try {
    const result = await postJson("/api/doctor", { run: requireRun() });
    $("#doctor-body").innerHTML = Object.entries(result).map(([tool, info]) => `
      <tr>
        <td>${escapeHtml(tool)}</td>
        <td>${info.detected ? "yes" : "no"}</td>
        <td>${escapeHtml(info.version)}</td>
        <td>${escapeHtml(info.path)}</td>
        <td>${escapeHtml(info.install_hint)}</td>
      </tr>
    `).join("");
  } catch (error) {
    fail(error);
  }
});

async function loadScope() {
  const text = await api(`/api/scope?run=${encodeURIComponent(requireRun())}`);
  $("#scope-editor").value = text;
}

$("#load-scope").addEventListener("click", () => loadScope().catch(fail));
$("#save-scope").addEventListener("click", async () => {
  try {
    say(await api("/api/scope", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run: requireRun(), content: $("#scope-editor").value }),
    }));
  } catch (error) {
    fail(error);
  }
});

$("#scan-generic").addEventListener("click", () => runScan({ run: requireRun() }));
$("#scan-profile").addEventListener("click", () => runScan({ run: requireRun(), profile: $("#profile-select").value }));
$("#scan-all").addEventListener("click", () => runScan({ run: requireRun(), all_profiles: true }));
$("#refresh-executions").addEventListener("click", () => load().catch(fail));

async function runScan(payload) {
  try {
    say(await postJson("/api/scan", payload));
    await load();
  } catch (error) {
    fail(error);
  }
}

$("#hypothesis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    say(await postJson("/api/hypotheses", { run: requireRun(), ...values }));
    event.currentTarget.reset();
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    say(await postJson("/api/import-leads", { run: requireRun(), file_path: values.file_path }));
    await load();
  } catch (error) {
    fail(error);
  }
});

async function gateHypothesis(id) {
  const decision = prompt(`Gate decision for ${id}`);
  if (decision === null) return;
  const notes = prompt("Gate notes") || "";
  try {
    say(await postJson("/api/gate-hypothesis", { run: requireRun(), hypothesis_id: id, decision, notes }));
    await load();
  } catch (error) {
    fail(error);
  }
}

async function closeHypothesis(id) {
  const status = prompt(`Close status for ${id}`, "Rejected - No Impact");
  if (status === null) return;
  const reason = prompt("Reason");
  if (reason === null) return;
  try {
    say(await postJson("/api/close-hypothesis", { run: requireRun(), hypothesis_id: id, status, reason }));
    await load();
  } catch (error) {
    fail(error);
  }
}

$("#export-tracker").addEventListener("click", async () => {
  try {
    say(await postJson("/api/export", { run: requireRun() }));
  } catch (error) {
    fail(error);
  }
});

$("#known-refresh").addEventListener("click", () => load().catch(fail));
$("#known-prepare-intel").addEventListener("click", () => prepareIntel().catch(fail));
$("#known-export").addEventListener("click", async () => {
  try {
    say(await postJson("/api/known/export", { run: requireRun() }));
    await load();
  } catch (error) {
    fail(error);
  }
});
$("#known-intel").addEventListener("click", async () => {
  try {
    say(await postJson("/api/known/intel", { run: requireRun() }));
  } catch (error) {
    fail(error);
  }
});
$("#known-dedupe").addEventListener("click", async () => {
  try {
    say(await postJson("/api/known/dedupe", { run: requireRun() }));
    await load();
  } catch (error) {
    fail(error);
  }
});
$("#known-seed-axelar").addEventListener("click", async () => {
  try {
    say(await postJson("/api/known/seed-axelar", { run: requireRun() }));
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#known-url-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    say(await postJson("/api/known/url", { run: requireRun(), ...values }));
    event.currentTarget.reset();
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#known-file-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    say(await postJson("/api/known/file", { run: requireRun(), ...values }));
    event.currentTarget.reset();
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#known-manual-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    say(await postJson("/api/known/manual", { run: requireRun(), ...values }));
    event.currentTarget.reset();
    await load();
  } catch (error) {
    fail(error);
  }
});

$("#known-search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const result = await postJson("/api/known/search", { run: requireRun(), query: values.query });
    state.known_matches = result.matches;
    renderKnownResults();
    say(`${result.matches.length} known issue matches.`);
  } catch (error) {
    fail(error);
  }
});

async function checkKnown(id) {
  try {
    const result = await postJson("/api/known/check", { run: requireRun(), hypothesis_id: id });
    state.known_matches = result.matches;
    renderKnownResults();
    showTab("known");
    say(result);
    await load();
  } catch (error) {
    fail(error);
  }
}

async function linkKnown(sourceId) {
  const hypothesisId = prompt("Hypothesis ID to link");
  if (!hypothesisId) return;
  const notes = prompt("Link notes") || "";
  try {
    say(await postJson("/api/known/link", { run: requireRun(), hypothesis_id: hypothesisId, source_id: sourceId, notes }));
  } catch (error) {
    fail(error);
  }
}

$("#prepare-intel").addEventListener("click", () => prepareIntel().catch(fail));
$("#export-packet").addEventListener("click", () => exportPacket().catch(fail));
$("#open-packet-folder").addEventListener("click", async () => {
  try {
    const folder = state.last_packet.review_packet || requireRun() + "/review_packet";
    await postJson("/api/open-path", { path: folder });
  } catch (error) {
    fail(error);
  }
});

async function prepareIntel() {
  const run = requireRun();
  say("Preparing intelligence packet...");
  const result = await postJson(`/api/prepare-intel?run=${encodeURIComponent(run)}`, { run });
  state.last_packet = result;
  $("#packet-path").textContent = JSON.stringify(result, null, 2);
  if (result.chatgpt_packet) {
    const text = await api(`/api/file?path=${encodeURIComponent(result.chatgpt_packet)}`);
    $("#packet-content").value = text;
  }
  showTab("packet");
  say(`Intelligence packet prepared.\n${JSON.stringify(result, null, 2)}`);
  await load();
}

async function exportPacket() {
  const result = await postJson("/api/review-packet", { run: requireRun() });
  state.last_packet = result;
  $("#packet-path").textContent = JSON.stringify(result, null, 2);
  const text = await api(`/api/file?path=${encodeURIComponent(result.chatgpt_packet)}`);
  $("#packet-content").value = text;
  showTab("packet");
  say("Review packet exported.");
}

$("#copy-packet").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#packet-content").value);
  say("Packet copied to clipboard.");
});

$("#close-file-dialog").addEventListener("click", () => $("#file-dialog").close());

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}

load().catch(fail);
