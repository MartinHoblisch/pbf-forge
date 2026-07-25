"""Tests for FilterHistory persistence, schema versioning, and corrupt-store recovery."""

from __future__ import annotations

import json

import pytest

from filter_history import FilterHistory


def _history(tmp_path) -> FilterHistory:
    return FilterHistory(tmp_path / "filter_history.json")


# ── persistence across instances ─────────────────────────────────────────────


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "filter_history.json"
    FilterHistory(path).record("a.osm.pbf", 500_000, "filter", "pbf", 5.0)
    h2 = FilterHistory(path)
    assert len(h2._entries) == 1
    assert h2._entries[0]["duration_seconds"] == pytest.approx(5.0)


# ── corrupt JSON → empty store + file renamed ────────────────────────────────


def test_corrupt_file_resets_and_renames(tmp_path):
    path = tmp_path / "filter_history.json"
    path.write_text("not valid json{{", encoding="utf-8")

    h = FilterHistory(path)
    assert h._entries == []
    assert not path.exists()
    assert len(list(tmp_path.glob("filter_history.corrupt-*.json"))) == 1


# ── wrong schema version → reset ─────────────────────────────────────────────


def test_wrong_schema_version_resets(tmp_path):
    path = tmp_path / "filter_history.json"
    path.write_text(json.dumps({"v": 99, "entries": []}), encoding="utf-8")
    assert FilterHistory(path)._entries == []


# ── atomic write: no .tmp file left behind ───────────────────────────────────


def test_no_tmp_file_after_save(tmp_path):
    h = _history(tmp_path)
    h.record("a.osm.pbf", 100_000, "filter", "pbf", 3.0)
    assert not (tmp_path / "filter_history.tmp").exists()
