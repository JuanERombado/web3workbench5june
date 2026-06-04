let state = {
  targets: [],
  hypotheses: [],
  manualVerdicts: [],
};

const targetForm = document.querySelector("#target-form");
const hypothesisForm = document.querySelector("#hypothesis-form");
const runForm = document.querySelector("#run-form");
const targetSelect = document.querySelector("#target-select");
const hypothesisSelect = document.querySelector("#hypothesis-select");
const resultsBody = document.querySelector("#results-body");
const runResult = document.querySelector("#run-result");
const refreshButton = document.querySelector("#refresh-button");

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function load() {
  const payload = await api("/api/bootstrap");
  state = {
    targets: payload.targets,
    hypotheses: payload.hypotheses,
    manualVerdicts: payload.manual_verdicts,
  };
  render();
}

function render() {
  targetSelect.innerHTML = state.targets
    .map((target) => `<option value="${escapeHtml(target.name)}">${escapeHtml(target.name)}</option>`)
    .join("");

  hypothesisSelect.innerHTML = state.hypotheses
    .map((hypothesis) => {
      const label = `#${hypothesis.id} ${hypothesis.title}`;
      return `<option value="${hypothesis.id}" data-target="${escapeHtml(hypothesis.target)}">${escapeHtml(label)}</option>`;
    })
    .join("");

  resultsBody.innerHTML = state.hypotheses.map(renderHypothesisRow).join("");
  bindVerdictControls();
}

function renderHypothesisRow(hypothesis) {
  const verdictOptions = state.manualVerdicts
    .map((verdict) => {
      const selected = verdict === hypothesis.manual_verdict ? "selected" : "";
      return `<option value="${verdict}" ${selected}>${verdict}</option>`;
    })
    .join("");
  return `
    <tr>
      <td>${hypothesis.id}</td>
      <td>${escapeHtml(hypothesis.title)}<br><span class="muted">${escapeHtml(hypothesis.summary || "")}</span></td>
      <td>${escapeHtml(hypothesis.tool)}</td>
      <td><span class="badge ${escapeHtml(hypothesis.tool_status)}">${escapeHtml(hypothesis.tool_status)}</span></td>
      <td><select class="verdict-select" data-id="${hypothesis.id}" data-target="${escapeHtml(hypothesis.target)}">${verdictOptions}</select></td>
      <td>${escapeHtml(hypothesis.updated_at)}</td>
      <td>${escapeHtml(hypothesis.raw_output_path || "")}</td>
      <td>
        <div class="notes-control">
          <input class="notes-input" data-id="${hypothesis.id}" data-target="${escapeHtml(hypothesis.target)}" value="${escapeAttr(hypothesis.decision_notes || "")}" />
          <button class="notes-save" data-id="${hypothesis.id}" data-target="${escapeHtml(hypothesis.target)}" type="button">Save</button>
        </div>
      </td>
    </tr>
  `;
}

function bindVerdictControls() {
  document.querySelectorAll(".verdict-select").forEach((select) => {
    select.addEventListener("change", async () => {
      await saveVerdict(select.dataset.target, Number(select.dataset.id), select.value);
    });
  });
  document.querySelectorAll(".notes-save").forEach((button) => {
    button.addEventListener("click", async () => {
      const input = document.querySelector(`.notes-input[data-id="${button.dataset.id}"]`);
      const select = document.querySelector(`.verdict-select[data-id="${button.dataset.id}"]`);
      await saveVerdict(button.dataset.target, Number(button.dataset.id), select.value, input.value);
    });
  });
}

async function saveVerdict(target, hypothesisId, manualVerdict, decisionNotes = "") {
  await api("/api/manual-verdict", {
    method: "POST",
    body: JSON.stringify({
      target,
      hypothesis_id: hypothesisId,
      manual_verdict: manualVerdict,
      decision_notes: decisionNotes,
    }),
  });
  await load();
}

targetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(targetForm);
  await api("/api/targets", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form)),
  });
  targetForm.reset();
  await load();
});

hypothesisForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(hypothesisForm);
  await api("/api/hypotheses", {
    method: "POST",
    body: JSON.stringify(Object.fromEntries(form)),
  });
  hypothesisForm.reset();
  await load();
});

runForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = hypothesisSelect.selectedOptions[0];
  if (!selected) return;
  const form = new FormData(runForm);
  const payload = Object.fromEntries(form);
  payload.hypothesis_id = Number(payload.hypothesis_id);
  payload.target = selected.dataset.target;
  const result = await api("/api/run-tool", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  runResult.textContent = `${result.tool_status}\n${result.summary}\n${result.raw_output_path}`;
  await load();
});

refreshButton.addEventListener("click", load);

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

load().catch((error) => {
  runResult.textContent = error.message;
});
