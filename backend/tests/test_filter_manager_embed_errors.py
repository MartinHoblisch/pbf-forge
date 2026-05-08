"""Tests for the embed_* exception-swallowing paths in FilterManager.

Bug class: an unwritable / locked / corrupt output file at embed-time would
crash the job AFTER the user already paid the cost of running osmium and
ogr2ogr. The embed must log+swallow so the file (without metadata) is still
delivered; partial metadata is better than a lost export.

The happy paths are covered by test_attribution.py — this file only adds
the exception-path tests for lines 690-691, 698-699, 753-754, 761-762.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from filter_manager import FilterManager


@pytest.fixture
def fm():
    return FilterManager(MagicMock())


# ── _embed_attribution_gpkg exception swallow ────────────────────────────────


def test_embed_attribution_gpkg_invalid_path_does_not_raise(fm, tmp_path):
    """sqlite3.connect on a path inside a non-existent directory raises
    OperationalError. Embed must catch and continue."""
    bogus = tmp_path / "no_such_dir" / "out.gpkg"  # parent doesn't exist
    fm._embed_attribution_gpkg(bogus)  # must not raise


def test_embed_attribution_gpkg_corrupt_file_does_not_raise(fm, tmp_path):
    """A path that exists but isn't a valid sqlite/gpkg file raises
    DatabaseError on first execute. Embed must catch and continue."""
    bogus = tmp_path / "corrupt.gpkg"
    bogus.write_bytes(b"not a sqlite database at all")
    fm._embed_attribution_gpkg(bogus)


# ── _embed_attribution_geojson exception swallow ─────────────────────────────


def test_embed_attribution_geojson_invalid_json_does_not_raise(fm, tmp_path):
    bogus = tmp_path / "corrupt.geojson"
    bogus.write_text("{not json", encoding="utf-8")
    original = bogus.read_text(encoding="utf-8")

    fm._embed_attribution_geojson(bogus)  # must not raise

    # Original content unchanged (write was guarded by try/except)
    assert bogus.read_text(encoding="utf-8") == original


def test_embed_attribution_geojson_missing_file_does_not_raise(fm, tmp_path):
    fm._embed_attribution_geojson(tmp_path / "does_not_exist.geojson")  # must not raise


# ── _embed_provenance_gpkg exception swallow ─────────────────────────────────


def test_embed_provenance_gpkg_invalid_path_does_not_raise(fm, tmp_path):
    bogus = tmp_path / "no_such_dir" / "out.gpkg"
    fm._embed_provenance(
        bogus, "gpkg", "berlin.osm.pbf", ["amenity"], [], ["nodes"]
    )


def test_embed_provenance_gpkg_corrupt_file_does_not_raise(fm, tmp_path):
    bogus = tmp_path / "corrupt.gpkg"
    bogus.write_bytes(b"not sqlite")
    fm._embed_provenance(
        bogus, "gpkg", "berlin.osm.pbf", ["amenity"], [], ["nodes"]
    )


# ── _embed_provenance_geojson exception swallow ──────────────────────────────


def test_embed_provenance_geojson_invalid_json_does_not_raise(fm, tmp_path):
    bogus = tmp_path / "corrupt.geojson"
    bogus.write_text("{nope", encoding="utf-8")
    original = bogus.read_text(encoding="utf-8")

    fm._embed_provenance(
        bogus, "geojson", "berlin.osm.pbf", ["amenity"], [], ["nodes"]
    )

    assert bogus.read_text(encoding="utf-8") == original


def test_embed_provenance_geojson_missing_file_does_not_raise(fm, tmp_path):
    fm._embed_provenance(
        tmp_path / "missing.geojson", "geojson",
        "berlin.osm.pbf", ["amenity"], [], ["nodes"],
    )
