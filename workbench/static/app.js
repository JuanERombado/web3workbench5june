const state = {
  runs: [],
  hypotheses: [],
  executions: [],
  profiles: {},
  statuses: [],
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
  renderProfiles();
  renderStatuses();
  renderExecutions();
  renderHypotheses();
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
        <button type="button" data-export-packet="${escapeAttr(run.run_path)}">Export Packet</button>
      </td>
    </tr>
  `).join("");
  document.querySelectorAll("[data-select-run]").forEach((button) => {
    button.addEventListener("click", async () => {
      currentRun.value = button.dataset.selectRun;
      await load();
      showTab("scope");
    });
  });
  document.querySelectorAll("[data-export-packet]").forEach((button) => {
    button.addEventListener("click", async () => {
      currentRun.value = button.dataset.exportPacket;
      await exportPacket();
    });
  });
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
  bindFileButtons();
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

$("#refresh-all").addEventListener("click", () => load().catch(fail));
$("#dashboard-refresh").addEventListener("click", () => load().catch(fail));
$("#open-run-folder").addEventListener("click", async () => {
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

$("#export-packet").addEventListener("click", () => exportPacket().catch(fail));

async function exportPacket() {
  const result = await postJson("/api/review-packet", { run: requireRun() });
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
