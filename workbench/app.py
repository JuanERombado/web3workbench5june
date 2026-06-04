from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import connect
from .models import MANUAL_VERDICTS, TOOLS
from .services import (
    add_hypothesis,
    create_target,
    ensure_schema,
    list_hypotheses,
    list_targets,
    run_mock_tool,
    update_manual_verdict,
)

APP_DIR = Path(__file__).parent

app = FastAPI(title="Hypothesis Workbench")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class TargetIn(BaseModel):
    name: str
    repo_path: str
    scope_path: str


class HypothesisIn(BaseModel):
    target: str
    title: str
    description: str
    affected_files: str = ""
    affected_functions: str = ""
    attacker_role: str = ""
    expected_impact: str = ""
    tool: str = "foundry"
    command: str = ""


class RunToolIn(BaseModel):
    target: str
    hypothesis_id: int
    tool: str
    command: str


class VerdictIn(BaseModel):
    target: str
    hypothesis_id: int
    manual_verdict: str
    decision_notes: str = ""


@app.on_event("startup")
def startup() -> None:
    with connect() as conn:
        ensure_schema(conn)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/bootstrap")
def bootstrap():
    with connect() as conn:
        ensure_schema(conn)
        return {
            "targets": rows_to_dicts(list_targets(conn)),
            "hypotheses": rows_to_dicts(list_hypotheses(conn)),
            "tools": TOOLS,
            "manual_verdicts": MANUAL_VERDICTS,
        }


@app.post("/api/targets")
def api_create_target(payload: TargetIn):
    try:
        with connect() as conn:
            return dict(create_target(conn, payload.name, payload.repo_path, payload.scope_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/hypotheses")
def api_add_hypothesis(payload: HypothesisIn):
    try:
        with connect() as conn:
            row = add_hypothesis(
                conn,
                payload.target,
                payload.title,
                payload.description,
                payload.affected_files,
                payload.affected_functions,
                payload.attacker_role,
                payload.expected_impact,
                payload.tool,
                payload.command,
            )
            return dict(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/run-tool")
def api_run_tool(payload: RunToolIn):
    try:
        with connect() as conn:
            return dict(run_mock_tool(conn, payload.target, payload.hypothesis_id, payload.tool, payload.command))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manual-verdict")
def api_manual_verdict(payload: VerdictIn):
    try:
        with connect() as conn:
            return dict(
                update_manual_verdict(
                    conn,
                    payload.target,
                    payload.hypothesis_id,
                    payload.manual_verdict,
                    payload.decision_notes,
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]
