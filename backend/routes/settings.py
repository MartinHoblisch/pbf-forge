"""HTTP endpoints for user settings: the host data directory and resource limits."""

from __future__ import annotations

import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import STARTUP_TIME, USER_CONFIG_FILE

_log = logging.getLogger(__name__)

router = APIRouter()


def _read_config() -> dict:
    if USER_CONFIG_FILE.exists():
        try:
            return json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("Could not read user config: %s", exc)
    return {"configured": False, "host_data_dir": "", "pending_restart": False}


def _write_config(cfg: dict) -> None:
    USER_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class SettingsPayload(BaseModel):
    host_data_dir: str


@router.get("/api/settings")
def get_settings() -> dict:
    cfg = _read_config()
    return {
        "configured": cfg.get("configured", False),
        "host_data_dir": cfg.get("host_data_dir", ""),
        "pending_restart": cfg.get("pending_restart", False),
        "startup_time": STARTUP_TIME,
    }


@router.post("/api/settings")
def post_settings(payload: SettingsPayload) -> dict:
    if not payload.host_data_dir or len(payload.host_data_dir) > 500:
        raise HTTPException(status_code=422, detail="Invalid path")
    if not re.fullmatch(r"[A-Za-z0-9 _\-./:\\]+", payload.host_data_dir):
        raise HTTPException(status_code=422, detail="Path contains invalid characters")
    cfg = _read_config()
    cfg["host_data_dir"] = payload.host_data_dir
    cfg["configured"] = True
    cfg["pending_restart"] = True
    _write_config(cfg)
    return {"ok": True}


# ── Resource limits ───────────────────────────────────────────────────────────


def _effective_limits(cfg: dict) -> tuple[int, int]:
    cpu = os.cpu_count() or 1
    mode = cfg.get("resource_mode", "full")
    preset_threads = max(1, cpu // 2) if mode == "background" else cpu
    preset_nice = 10 if mode == "background" else 0
    raw_t = cfg.get("osmium_threads_override")
    raw_n = cfg.get("nice_override")
    threads = max(1, min(cpu, int(raw_t))) if raw_t else preset_threads
    nice = max(0, min(19, int(raw_n))) if raw_n is not None else preset_nice
    return threads, nice


class ResourceLimitsPayload(BaseModel):
    mode: str
    osmium_threads_override: int | None = None
    nice_override: int | None = None


@router.get("/api/resource-limits")
def get_resource_limits() -> dict:
    cfg = _read_config()
    cpu = os.cpu_count() or 1
    threads, nice = _effective_limits(cfg)
    return {
        "mode": cfg.get("resource_mode", "full"),
        "cpu_count": cpu,
        "osmium_threads_override": cfg.get("osmium_threads_override"),
        "nice_override": cfg.get("nice_override"),
        "effective_threads": threads,
        "effective_nice": nice,
    }


@router.post("/api/resource-limits")
def post_resource_limits(payload: ResourceLimitsPayload) -> dict:
    if payload.mode not in ("full", "background"):
        raise HTTPException(status_code=422, detail="mode must be 'full' or 'background'")
    cpu = os.cpu_count() or 1
    if payload.osmium_threads_override is not None:
        if not (1 <= payload.osmium_threads_override <= cpu):
            raise HTTPException(status_code=422, detail=f"threads must be 1–{cpu}")
    if payload.nice_override is not None:
        if not (0 <= payload.nice_override <= 19):
            raise HTTPException(status_code=422, detail="nice must be 0–19")
    cfg = _read_config()
    cfg["resource_mode"] = payload.mode
    cfg["osmium_threads_override"] = payload.osmium_threads_override
    cfg["nice_override"] = payload.nice_override
    _write_config(cfg)
    return {"ok": True}
