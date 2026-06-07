from __future__ import annotations

from pathlib import Path
import subprocess
import zipfile

from fastapi.testclient import TestClient

from workbench.db import connect
from workbench.app import app
from workbench import services
from workbench import web3bb
from workbench.services import (
    add_hypothesis,
    add_hypothesis_from_file,
    create_target,
    ensure_schema,
    get_hypothesis,
    run_mock_tool,
    update_manual_verdict,
)


def make_conn(tmp_path):
    conn = connect(tmp_path / "workbench.db")
    ensure_schema(conn)
    return conn


def test_create_target(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "RUNS_ROOT", tmp_path / "runs")
    with make_conn(tmp_path) as conn:
        target = create_target(conn, "demo target", str(tmp_path / "repo"), str(tmp_path / "scope.md"))

    assert target["name"] == "demo-target"
    assert (tmp_path / "runs" / "demo-target" / "target.json").exists()
    assert (tmp_path / "runs" / "demo-target" / "hypotheses").is_dir()


def test_add_hypothesis_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "RUNS_ROOT", tmp_path / "runs")
    with make_conn(tmp_path) as conn:
        create_target(conn, "demo", str(tmp_path / "repo"), str(tmp_path / "scope.md"))
        hypothesis = add_hypothesis(conn, "demo", "Reentrancy", "A markdown hypothesis")

    assert hypothesis["tool_status"] == "PENDING"
    assert hypothesis["manual_verdict"] == "UNREVIEWED"
    assert (tmp_path / "runs" / "demo" / "hypotheses" / f"{hypothesis['id']}.md").exists()


def test_add_hypothesis_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "RUNS_ROOT", tmp_path / "runs")
    hypothesis_file = tmp_path / "hypothesis.md"
    hypothesis_file.write_text("# Price oracle drift\nBody", encoding="utf-8")
    with make_conn(tmp_path) as conn:
        create_target(conn, "demo", str(tmp_path / "repo"), str(tmp_path / "scope.md"))
        hypothesis = add_hypothesis_from_file(conn, "demo", str(hypothesis_file))

    assert hypothesis["title"] == "Price oracle drift"
    assert hypothesis["description"] == "# Price oracle drift\nBody"


def test_fake_foundry_pass_updates_record_and_saves_log(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "RUNS_ROOT", tmp_path / "runs")
    with make_conn(tmp_path) as conn:
        create_target(conn, "demo", str(tmp_path / "repo"), str(tmp_path / "scope.md"))
        hypothesis = add_hypothesis(conn, "demo", "Foundry pass", "Proof")
        update_manual_verdict(conn, "demo", hypothesis["id"], "KEEP", "Worth keeping")
        run = run_mock_tool(conn, "demo", hypothesis["id"], "foundry", "mock:foundry:pass")
        updated = get_hypothesis(conn, "demo", hypothesis["id"])

    assert run["tool_status"] == "PASS"
    assert updated["tool_status"] == "PASS"
    assert updated["manual_verdict"] == "KEEP"
    assert Path(run["raw_output_path"]).exists()
    assert "1 passed; 0 failed" in Path(run["raw_output_path"]).read_text(encoding="utf-8")


def test_fake_slither_needs_review_updates_record_and_saves_log(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "RUNS_ROOT", tmp_path / "runs")
    with make_conn(tmp_path) as conn:
        create_target(conn, "demo", str(tmp_path / "repo"), str(tmp_path / "scope.md"))
        hypothesis = add_hypothesis(conn, "demo", "Slither finding", "Review detector output", tool="slither")
        run = run_mock_tool(conn, "demo", hypothesis["id"], "slither", "mock:slither:needs_review")
        updated = get_hypothesis(conn, "demo", hypothesis["id"])

    assert run["tool_status"] == "NEEDS_REVIEW"
    assert updated["tool_status"] == "NEEDS_REVIEW"
    assert "high=1" in updated["summary"]
    assert Path(run["raw_output_path"]).exists()


def test_web3bb_run_ingest_seed_and_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "test").mkdir()
    (source / "foundry.toml").write_text('[profile.default]\nsrc = "src"\ntest = "test"\n', encoding="utf-8")
    (source / "src" / "BridgeToken.sol").write_text(
        """
        // SPDX-License-Identifier: MIT
        pragma solidity ^0.8.20;
        contract BridgeToken {
            address public owner;
            function mint(address to, uint256 amount) external {}
        }
        """,
        encoding="utf-8",
    )
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source))

    run_path = web3bb.init_run("Demo Target", "https://example.com/program", archive)
    project = web3bb.ingest_run(run_path)
    web3bb.scope_run(run_path, ["https://example.com/docs"])
    seeded = web3bb.seed_axelar(run_path)
    exports = web3bb.export_run(run_path)

    assert project["project_type"] == "foundry"
    assert "^0.8.20" in project["solidity_versions"]
    assert seeded["id"] == "H-001"
    assert (run_path / "metadata" / "web3bb.sqlite").exists()
    assert (run_path / "scope" / "scope_brief.md").exists()
    assert Path(exports["csv"]).exists()
    assert Path(exports["xlsx"]).exists()


def test_close_hypothesis_sets_lifecycle_status_and_exports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_path = tmp_path / "run"
    (run_path / "metadata").mkdir(parents=True)
    web3bb.write_json(run_path / "metadata" / "run_metadata.json", {"target_name": "Demo", "program_url": ""})
    hypothesis = web3bb.add_hypothesis(
        run_path,
        {
            "id": "H-001",
            "title": "Lifecycle",
            "hypothesis": "Demo",
            "status": "PoC Validated",
        },
    )

    row = web3bb.close_hypothesis(
        run_path,
        hypothesis["id"],
        "Rejected - No Impact / Needs Scoped Asset",
        "Accounting mismatch validated, but no scoped fee-asymmetric asset was found.",
    )

    md = (run_path / "hypotheses" / "H-001.md").read_text(encoding="utf-8")
    tracker = (run_path / "tracker" / "tracker.csv").read_text(encoding="utf-8")
    summary = (run_path / "tracker" / "summary.md").read_text(encoding="utf-8")

    assert row["status"] == "Rejected - No Impact / Needs Scoped Asset"
    assert "## Closure Notes" in md
    assert "Accounting mismatch validated" in md
    assert "closure_notes" in tracker
    assert "Rejected - No Impact / Needs Scoped Asset" in tracker
    assert "- Status: Rejected - No Impact / Needs Scoped Asset" in summary


def test_import_leads_from_csv_defaults_and_exports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_path = tmp_path / "run"
    (run_path / "metadata").mkdir(parents=True)
    web3bb.write_json(run_path / "metadata" / "run_metadata.json", {"target_name": "Demo", "program_url": ""})
    leads = tmp_path / "leads.csv"
    leads.write_text(
        "title,target,contract,function,hypothesis,source,next_action\n"
        "Oracle stale price,Demo,OracleVault,withdraw,Withdraw may use stale price,Manual,Build PoC\n"
        "Allowance drift,Demo,Token,,Allowance may be reused,,Review approvals\n",
        encoding="utf-8",
    )

    rows = web3bb.import_leads(run_path, leads)

    assert [row["id"] for row in rows] == ["H-001", "H-002"]
    assert rows[0]["title"] == "Oracle stale price"
    assert rows[0]["poc_status"] == "Needs PoC"
    assert rows[0]["validation_status"] == "Unvalidated"
    assert rows[1]["source"] == "Manual"
    assert (run_path / "hypotheses" / "H-001.md").exists()
    tracker = (run_path / "tracker" / "tracker.csv").read_text(encoding="utf-8")
    assert "Oracle stale price" in tracker
    assert "Allowance drift" in tracker


def test_import_leads_from_markdown_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_path = tmp_path / "run"
    (run_path / "metadata").mkdir(parents=True)
    web3bb.write_json(run_path / "metadata" / "run_metadata.json", {"target_name": "Demo", "program_url": ""})
    leads = tmp_path / "leads.md"
    leads.write_text(
        """# Reimbursement mismatch

## Contract
Bridge

## Function
execute

## Hypothesis
Executor may be reimbursed too much.

## Source
Manual notes

## Evidence
Trace shows amount mismatch.

## Scope Mapping
Bridge is in scope.

## Impact Mapping
Direct loss if exploitable.

## Next Action
Write Foundry test.
""",
        encoding="utf-8",
    )

    rows = web3bb.import_leads(run_path, leads)

    assert len(rows) == 1
    assert rows[0]["id"] == "H-001"
    assert rows[0]["title"] == "Reimbursement mismatch"
    assert rows[0]["contract"] == "Bridge"
    assert rows[0]["function"] == "execute"
    assert rows[0]["hypothesis"] == "Executor may be reimbursed too much."
    assert rows[0]["source"] == "Manual notes"
    assert rows[0]["manual_evidence"] == "Trace shows amount mismatch."
    assert rows[0]["scope_mapping"] == "Bridge is in scope."
    assert rows[0]["impact_mapping"] == "Direct loss if exploitable."
    assert rows[0]["next_action"] == "Write Foundry test."


def test_export_review_packet_collects_review_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_path = tmp_path / "run"
    for folder in ("metadata", "scope", "tracker", "hypotheses", "poc", "tool-output", "repo"):
        (run_path / folder).mkdir(parents=True)
    web3bb.write_json(
        run_path / "metadata" / "run_metadata.json",
        {"target_name": "Demo", "program_url": "https://example.com/program", "created_at": "2026-06-07T00:00:00+00:00"},
    )
    web3bb.write_json(run_path / "scope" / "resources.json", {"urls": ["https://example.com/program", "https://example.com/scope"]})
    web3bb.write_json(run_path / "metadata" / "project_detect.json", {"project_type": "foundry"})
    web3bb.write_json(run_path / "metadata" / "profiles.json", {"default": {}})
    web3bb.write_json(run_path / "metadata" / "tool_versions.json", {})
    (run_path / "scope" / "scope_brief.md").write_text("# Scope Brief\n", encoding="utf-8")
    (run_path / "poc" / "note.md").write_text("# PoC note\n", encoding="utf-8")

    first = web3bb.add_hypothesis(run_path, {"title": "Current", "hypothesis": "Still active", "next_action": "Review"})
    second = web3bb.add_hypothesis(run_path, {"title": "Closed", "hypothesis": "No impact"})
    web3bb.close_hypothesis(run_path, second["id"], "Rejected - No Impact", "No recoverable value.")

    out_dir = run_path / "tool-output" / "forge" / "20260607T000000Z-build-001"
    out_dir.mkdir(parents=True)
    stdout = out_dir / "stdout.txt"
    stderr = out_dir / "stderr.txt"
    stdout.write_text("forge ok\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    web3bb.write_json(out_dir / "execution.json", {"tool": "forge"})
    with web3bb.run_db(run_path) as conn:
        web3bb.ensure_run_schema(conn)
        conn.execute(
            """
            INSERT INTO tool_executions (tool, command, start_time, end_time, exit_code, stdout_path, stderr_path, parsed_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("forge", "forge build", "start", "end", 0, str(stdout), str(stderr), "forge completed successfully."),
        )
        conn.commit()

    result = web3bb.export_review_packet(run_path)
    packet = Path(result["review_packet"])
    packet_md = Path(result["chatgpt_packet"])

    assert (packet / "scope" / "scope_brief.md").exists()
    assert (packet / "tracker" / "tracker.csv").exists()
    assert (packet / "hypotheses" / first["id"]).with_suffix(".md").exists()
    assert (packet / "poc" / "note.md").exists()
    assert (packet / "tool-output" / "forge" / "20260607T000000Z-build-001" / "stdout.txt").exists()
    text = packet_md.read_text(encoding="utf-8")
    assert "Current Hypotheses" in text
    assert "Closed Or Rejected Hypotheses" in text
    assert "forge completed successfully" in text
    assert "forge ok" in text


def test_web_health_route():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["local"] == "127.0.0.1"


def test_web_review_packet_route_exports_packet(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    run_path = tmp_path / "run"
    (run_path / "metadata").mkdir(parents=True)
    (run_path / "scope").mkdir()
    web3bb.write_json(
        run_path / "metadata" / "run_metadata.json",
        {"target_name": "Demo", "program_url": "https://example.com/program", "created_at": "2026-06-07T00:00:00+00:00"},
    )
    web3bb.write_json(run_path / "metadata" / "tool_versions.json", {})
    (run_path / "scope" / "scope_brief.md").write_text("# Scope\n", encoding="utf-8")
    web3bb.add_hypothesis(run_path, {"title": "Route packet", "hypothesis": "Export through web route."})

    response = client.post("/api/review-packet", json={"run": str(run_path)})

    assert response.status_code == 200
    payload = response.json()
    assert Path(payload["chatgpt_packet"]).exists()
    assert (run_path / "review_packet" / "tracker" / "tracker.csv").exists()
    assert "Route packet" in Path(payload["chatgpt_packet"]).read_text(encoding="utf-8")


def test_web3bb_init_accepts_source_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source-dir"
    (source / "contracts").mkdir(parents=True)
    (source / "hardhat.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    (source / "contracts" / "Vault.sol").write_text(
        """
        // SPDX-License-Identifier: MIT
        pragma solidity ^0.8.19;
        contract Vault {}
        """,
        encoding="utf-8",
    )

    run_path = web3bb.init_run("Directory Target", "https://example.com/program", source)
    project = web3bb.ingest_run(run_path)

    assert (run_path / "input" / "source-dir" / "hardhat.config.js").exists()
    assert (run_path / "repo" / "hardhat.config.js").exists()
    assert (run_path / "repo" / "contracts" / "Vault.sol").exists()
    assert project["project_type"] == "hardhat"
    assert "^0.8.19" in project["solidity_versions"]


def test_doctor_runs_windows_cmd_wrappers_with_shell(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(web3bb, "DOCTOR_TOOLS", {"npm": ["npm", "--version"]})
    monkeypatch.setattr(web3bb.os, "name", "nt")
    monkeypatch.setattr(web3bb.shutil, "which", lambda exe: f"C:\\Tools\\{exe}.CMD")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="10.0.0\n", stderr="")

    monkeypatch.setattr(web3bb.subprocess, "run", fake_run)

    result = web3bb.doctor(tmp_path)

    assert result["npm"]["detected"] is True
    assert result["npm"]["version"] == "10.0.0"
    assert calls == [
        (
            "npm --version",
            {"capture_output": True, "text": True, "timeout": 10, "shell": True},
        )
    ]


def test_execute_tool_decodes_invalid_bytes_and_none_stderr(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, stdout=b"ok\xffbad\x80\n", stderr=None)

    monkeypatch.setattr(web3bb.subprocess, "run", fake_run)

    run_path = tmp_path / "run"
    cwd = tmp_path / "repo"
    cwd.mkdir(parents=True)

    record = web3bb.execute_tool(run_path, cwd, "fake-tool", ["fake-tool", "--version"])

    stdout_text = Path(record["stdout_path"]).read_text(encoding="utf-8")
    stderr_text = Path(record["stderr_path"]).read_text(encoding="utf-8")

    assert record["exit_code"] == 1
    assert "ok" in stdout_text
    assert "bad" in stdout_text
    assert "\ufffd" in stdout_text
    assert stderr_text == ""
    assert calls[0][1]["text"] is False


def test_execute_tool_uses_unique_output_dirs_in_same_second(tmp_path, monkeypatch):
    outputs = [b"first\n", b"second\n"]

    monkeypatch.setattr(web3bb, "timestamp", lambda: "20260606T153942Z")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=outputs.pop(0), stderr=b"")

    monkeypatch.setattr(web3bb.subprocess, "run", fake_run)

    run_path = tmp_path / "run"
    cwd = tmp_path / "repo"
    cwd.mkdir(parents=True)

    first = web3bb.execute_tool(run_path, cwd, "forge", ["forge", "build"])
    second = web3bb.execute_tool(run_path, cwd, "forge", ["forge", "build"])

    first_stdout = Path(first["stdout_path"])
    second_stdout = Path(second["stdout_path"])

    assert first_stdout.parent != second_stdout.parent
    assert first_stdout.parent.name == "20260606T153942Z-build-001"
    assert second_stdout.parent.name == "20260606T153942Z-build-002"
    assert first_stdout.read_text(encoding="utf-8") == "first\n"
    assert second_stdout.read_text(encoding="utf-8") == "second\n"
    assert first["profile"] == ""
    assert first["command_profile"] == "build"
    assert first["command_args"] == ["forge", "build"]


def test_tool_command_profile_uses_stable_short_known_names():
    assert web3bb.tool_command_profile(["forge", "build"]) == "build"
    assert web3bb.tool_command_profile(["forge", "test"]) == "test"
    assert web3bb.tool_command_profile(["slither", ".", "--compile-force-framework", "foundry"]) == "slither"
    assert web3bb.tool_command_profile(["semgrep", "--config", "auto", "--json", "."]) == "semgrep"
    assert web3bb.tool_command_profile(["aderyn", "."]) == "aderyn"
    assert web3bb.tool_command_profile(["surya", "describe", "contracts/**/*.sol"]) == "surya"
    assert web3bb.tool_command_profile(["sol2uml", "class", "."]) == "sol2uml"


def test_execute_tool_records_foundry_profile_in_folder_json_and_env(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(web3bb, "timestamp", lambda: "20260606T153942Z")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"profiled\n", stderr=b"")

    monkeypatch.setattr(web3bb.subprocess, "run", fake_run)

    run_path = tmp_path / "run"
    cwd = tmp_path / "repo"
    cwd.mkdir(parents=True)

    record = web3bb.execute_tool(
        run_path,
        cwd,
        "forge",
        ["forge", "build"],
        profile="mainnet",
        env={"FOUNDRY_PROFILE": "mainnet"},
    )
    execution = web3bb.read_json(Path(record["stdout_path"]).parent / "execution.json")

    assert Path(record["stdout_path"]).parent.name == "20260606T153942Z-mainnet-build-001"
    assert record["profile"] == "mainnet"
    assert execution["profile"] == "mainnet"
    assert execution["command_profile"] == "build"
    assert execution["env"] == {"FOUNDRY_PROFILE": "mainnet"}
    assert calls[0][1]["env"]["FOUNDRY_PROFILE"] == "mainnet"


def test_scan_all_profiles_runs_profiled_foundry_and_slither(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_path = tmp_path / "run"
    (run_path / "metadata").mkdir(parents=True)
    (run_path / "repo").mkdir()
    web3bb.write_json(
        run_path / "metadata" / "project_detect.json",
        {"foundry_toml": ["foundry.toml"], "test_folders": ["test"]},
    )
    web3bb.write_json(run_path / "metadata" / "profiles.json", {"default": {}, "lite": {}})

    monkeypatch.setattr(
        web3bb,
        "doctor",
        lambda output_dir=None: {
            "forge": {"detected": True, "version": "", "path": "", "install_hint": ""},
            "slither": {"detected": True, "version": "", "path": "", "install_hint": ""},
        },
    )
    monkeypatch.setattr(web3bb, "store_tools", lambda run_path, tools: None)

    calls = []

    def fake_execute(run_path_arg, cwd, tool, command, profile=None, env=None):
        calls.append({"tool": tool, "command": command, "profile": profile, "env": env})
        return calls[-1]

    monkeypatch.setattr(web3bb, "execute_tool", fake_execute)

    executions = web3bb.scan_run(run_path, all_profiles=True)

    assert executions == calls
    assert calls == [
        {"tool": "forge", "command": ["forge", "build"], "profile": "default", "env": {"FOUNDRY_PROFILE": "default"}},
        {"tool": "forge", "command": ["forge", "test"], "profile": "default", "env": {"FOUNDRY_PROFILE": "default"}},
        {"tool": "forge", "command": ["forge", "build"], "profile": "lite", "env": {"FOUNDRY_PROFILE": "lite"}},
        {"tool": "forge", "command": ["forge", "test"], "profile": "lite", "env": {"FOUNDRY_PROFILE": "lite"}},
        {
            "tool": "slither",
            "command": ["slither", ".", "--compile-force-framework", "foundry", "--json", "slither.json"],
            "profile": "default",
            "env": {"FOUNDRY_PROFILE": "default"},
        },
        {
            "tool": "slither",
            "command": ["slither", ".", "--compile-force-framework", "foundry", "--json", "slither.json"],
            "profile": "lite",
            "env": {"FOUNDRY_PROFILE": "lite"},
        },
    ]
