"""Tests for the legacy German→English preset ID migration in presets._migrate.

Bug class to prevent: A user upgrading from v0.x (where preset IDs were
'preset-schienennetz', 'preset-strassennetz', etc.) to v1.0 (where IDs are
the English equivalents) silently sees their bundled defaults disappear,
or sees doubled entries because the migrator failed.

The mapping is:
    preset-schienennetz   → preset-railway-network    "Railway Network"
    preset-wasserstrassen → preset-waterways          "Waterways"
    preset-strassennetz   → preset-road-network       "Road Network"
    preset-schutzgebiete  → preset-protected-areas    "Protected Areas"
    preset-gebaeude       → preset-buildings          "Buildings"
"""

from __future__ import annotations

import json

import pytest

import presets as pm
from presets import _ID_MIGRATION


def _write(presets: list[dict]) -> None:
    pm.PRESETS_FILE.write_text(json.dumps(presets), encoding="utf-8")


def _read() -> list[dict]:
    return json.loads(pm.PRESETS_FILE.read_text(encoding="utf-8"))


# ── Single-entry migration ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "old_id,expected_new_id,expected_new_name,expected_new_suffix",
    [(old, new, name, suffix) for old, (new, name, suffix) in _ID_MIGRATION.items()],
)
def test_migrates_each_legacy_id(
    tmp_data_dir, old_id, expected_new_id, expected_new_name, expected_new_suffix
):
    _write([{"id": old_id, "name": "OLD NAME", "suffix": "old_suffix", "tags": ["x"]}])

    result = pm.list_presets()

    assert len(result) == 1
    assert result[0]["id"] == expected_new_id
    assert result[0]["name"] == expected_new_name
    assert result[0]["suffix"] == expected_new_suffix
    assert result[0]["tags"] == ["x"]  # other fields preserved


def test_migration_persists_to_disk(tmp_data_dir):
    """Migration must write the new IDs back to PRESETS_FILE — otherwise the
    next `list_presets()` call would migrate again, masking the first save bug."""
    _write([{"id": "preset-schienennetz", "name": "Old"}])

    pm.list_presets()

    on_disk = _read()
    assert on_disk[0]["id"] == "preset-railway-network"
    assert on_disk[0]["name"] == "Railway Network"


def test_modern_id_unchanged(tmp_data_dir):
    """Already-migrated presets must not be touched."""
    _write([{"id": "preset-railway-network", "name": "Railway Network", "tags": ["railway"]}])

    result = pm.list_presets()

    assert result[0]["id"] == "preset-railway-network"
    assert result[0]["name"] == "Railway Network"


def test_uuid_id_unchanged(tmp_data_dir):
    """User-created presets (UUID IDs) must never be touched by the migrator."""
    uuid_str = "12345678-1234-1234-1234-1234567890ab"
    _write([{"id": uuid_str, "name": "My Custom Preset", "suffix": "meine_endung"}])

    result = pm.list_presets()

    assert result[0]["id"] == uuid_str
    assert result[0]["name"] == "My Custom Preset"
    assert result[0]["suffix"] == "meine_endung"


# ── Mixed file ───────────────────────────────────────────────────────────────


def test_mixed_legacy_and_modern_only_legacy_migrated(tmp_data_dir):
    uuid_str = "abcdef01-2345-6789-abcd-ef0123456789"
    _write(
        [
            {"id": "preset-schienennetz", "name": "Schienennetz"},
            {"id": "preset-railway-network", "name": "Railway Network"},
            {"id": uuid_str, "name": "Custom"},
            {"id": "preset-gebaeude", "name": "Gebäude"},
        ]
    )

    result = pm.list_presets()
    ids = [p["id"] for p in result]

    assert ids == [
        "preset-railway-network",  # migrated
        "preset-railway-network",  # already modern
        uuid_str,
        "preset-buildings",  # migrated
    ]
    # Names also updated for migrated entries
    assert result[0]["name"] == "Railway Network"
    assert result[3]["name"] == "Buildings"


# ── No-op when no legacy IDs ──────────────────────────────────────────────────


def test_no_save_when_no_legacy_ids_present(tmp_data_dir):
    """If no migration happens, _migrate returns changed=False → no disk
    write. Verify by checking file mtime stays the same."""
    _write([{"id": "preset-railway-network", "name": "Railway Network"}])
    mtime_before = pm.PRESETS_FILE.stat().st_mtime_ns

    pm.list_presets()

    mtime_after = pm.PRESETS_FILE.stat().st_mtime_ns
    assert mtime_after == mtime_before, "PRESETS_FILE rewritten despite no migration"


# ── default file load failure ────────────────────────────────────────────────


def test_corrupt_default_presets_file_falls_back_to_empty(tmp_data_dir, tmp_path, monkeypatch):
    """If the bundled defaults file is corrupt (build pipeline issue,
    bit-rot, partial extract), list_presets must not crash — log a warning
    and return [] so the user can re-add their own presets."""
    corrupt_default = tmp_path / "broken_default.json"
    corrupt_default.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(pm, "_DEFAULT_FILE", corrupt_default)

    # PRESETS_FILE doesn't exist (reset_state fixture), forcing the default-load path
    assert pm.list_presets() == []
