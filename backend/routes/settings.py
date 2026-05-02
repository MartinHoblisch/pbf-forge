from __future__ import annotations

import json
import logging
import re

from config import STARTUP_TIME, USER_CONFIG_FILE
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    if re.search(r'[&|;<>`"^]', payload.host_data_dir):
        raise HTTPException(status_code=422, detail="Path contains invalid characters")
    cfg = _read_config()
    cfg["host_data_dir"] = payload.host_data_dir
    cfg["configured"] = True
    cfg["pending_restart"] = True
    _write_config(cfg)
    return {"ok": True}
