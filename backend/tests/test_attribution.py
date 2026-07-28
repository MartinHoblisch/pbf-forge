"""Tests that outputs carry ODbL attribution and provenance metadata.

Covers GPKG and GeoJSON embedding, the PBF no-op, and UTF-8 preservation.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from unittest.mock import MagicMock

import pytest

from config import ATTRIBUTION
from filter_manager import FilterManager

# ── helpers ───────────────────────────────────────────────────────────────────
# closing() around connect(): the connection's own context manager commits the
# transaction but leaves the handle open, which surfaces as an intermittent
# ResourceWarning once the garbage collector gets around to it.


def _make_gpkg(path) -> None:
    """A minimal SQLite file standing in for a GeoPackage."""
    with closing(sqlite3.connect(str(path))) as conn, conn:
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")


def _query(path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(str(path))) as conn:
        return conn.execute(sql).fetchall()


# ── Provenance ────────────────────────────────────────────────────────────────


def test_provenance_gpkg_row_inserted(tmp_path):
    fm = FilterManager(ws_manager=MagicMock())
    gpkg = tmp_path / "test.gpkg"
    _make_gpkg(gpkg)

    fm._embed_provenance(gpkg, "gpkg", "berlin.osm.pbf", ["amenity=cafe"], [], ["nodes"])

    rows = _query(gpkg, "SELECT md_scope, mime_type, metadata FROM gpkg_metadata")

    assert len(rows) == 1
    scope, mime, metadata = rows[0]
    assert scope == "dataset"
    assert mime == "application/json"
    prov = json.loads(metadata)
    assert prov["source"] == "berlin.osm.pbf"
    assert prov["tags"] == ["amenity=cafe"]
    assert prov["exclude_tags"] == []
    assert prov["geometry_types"] == ["nodes"]
    assert "generated_by" in prov
    assert "generated_at" in prov


def test_provenance_geojson_key_inserted(tmp_path):
    fm = FilterManager(ws_manager=MagicMock())
    geojson = tmp_path / "test.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    fm._embed_provenance(
        geojson,
        "geojson",
        "berlin.osm.pbf",
        ["highway"],
        ["railway:traffic_mode=passenger"],
        ["ways"],
    )

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert "provenance" in data
    prov = data["provenance"]
    assert prov["source"] == "berlin.osm.pbf"
    assert prov["tags"] == ["highway"]
    assert prov["exclude_tags"] == ["railway:traffic_mode=passenger"]
    assert prov["geometry_types"] == ["ways"]
    assert "generated_at" in prov


def test_provenance_pbf_noop(tmp_path):
    fm = FilterManager(ws_manager=MagicMock())
    pbf = tmp_path / "out.osm.pbf"
    pbf.write_bytes(b"\x00\x01\x02")

    fm._embed_provenance(pbf, "pbf", "berlin.osm.pbf", ["highway"], [], ["ways"])

    assert pbf.read_bytes() == b"\x00\x01\x02"


# ── UTF-8 multibyte ───────────────────────────────────────────────────────────


def test_geojson_multibyte_features_preserved(tmp_path):
    """Attribution + provenance embedding must not corrupt non-ASCII feature data."""
    fm = FilterManager(ws_manager=MagicMock())
    geojson = tmp_path / "tokyo.geojson"
    features = [
        {"type": "Feature", "properties": {"name": "東京都", "amenity": "café"}, "geometry": None},
        {"type": "Feature", "properties": {"name": "Αθήνα"}, "geometry": None},
        {"type": "Feature", "properties": {"name": "مكة المكرمة"}, "geometry": None},
    ]
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )

    fm._embed_attribution_geojson(geojson)
    fm._embed_provenance(geojson, "geojson", "japan.osm.pbf", ["name"], [], ["nodes"])

    data = json.loads(geojson.read_text(encoding="utf-8"))
    names = [f["properties"]["name"] for f in data["features"]]
    assert names == ["東京都", "Αθήνα", "مكة المكرمة"]
    assert data["features"][0]["properties"]["amenity"] == "café"
    assert "attribution" in data
    assert "provenance" in data


def test_provenance_multibyte_tags_preserved(tmp_path):
    """Provenance JSON must round-trip non-ASCII tags without corruption."""
    fm = FilterManager(ws_manager=MagicMock())
    geojson = tmp_path / "test.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )
    tags = ["name:ja=東京都", "name:ar=مكة", "name:el=Αθήνα"]

    fm._embed_provenance(geojson, "geojson", "test.osm.pbf", tags, [], ["nodes"])

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert data["provenance"]["tags"] == tags


@pytest.fixture
def fm():
    return FilterManager(ws_manager=MagicMock())


def test_gpkg_attribution_inserted(tmp_path, fm):
    gpkg = tmp_path / "test.gpkg"
    _make_gpkg(gpkg)

    fm._embed_attribution_gpkg(gpkg)

    rows = _query(gpkg, "SELECT metadata FROM gpkg_metadata")

    assert len(rows) == 1
    assert rows[0][0] == ATTRIBUTION


def test_gpkg_attribution_string_contains_odbl(tmp_path, fm):
    gpkg = tmp_path / "test.gpkg"
    _make_gpkg(gpkg)

    fm._embed_attribution_gpkg(gpkg)

    metadata = _query(gpkg, "SELECT metadata FROM gpkg_metadata")[0][0]

    assert "ODbL" in metadata
    assert "OpenStreetMap" in metadata
    # The distributor is deliberately absent: ODbL credits the contributors,
    # and the host varies per source. The report names it per output instead.
    assert "Geofabrik" not in metadata


def test_gpkg_attribution_scope_and_mime(tmp_path, fm):
    gpkg = tmp_path / "test.gpkg"
    _make_gpkg(gpkg)

    fm._embed_attribution_gpkg(gpkg)

    row = _query(gpkg, "SELECT md_scope, mime_type FROM gpkg_metadata")[0]

    assert row[0] == "dataset"
    assert row[1] == "text/plain"


def test_geojson_attribution_inserted(tmp_path, fm):
    geojson = tmp_path / "test.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    fm._embed_attribution_geojson(geojson)

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert data["attribution"] == ATTRIBUTION


def test_geojson_attribution_string_contains_odbl(tmp_path, fm):
    geojson = tmp_path / "test.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    fm._embed_attribution_geojson(geojson)

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert "ODbL" in data["attribution"]
    assert "OpenStreetMap" in data["attribution"]
    assert "Geofabrik" not in data["attribution"]


def test_geojson_existing_keys_preserved(tmp_path, fm):
    geojson = tmp_path / "test.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"id": 1}]}),
        encoding="utf-8",
    )

    fm._embed_attribution_geojson(geojson)

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert data["features"] == [{"id": 1}]
    assert "attribution" in data


def test_embed_attribution_dispatches_gpkg(tmp_path, fm):
    gpkg = tmp_path / "out.gpkg"
    _make_gpkg(gpkg)

    fm._embed_attribution(gpkg, "gpkg")

    rows = _query(gpkg, "SELECT metadata FROM gpkg_metadata")
    assert len(rows) == 1


def test_embed_attribution_dispatches_geojson(tmp_path, fm):
    geojson = tmp_path / "out.geojson"
    geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    fm._embed_attribution(geojson, "geojson")

    data = json.loads(geojson.read_text(encoding="utf-8"))
    assert "attribution" in data


def test_embed_attribution_pbf_noop(tmp_path, fm):
    pbf = tmp_path / "out.osm.pbf"
    pbf.write_bytes(b"\x00\x01\x02")

    fm._embed_attribution(pbf, "pbf")

    assert pbf.read_bytes() == b"\x00\x01\x02"
