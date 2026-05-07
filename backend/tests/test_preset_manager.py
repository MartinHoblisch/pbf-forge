from __future__ import annotations

import json

import presets as pm


def test_list_presets_returns_defaults_when_no_custom_file(tmp_data_dir):
    # PRESETS_FILE patched to tmp_data_dir/.osm_tool_presets.json (not exists yet)
    result = pm.list_presets()
    # default file exists in source tree — must return non-empty list
    assert isinstance(result, list)
    assert len(result) > 0
    assert all("id" in p for p in result)


def test_list_presets_returns_custom_from_file(tmp_data_dir):
    custom = [{"id": "abc", "name": "My Preset", "tags": ["amenity"], "geometry_types": ["nodes"]}]
    pm.PRESETS_FILE.write_text(json.dumps(custom), encoding="utf-8")
    result = pm.list_presets()
    assert result == custom


def test_list_presets_returns_empty_on_corrupt_json(tmp_data_dir):
    pm.PRESETS_FILE.write_text("{bad json!!!", encoding="utf-8")
    result = pm.list_presets()
    assert result == []


def test_create_preset_assigns_uuid_and_persists(tmp_data_dir):
    data = {"name": "Roads", "tags": ["highway"], "geometry_types": ["ways"]}
    preset = pm.create_preset(data)

    assert "id" in preset
    assert len(preset["id"]) == 36  # UUID format
    assert preset["name"] == "Roads"

    # persisted
    stored = json.loads(pm.PRESETS_FILE.read_text(encoding="utf-8"))
    assert any(p["id"] == preset["id"] for p in stored)


def test_update_preset_changes_fields_keeps_id(tmp_data_dir):
    original = pm.create_preset({"name": "Old", "tags": ["amenity"]})
    preset_id = original["id"]

    updated = pm.update_preset(preset_id, {"name": "New", "tags": ["shop"]})
    assert updated is not None
    assert updated["id"] == preset_id
    assert updated["name"] == "New"
    assert updated["tags"] == ["shop"]

    stored = json.loads(pm.PRESETS_FILE.read_text(encoding="utf-8"))
    match = next(p for p in stored if p["id"] == preset_id)
    assert match["name"] == "New"


def test_delete_preset_removes_and_persists(tmp_data_dir):
    preset = pm.create_preset({"name": "ToDelete"})
    preset_id = preset["id"]

    result = pm.delete_preset(preset_id)
    assert result is True

    stored = json.loads(pm.PRESETS_FILE.read_text(encoding="utf-8"))
    assert not any(p["id"] == preset_id for p in stored)


def test_get_preset_unknown_id_returns_none(tmp_data_dir):
    assert pm.get_preset("non-existent-uuid") is None


def test_presets_migrate_from_data_dir(tmp_path, monkeypatch):
    """Existing presets in DATA_DIR are copied to CONFIG_DIR on first load."""
    import shutil as _shutil
    import config as cfg
    import presets as pm

    tmp_data = tmp_path / "data"
    tmp_data.mkdir(exist_ok=True)
    tmp_cfg = tmp_path / "config"
    tmp_cfg.mkdir(exist_ok=True)

    old_file = tmp_data / ".osm_tool_presets.json"
    new_file = tmp_cfg / ".osm_tool_presets.json"
    old_file.write_text('[{"id": "migrated", "name": "Migrated"}]', encoding="utf-8")

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data)
    monkeypatch.setattr(cfg, "PRESETS_FILE", new_file)
    monkeypatch.setattr(pm, "PRESETS_FILE", new_file)
    monkeypatch.setattr(pm, "DATA_DIR", tmp_data)

    result = pm.list_presets()

    assert new_file.exists()
    assert result[0]["id"] == "migrated"
    assert old_file.exists()  # original not deleted


def test_all_schema_fields_present_after_create(tmp_data_dir):
    data = {
        "name": "Full Preset",
        "tags": ["amenity=cafe"],
        "geometry_types": ["nodes", "ways"],
        "suffix": "cafe",
        "output_formats": ["gpkg"],
        "columns_mode": "other_tags",
        "manual_keys": [],
    }
    preset = pm.create_preset(data)
    for key in data:
        assert key in preset
    assert "id" in preset
