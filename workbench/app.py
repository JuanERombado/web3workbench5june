from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import web3bb


APP_DIR = Path(__file__).parent

app = FastAPI(title="Web3 Bug Bounty Workbench")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class RunPathIn(BaseModel):
    run: str


class ScopeIn(RunPathIn):
    content: str


class ScanIn(RunPathIn):
    profile: str | None = None
    all_profiles: bool = False


class HypothesisIn(RunPathIn):
    title: str
    contract: str = ""
    function: str = ""
    hypothesis: str = ""
    source: str = "Manual"
    status: str = "New"
    poc_status: str = "Needs PoC"
    validation_status: str = "Unvalidated"
    gate_decision: str = ""
    next_action: str = ""
    notes: str = ""


class GateIn(RunPathIn):
    hypothesis_id: str
    decision: str
    notes: str = ""


class CloseIn(RunPathIn):
    hypothesis_id: str
    status: str
    reason: str


class ImportLeadsIn(RunPathIn):
    file_path: str


class OpenPathIn(BaseModel):
    path: str


@app.get("/health")
def health() -> dict:
    return {"ok": True, "app": "Web3 Bug Bounty Workbench", "local": "127.0.0.1"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/bootstrap")
def bootstrap(run: str = "") -> dict:
    selected = Path(run) if run else None
    return {
        "runs": web3bb.list_runs(),
        "selected_run": str(selected) if selected else "",
        "hypotheses": rows_for_run(selected),
        "profiles": profiles_for_run(selected),
        "executions": executions_for_run(selected),
        "statuses": web3bb.HYPOTHESIS_STATUSES,
    }


@app.post("/api/runs")
async def create_run(
    target_name: str = Form(...),
    program_url: str = Form(...),
    scope_url: str = Form(""),
    resources_url: str = Form(""),
    source_path: str = Form(""),
    source_upload: UploadFile | None = File(None),
) -> dict:
    try:
        source = await resolve_source(source_path, source_upload)
        run_path = web3bb.init_run(target_name, program_url, source)
        if scope_url or resources_url:
            web3bb.scope_run(run_path, [scope_url, resources_url])
        return {"run": str(run_path), "metadata": web3bb.read_json(run_path / "metadata" / "run_metadata.json")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ingest")
def ingest(payload: RunPathIn) -> dict:
    try:
        return web3bb.ingest_run(Path(payload.run))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/scope")
def scope(payload: RunPathIn) -> dict:
    try:
        path = web3bb.scope_run(Path(payload.run))
        return {"scope_brief": str(path)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/scope", response_class=PlainTextResponse)
def get_scope(run: str = Query(...)) -> str:
    path = Path(run) / "scope" / "scope_brief.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@app.put("/api/scope")
def save_scope(payload: ScopeIn) -> dict:
    path = Path(payload.run) / "scope" / "scope_brief.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.content, encoding="utf-8")
    return {"scope_brief": str(path)}


@app.post("/api/doctor")
def doctor(payload: RunPathIn) -> dict:
    try:
        output_dir = Path(payload.run) / "metadata" if payload.run else Path.cwd()
        return web3bb.doctor(output_dir)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/scan")
def scan(payload: ScanIn) -> dict:
    try:
        executions = web3bb.scan_run(Path(payload.run), profile=payload.profile or None, all_profiles=payload.all_profiles)
        return {"executions": executions}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/executions")
def executions(run: str = Query(...)) -> dict:
    try:
        return {"executions": web3bb.tool_execution_history(Path(run))}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/file", response_class=PlainTextResponse)
def read_file(path: str = Query(...)) -> str:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return file_path.read_text(encoding="utf-8", errors="replace")


@app.post("/api/open-path")
def open_path(payload: OpenPathIn) -> dict:
    path = Path(payload.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    os.startfile(path)  # type: ignore[attr-defined]
    return {"opened": str(path)}


@app.get("/api/hypotheses")
def hypotheses(run: str = Query(...)) -> dict:
    try:
        return {"hypotheses": rows_for_run(Path(run))}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/hypotheses")
def add_hypothesis(payload: HypothesisIn) -> dict:
    try:
        row = web3bb.add_hypothesis(Path(payload.run), payload.model_dump(exclude={"run"}))
        web3bb.export_run(Path(payload.run))
        return dict(row)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/import-leads")
def import_leads(payload: ImportLeadsIn) -> dict:
    try:
        rows = web3bb.import_leads(Path(payload.run), Path(payload.file_path))
        return {"imported": [dict(row) for row in rows]}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/gate-hypothesis")
def gate_hypothesis(payload: GateIn) -> dict:
    try:
        row = web3bb.gate_hypothesis(Path(payload.run), payload.hypothesis_id, payload.decision, payload.notes)
        return dict(row)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/close-hypothesis")
def close_hypothesis(payload: CloseIn) -> dict:
    try:
        row = web3bb.close_hypothesis(Path(payload.run), payload.hypothesis_id, payload.status, payload.reason)
        return dict(row)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/export")
def export_tracker(payload: RunPathIn) -> dict:
    try:
        return web3bb.export_run(Path(payload.run))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review-packet")
def review_packet(payload: RunPathIn) -> dict:
    try:
        return web3bb.export_review_packet(Path(payload.run))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def rows_for_run(run_path: Path | None) -> list[dict]:
    if not run_path:
        return []
    try:
        return [dict(row) for row in web3bb.list_hypotheses(run_path)]
    except Exception:
        return []


def profiles_for_run(run_path: Path | None) -> dict:
    if not run_path:
        return {}
    path = run_path / "metadata" / "profiles.json"
    return web3bb.read_json(path) if path.exists() else {}


def executions_for_run(run_path: Path | None) -> list[dict]:
    if not run_path:
        return []
    try:
        return web3bb.tool_execution_history(run_path)
    except Exception:
        return []


async def resolve_source(source_path: str, source_upload: UploadFile | None) -> Path:
    if source_upload and source_upload.filename:
        suffix = Path(source_upload.filename).suffix or ".zip"
        temp_dir = Path(tempfile.mkdtemp(prefix="web3bb-upload-"))
        dest = temp_dir / f"upload{suffix}"
        with dest.open("wb") as handle:
            shutil.copyfileobj(source_upload.file, handle)
        return dest
    if source_path.strip():
        return Path(source_path.strip())
    raise ValueError("Provide a source zip/folder path or upload a zip.")
