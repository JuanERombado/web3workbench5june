from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .db import init_db
from .models import MANUAL_VERDICTS, TOOLS
from .tool_runner import make_mock_run, parse_tool_output

RUNS_ROOT = Path("workbench_runs")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "target"


def ensure_schema(conn: sqlite3.Connection) -> None:
    init_db(conn)


def create_target(conn: sqlite3.Connection, name: str, repo_path: str, scope_path: str) -> sqlite3.Row:
    ensure_schema(conn)
    target = slugify(name)
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO targets (name, repo_path, scope_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            repo_path = excluded.repo_path,
            scope_path = excluded.scope_path,
            updated_at = excluded.updated_at
        """,
        (target, repo_path, scope_path, stamp, stamp),
    )
    conn.commit()
    target_root = target_root_path(target)
    for folder in ("hypotheses", "tool_runs", "logs", "evidence"):
        (target_root / folder).mkdir(parents=True, exist_ok=True)
    (target_root / "target.json").write_text(
        json.dumps({"name": target, "repo_path": repo_path, "scope_path": scope_path}, indent=2),
        encoding="utf-8",
    )
    return get_target(conn, target)


def get_target(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM targets WHERE name = ?", (slugify(name),)).fetchone()
    if row is None:
        raise ValueError(f"Target not found: {name}")
    return row


def list_targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    ensure_schema(conn)
    return conn.execute("SELECT * FROM targets ORDER BY updated_at DESC").fetchall()


def add_hypothesis(
    conn: sqlite3.Connection,
    target: str,
    title: str,
    description: str,
    affected_files: str = "",
    affected_functions: str = "",
    attacker_role: str = "",
    expected_impact: str = "",
    tool: str = "foundry",
    command: str = "",
) -> sqlite3.Row:
    ensure_schema(conn)
    target_name = slugify(target)
    get_target(conn, target_name)
    if tool not in TOOLS:
        raise ValueError(f"Unsupported tool: {tool}")
    stamp = now_iso()
    cur = conn.execute(
        """
        INSERT INTO hypotheses (
            target, title, description, affected_files, affected_functions,
            attacker_role, expected_impact, tool, command, tool_status,
            manual_verdict, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'UNREVIEWED', ?, ?)
        """,
        (
            target_name,
            title.strip() or "Untitled hypothesis",
            description,
            affected_files,
            affected_functions,
            attacker_role,
            expected_impact,
            tool,
            command,
            stamp,
            stamp,
        ),
    )
    hypothesis_id = cur.lastrowid
    conn.commit()
    path = target_root_path(target_name) / "hypotheses" / f"{hypothesis_id}.md"
    path.write_text(description, encoding="utf-8")
    return get_hypothesis(conn, target_name, int(hypothesis_id))


def add_hypothesis_from_file(conn: sqlite3.Connection, target: str, file_path: str, tool: str = "foundry") -> sqlite3.Row:
    path = Path(file_path)
    description = path.read_text(encoding="utf-8")
    title = _title_from_text(description, path.stem)
    return add_hypothesis(conn, target, title, description, tool=tool)


def get_hypothesis(conn: sqlite3.Connection, target: str, hypothesis_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM hypotheses WHERE target = ? AND id = ?",
        (slugify(target), hypothesis_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Hypothesis not found: {hypothesis_id}")
    return row


def list_hypotheses(conn: sqlite3.Connection, target: str | None = None) -> list[sqlite3.Row]:
    ensure_schema(conn)
    if target:
        return conn.execute(
            "SELECT * FROM hypotheses WHERE target = ? ORDER BY updated_at DESC, id DESC",
            (slugify(target),),
        ).fetchall()
    return conn.execute("SELECT * FROM hypotheses ORDER BY updated_at DESC, id DESC").fetchall()


def update_manual_verdict(
    conn: sqlite3.Connection,
    target: str,
    hypothesis_id: int,
    manual_verdict: str,
    decision_notes: str | None = None,
) -> sqlite3.Row:
    if manual_verdict not in MANUAL_VERDICTS:
        raise ValueError(f"Unsupported manual verdict: {manual_verdict}")
    stamp = now_iso()
    if decision_notes is None:
        conn.execute(
            "UPDATE hypotheses SET manual_verdict = ?, updated_at = ? WHERE target = ? AND id = ?",
            (manual_verdict, stamp, slugify(target), hypothesis_id),
        )
    else:
        conn.execute(
            """
            UPDATE hypotheses
            SET manual_verdict = ?, decision_notes = ?, updated_at = ?
            WHERE target = ? AND id = ?
            """,
            (manual_verdict, decision_notes, stamp, slugify(target), hypothesis_id),
        )
    conn.commit()
    return get_hypothesis(conn, target, hypothesis_id)


def run_mock_tool(conn: sqlite3.Connection, target: str, hypothesis_id: int, tool: str, command: str) -> sqlite3.Row:
    ensure_schema(conn)
    target_name = slugify(target)
    get_target(conn, target_name)
    hypothesis = get_hypothesis(conn, target_name, hypothesis_id)
    if tool not in TOOLS:
        raise ValueError(f"Unsupported tool: {tool}")

    mock = make_mock_run(tool, command)
    parsed = parse_tool_output(tool, mock.raw_output, mock.exit_code, mock.parser_payload)
    stamp = now_iso()
    cur = conn.execute(
        """
        INSERT INTO tool_runs (
            hypothesis_id, target, tool, command, tool_status, raw_output_path,
            summary, created_at, exit_code, parser_metadata
        )
        VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?)
        """,
        (
            hypothesis["id"],
            target_name,
            tool,
            command,
            parsed.tool_status,
            parsed.summary,
            stamp,
            mock.exit_code,
            json.dumps(parsed.metadata),
        ),
    )
    run_id = int(cur.lastrowid)
    run_root = target_root_path(target_name) / "tool_runs" / str(run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "command.txt").write_text(command, encoding="utf-8")
    (run_root / "result.json").write_text(
        json.dumps(
            {
                "tool_status": parsed.tool_status,
                "summary": parsed.summary,
                "metadata": parsed.metadata,
                "exit_code": mock.exit_code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log_path = target_root_path(target_name) / "logs" / f"{run_id}.log"
    log_path.write_text(mock.raw_output, encoding="utf-8")
    raw_output_path = str(log_path)

    conn.execute("UPDATE tool_runs SET raw_output_path = ? WHERE id = ?", (raw_output_path, run_id))
    conn.execute(
        """
        UPDATE hypotheses
        SET tool = ?, command = ?, tool_status = ?, raw_output_path = ?,
            summary = ?, updated_at = ?
        WHERE id = ? AND target = ?
        """,
        (tool, command, parsed.tool_status, raw_output_path, parsed.summary, stamp, hypothesis_id, target_name),
    )
    conn.commit()
    return get_tool_run(conn, run_id)


def get_tool_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tool_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Tool run not found: {run_id}")
    return row


def list_tool_runs(conn: sqlite3.Connection, target: str | None = None) -> list[sqlite3.Row]:
    ensure_schema(conn)
    if target:
        return conn.execute(
            "SELECT * FROM tool_runs WHERE target = ? ORDER BY created_at DESC, id DESC",
            (slugify(target),),
        ).fetchall()
    return conn.execute("SELECT * FROM tool_runs ORDER BY created_at DESC, id DESC").fetchall()


def target_root_path(target: str) -> Path:
    return RUNS_ROOT / slugify(target)


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped[:80]
    return fallback
