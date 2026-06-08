from __future__ import annotations

import csv
import hashlib
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
KNOWN_SOURCE_TYPES = ["audit", "report", "docs", "github", "scope", "rejection", "manual"]


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


def known_add_url(run_path: Path, url: str, source_type: str, title: str, notes: str = "") -> sqlite3.Row:
    try:
        text = fetch_page_text(url)
        fetch_status = "fetched"
    except Exception as exc:
        text = f"{title}\n{url}\n{notes}"
        fetch_status = "FETCH_FAILED"
        notes = f"{notes}\nFETCH_FAILED: {exc}".strip()
    return add_known_source(run_path, title, source_type, text, url=url, notes=notes, fetch_status=fetch_status)


def known_import_file(run_path: Path, file_path: Path, source_type: str, title: str | None = None, notes: str = "") -> sqlite3.Row:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return add_known_source(run_path, title or file_path.stem, source_type, text, file_path=str(file_path), notes=notes, fetch_status="file")


def known_add_manual(run_path: Path, title: str, source_type: str, text: str, notes: str = "") -> sqlite3.Row:
    return add_known_source(run_path, title, source_type, text, notes=notes, fetch_status="manual")


def add_known_source(
    run_path: Path,
    title: str,
    source_type: str,
    text: str,
    url: str = "",
    file_path: str = "",
    notes: str = "",
    fetch_status: str = "manual",
    source_key: str = "",
) -> sqlite3.Row:
    source_type = validate_known_source_type(source_type)
    clean_text = normalize_known_text(text)
    if not clean_text:
        clean_text = "\n".join(part for part in [title, url, file_path, notes] if part)
    digest = text_hash(clean_text)
    source_key = source_key or known_source_key(title, source_type, digest, url)
    stamp = now_iso()
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        existing = find_known_source_by_key(conn, source_key)
        if existing:
            if should_replace_known_source(existing["fetch_status"], fetch_status):
                conn.execute(
                    """
                    UPDATE known_sources
                    SET title = ?, url = ?, file_path = ?, source_type = ?, fetched_at = ?,
                        text_hash = ?, notes = ?, fetch_status = ?, source_key = ?
                    WHERE id = ?
                    """,
                    (
                        title.strip() or "Untitled known source",
                        url,
                        file_path,
                        source_type,
                        stamp,
                        digest,
                        notes,
                        fetch_status,
                        source_key,
                        existing["id"],
                    ),
                )
                replace_known_chunks(conn, existing["id"], title.strip() or "Untitled known source", source_type, clean_text)
                conn.commit()
            return conn.execute("SELECT * FROM known_sources WHERE id = ?", (existing["id"],)).fetchone()
        existing = conn.execute("SELECT * FROM known_sources WHERE text_hash = ?", (digest,)).fetchone()
        if existing:
            if not existing["source_key"]:
                conn.execute("UPDATE known_sources SET source_key = ? WHERE id = ?", (source_key, existing["id"]))
                conn.commit()
            return conn.execute("SELECT * FROM known_sources WHERE id = ?", (existing["id"],)).fetchone()
        cursor = conn.execute(
            """
            INSERT INTO known_sources (title, url, file_path, source_type, fetched_at, text_hash, notes, fetch_status, source_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title.strip() or "Untitled known source", url, file_path, source_type, stamp, digest, notes, fetch_status, source_key),
        )
        source_id = cursor.lastrowid
        replace_known_chunks(conn, source_id, title.strip() or "Untitled known source", source_type, clean_text)
        conn.commit()
        return conn.execute("SELECT * FROM known_sources WHERE id = ?", (source_id,)).fetchone()


def find_known_source_by_key(conn: sqlite3.Connection, source_key: str) -> sqlite3.Row | None:
    existing = conn.execute("SELECT * FROM known_sources WHERE source_key = ? ORDER BY id LIMIT 1", (source_key,)).fetchone()
    if existing:
        return existing
    for row in conn.execute("SELECT * FROM known_sources WHERE source_key = '' OR source_key IS NULL ORDER BY id").fetchall():
        inferred = infer_existing_source_key(conn, dict(row))
        if inferred == source_key:
            conn.execute("UPDATE known_sources SET source_key = ? WHERE id = ?", (source_key, row["id"]))
            return conn.execute("SELECT * FROM known_sources WHERE id = ?", (row["id"],)).fetchone()
    return None


def replace_known_chunks(conn: sqlite3.Connection, source_id: int, title: str, source_type: str, text: str) -> None:
    chunk_ids = [row["id"] for row in conn.execute("SELECT id FROM known_chunks WHERE source_id = ?", (source_id,)).fetchall()]
    if chunk_ids and known_fts_available(conn):
        placeholders = ",".join("?" for _ in chunk_ids)
        conn.execute(f"DELETE FROM known_chunks_fts WHERE rowid IN ({placeholders})", chunk_ids)
    conn.execute("DELETE FROM known_chunks WHERE source_id = ?", (source_id,))
    for idx, chunk in enumerate(chunk_text(text)):
        chunk_cursor = conn.execute(
            "INSERT INTO known_chunks (source_id, chunk_index, text) VALUES (?, ?, ?)",
            (source_id, idx, chunk),
        )
        if known_fts_available(conn):
            conn.execute(
                "INSERT INTO known_chunks_fts (rowid, source_id, title, source_type, text) VALUES (?, ?, ?, ?, ?)",
                (chunk_cursor.lastrowid, source_id, title, source_type, chunk),
            )


def known_list(run_path: Path) -> list[dict]:
    index_closed_hypotheses(run_path)
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        rows = conn.execute(
            """
            SELECT s.*, COUNT(c.id) AS chunk_count
            FROM known_sources s
            LEFT JOIN known_chunks c ON c.source_id = s.id
            GROUP BY s.id
            ORDER BY s.fetched_at DESC, s.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def known_search(run_path: Path, query: str, limit: int = 20) -> list[dict]:
    index_closed_hypotheses(run_path)
    query = query.strip()
    if not query:
        return []
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        if known_fts_available(conn):
            try:
                return known_search_fts(conn, query, limit)
            except sqlite3.Error:
                pass
        return known_search_like(conn, query, limit)


def known_search_fts(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    fts_query = " OR ".join(f'"{term}"' for term in search_terms(query)[:12])
    rows = conn.execute(
        """
        SELECT s.id AS source_id, s.title, s.url, s.file_path, s.source_type, c.text AS snippet,
               bm25(known_chunks_fts) AS rank
        FROM known_chunks_fts
        JOIN known_chunks c ON c.id = known_chunks_fts.rowid
        JOIN known_sources s ON s.id = c.source_id
        WHERE known_chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_query, limit),
    ).fetchall()
    return [decorate_known_match(dict(row), query) for row in rows]


def known_search_like(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    terms = search_terms(query)[:12]
    clauses = " OR ".join(["LOWER(c.text) LIKE ? OR LOWER(s.title) LIKE ?" for _ in terms])
    params = []
    for term in terms:
        like = f"%{term.lower()}%"
        params.extend([like, like])
    rows = conn.execute(
        f"""
        SELECT s.id AS source_id, s.title, s.url, s.file_path, s.source_type, c.text AS snippet
        FROM known_chunks c
        JOIN known_sources s ON s.id = c.source_id
        WHERE {clauses}
        ORDER BY s.fetched_at DESC, s.id DESC, c.chunk_index
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [decorate_known_match(dict(row), query) for row in rows]


def check_known(run_path: Path, hypothesis_id: str) -> dict:
    row = dict(get_hypothesis(run_path, hypothesis_id))
    query = " ".join(
        value
        for value in [
            row.get("title", ""),
            row.get("contract", ""),
            row.get("function", ""),
            row.get("hypothesis", ""),
            row.get("impact_mapping", ""),
        ]
        if value
    )
    matches = [
        score_known_match_for_hypothesis(match, row)
        for match in known_search(run_path, query, limit=20)
    ]
    self_history_matches = [match for match in matches if match.get("match_kind") == "self-history match"]
    public_known_matches = [
        match
        for match in matches
        if match.get("match_kind") == "public known match" and match.get("confidence") in {"High", "Medium"}
    ]
    weak_context_matches = [
        match
        for match in matches
        if match not in self_history_matches and match not in public_known_matches
    ]
    recommendation = check_known_recommendation(self_history_matches, public_known_matches)
    stamp = now_iso()
    note = (
        f"{stamp} known-check: {recommendation}; "
        f"{len(self_history_matches)} self-history, {len(public_known_matches)} public, "
        f"{len(weak_context_matches)} weak context matches."
    )
    update_hypothesis(
        run_path,
        hypothesis_id,
        {
            "known_issue_check": note,
            "notes": f"{row.get('notes', '').rstrip()}\n{note}".strip(),
        },
    )
    return {
        "hypothesis_id": hypothesis_id,
        "query": query,
        "self_history_matches": self_history_matches,
        "public_known_matches": public_known_matches,
        "weak_context_matches": weak_context_matches,
        "matches": matches,
        "recommendation": recommendation,
    }


def link_known_issue(run_path: Path, hypothesis_id: str, source_id: int, notes: str = "") -> dict:
    stamp = now_iso()
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        conn.execute(
            """
            INSERT INTO known_hypothesis_links (hypothesis_id, source_id, notes, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (hypothesis_id, source_id, notes, stamp),
        )
        conn.commit()
    return {"hypothesis_id": hypothesis_id, "source_id": source_id, "notes": notes, "created_at": stamp}


def known_export(run_path: Path) -> dict:
    index_closed_hypotheses(run_path)
    tracker = run_path / "tracker"
    tracker.mkdir(parents=True, exist_ok=True)
    sources = known_list(run_path)
    csv_path = tracker / "known_sources.csv"
    md_path = tracker / "known_sources.md"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["id", "title", "url", "file_path", "source_type", "fetch_status", "fetched_at", "text_hash", "notes", "chunk_count"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sources)
    lines = ["# Known Issue Corpus", ""]
    for source in sources:
        loc = source.get("url") or source.get("file_path") or ""
        lines.extend(
            [
                f"## {source.get('id')} - {source.get('title', '')}",
                f"- Type: {source.get('source_type', '')}",
                f"- Fetch Status: {source.get('fetch_status', '')}",
                f"- Location: {loc}",
                f"- Fetched: {source.get('fetched_at', '')}",
                f"- Chunks: {source.get('chunk_count', 0)}",
                f"- Notes: {source.get('notes', '')}",
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"known_csv": str(csv_path), "known_summary": str(md_path), "sources": len(sources)}


def known_intel(run_path: Path) -> dict:
    known_export(run_path)
    out_dir = run_path / "known_intel"
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = known_list(run_path)
    chunks = known_chunks_for_intel(run_path)
    hypotheses = [dict(row) for row in list_hypotheses(run_path)]
    corpus_text = "\n".join([chunk["text"] for chunk in chunks] + [row.get("hypothesis", "") for row in hypotheses])

    contracts = top_terms(re.findall(r"\b[A-Z][A-Za-z0-9_]{3,}\b", corpus_text), exclude={"Code4rena", "Axelar", "Immunefi"})
    function_matches = re.findall(r"\b(?:function|func|method)\s+([A-Za-z_][A-Za-z0-9_]*)\b|\b([a-z_][A-Za-z0-9_]{2,})\s*\(", corpus_text)
    functions = top_terms([item for pair in function_matches for item in pair if item])
    themes = top_terms([term for term in search_terms(corpus_text) if term in known_theme_words()], limit=20)
    rejected = [row for row in hypotheses if str(row.get("status", "")).startswith("Rejected") or row.get("closure_notes")]
    overhunted = [term for term, count in themes if count >= 3][:12]
    negative_space = negative_space_hints(contracts, themes, rejected)

    terms_path = out_dir / "known_issue_terms.csv"
    with terms_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "term", "count"])
        writer.writeheader()
        for kind, rows in [("contract_or_module", contracts), ("function", functions), ("theme", themes)]:
            for term, count in rows:
                writer.writerow({"kind": kind, "term": term, "count": count})

    report_path = out_dir / "known_issue_intel.md"
    lines = [
        "# Known Issue Intelligence",
        "",
        "## Source Inventory",
        *[f"- {source.get('title', '')} ({source.get('source_type', '')}; {source.get('fetch_status', '')}; chunks: {source.get('chunk_count', 0)}) {source.get('url') or source.get('file_path') or ''}" for source in sources],
        "",
        "## Top Contracts/Modules",
        *format_count_lines(contracts),
        "",
        "## Top Function Names",
        *format_count_lines(functions),
        "",
        "## Known Issue Themes",
        *format_count_lines(themes),
        "",
        "## Prior Rejected/Closed Hypotheses",
        *( [f"- {row.get('id')}: {row.get('title', '')} ({row.get('status', '')})" for row in rejected] or ["- None"] ),
        "",
        "## Overhunted Areas",
        *( [f"- {term}" for term in overhunted] or ["- None identified"] ),
        "",
        "## Negative-Space Hints",
        *( [f"- {hint}" for hint in negative_space] or ["- Review sparse modules and contracts absent from known issue themes."] ),
        "",
        "## Query Pack For ChatGPT",
        *[f"- {term} {theme}" for term, _ in contracts[:8] for theme, _ in themes[:3]],
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return {"known_intel": str(report_path), "known_terms": str(terms_path), "known_source_count": len(sources)}


def prepare_intel(run_path: Path) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    metadata = read_json(run_path / "metadata" / "run_metadata.json") if (run_path / "metadata" / "run_metadata.json").exists() else {}
    target = f"{metadata.get('target_name', '')} {metadata.get('program_url', '')}".lower()
    if "axelar" in target:
        try:
            seed_axelar_known_sources(run_path)
        except Exception as exc:
            warnings.append(f"Axelar known-source seed warning: {exc}")
    else:
        try:
            index_saved_scope_urls(run_path)
        except Exception as exc:
            warnings.append(f"Saved URL indexing warning: {exc}")
    try:
        known_dedupe(run_path)
        known_exports = known_export(run_path)
        intel = known_intel(run_path)
        packet = export_review_packet(run_path)
    except Exception as exc:
        errors.append(str(exc))
        raise
    return {
        "review_packet": packet.get("review_packet", ""),
        "chatgpt_packet": packet.get("chatgpt_packet", ""),
        "known_intel": intel.get("known_intel", ""),
        "known_terms": intel.get("known_terms", ""),
        "known_source_count": intel.get("known_source_count", 0),
        "known_csv": known_exports.get("known_csv", ""),
        "known_summary": known_exports.get("known_summary", ""),
        "warnings": warnings,
        "errors": errors,
    }


def index_saved_scope_urls(run_path: Path) -> None:
    metadata = read_json(run_path / "metadata" / "run_metadata.json") if (run_path / "metadata" / "run_metadata.json").exists() else {}
    resources = read_json(run_path / "scope" / "resources.json") if (run_path / "scope" / "resources.json").exists() else {}
    urls = [metadata.get("program_url", ""), *resources.get("urls", [])]
    for url in sorted({url for url in urls if url}):
        known_add_url(run_path, url, "scope", f"Saved scope URL - {url}", "Imported from saved run URLs.")


def known_chunks_for_intel(run_path: Path) -> list[dict]:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.text, s.title, s.source_type, s.fetch_status
                FROM known_chunks c
                JOIN known_sources s ON s.id = c.source_id
                ORDER BY s.id, c.chunk_index
                """
            ).fetchall()
        ]


def top_terms(values: Iterable[str], limit: int = 20, exclude: set[str] | None = None) -> list[tuple[str, int]]:
    exclude_lower = {item.lower() for item in (exclude or set())}
    counts: dict[str, int] = {}
    for value in values:
        term = str(value).strip()
        if len(term) < 3 or term.lower() in exclude_lower:
            continue
        counts[term] = counts.get(term, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]


def known_theme_words() -> set[str]:
    return {
        "accounting",
        "authorization",
        "bridge",
        "crosschain",
        "deflationary",
        "dos",
        "express",
        "fee",
        "gateway",
        "governance",
        "interchain",
        "lock",
        "manager",
        "oracle",
        "reentrancy",
        "reimbursement",
        "rounding",
        "scope",
        "slippage",
        "token",
        "transfer",
        "unlock",
        "validation",
    }


def format_count_lines(rows: list[tuple[str, int]]) -> list[str]:
    return [f"- {term}: {count}" for term, count in rows] or ["- None identified"]


def negative_space_hints(
    contracts: list[tuple[str, int]],
    themes: list[tuple[str, int]],
    rejected: list[dict],
) -> list[str]:
    hints = []
    rejected_titles = " ".join(row.get("title", "") for row in rejected).lower()
    theme_names = {term for term, _ in themes}
    for contract, _ in contracts[:8]:
        if contract.lower() not in rejected_titles:
            hints.append(f"Review {contract} paths not already represented in rejected hypotheses.")
    for missing in ["authorization", "oracle", "rounding", "validation", "slippage"]:
        if missing not in theme_names:
            hints.append(f"Known corpus has little {missing} coverage; check only if in scope and code suggests it.")
    return hints[:12] or ["No obvious negative-space hints from deterministic term extraction."]


def known_dedupe(run_path: Path) -> dict:
    with run_db(run_path) as conn:
        ensure_run_schema(conn)
        rows = [dict(row) for row in conn.execute("SELECT * FROM known_sources ORDER BY id").fetchall()]
        groups: dict[str, list[dict]] = {}
        for row in rows:
            key = infer_existing_source_key(conn, row) if row.get("source_type") == "rejection" else row.get("source_key") or infer_existing_source_key(conn, row)
            conn.execute("UPDATE known_sources SET source_key = ? WHERE id = ?", (key, row["id"]))
            row["source_key"] = key
            groups.setdefault(key, []).append(row)
        removed = 0
        merged = 0
        for key, items in groups.items():
            if len(items) < 2:
                continue
            winner = sorted(items, key=known_source_preference, reverse=True)[0]
            loser_ids = [item["id"] for item in items if item["id"] != winner["id"]]
            for loser_id in loser_ids:
                delete_known_source(conn, loser_id)
                removed += 1
            merged += 1
        conn.commit()
    return {"merged_groups": merged, "removed_sources": removed}


def seed_axelar_known_sources(run_path: Path) -> list[sqlite3.Row]:
    fetch_seeds = [
        ("Axelar Immunefi scope page", "scope", "https://immunefi.com/bug-bounty/axelarnetwork/scope/", "Immunefi Axelar scope page URL seed."),
        ("Axelar Immunefi resources page", "scope", "https://immunefi.com/bug-bounty/axelarnetwork/resources/", "Immunefi Axelar resources page URL seed."),
        ("Axelar Immunefi information page", "scope", "https://immunefi.com/bug-bounty/axelarnetwork/information/", "Immunefi Axelar information page URL seed."),
        ("Code4rena 2023 Axelar report", "report", "https://code4rena.com/reports/2023-07-axelar", "Code4rena public report URL seed."),
        ("Code4rena 2024 Axelar report", "report", "https://code4rena.com/reports/2024-08-axelar-network", "Code4rena public report URL seed."),
        ("Axelar ITS docs", "docs", "https://docs.axelar.dev/dev/send-tokens/interchain-tokens/intro/", "Axelar interchain token service docs URL seed."),
    ]
    note_seeds = [
        ("axelar-contract-deployments repo", "github", "https://github.com/axelarnetwork/axelar-contract-deployments", "Deployment registry repo URL seed."),
        ("axelar-configs repo", "github", "https://github.com/axelarnetwork/axelar-configs", "Configs repo URL seed."),
    ]
    rows = [known_add_url(run_path, url, source_type, title, notes) for title, source_type, url, notes in fetch_seeds]
    rows.extend(
        add_known_source(
            run_path,
            title,
            source_type,
            f"{title}\n{url}\n{notes}",
            url=url,
            notes=notes,
            fetch_status="stub",
        )
        for title, source_type, url, notes in note_seeds
    )
    index_closed_hypotheses(run_path)
    return rows


def index_closed_hypotheses(run_path: Path) -> None:
    try:
        rows = [dict(row) for row in list_hypotheses(run_path)]
    except sqlite3.Error:
        return
    for row in rows:
        status = row.get("status", "")
        if not (str(status).startswith("Rejected") or row.get("closure_notes")):
            continue
        text = "\n".join(
            str(row.get(key, ""))
            for key in [
                "id",
                "title",
                "status",
                "contract",
                "function",
                "hypothesis",
                "scope_mapping",
                "impact_mapping",
                "known_issue_check",
                "notes",
                "closure_notes",
            ]
            if row.get(key)
        )
        add_known_source(
            run_path,
            f"{row.get('id')} closed hypothesis - {row.get('title', '')}",
            "rejection",
            text,
            notes="Auto-indexed from closed/rejected tracker hypothesis.",
            fetch_status="rejection",
            source_key=f"rejection:{row.get('id', '').strip().lower()}",
        )


def export_review_packet(run_path: Path, hypothesis_ids: Iterable[str] | None = None) -> dict:
    exports = export_run(run_path)
    known_exports = known_export(run_path)
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
        Path("tracker") / "known_sources.csv",
        Path("tracker") / "known_sources.md",
        Path("known_intel") / "known_issue_intel.md",
        Path("known_intel") / "known_issue_terms.csv",
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
    return {"review_packet": str(packet), "chatgpt_packet": str(packet_md), "included_files": included, **exports, **known_exports}


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
    known_sources = known_list(run_path)
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
    lines.extend(["", "## Known Issue Corpus"])
    if known_sources:
        for source in known_sources[:25]:
            loc = source.get("url") or source.get("file_path") or ""
            lines.append(
                f"- {source.get('title', '')} ({source.get('source_type', '')}; "
                f"{source.get('fetch_status', '')}; chunks: {source.get('chunk_count', 0)}) {loc}"
            )
    else:
        lines.append("- No known issue sources indexed.")
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
        CREATE TABLE IF NOT EXISTS known_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            text_hash TEXT NOT NULL UNIQUE,
            fetch_status TEXT NOT NULL DEFAULT 'manual',
            source_key TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS known_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES known_sources(id)
        );
        CREATE TABLE IF NOT EXISTS known_hypothesis_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES known_sources(id)
        );
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS known_chunks_fts
            USING fts5(source_id UNINDEXED, title, source_type UNINDEXED, text)
            """
        )
    except sqlite3.Error:
        pass
    ensure_columns(
        conn,
        "hypotheses",
        {
            "status": "TEXT NOT NULL DEFAULT 'New'",
            "gate_decision": "TEXT NOT NULL DEFAULT ''",
            "closure_notes": "TEXT NOT NULL DEFAULT ''",
        },
    )
    ensure_columns(
        conn,
        "known_sources",
        {
            "fetch_status": "TEXT NOT NULL DEFAULT 'manual'",
            "source_key": "TEXT NOT NULL DEFAULT ''",
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


def validate_known_source_type(source_type: str) -> str:
    cleaned = source_type.strip().lower()
    if cleaned not in KNOWN_SOURCE_TYPES:
        raise ValueError(f"Invalid known source type: {source_type}. Allowed: {', '.join(KNOWN_SOURCE_TYPES)}")
    return cleaned


def known_source_key(title: str, source_type: str, digest: str, url: str = "") -> str:
    normalized = normalize_url_key(url)
    if normalized:
        return f"url:{normalized}"
    return f"{source_type}:{slugify(title)}:{digest}"


def normalize_url_key(url: str) -> str:
    cleaned = url.strip().lower()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    cleaned = re.sub(r"/+", "/", cleaned)
    return cleaned


def infer_existing_source_key(conn: sqlite3.Connection, row: dict) -> str:
    if row.get("source_type") == "rejection":
        rejection_id = extract_hypothesis_id(" ".join(str(row.get(key, "")) for key in ("title", "notes", "source_key")))
        if not rejection_id:
            chunk = conn.execute(
                "SELECT text FROM known_chunks WHERE source_id = ? ORDER BY chunk_index LIMIT 1",
                (row["id"],),
            ).fetchone()
            rejection_id = extract_hypothesis_id(chunk["text"] if chunk else "")
        if rejection_id:
            return f"rejection:{rejection_id}"
    url = row.get("url") or extract_first_url(row.get("notes", ""))
    if not url:
        chunk = conn.execute(
            "SELECT text FROM known_chunks WHERE source_id = ? ORDER BY chunk_index LIMIT 1",
            (row["id"],),
        ).fetchone()
        url = extract_first_url(chunk["text"] if chunk else "")
    if url:
        return f"url:{normalize_url_key(url)}"
    return known_source_key(row.get("title", ""), row.get("source_type", "manual"), row.get("text_hash", ""), "")


def extract_hypothesis_id(text: str) -> str:
    match = re.search(r"\bH-\d{1,6}\b", text or "", flags=re.IGNORECASE)
    return match.group(0).lower() if match else ""


def extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s)>\]]+", text or "")
    return match.group(0).rstrip(".,;") if match else ""


def known_source_preference(row: dict) -> tuple[int, int, int, str]:
    status_score = {
        "fetched": 50,
        "file": 40,
        "manual": 30,
        "rejection": 30,
        "stub": 10,
        "FETCH_FAILED": 5,
    }.get(row.get("fetch_status", ""), 0)
    has_url = 1 if row.get("url") else 0
    richness = len(str(row.get("notes", ""))) + len(str(row.get("text_hash", "")))
    fetched_at = str(row.get("fetched_at", ""))
    return (status_score, has_url, richness, fetched_at)


def should_replace_known_source(existing_status: str, new_status: str) -> bool:
    existing = {"fetch_status": existing_status, "id": 0}
    new = {"fetch_status": new_status, "id": 1}
    return known_source_preference(new) > known_source_preference(existing)


def delete_known_source(conn: sqlite3.Connection, source_id: int) -> None:
    chunk_ids = [row["id"] for row in conn.execute("SELECT id FROM known_chunks WHERE source_id = ?", (source_id,)).fetchall()]
    if chunk_ids and known_fts_available(conn):
        placeholders = ",".join("?" for _ in chunk_ids)
        conn.execute(f"DELETE FROM known_chunks_fts WHERE rowid IN ({placeholders})", chunk_ids)
    conn.execute("DELETE FROM known_chunks WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM known_hypothesis_links WHERE source_id = ?", (source_id,))
    conn.execute("DELETE FROM known_sources WHERE id = ?", (source_id,))


def normalize_known_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def chunk_text(text: str, size: int = 1200, overlap: int = 160) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(1, size - overlap)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + size]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def known_fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'known_chunks_fts'"
    ).fetchone()
    return row is not None


def search_terms(query: str) -> list[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "can",
        "may",
        "using",
        "when",
        "then",
        "than",
        "will",
        "are",
        "not",
        "issue",
        "hypothesis",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9_]{3,}", query.lower()):
        if term not in stop and term not in terms:
            terms.append(term)
    return terms or [query.strip().lower()]


def decorate_known_match(row: dict, query: str) -> dict:
    snippet = compact_snippet(row.get("snippet", ""), search_terms(query))
    row["snippet"] = snippet
    row["confidence"] = known_confidence(row, query)
    row["recommendation"] = known_match_recommendation(row["confidence"])
    return row


def compact_snippet(text: str, terms: list[str], radius: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    lower = clean.lower()
    positions = [lower.find(term.lower()) for term in terms if lower.find(term.lower()) >= 0]
    if not positions:
        return clean[: radius * 2]
    pos = min(positions)
    start = max(0, pos - radius)
    end = min(len(clean), pos + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"


def known_confidence(row: dict, query: str) -> str:
    terms = search_terms(query)
    haystack = f"{row.get('title', '')} {row.get('snippet', '')}".lower()
    hits = sum(1 for term in terms if term.lower() in haystack)
    if hits >= 5 or (hits >= 3 and row.get("source_type") in {"audit", "report", "rejection"}):
        return "High"
    if hits >= 2:
        return "Medium"
    return "Low"


def known_match_recommendation(confidence: str) -> str:
    if confidence == "High":
        return "likely duplicate"
    if confidence == "Medium":
        return "needs manual review"
    return "proceed"


def known_recommendation(matches: list[dict]) -> str:
    confidences = [match.get("confidence") for match in matches]
    if "High" in confidences:
        return "likely duplicate"
    if "Medium" in confidences:
        return "needs manual review"
    return "proceed"


def score_known_match_for_hypothesis(match: dict, hypothesis: dict) -> dict:
    text = f"{match.get('title', '')} {match.get('snippet', '')}".lower()
    features = []
    contract = str(hypothesis.get("contract", "")).strip().lower()
    function = str(hypothesis.get("function", "")).strip().lower()
    if contract and contract in text:
        features.append("same contract name")
    if function and function in text:
        features.append("same function name")
    mechanism_overlap = keyword_overlap(bug_mechanism_terms(hypothesis), text)
    if mechanism_overlap >= 2:
        features.append("same bug mechanism keywords")
    impact_overlap = keyword_overlap(impact_terms(hypothesis), text)
    if impact_overlap >= 1:
        features.append("same impact class")
    condition_overlap = keyword_overlap(asset_condition_terms(hypothesis), text)
    if condition_overlap >= 1:
        features.append("same asset/config condition")

    boilerplate = is_boilerplate_known_text(text)
    self_history = is_self_history_match(match, hypothesis)
    if self_history:
        confidence = "High"
        kind = "self-history match"
        recommendation = "self-history match"
    else:
        has_mechanism = "same bug mechanism keywords" in features
        has_anchor = any(
            feature in features
            for feature in ("same function name", "same contract name", "same impact class")
        )
        if boilerplate:
            confidence = "Low"
        elif has_mechanism and has_anchor:
            confidence = "High"
        elif len(features) == 1:
            confidence = "Medium"
        elif "same contract name" in features and "same asset/config condition" in features:
            confidence = "Medium"
        elif len(features) >= 2:
            confidence = "Medium"
        else:
            confidence = "Low"
        kind = "public known match" if confidence in {"High", "Medium"} else "weak context match"
        recommendation = known_match_recommendation(confidence)
    scored = dict(match)
    scored["feature_matches"] = features
    scored["confidence"] = confidence
    scored["recommendation"] = recommendation
    scored["match_kind"] = kind
    return scored


def check_known_recommendation(self_history_matches: list[dict], public_known_matches: list[dict]) -> str:
    high_public = any(match.get("confidence") == "High" for match in public_known_matches)
    if high_public:
        return "likely public duplicate"
    if self_history_matches and not public_known_matches:
        return "self-history match; no strong public duplicate"
    if self_history_matches and public_known_matches:
        return "self-history match; public matches need manual review"
    if public_known_matches:
        return "needs manual review"
    return "proceed"


def is_self_history_match(match: dict, hypothesis: dict) -> bool:
    if match.get("source_type") != "rejection":
        return False
    hypothesis_id = str(hypothesis.get("id", "")).lower()
    title = str(match.get("title", "")).lower()
    return bool(hypothesis_id and hypothesis_id in title)


def bug_mechanism_terms(hypothesis: dict) -> list[str]:
    text = " ".join(
        str(hypothesis.get(key, ""))
        for key in ["title", "hypothesis", "notes", "known_issue_check"]
    )
    mechanism_words = {
        "reentrancy",
        "oracle",
        "stale",
        "rounding",
        "precision",
        "overflow",
        "underflow",
        "reimbursement",
        "accounting",
        "mismatch",
        "fee",
        "deflationary",
        "authorization",
        "access",
        "signature",
        "replay",
        "slippage",
        "liquidation",
        "donation",
        "inflation",
        "transfer",
        "lock",
        "unlock",
        "express",
    }
    return [term for term in search_terms(text) if term in mechanism_words]


def impact_terms(hypothesis: dict) -> list[str]:
    text = " ".join(str(hypothesis.get(key, "")) for key in ["impact_mapping", "hypothesis", "title"]).lower()
    classes = []
    for terms in [
        {"loss", "funds", "theft", "steal", "drain"},
        {"insolvency", "undercollateralized", "bad", "debt"},
        {"dos", "denial", "griefing", "stuck"},
        {"governance", "admin", "privilege"},
        {"price", "oracle", "manipulation"},
    ]:
        if any(term in text for term in terms):
            classes.extend(terms)
    return sorted(classes)


def asset_condition_terms(hypothesis: dict) -> list[str]:
    text = " ".join(str(hypothesis.get(key, "")) for key in ["title", "hypothesis", "scope_mapping", "impact_mapping"]).lower()
    condition_words = {
        "fee",
        "fee-on-transfer",
        "deflationary",
        "rebasing",
        "tokenmanager",
        "token",
        "lock_unlock_fee",
        "custom",
        "scoped",
        "profile",
        "asset",
        "manager",
        "exempt",
    }
    return [term for term in search_terms(text) if term in condition_words]


def keyword_overlap(terms: list[str], text: str) -> int:
    lower = text.lower()
    return sum(1 for term in set(terms) if term and term.lower() in lower)


def is_boilerplate_known_text(text: str) -> bool:
    boilerplate = [
        "gas optimization",
        "gas optimizations",
        "non-critical",
        "quality assurance",
        "informational",
        "low risk",
        "contest details",
        "overview",
    ]
    security_terms = ["loss", "funds", "drain", "theft", "reentrancy", "oracle", "authorization", "accounting"]
    return any(term in text for term in boilerplate) and not any(term in text for term in security_terms)


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
