from __future__ import annotations

import re
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

import config
import state
from config import DATA_DIR

router = APIRouter(prefix="/api/filter")


class FilterRequest(BaseModel):
    source_files: list[str]
    tags: list[str]
    geometry_types: list[Literal["nodes", "ways", "relations"]]
    suffix: str
    output_formats: list[Literal["pbf", "geojson", "gpkg"]] = ["gpkg"]
    output_dir: Optional[str] = None
    columns_mode: Literal["other_tags", "all", "manual"] = "other_tags"
    manual_keys: list[str] = []
    exclude_tags: list[str] = []

    @field_validator("suffix")
    @classmethod
    def validate_suffix(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_\-]+$", v):
            raise ValueError("suffix may only contain letters, digits, _ and -")
        return v

    @field_validator("output_formats")
    @classmethod
    def validate_output_formats(cls, v: list) -> list:
        if not v:
            raise ValueError("At least one output format must be selected")
        seen: set = set()
        return [x for x in v if not (x in seen or seen.add(x))]


def _resolve_output_dir(req: FilterRequest) -> str:
    if not req.output_dir:
        return str(DATA_DIR)
    if not _SAFE_OUTPUT_DIR.match(req.output_dir) or ".." in req.output_dir.split("/"):
        raise HTTPException(status_code=400, detail="Invalid output_dir")
    try:
        resolved = (DATA_DIR / req.output_dir).resolve()
        # is_relative_to (Py 3.9+) avoids the prefix-string trap where /data2
        # would startswith(/data) and bypass the guard.
        if not resolved.is_relative_to(DATA_DIR.resolve()):
            raise HTTPException(status_code=400, detail="output_dir outside allowed directory")
        return str(resolved)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid output_dir")


_SAFE_SOURCE = re.compile(r"^[a-zA-Z0-9_\-\.]+\.osm\.pbf$")
_SAFE_OUTPUT_DIR = re.compile(r"^[a-zA-Z0-9_\-/]+$")


def _validate_source_files(source_files: list[str], *, check_exists: bool = False) -> None:
    for source in source_files:
        if not _SAFE_SOURCE.match(source):
            raise HTTPException(status_code=400, detail=f"Invalid filename: {source}")
        if check_exists and not (DATA_DIR / source).exists():
            raise HTTPException(status_code=422, detail=f"File not found: {source}")


@router.get("/files")
def list_filterable_files():
    return state.filter_manager.list_pbf_files()


@router.post("/check")
def check_overwrite(req: FilterRequest):
    _validate_source_files(req.source_files, check_exists=True)
    resolved_output_dir = _resolve_output_dir(req)
    would_overwrite = state.filter_manager.check_would_overwrite(
        source_files=req.source_files,
        suffix=req.suffix,
        output_formats=req.output_formats,
        output_dir=resolved_output_dir,
    )
    return {"would_overwrite": would_overwrite}


@router.post("/run")
def run_filter(req: FilterRequest, background_tasks: BackgroundTasks):
    _validate_source_files(req.source_files)
    resolved_output_dir = _resolve_output_dir(req)

    job = state.filter_manager.create_job(
        source_files=req.source_files,
        tags=req.tags,
        geometry_types=req.geometry_types,
        suffix=req.suffix,
        output_formats=req.output_formats,
        output_dir=resolved_output_dir,
        columns_mode=req.columns_mode,
        manual_keys=req.manual_keys,
        exclude_tags=req.exclude_tags,
    )
    background_tasks.add_task(state.filter_manager.run_job, job)
    return {"job_id": job.id}


@router.get("/jobs")
def list_jobs():
    return state.filter_manager.list_jobs()


@router.delete("/jobs")
def clear_jobs():
    state.filter_manager.clear_completed_jobs()
    return {"ok": True}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = state.filter_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    cancelled = await state.filter_manager.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Job not found or not running")
    return {"status": "cancelling"}


_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9\-]{1,64}$")


@router.get("/jobs/{job_id}/log")
def get_job_log(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    jobs_dir = (config.CONFIG_DIR / "jobs").resolve()
    log_path = (jobs_dir / f"{job_id}.log").resolve()
    if not log_path.is_relative_to(jobs_dir):
        raise HTTPException(status_code=400, detail="Invalid log path")
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(
        path=str(log_path),
        media_type="text/plain; charset=utf-8",
        filename=f"{job_id}.log",
    )
