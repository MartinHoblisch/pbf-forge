"""Tests for streaming GeoJSON key injection.

Replaces the previous full-file json.loads/json.dumps round-trip which
OOM-killed the backend on multi-GB GeoJSON outputs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from filter_manager import FilterManager


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _write_geojson(path, n_features: int) -> None:
    """Build a syntactically valid GeoJSON file with n_features."""
    head = '{"type":"FeatureCollection","features":['
    feature = (
        '{"type":"Feature","properties":{"id":%d},"geometry":{"type":"Point","coordinates":[0,0]}}'
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head)
        for i in range(n_features):
            if i:
                fh.write(",")
            fh.write(feature % i)
        fh.write("]}")


def test_inject_attribution_preserves_features(tmp_path):
    fm = _fm()
    gj = tmp_path / "out.geojson"
    _write_geojson(gj, n_features=10)

    fm._stream_inject_geojson_keys(gj, {"attribution": "© OSM"})

    data = json.loads(gj.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert data["attribution"] == "© OSM"
    assert len(data["features"]) == 10
    assert data["features"][0]["properties"]["id"] == 0


def test_inject_two_keys_sequentially(tmp_path):
    """Mirrors real pipeline: attribution then provenance, both before features."""
    fm = _fm()
    gj = tmp_path / "out.geojson"
    _write_geojson(gj, n_features=5)

    fm._stream_inject_geojson_keys(gj, {"attribution": "© OSM"})
    fm._stream_inject_geojson_keys(gj, {"provenance": {"source": "europe.osm.pbf"}})

    data = json.loads(gj.read_text(encoding="utf-8"))
    assert data["attribution"] == "© OSM"
    assert data["provenance"] == {"source": "europe.osm.pbf"}
    assert len(data["features"]) == 5


def test_inject_handles_features_token_straddling_chunk(tmp_path):
    """The "features" token can be split across the 64-KB read boundary."""
    fm = _fm()
    gj = tmp_path / "out.geojson"
    # Pad metadata so `"features"` lands across the first chunk boundary
    padding = "x" * (64 * 1024 - 4)  # ensures token spans 64-KB boundary
    gj.write_text(
        '{"type":"FeatureCollection","pad":"' + padding + '","features":[]}',
        encoding="utf-8",
    )

    fm._stream_inject_geojson_keys(gj, {"attribution": "© OSM"})

    data = json.loads(gj.read_text(encoding="utf-8"))
    assert data["attribution"] == "© OSM"
    assert data["features"] == []
    assert data["pad"] == padding


def test_inject_missing_features_key_raises(tmp_path):
    fm = _fm()
    gj = tmp_path / "broken.geojson"
    gj.write_text('{"type":"FeatureCollection"}', encoding="utf-8")
    # Direct call raises so caller can log and skip
    import pytest

    with pytest.raises(ValueError, match='"features" key not found'):
        fm._stream_inject_geojson_keys(gj, {"attribution": "© OSM"})


def test_inject_empty_extras_is_noop(tmp_path):
    fm = _fm()
    gj = tmp_path / "out.geojson"
    _write_geojson(gj, n_features=3)
    original = gj.read_text(encoding="utf-8")

    fm._stream_inject_geojson_keys(gj, {})

    assert gj.read_text(encoding="utf-8") == original


def test_embed_attribution_wrapper_does_not_raise_on_broken_file(tmp_path):
    """Public wrapper logs and swallows errors to keep pipeline alive."""
    fm = _fm()
    gj = tmp_path / "broken.geojson"
    gj.write_text('{"type":"FeatureCollection"}', encoding="utf-8")
    # Should not raise
    fm._embed_attribution_geojson(gj)
