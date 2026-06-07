from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape


RUNS_ROOT = Path("runs")
DOCTOR_TOOLS = {
    "forge": ["forge", "--version"],
    "cast": ["cast", "--version"],
    "anvil": ["anvil", "--version"],
    "slither": ["slither", "--version"],
    "solc": ["solc", "--version"],
    "solc-select": ["solc-select", "--version"],
    "echidna": ["echidna", "--version"],
    "medusa": ["medusa", "--version"],
    "halmos": ["halmos", "--version"],
    "semgrep": ["semgrep", "--version"],
    "surya": ["surya", "--version"],
    "sol2uml": ["sol2uml", "--version"],
    "aderyn": ["aderyn", "--version"],
    "jq": ["jq", "--version"],
    "git": ["git", "--version"],
    "python": ["python", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "rust": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
}
INSTALL_HINTS = {
    "forge": "curl -L https://foundry.paradigm.xyz | bash; foundryup",
    "cast": "curl -L https://foundry.paradigm.xyz | bash; foundryup",
    "anvil": "curl -L https://foundry.paradigm.xyz | bash; foundryup",
    "slither": "python -m pip install slither-analyzer",
    "solc": "python -m pip install solc-select && solc-select install <version>",
    "solc-select": "python -m pip install solc-select",
    "echidna": "See https://github.com/crytic/echidna#installation",
    "medusa": "go install github.com/crytic/medusa@latest",
    "halmos": "python -m pip install halmos",
    "semgrep": "python -m pip install semgrep",
    "surya": "npm install -g surya",
    "sol2uml": "npm install -g sol2uml",
    "aderyn": "cargo install aderyn",
    "jq": "winget install jqlang.jq",
    "git": "winget install Git.Git",
    "python": "winget install Python.Python.3.12",
    "node": "winget install OpenJS.NodeJS.LTS",
    "npm": "Install Node.js LTS from https://nodejs.org/",
    "rust": "winget install Rustlang.Rustup",
    "cargo": "winget install Rustlang.Rustup",
}
HYPOTHESIS_STATUSES = [
    "New",
    "Needs PoC",
    "PoC Validated",
    "Needs Scoped Asset",
    "Rejected - No Impact",
    "Rejected - Out of Scope",
    "Rejected - Known Issue",
    "Report Candidate",
    "Submitted",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-").lower() or "target"


def doctor(output_dir: Path | None = None) -> dict:
    results = {}
    for name, command in DOCTOR_TOOLS.items():
        exe = command[0]
        path = shutil.which(exe)
        detected = path is not None
        version = ""
        if detected:
            try:
                proc = run_version_command(command)
                version = first_line(proc.stdout) or first_line(proc.stderr)
            except Exception as exc:  # pragma: no cover - defensive around local tools
                version = f"version check failed: {exc}"
        results[name] = {
            "detected": detected,
            "version": version,
            "path": path or "",
            "install_hint": "" if detected else INSTALL_HINTS.get(name, "Install from the tool's official docs."),
        }

    output = output_dir or Path.cwd()
    output.mkdir(parents=True, exist_ok=True)
    (output / "tool_versions.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def run_version_command(command: list[str]) -> subprocess.CompletedProcess:
    if os.name == "nt":
        return subprocess.run(
            subprocess.list2cmdline(command),
            capture_output=True,
            text=True,
            timeout=10,
            shell=True,
        )
    return subprocess.run(command, capture_output=True, text=True, timeout=10)


def init_run(target_name: str, program_url: str, zip_path: Path) -> Path:
    run_path = RUNS_ROOT / slugify(target_name) / timestamp()
    for folder in ("input", "scope", "repo", "tool-output", "hypotheses", "poc", "reports", "tracker", "metadata"):
        (run_path / folder).mkdir(parents=True, exist_ok=True)

    input_dest = run_path / "input" / zip_path.name
    if zip_path.is_dir():
        shutil.copytree(zip_path, input_dest, dirs_exist_ok=True)
    else:
        shutil.copy2(zip_path, input_dest)
    extract_repo(zip_path, run_path / "repo")

    metadata = {
        "target_name": target_name,
        "target_slug": slugify(target_name),
        "program_url": program_url,
        "source_zip": str(zip_path.resolve()),
        "source_input_type": "directory" if zip_path.is_dir() else "zip",
        "created_at": now_iso(),
        "run_path": str(run_path.resolve()),
        "rules": {
            "local_analysis_only": True,
            "live_mainnet_or_testnet_transactions": False,
            "forked_simulation_only_through_foundry_or_hardhat": True,
            "auto_claim_vulnerabilities": False,
        },
    }
    (run_path / "metadata" / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        conn.execute(
            "INSERT INTO runs (run_path, target_name, program_url, created_at) VALUES (?, ?, ?, ?)",
            (str(run_path.resolve()), target_name, program_url, metadata["created_at"]),
        )
        conn.commit()
    return run_path


def list_runs(root: Path = RUNS_ROOT) -> list[dict]:
    runs = []
    if not root.exists():
        return runs
    for metadata_path in root.glob("*/*/metadata/run_metadata.json"):
        run_path = metadata_path.parents[1]
        metadata = read_json(metadata_path)
        try:
            hypotheses = [dict(row) for row in list_hypotheses(run_path)]
        except sqlite3.Error:
            hypotheses = []
        status_counts: dict[str, int] = {}
        for row in hypotheses:
            status = row.get("status", "New")
            status_counts[status] = status_counts.get(status, 0) + 1
        latest_status = ""
        if hypotheses:
            latest = max(hypotheses, key=lambda item: item.get("updated_at", ""))
            latest_status = latest.get("status", "")
        runs.append(
            {
                "run_path": str(run_path),
                "target_name": metadata.get("target_name", run_path.parent.name),
                "program_url": metadata.get("program_url", ""),
                "created_at": metadata.get("created_at", run_path.name),
                "hypothesis_count": len(hypotheses),
                "status_counts": status_counts,
                "latest_status": latest_status,
            }
        )
    return sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)


def extract_repo(zip_path: Path, repo_dir: Path) -> None:
    if zip_path.is_dir():
        copy_tree_contents(zip_path, repo_dir)
        return
    if zip_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(repo_dir)
        flatten_single_directory(repo_dir)
        return
    raise ValueError(f"--zip must point to a .zip file or source directory: {zip_path}")


def ingest_run(run_path: Path) -> dict:
    repo = run_path / "repo"
    solidity_files = list(repo.rglob("*.sol"))
    package_json = list(repo.rglob("package.json"))
    foundry_toml = list(repo.rglob("foundry.toml"))
    hardhat_configs = [
        p for pattern in ("hardhat.config.*",) for p in repo.rglob(pattern) if p.is_file()
    ]
    remappings = list(repo.rglob("remappings.txt"))
    tests = [p for p in repo.rglob("*") if p.is_dir() and p.name.lower() in {"test", "tests"}]
    contracts = [p for p in repo.rglob("*") if p.is_dir() and p.name.lower() in {"src", "contracts", "contract"}]

    versions = sorted({v for path in solidity_files for v in solidity_versions(path)})
    contracts_index = [classify_contract(path, repo) for path in solidity_files]
    profiles = foundry_profiles(foundry_toml[0]) if foundry_toml else {}

    project_detect = {
        "project_type": detect_project_type(foundry_toml, hardhat_configs),
        "foundry_toml": rels(foundry_toml, repo),
        "hardhat_config": rels(hardhat_configs, repo),
        "package_json": rels(package_json, repo),
        "remappings_txt": rels(remappings, repo),
        "solidity_versions": versions,
        "test_folders": rels(tests, repo),
        "contract_folders": rels(contracts, repo),
        "solidity_file_count": len(solidity_files),
    }
    write_json(run_path / "metadata" / "project_detect.json", project_detect)
    write_json(run_path / "metadata" / "contracts_index.json", contracts_index)
    write_json(run_path / "metadata" / "profiles.json", profiles)
    return project_detect


def scope_run(run_path: Path, resource_urls: Iterable[str] = ()) -> Path:
    metadata = read_json(run_path / "metadata" / "run_metadata.json")
    urls = [metadata.get("program_url", "")] + [u for u in resource_urls if u]
    (run_path / "scope" / "resources.json").write_text(json.dumps({"urls": urls}, indent=2), encoding="utf-8")
    brief = f"""# Scope Brief

## Program URL
{metadata.get("program_url", "")}

## In-scope assets

## Out-of-scope assets

## In-scope impacts

## Exclusions

## PoC requirements

## Testing restrictions

## KYC/reward notes

## Known issue links

## Notes
"""
    path = run_path / "scope" / "scope_brief.md"
    if not path.exists():
        path.write_text(brief, encoding="utf-8")
    return path


def scan_run(run_path: Path, profile: str | None = None, all_profiles: bool = False) -> list[dict]:
    tools = doctor(run_path / "metadata")
    store_tools(run_path, tools)
    project = read_json(run_path / "metadata" / "project_detect.json") if (run_path / "metadata" / "project_detect.json").exists() else ingest_run(run_path)
    repo = run_path / "repo"
    selected_profiles = scan_profiles(run_path, profile, all_profiles)
    commands: list[tuple[str, list[str], str | None]] = []
    if tools.get("forge", {}).get("detected") and project.get("foundry_toml"):
        for selected_profile in selected_profiles:
            commands.append(("forge", ["forge", "build"], selected_profile))
            if project.get("test_folders"):
                commands.append(("forge", ["forge", "test"], selected_profile))
    if tools.get("slither", {}).get("detected"):
        for selected_profile in selected_profiles:
            if selected_profile:
                commands.append(
                    (
                        "slither",
                        ["slither", ".", "--compile-force-framework", "foundry", "--json", "slither.json"],
                        selected_profile,
                    )
                )
            else:
                commands.append(("slither", ["slither", ".", "--json", "slither.json"], None))
    if tools.get("semgrep", {}).get("detected"):
        commands.append(("semgrep", ["semgrep", "--config", "auto", "--json", "."], None))
    if tools.get("aderyn", {}).get("detected"):
        commands.append(("aderyn", ["aderyn", "."], None))
    if tools.get("surya", {}).get("detected"):
        commands.append(("surya", ["surya", "describe", "contracts/**/*.sol"], None))
    if tools.get("sol2uml", {}).get("detected"):
        commands.append(("sol2uml", ["sol2uml", "class", "."], None))

    executions = [
        execute_tool(
            run_path,
            repo,
            tool,
            command,
            profile=selected_profile,
            env={"FOUNDRY_PROFILE": selected_profile} if selected_profile else None,
        )
        for tool, command, selected_profile in commands
    ]
    return executions


def scan_profiles(run_path: Path, profile: str | None, all_profiles: bool) -> list[str | None]:
    if profile and all_profiles:
        raise ValueError("Use either --profile or --all-profiles, not both.")
    if profile:
        return [profile]
    if all_profiles:
        profiles_path = run_path / "metadata" / "profiles.json"
        profiles = read_json(profiles_path) if profiles_path.exists() else {}
        return sorted(profiles) or [None]
    return [None]


def execute_tool(
    run_path: Path,
    cwd: Path,
    tool: str,
    command: list[str],
    profile: str | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    start = now_iso()
    command_profile = tool_command_profile(command)
    out_dir = unique_tool_output_dir(run_path, tool, command_profile, profile)
    stdout_path = out_dir / "stdout.txt"
    stderr_path = out_dir / "stderr.txt"
    command_text = " ".join(command)
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    try:
        proc = subprocess.run(
            command_text if os.name == "nt" else command,
            cwd=cwd,
            capture_output=True,
            text=False,
            timeout=600,
            shell=os.name == "nt",
            env=process_env,
        )
        stdout_bytes = proc.stdout or b""
        stderr_bytes = proc.stderr or b""
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode
    except Exception as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = 127
    end = now_iso()
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    summary = parse_summary(tool, stdout, stderr, exit_code)
    record = {
        "tool": tool,
        "command": command_text,
        "command_args": command,
        "profile": profile or "",
        "command_profile": command_profile,
        "env": env or {},
        "start_time": start,
        "end_time": end,
        "exit_code": exit_code,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "parsed_summary": summary,
    }
    write_json(out_dir / "execution.json", record)
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        conn.execute(
            """
            INSERT INTO tool_executions (tool, command, start_time, end_time, exit_code, stdout_path, stderr_path, parsed_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tool, command_text, start, end, exit_code, str(stdout_path), str(stderr_path), summary),
        )
        conn.commit()
    return record


def unique_tool_output_dir(run_path: Path, tool: str, command_profile: str, profile: str | None = None) -> Path:
    root = run_path / "tool-output" / tool
    root.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    prefix = f"{slugify(profile)}-" if profile else ""
    counter = 1
    while True:
        candidate = root / f"{stamp}-{prefix}{command_profile}-{counter:03d}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            counter += 1


def tool_command_profile(command: list[str]) -> str:
    if command[:2] == ["forge", "build"]:
        return "build"
    if command[:2] == ["forge", "test"]:
        return "test"
    if command and command[0] in {"slither", "semgrep", "aderyn", "surya", "sol2uml"}:
        return command[0]
    parts = command[1:] or command[:1] or ["command"]
    raw = "-".join(part.lstrip("-") for part in parts if part)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-").lower()
    return slug[:48] or "command"


def add_hypothesis(run_path: Path, values: dict) -> sqlite3.Row:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        existing = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
        hypothesis_id = values.get("id") or f"H-{existing + 1:03d}"
        stamp = now_iso()
        status = validate_hypothesis_status(values.get("status", "New"))
        conn.execute(
            """
            INSERT INTO hypotheses (
                id, status, title, target, contract, function, hypothesis, source, tool_evidence,
                manual_evidence, scope_mapping, impact_mapping, poc_status, validation_status,
                gate_decision, known_issue_check, notes, next_action, closure_notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis_id,
                status,
                values.get("title", hypothesis_id),
                values.get("target", ""),
                values.get("contract", ""),
                values.get("function", ""),
                values.get("hypothesis", ""),
                values.get("source", ""),
                values.get("tool_evidence", ""),
                values.get("manual_evidence", ""),
                values.get("scope_mapping", ""),
                values.get("impact_mapping", ""),
                values.get("poc_status", "Needs PoC"),
                values.get("validation_status", "Unvalidated"),
                values.get("gate_decision", ""),
                values.get("known_issue_check", ""),
                values.get("notes", ""),
                values.get("next_action", ""),
                values.get("closure_notes", ""),
                stamp,
                stamp,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    write_hypothesis_md(run_path, row)
    return row


def import_leads(run_path: Path, file_path: Path) -> list[sqlite3.Row]:
    if not file_path.exists():
        raise ValueError(f"Lead file not found: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        leads = parse_csv_leads(file_path)
    elif suffix == ".md":
        leads = parse_markdown_leads(file_path)
    else:
        raise ValueError("Lead import accepts only .csv and .md files.")
    imported = [add_hypothesis(run_path, lead) for lead in leads]
    export_run(run_path)
    return imported


def parse_csv_leads(file_path: Path) -> list[dict]:
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV lead file must include a header row.")
        leads = [normalize_lead_values(row) for row in reader]
    return [lead for lead in leads if lead.get("title") or lead.get("hypothesis")]


def parse_markdown_leads(file_path: Path) -> list[dict]:
    text = file_path.read_text(encoding="utf-8")
    chunks = split_markdown_leads(text)
    leads = []
    for title, body in chunks:
        sections = markdown_sections(body)
        lead = normalize_lead_values(
            {
                "title": title,
                "contract": sections.get("contract", ""),
                "function": sections.get("function", ""),
                "hypothesis": sections.get("hypothesis", ""),
                "source": sections.get("source", ""),
                "manual_evidence": sections.get("evidence", ""),
                "scope_mapping": sections.get("scope mapping", ""),
                "impact_mapping": sections.get("impact mapping", ""),
                "next_action": sections.get("next action", ""),
            }
        )
        if lead.get("title") or lead.get("hypothesis"):
            leads.append(lead)
    return leads


def split_markdown_leads(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^# (.+?)\s*$", text))
    if not matches:
        return [("Untitled lead", text)]
    chunks = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append((match.group(1).strip(), text[start:end]))
    return chunks


def markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def normalize_lead_values(values: dict) -> dict:
    allowed = {
        "title",
        "target",
        "contract",
        "function",
        "hypothesis",
        "source",
        "tool_evidence",
        "manual_evidence",
        "scope_mapping",
        "impact_mapping",
        "poc_status",
        "validation_status",
        "gate_decision",
        "known_issue_check",
        "notes",
        "next_action",
    }
    normalized = {key: clean_cell(values.get(key, "")) for key in allowed}
    normalized["source"] = normalized["source"] or "Manual"
    normalized["poc_status"] = normalized["poc_status"] or "Needs PoC"
    normalized["validation_status"] = normalized["validation_status"] or "Unvalidated"
    normalized["status"] = "New"
    return normalized


def seed_axelar(run_path: Path) -> sqlite3.Row:
    return add_hypothesis(
        run_path,
        {
            "title": "Axelar ITS express execution reimbursement mismatch",
            "target": "Axelar",
            "contract": "InterchainTokenService",
            "function": "expressExecute, _processInterchainTransferPayload",
            "hypothesis": "Express executor may transfer less than the declared interchain payload amount but later be reimbursed the full payload amount during final settlement.",
            "source": "Manual seed",
            "tool_evidence": "",
            "manual_evidence": "Test idea: create or identify a token/token-manager path where actualTransferred < payloadAmount during express execution, then verify whether final settlement reimburses payloadAmount instead of actualTransferred.",
            "scope_mapping": "Must be mapped to the active Axelar scope before submission.",
            "impact_mapping": "Potential unauthorized transfer, direct loss of funds, insolvency, or improper wrapped asset accounting, depending on scoped asset and token-manager behavior.",
            "poc_status": "Needs PoC",
            "validation_status": "Unvalidated",
            "known_issue_check": "High known-issue risk. Must check prior Axelar audits, Code4rena reports, GitHub issues, and Immunefi known issues before any submission.",
            "notes": "",
            "next_action": "Check known issues, then build a Foundry reproduction against scoped assets.",
        },
    )


def list_hypotheses(run_path: Path) -> list[sqlite3.Row]:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        return conn.execute("SELECT * FROM hypotheses ORDER BY created_at, id").fetchall()


def update_hypothesis(run_path: Path, hypothesis_id: str, values: dict) -> sqlite3.Row:
    allowed = {
        "status",
        "title",
        "target",
        "contract",
        "function",
        "hypothesis",
        "source",
        "tool_evidence",
        "manual_evidence",
        "scope_mapping",
        "impact_mapping",
        "poc_status",
        "validation_status",
        "gate_decision",
        "known_issue_check",
        "notes",
        "next_action",
    }
    updates = {k: v for k, v in values.items() if k in allowed and v is not None}
    if "status" in updates:
        updates["status"] = validate_hypothesis_status(updates["status"])
    if not updates:
        raise ValueError("No updatable fields were provided.")
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values()) + [hypothesis_id]
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        conn.execute(f"UPDATE hypotheses SET {assignments} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    if row is None:
        raise ValueError(f"Hypothesis not found: {hypothesis_id}")
    write_hypothesis_md(run_path, row)
    return row


def gate_hypothesis(run_path: Path, hypothesis_id: str, decision: str, notes: str = "") -> sqlite3.Row:
    note = notes.strip()
    values = {"gate_decision": decision.strip()}
    if note:
        existing = get_hypothesis(run_path, hypothesis_id)["notes"]
        stamp = now_iso()
        values["notes"] = f"{existing.rstrip()}\n{stamp} gate: {note}".strip() if existing else f"{stamp} gate: {note}"
    row = update_hypothesis(run_path, hypothesis_id, values)
    export_run(run_path)
    return row


def close_hypothesis(run_path: Path, hypothesis_id: str, status: str, reason: str) -> sqlite3.Row:
    if not reason.strip():
        raise ValueError("--reason is required.")
    status = validate_hypothesis_status(status)
    stamp = now_iso()
    note = f"{stamp} - {status}: {reason.strip()}"
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
        if row is None:
            raise ValueError(f"Hypothesis not found: {hypothesis_id}")
        existing = row["closure_notes"] or ""
        closure_notes = f"{existing.rstrip()}\n{note}".strip() if existing else note
        conn.execute(
            """
            UPDATE hypotheses
            SET status = ?, closure_notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, closure_notes, stamp, hypothesis_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    append_hypothesis_closure_note(run_path, row, note)
    export_run(run_path)
    return row


def get_hypothesis(run_path: Path, hypothesis_id: str) -> sqlite3.Row:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        row = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
    if row is None:
        raise ValueError(f"Hypothesis not found: {hypothesis_id}")
    return row


def tool_execution_history(run_path: Path) -> list[dict]:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM tool_executions ORDER BY id DESC").fetchall()
        ]


def fetch_page_text(url: str) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - dependency guidance
        raise RuntimeError("Install requests and beautifulsoup4 to fetch page text.") from exc

    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Web3 Bug Bounty Workbench/0.1"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def export_review_packet(run_path: Path, hypothesis_ids: Iterable[str] | None = None) -> dict:
    exports = export_run(run_path)
    packet = run_path / "review_packet"
    if packet.exists():
        shutil.rmtree(packet)
    packet.mkdir(parents=True)
    selected_ids = set(hypothesis_ids or [])

    included: list[str] = []
    for rel in [
        Path("scope") / "scope_brief.md",
        Path("tracker") / "summary.md",
        Path("tracker") / "tracker.csv",
        Path("tracker") / "run_summary.md",
        Path("metadata") / "project_detect.json",
        Path("metadata") / "profiles.json",
    ]:
        copy_review_file(run_path, packet, rel, included)

    hypotheses = [dict(row) for row in list_hypotheses(run_path)]
    for row in hypotheses:
        if selected_ids and row["id"] not in selected_ids:
            continue
        copy_review_file(run_path, packet, Path("hypotheses") / f"{row['id']}.md", included)

    for poc_path in sorted((run_path / "poc").glob("*")) if (run_path / "poc").exists() else []:
        if poc_path.is_file() and poc_path.suffix.lower() in {".md", ".txt"}:
            copy_review_file(run_path, packet, poc_path.relative_to(run_path), included)

    for path in sorted((run_path / "tool-output").rglob("*")) if (run_path / "tool-output").exists() else []:
        if path.is_file() and path.name in {"stdout.txt", "stderr.txt", "execution.json"}:
            copy_review_file(run_path, packet, path.relative_to(run_path), included)

    packet_md = packet / "chatgpt_packet.md"
    packet_md.write_text(build_chatgpt_packet(run_path, hypotheses, included), encoding="utf-8")
    included.append(str(packet_md.relative_to(packet)))
    return {"review_packet": str(packet), "chatgpt_packet": str(packet_md), "included_files": included, **exports}


def copy_review_file(run_path: Path, packet: Path, rel: Path, included: list[str]) -> None:
    src = run_path / rel
    if not src.exists() or not src.is_file():
        return
    dest = packet / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    included.append(str(rel))


def build_chatgpt_packet(run_path: Path, hypotheses: list[dict], included: list[str]) -> str:
    metadata = read_json(run_path / "metadata" / "run_metadata.json") if (run_path / "metadata" / "run_metadata.json").exists() else {}
    resources = read_json(run_path / "scope" / "resources.json") if (run_path / "scope" / "resources.json").exists() else {}
    open_rows = [row for row in hypotheses if not str(row.get("status", "")).startswith("Rejected")]
    closed_rows = [row for row in hypotheses if str(row.get("status", "")).startswith("Rejected")]
    executions = tool_execution_history(run_path)
    lines = [
        "# ChatGPT Review Packet",
        "",
        "## Target",
        f"- Name: {metadata.get('target_name', '')}",
        f"- Program URL: {metadata.get('program_url', '')}",
        f"- Run path: {run_path}",
        "",
        "## Scope URLs",
    ]
    for url in resources.get("urls", []):
        lines.append(f"- {url}")
    lines.extend(["", "## Current Hypotheses"])
    add_packet_hypotheses(lines, open_rows)
    lines.extend(["", "## Closed Or Rejected Hypotheses"])
    add_packet_hypotheses(lines, closed_rows)
    lines.extend(["", "## Tool Results"])
    for execution in executions:
        lines.append(
            f"- {execution['tool']} exit {execution['exit_code']}: {execution.get('parsed_summary', '')}"
        )
        lines.extend(tool_snippets(execution))
    lines.extend(
        [
            "",
            "## Known Blockers",
            "- Review manually for stale scope, missing tool output, and unproven asset assumptions.",
            "",
            "## Files Included",
        ]
    )
    lines.extend(f"- {item}" for item in included)
    lines.extend(
        [
            "",
            "## Questions For ChatGPT Or Manual Reviewer",
            "- Which hypotheses still need scoped-asset confirmation?",
            "- Which rejected hypotheses should stay closed?",
            "- What is the next lowest-cost validation step?",
        ]
    )
    return "\n".join(lines) + "\n"


def add_packet_hypotheses(lines: list[str], rows: list[dict]) -> None:
    if not rows:
        lines.append("- None")
        return
    for row in rows:
        lines.append(
            f"- {row.get('id', '')}: {row.get('title', '')} "
            f"({row.get('status', '')}; next: {row.get('next_action', '')})"
        )


def tool_snippets(execution: dict, limit: int = 800) -> list[str]:
    snippets = []
    for label, key in [("stdout", "stdout_path"), ("stderr", "stderr_path")]:
        path = Path(execution.get(key, ""))
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                snippets.append(f"  - {label}: {text[:limit]}")
    return snippets


def export_run(run_path: Path) -> dict:
    tracker = run_path / "tracker"
    tracker.mkdir(parents=True, exist_ok=True)
    rows = [dict(row) for row in list_hypotheses(run_path)]
    csv_path = tracker / "tracker.csv"
    xlsx_path = tracker / "tracker.xlsx"
    summary_path = tracker / "summary.md"
    run_summary_path = tracker / "run_summary.md"
    tool_versions_src = run_path / "metadata" / "tool_versions.json"
    if tool_versions_src.exists():
        shutil.copy2(tool_versions_src, tracker / "tool_versions.json")
    else:
        doctor(tracker)
    write_csv(csv_path, rows)
    write_xlsx(xlsx_path, rows)
    write_summary(summary_path, rows)
    write_run_summary(run_path, run_summary_path, rows)
    return {
        "csv": str(csv_path),
        "xlsx": str(xlsx_path),
        "summary": str(summary_path),
        "run_summary": str(run_summary_path),
        "tool_versions": str(tracker / "tool_versions.json"),
    }


def ensure_run_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_path TEXT NOT NULL,
            target_name TEXT NOT NULL,
            program_url TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tools (
            name TEXT PRIMARY KEY,
            detected INTEGER NOT NULL,
            version TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            install_hint TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tool_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool TEXT NOT NULL,
            command TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            stdout_path TEXT NOT NULL,
            stderr_path TEXT NOT NULL,
            parsed_summary TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'New',
            title TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            contract TEXT NOT NULL DEFAULT '',
            function TEXT NOT NULL DEFAULT '',
            hypothesis TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            tool_evidence TEXT NOT NULL DEFAULT '',
            manual_evidence TEXT NOT NULL DEFAULT '',
            scope_mapping TEXT NOT NULL DEFAULT '',
            impact_mapping TEXT NOT NULL DEFAULT '',
            poc_status TEXT NOT NULL DEFAULT 'Needs PoC',
            validation_status TEXT NOT NULL DEFAULT 'Unvalidated',
            gate_decision TEXT NOT NULL DEFAULT '',
            known_issue_check TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            closure_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id TEXT,
            path TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    ensure_columns(
        conn,
        "hypotheses",
        {
            "status": "TEXT NOT NULL DEFAULT 'New'",
            "gate_decision": "TEXT NOT NULL DEFAULT ''",
            "closure_notes": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.commit()


def run_db(run_path: Path) -> sqlite3.Connection:
    db_path = run_path / "metadata" / "web3bb.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def store_tools(run_path: Path, tools: dict) -> None:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        checked_at = now_iso()
        for name, info in tools.items():
            conn.execute(
                """
                INSERT INTO tools (name, detected, version, path, install_hint, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    detected = excluded.detected,
                    version = excluded.version,
                    path = excluded.path,
                    install_hint = excluded.install_hint,
                    checked_at = excluded.checked_at
                """,
                (name, int(info["detected"]), info["version"], info["path"], info["install_hint"], checked_at),
            )
        conn.commit()


def classify_contract(path: Path, repo: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    name = path.stem
    words = f"{name}\n{text[:5000]}".lower()
    flags = {
        "likely_core": any(x in words for x in ("vault", "pool", "router", "manager", "service", "exchange", "protocol")),
        "proxy": any(x in words for x in ("proxy", "upgradeable", "delegatecall", "transparentupgradeableproxy", "uups")),
        "token": any(x in words for x in ("erc20", "erc721", "erc1155", "token", "mint", "burn")),
        "bridge_cross_chain": any(x in words for x in ("bridge", "crosschain", "cross-chain", "interchain", "axelar", "layerzero", "wormhole")),
        "access_control_admin": any(x in words for x in ("ownable", "accesscontrol", "admin", "role", "onlyowner", "governor")),
    }
    functions = re.findall(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    contracts = re.findall(r"\b(?:contract|interface|library)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    return {
        "path": str(path.relative_to(repo)),
        "declared_contracts": contracts,
        "functions_sample": functions[:50],
        **flags,
    }


def solidity_versions(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return re.findall(r"pragma\s+solidity\s+([^;]+);", text)


def foundry_profiles(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    profiles = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"\s*\[profile\.([^\]]+)\]\s*", line)
        if match:
            current = match.group(1)
            profiles[current] = {}
            continue
        if current and "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            profiles[current][key.strip()] = value.strip()
    return profiles


def detect_project_type(foundry_toml: list[Path], hardhat_configs: list[Path]) -> str:
    if foundry_toml and hardhat_configs:
        return "foundry+hardhat"
    if foundry_toml:
        return "foundry"
    if hardhat_configs:
        return "hardhat"
    return "unknown"


def parse_summary(tool: str, stdout: str, stderr: str, exit_code: int) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if tool == "forge" and "compiler run failed" in text:
        return "Forge compile failure."
    if tool == "forge" and "suite result" in text:
        return first_matching_line(stdout, "Suite result") or f"Forge exited {exit_code}."
    if tool == "slither" and "detectors" in text:
        return "Slither produced detector output; review JSON/stdout."
    if exit_code == 0:
        return f"{tool} completed successfully."
    return f"{tool} exited with code {exit_code}; manual review required."


def write_hypothesis_md(run_path: Path, row: sqlite3.Row) -> None:
    data = dict(row)
    lines = [f"# {data['id']} {data.get('title') or ''}".strip(), ""]
    for label, key in [
        ("Status", "status"),
        ("Target", "target"),
        ("Contract", "contract"),
        ("Function", "function"),
        ("Hypothesis", "hypothesis"),
        ("Source", "source"),
        ("Tool Evidence", "tool_evidence"),
        ("Manual Evidence", "manual_evidence"),
        ("Scope Mapping", "scope_mapping"),
        ("Impact Mapping", "impact_mapping"),
        ("PoC Status", "poc_status"),
        ("Validation Status", "validation_status"),
        ("Gate Decision", "gate_decision"),
        ("Known Issue Check", "known_issue_check"),
        ("Notes", "notes"),
        ("Next Action", "next_action"),
        ("Closure Notes", "closure_notes"),
    ]:
        lines.extend([f"## {label}", data.get(key, ""), ""])
    hypotheses_dir = run_path / "hypotheses"
    hypotheses_dir.mkdir(parents=True, exist_ok=True)
    (hypotheses_dir / f"{data['id']}.md").write_text("\n".join(lines), encoding="utf-8")


def append_hypothesis_closure_note(run_path: Path, row: sqlite3.Row, note: str) -> None:
    data = dict(row)
    path = run_path / "hypotheses" / f"{data['id']}.md"
    if not path.exists():
        write_hypothesis_md(run_path, row)
        return
    text = path.read_text(encoding="utf-8")
    addition = (
        "\n\n## Lifecycle Status\n"
        f"{data.get('status', '')}\n\n"
        "## Closure Notes\n"
        f"{note}\n"
    )
    path.write_text(text.rstrip() + addition, encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = hypothesis_fields()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(path: Path, rows: list[dict]) -> None:
    fields = hypothesis_fields()
    sheet_rows = [fields] + [[str(row.get(field, "")) for field in fields] for row in rows]
    xml_rows = []
    for r_idx, row in enumerate(sheet_rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            cells.append(f'<c r="{column_name(c_idx)}{r_idx}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{r_idx}">' + "".join(cells) + "</row>")
    sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", RELS_XML)
        zf.writestr("xl/workbook.xml", WORKBOOK_XML)
        zf.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def write_summary(path: Path, rows: list[dict]) -> None:
    lines = ["# Hypothesis Tracker", ""]
    for row in rows:
        lines.append(f"## {row.get('id')} - {row.get('title', '')}")
        lines.append(f"- Status: {row.get('status', '')}")
        lines.append(f"- Contract: {row.get('contract', '')}")
        lines.append(f"- Function: {row.get('function', '')}")
        lines.append(f"- PoC Status: {row.get('poc_status', '')}")
        lines.append(f"- Validation Status: {row.get('validation_status', '')}")
        lines.append(f"- Gate Decision: {row.get('gate_decision', '')}")
        lines.append(f"- Next Action: {row.get('next_action', '')}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_run_summary(run_path: Path, path: Path, rows: list[dict]) -> None:
    metadata = read_json(run_path / "metadata" / "run_metadata.json") if (run_path / "metadata" / "run_metadata.json").exists() else {}
    executions = []
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        executions = [dict(row) for row in conn.execute("SELECT * FROM tool_executions ORDER BY id").fetchall()]
    lines = [
        "# Run Summary",
        "",
        f"- Target: {metadata.get('target_name', '')}",
        f"- Program URL: {metadata.get('program_url', '')}",
        f"- Run path: {run_path}",
        f"- Hypotheses: {len(rows)}",
        f"- Tool executions: {len(executions)}",
        "",
        "## Tool Executions",
        "",
    ]
    for item in executions:
        lines.append(f"- {item['tool']}: exit {item['exit_code']} - {item['parsed_summary']}")
    path.write_text("\n".join(lines), encoding="utf-8")


def print_table(rows: list[sqlite3.Row]) -> str:
    headers = ["ID", "Title", "Status", "Contract", "PoC", "Validation", "Gate", "Next Action"]
    body = [
        [
            row["id"],
            row["title"],
            row["status"],
            row["contract"],
            row["poc_status"],
            row["validation_status"],
            row["gate_decision"],
            row["next_action"],
        ]
        for row in rows
    ]
    widths = [len(h) for h in headers]
    for row in body:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], min(len(str(value)), 48))
    def fmt(row: list[str]) -> str:
        return " | ".join(str(value)[:48].ljust(widths[idx]) for idx, value in enumerate(row))
    return "\n".join([fmt(headers), "-+-".join("-" * w for w in widths), *[fmt(r) for r in body]])


def hypothesis_fields() -> list[str]:
    return [
        "id",
        "status",
        "title",
        "target",
        "contract",
        "function",
        "hypothesis",
        "source",
        "tool_evidence",
        "manual_evidence",
        "scope_mapping",
        "impact_mapping",
        "poc_status",
        "validation_status",
        "gate_decision",
        "known_issue_check",
        "notes",
        "next_action",
        "closure_notes",
        "created_at",
        "updated_at",
    ]


def validate_hypothesis_status(status: str) -> str:
    cleaned = " / ".join(part.strip() for part in status.split("/") if part.strip())
    if not cleaned:
        raise ValueError("Status is required.")
    parts = [part.strip() for part in cleaned.split("/")]
    invalid = [part for part in parts if part not in HYPOTHESIS_STATUSES]
    if invalid:
        allowed = ", ".join(HYPOTHESIS_STATUSES)
        raise ValueError(f"Invalid hypothesis status: {', '.join(invalid)}. Allowed: {allowed}")
    return cleaned


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def copy_tree_contents(src: Path, dest: Path) -> None:
    for child in src.iterdir():
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def flatten_single_directory(path: Path) -> None:
    children = list(path.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return
    inner = children[0]
    temp = path.parent / f"{path.name}-tmp"
    inner.rename(temp)
    shutil.rmtree(path)
    temp.rename(path)


def rels(paths: Iterable[Path], root: Path) -> list[str]:
    out = []
    for path in paths:
        try:
            out.append(str(path.relative_to(root)))
        except ValueError:
            out.append(str(path))
    return sorted(out)


def first_line(value: str) -> str:
    return value.strip().splitlines()[0] if value and value.strip() else ""


def first_matching_line(value: str, needle: str) -> str:
    for line in value.splitlines():
        if needle.lower() in line.lower():
            return line.strip()
    return ""


def clean_cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def column_name(idx: int) -> str:
    name = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Tracker" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
WORKBOOK_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
