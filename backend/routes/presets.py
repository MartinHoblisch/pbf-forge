from __future__ import annotations

from typing import Literal

import presets as presets_mod
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/presets")


class PresetBody(BaseModel):
    name: str
    tags: list[str]
    exclude_tags: list[str] = []
    geometry_types: list[Literal["nodes", "ways", "relations"]]
    output_formats: list[Literal["pbf", "geojson", "gpkg"]] = ["gpkg"]
    suffix: str = ""
    columns_mode: Literal["other_tags", "all", "manual"] = "other_tags"
    manual_keys: list[str] = []


@router.get("")
def list_presets():
    return presets_mod.list_presets()


@router.post("")
def create_preset(body: PresetBody):
    return presets_mod.create_preset(body.model_dump())


@router.put("/{preset_id}")
def update_preset(preset_id: str, body: PresetBody):
    result = presets_mod.update_preset(preset_id, body.model_dump())
    if not result:
        raise HTTPException(status_code=404, detail="Preset not found")
    return result


@router.delete("/{preset_id}")
def delete_preset(preset_id: str):
    if not presets_mod.delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "deleted"}
