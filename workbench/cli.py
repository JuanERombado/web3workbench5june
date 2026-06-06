from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

import uvicorn

from .db import connect
from . import web3bb
from .services import (
    add_hypothesis_from_file,
    create_target,
    ensure_schema,
    list_hypotheses,
    list_targets,
    run_mock_tool,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="web3bb")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    init_target = subparsers.add_parser("init-target")
    init_target.add_argument("--name", required=True)
    init_target.add_argument("--repo", required=True)
    init_target.add_argument("--scope", required=True)

    run_tool = subparsers.add_parser("run-tool")
    run_tool.add_argument("--target", required=True)
    run_tool.add_argument("--hypothesis", required=True, type=int)
    run_tool.add_argument("--tool", required=True, choices=["foundry", "slither"])
    run_tool.add_argument("--command", required=True)

    status = subparsers.add_parser("status")
    status.add_argument("--target", required=True)

    doctor = subparsers.add_parser("doctor")

    init = subparsers.add_parser("init")
    init.add_argument("--target-name", required=True)
    init.add_argument("--program-url", required=True)
    init.add_argument("--zip", required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--run", required=True)

    scope = subparsers.add_parser("scope")
    scope.add_argument("--run", required=True)
    scope.add_argument("--resource-url", action="append", default=[])

    scan = subparsers.add_parser("scan")
    scan.add_argument("--run", required=True)
    scan.add_argument("--profile")
    scan.add_argument("--all-profiles", action="store_true")

    add = subparsers.add_parser("add-hypothesis")
    add.add_argument("--run")
    add.add_argument("--target")
    add.add_argument("--file")
    add.add_argument("--tool", default="foundry", choices=["foundry", "slither"])
    add.add_argument("--id")
    add.add_argument("--title")
    add.add_argument("--contract", default="")
    add.add_argument("--function", default="")
    add.add_argument("--hypothesis")
    add.add_argument("--source", default="Manual")
    add.add_argument("--tool-evidence", default="")
    add.add_argument("--manual-evidence", default="")
    add.add_argument("--scope-mapping", default="")
    add.add_argument("--impact-mapping", default="")
    add.add_argument("--status", default="New", choices=web3bb.HYPOTHESIS_STATUSES)
    add.add_argument("--poc-status", default="Needs PoC")
    add.add_argument("--validation-status", default="Unvalidated")
    add.add_argument("--known-issue-check", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--next-action", default="")

    list_h = subparsers.add_parser("list-hypotheses")
    list_h.add_argument("--run", required=True)

    update = subparsers.add_parser("update-hypothesis")
    update.add_argument("--run", required=True)
    update.add_argument("--id", required=True)
    update.add_argument("--status")
    update.add_argument("--tool-evidence")
    update.add_argument("--manual-evidence")
    update.add_argument("--scope-mapping")
    update.add_argument("--impact-mapping")
    update.add_argument("--poc-status")
    update.add_argument("--validation-status")
    update.add_argument("--known-issue-check")
    update.add_argument("--notes")
    update.add_argument("--next-action")

    export = subparsers.add_parser("export")
    export.add_argument("--run", required=True)

    close = subparsers.add_parser("close-hypothesis")
    close.add_argument("--run", required=True)
    close.add_argument("--id", required=True)
    close.add_argument("--status", required=True)
    close.add_argument("--reason", required=True)

    seed = subparsers.add_parser("seed-axelar")
    seed.add_argument("--run", required=True)

    args = parser.parse_args()
    if args.command_name == "doctor":
        result = web3bb.doctor()
        print_json(result)
        print_missing_hints(result)
        return
    if args.command_name == "init":
        run_path = web3bb.init_run(args.target_name, args.program_url, Path(args.zip))
        print_json({"run": str(run_path)})
        return
    if args.command_name == "ingest":
        print_json(web3bb.ingest_run(Path(args.run)))
        return
    if args.command_name == "scope":
        path = web3bb.scope_run(Path(args.run), args.resource_url)
        print_json({"scope_brief": str(path)})
        return
    if args.command_name == "scan":
        if args.profile and args.all_profiles:
            parser.error("scan accepts either --profile or --all-profiles, not both")
        executions = web3bb.scan_run(Path(args.run), profile=args.profile, all_profiles=args.all_profiles)
        print_json({"executions": executions})
        return
    if args.command_name == "add-hypothesis" and args.run:
        values = hypothesis_args(args)
        if not values["title"]:
            values["title"] = input("Title: ").strip()
        if not values["hypothesis"]:
            values["hypothesis"] = input("Hypothesis: ").strip()
        row = web3bb.add_hypothesis(Path(args.run), values)
        print_json(dict(row))
        return
    if args.command_name == "list-hypotheses":
        print(web3bb.print_table(web3bb.list_hypotheses(Path(args.run))))
        return
    if args.command_name == "update-hypothesis":
        row = web3bb.update_hypothesis(Path(args.run), args.id, update_args(args))
        print_json(dict(row))
        return
    if args.command_name == "close-hypothesis":
        row = web3bb.close_hypothesis(Path(args.run), args.id, args.status, args.reason)
        print_json(dict(row))
        return
    if args.command_name == "export":
        print_json(web3bb.export_run(Path(args.run)))
        return
    if args.command_name == "seed-axelar":
        row = web3bb.seed_axelar(Path(args.run))
        print_json(dict(row))
        return

    if args.command_name == "serve":
        uvicorn.run("workbench.app:app", host=args.host, port=args.port, reload=False)
        return

    with connect() as conn:
        ensure_schema(conn)
        if args.command_name == "init-target":
            print_json(create_target(conn, args.name, args.repo, args.scope))
        elif args.command_name == "add-hypothesis":
            if not args.target or not args.file:
                parser.error("legacy add-hypothesis requires --target and --file when --run is not provided")
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


def hypothesis_args(args) -> dict:
    return {
        "id": args.id,
        "title": args.title or "",
        "target": args.target or "",
        "contract": args.contract,
        "function": args.function,
        "hypothesis": args.hypothesis or "",
        "source": args.source,
        "tool_evidence": args.tool_evidence,
        "manual_evidence": args.manual_evidence,
        "scope_mapping": args.scope_mapping,
        "impact_mapping": args.impact_mapping,
        "status": args.status,
        "poc_status": args.poc_status,
        "validation_status": args.validation_status,
        "known_issue_check": args.known_issue_check,
        "notes": args.notes,
        "next_action": args.next_action,
    }


def update_args(args) -> dict:
    return {
        "status": args.status,
        "tool_evidence": args.tool_evidence,
        "manual_evidence": args.manual_evidence,
        "scope_mapping": args.scope_mapping,
        "impact_mapping": args.impact_mapping,
        "poc_status": args.poc_status,
        "validation_status": args.validation_status,
        "known_issue_check": args.known_issue_check,
        "notes": args.notes,
        "next_action": args.next_action,
    }


def print_missing_hints(results: dict) -> None:
    missing = [name for name, info in results.items() if not info["detected"]]
    if not missing:
        return
    print("\nMissing tool suggestions only; scans will skip missing tools:")
    for name in missing:
        print(f"- {name}: {results[name]['install_hint']}")


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
