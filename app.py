"""
RTU Heat Pump Benchmarking Application — FastAPI entrypoint.

Run with:
    uvicorn app:app --reload --host 0.0.0.0 --port 8000

Routes are intentionally thin: all real logic lives in `core/*` modules so
this file stays readable as a map of "what the API looks like".
"""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.auth import require_auth
from core.config import (
    BENCHMARK_JSON_PATH,
    COMPARISON_EXCEL_PATH,
    DEFAULT_PARAMETER_FILE,
    Registry,
    STATIC_DIR,
    TEMPLATES_DIR,
    UPLOADS_DIR,
    settings,
)
from core.excel_io import read_parameters_from_excel
from core.job_manager import job_manager
from core.logging_setup import configure_logging
from core.pipeline import BenchmarkPipeline
from core.schemas import StartBenchmarkRequest
from scripts.generate_sample_physical_data import ensure_sample_parameter_file

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RTU Heat Pump Benchmarking",
    description="Automated benchmarking of commercial HVAC heat pump rooftop units.",
    version="1.0.0",
    # Applies to every page and API route below (not to /static, which is a
    # separately-mounted sub-app — harmless to leave CSS/JS/icons public).
    # No-ops locally; activates the moment RTU_AUTH_USERNAME/PASSWORD are set,
    # which is exactly what a public deployment should do. See core/auth.py.
    dependencies=[Depends(require_auth)],
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-memory map of upload token -> parsed parameter rows (kept small & simple;
# the underlying .xlsx also stays on disk under /uploads for audit purposes).
_uploaded_parameter_sets: dict[str, list[dict]] = {}


@app.on_event("startup")
def on_startup() -> None:
    ensure_sample_parameter_file(DEFAULT_PARAMETER_FILE)
    logger.info("Application ready. Default parameter file: %s", DEFAULT_PARAMETER_FILE)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    competitors = Registry.competitors()
    return templates.TemplateResponse(
        request, "index.html", {"competitors": competitors}
    )


@app.get("/dashboard")
def dashboard(request: Request):
    competitors = Registry.competitors()
    return templates.TemplateResponse(
        request, "dashboard.html", {"competitors": competitors}
    )


# ---------------------------------------------------------------------------
# Config / metadata
# ---------------------------------------------------------------------------

@app.get("/api/config/competitors")
def list_competitors():
    return [
        {"id": c.id, "name": c.name, "color": c.color, "logo": c.logo, "homepage": c.homepage}
        for c in Registry.competitors()
    ]


@app.get("/api/config/default-parameters")
def default_parameters():
    if not DEFAULT_PARAMETER_FILE.exists():
        raise HTTPException(404, "Physical_Data.xlsx not found in the project folder.")
    rows = _read_default_parameters_or_503()
    return {"count": len(rows), "parameters": rows}


def _read_default_parameters_or_503() -> list[dict]:
    """Read Physical_Data.xlsx, converting the common transient failure mode
    (file briefly locked by OneDrive sync or by Excel having it open) into a
    clear, actionable error instead of a raw 500."""
    try:
        return read_parameters_from_excel(DEFAULT_PARAMETER_FILE)
    except PermissionError as exc:
        raise HTTPException(
            503,
            "Physical_Data.xlsx exists but couldn't be opened right now — it may still be "
            "syncing (OneDrive) or open in Excel. Close it / wait a moment and try again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(400, f"Could not read Physical_Data.xlsx: {exc}") from exc


# ---------------------------------------------------------------------------
# Upload custom benchmark parameter sheet
# ---------------------------------------------------------------------------

@app.post("/api/upload-parameters")
async def upload_parameters(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Please upload an Excel file (.xlsx or .xls).")

    token = uuid.uuid4().hex[:10]
    dest = UPLOADS_DIR / f"{token}_{file.filename}"
    contents = await file.read()
    dest.write_bytes(contents)

    try:
        rows = read_parameters_from_excel(dest)
    except Exception as exc:
        raise HTTPException(400, f"Could not read Excel file: {exc}") from exc

    if not rows:
        raise HTTPException(400, "No parameters found in Column B of the uploaded sheet.")

    _uploaded_parameter_sets[token] = rows
    logger.info("Uploaded parameter sheet '%s' -> token=%s (%d parameters)",
                file.filename, token, len(rows))
    return {"token": token, "count": len(rows), "preview": rows[:15]}


# ---------------------------------------------------------------------------
# Benchmarking job lifecycle
# ---------------------------------------------------------------------------

@app.post("/api/start-benchmark")
def start_benchmark(payload: StartBenchmarkRequest):
    if not payload.competitors:
        raise HTTPException(400, "Select at least one competitor.")

    valid_ids = set(Registry.competitor_map().keys())
    unknown = set(payload.competitors) - valid_ids
    if unknown:
        raise HTTPException(400, f"Unknown competitor id(s): {sorted(unknown)}")

    if payload.use_default_parameters:
        if not DEFAULT_PARAMETER_FILE.exists():
            raise HTTPException(404, "Physical_Data.xlsx not found.")
        parameter_defs = _read_default_parameters_or_503()
    else:
        if not payload.uploaded_file_token or payload.uploaded_file_token not in _uploaded_parameter_sets:
            raise HTTPException(400, "No valid uploaded_file_token provided.")
        parameter_defs = _uploaded_parameter_sets[payload.uploaded_file_token]

    # Series name takes priority; the free-text configuration description is
    # only used as a fallback when no exact series/model name was given.
    unit_query = (payload.series_name or "").strip() or (payload.unit_config_description or "").strip() or None

    job_id = job_manager.create_job()

    def _task() -> None:
        pipeline = BenchmarkPipeline(
            job_id=job_id,
            competitor_ids=payload.competitors,
            parameter_defs=parameter_defs,
            enable_web_scraping=payload.enable_web_scraping,
            unit_query=unit_query,
        )
        pipeline.run()

    job_manager.run_async(job_id, _task)
    return {"job_id": job_id, "unit_query": unit_query}


@app.get("/api/job-status/{job_id}")
def job_status(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id.")
    return job


@app.post("/api/cancel-benchmark/{job_id}")
def cancel_benchmark(job_id: str):
    """Stop a queued or running job so it doesn't keep occupying the single
    benchmark worker — a job nobody's watching anymore shouldn't block the
    next one from starting."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id.")
    cancelled = job_manager.request_cancel(job_id)
    if not cancelled:
        raise HTTPException(409, f"Job is already {job.status} — nothing to cancel.")
    return job_manager.get(job_id)


# ---------------------------------------------------------------------------
# Results / dashboard data
# ---------------------------------------------------------------------------

@app.get("/api/results")
def get_results():
    if not BENCHMARK_JSON_PATH.exists():
        raise HTTPException(404, "No benchmark results yet — run a benchmark first.")
    with open(BENCHMARK_JSON_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@app.get("/api/export/excel")
def export_excel():
    if not COMPARISON_EXCEL_PATH.exists():
        raise HTTPException(404, "No comparison workbook yet — run a benchmark first.")
    return FileResponse(
        COMPARISON_EXCEL_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="comparison.xlsx",
    )


@app.get("/api/export/csv")
def export_csv():
    if not BENCHMARK_JSON_PATH.exists():
        raise HTTPException(404, "No results yet — run a benchmark first.")
    data = json.loads(BENCHMARK_JSON_PATH.read_text(encoding="utf-8"))
    competitors = data["competitors"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Category", "Parameter", "Unit"] + [c["name"] for c in competitors] +
                     ["Has Discrepancy"])
    for p in data["parameters"]:
        row = [p.get("category") or "", p["parameter"], p.get("unit") or ""]
        for c in competitors:
            cell = p["values"].get(c["id"], {})
            row.append(cell.get("value") or "")
        row.append("Yes" if p.get("has_discrepancy") else "No")
        writer.writerow(row)

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=comparison.csv"},
    )


@app.get("/api/export/pdf")
def export_pdf():
    if not BENCHMARK_JSON_PATH.exists():
        raise HTTPException(404, "No results yet — run a benchmark first.")
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except ImportError as exc:
        raise HTTPException(
            501, "PDF export requires the optional 'reportlab' package. "
                 "Install it with: pip install reportlab"
        ) from exc

    data = json.loads(BENCHMARK_JSON_PATH.read_text(encoding="utf-8"))
    competitors = data["competitors"]
    header = ["Parameter"] + [c["name"] for c in competitors]
    table_data = [header]
    for p in data["parameters"]:
        row = [p["parameter"]]
        for c in competitors:
            cell = p["values"].get(c["id"], {})
            row.append(cell.get("value") or "—")
        table_data.append(row)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B57")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
    ]))
    doc.build([table])
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=comparison.pdf"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.host, port=settings.port, reload=True)
