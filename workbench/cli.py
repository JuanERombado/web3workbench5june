from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass

import uvicorn

from .db import connect
from .services import (
    add_hypothesis_from_file,
    create_target,
    ensure_schema,
    list_hypotheses,
    list_targets,
    run_mock_tool,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m workbench")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    init_target = subparsers.add_parser("init-target")
    init_target.add_argument("--name", required=True)
    init_target.add_argument("--repo", required=True)
    init_target.add_argument("--scope", required=True)

    add_hypothesis = subparsers.add_parser("add-hypothesis")
    add_hypothesis.add_argument("--target", required=True)
    add_hypothesis.add_argument("--file", required=True)
    add_hypothesis.add_argument("--tool", default="foundry", choices=["foundry", "slither"])

    run_tool = subparsers.add_parser("run-tool")
    run_tool.add_argument("--target", required=True)
    run_tool.add_argument("--hypothesis", required=True, type=int)
    run_tool.add_argument("--tool", required=True, choices=["foundry", "slither"])
    run_tool.add_argument("--command", required=True)

    status = subparsers.add_parser("status")
    status.add_argument("--target", required=True)

    args = parser.parse_args()
    if args.command_name == "serve":
        uvicorn.run("workbench.app:app", host=args.host, port=args.port, reload=False)
        return

    with connect() as conn:
        ensure_schema(conn)
        if args.command_name == "init-target":
            print_json(create_target(conn, args.name, args.repo, args.scope))
        elif args.command_name == "add-hypothesis":
            print_json(add_hypothesis_from_file(conn, args.target, args.file, args.tool))
        elif args.command_name == "run-tool":
            print_json(run_mock_tool(conn, args.target, args.hypothesis, args.tool, args.command))
        elif args.command_name == "status":
            print_json(
                {
                    "targets": rows_to_dicts(list_targets(conn)),
                    "hypotheses": rows_to_dicts(list_hypotheses(conn, args.target)),
                }
            )


def print_json(value) -> None:
    if hasattr(value, "keys"):
        value = dict(value)
    elif isinstance(value, list):
        value = rows_to_dicts(value)
    elif is_dataclass(value):
        value = asdict(value)
    print(json.dumps(value, indent=2))


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) if hasattr(row, "keys") else row for row in rows]
