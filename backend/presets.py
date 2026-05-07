from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Optional

from config import DATA_DIR, PRESETS_FILE

_lock = threading.Lock()
_log = logging.getLogger(__name__)

_DEFAULT_FILE = Path(__file__).parent / "presets_default.json"

_ID_MIGRATION = {
    "preset-schienennetz": ("preset-railway-network", "Railway Network"),
    "preset-wasserstrassen": ("preset-waterways", "Waterways"),
    "preset-strassennetz": ("preset-road-network", "Road Network"),
    "preset-schutzgebiete": ("preset-protected-areas", "Protected Areas"),
    "preset-gebaeude": ("preset-buildings", "Buildings"),
}


def _migrate(presets: list[dict]) -> tuple[list[dict], bool]:
    changed = False
    for p in presets:
        if p.get("id") in _ID_MIGRATION:
            new_id, new_name = _ID_MIGRATION[p["id"]]
            p["id"] = new_id
            p["name"] = new_name
            changed = True
    return presets, changed


def _load() -> list[dict]:
    _old = DATA_DIR / ".osm_tool_presets.json"
    if not PRESETS_FILE.exists() and _old.exists():
        shutil.copy(_old, PRESETS_FILE)
    if not PRESETS_FILE.exists():
        if _DEFAULT_FILE.exists():
            try:
                data = json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))
                PRESETS_FILE.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                return data
            except Exception as exc:
                _log.warning("Could not load default presets file: %s", exc)
        return []
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        data, changed = _migrate(data)
        if changed:
            _save(data)
        return data
    except Exception as exc:
        _log.warning("Could not read presets file: %s", exc)
        return []


def _save(presets: list[dict]) -> None:
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")


def list_presets() -> list[dict]:
    with _lock:
        return _load()


def get_preset(preset_id: str) -> Optional[dict]:
    with _lock:
        return next((p for p in _load() if p["id"] == preset_id), None)


def create_preset(data: dict) -> dict:
    with _lock:
        presets = _load()
        preset = {"id": str(uuid.uuid4()), **data}
        presets.append(preset)
        _save(presets)
        return preset


def update_preset(preset_id: str, data: dict) -> Optional[dict]:
    with _lock:
        presets = _load()
        for i, p in enumerate(presets):
            if p["id"] == preset_id:
                presets[i] = {"id": preset_id, **data}
                _save(presets)
                return presets[i]
        return None


def delete_preset(preset_id: str) -> bool:
    with _lock:
        presets = _load()
        new_list = [p for p in presets if p["id"] != preset_id]
        if len(new_list) == len(presets):
            return False
        _save(new_list)
        return True
