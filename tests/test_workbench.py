from __future__ import annotations

from pathlib import Path
import zipfile

from workbench.db import connect
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
