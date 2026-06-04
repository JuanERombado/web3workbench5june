from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(os.environ.get("WORKBENCH_DB", "workbench.db"))


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            repo_path TEXT NOT NULL,
            scope_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            affected_files TEXT NOT NULL DEFAULT '',
            affected_functions TEXT NOT NULL DEFAULT '',
            attacker_role TEXT NOT NULL DEFAULT '',
            expected_impact TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT 'foundry',
            command TEXT NOT NULL DEFAULT '',
            tool_status TEXT NOT NULL DEFAULT 'PENDING',
            manual_verdict TEXT NOT NULL DEFAULT 'UNREVIEWED',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            raw_output_path TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            decision_notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (target) REFERENCES targets(name) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tool_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id INTEGER NOT NULL,
            target TEXT NOT NULL,
            tool TEXT NOT NULL,
            command TEXT NOT NULL,
            tool_status TEXT NOT NULL,
            raw_output_path TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            parser_metadata TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id) ON DELETE CASCADE,
            FOREIGN KEY (target) REFERENCES targets(name) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
