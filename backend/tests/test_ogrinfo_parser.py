"""Tests for FilterManager._get_fields — ogrinfo output parser.

Bug class:
  - The regex is anchored to GDAL field types on the right side. If the anchor
    is wrong, header lines like 'INFO: Open of...' or 'Geometry: Unknown (any)'
    leak into the field list → invalid SQL when fed to ogr2ogr → blank output.
  - @id must be filtered (it's renamed to osm_id elsewhere).
  - OSM tag keys with colon (addr:street, name:en) must be recognised.

Each test mocks asyncio.create_subprocess_exec so no real ogrinfo is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from filter_manager import FilterManager


def _proc_with_stdout(text: str) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(text.encode("utf-8"), b""))
    return proc


@pytest.fixture
def fm():
    return FilterManager(ws_manager=AsyncMock())


_REALISTIC_OGRINFO = """\
INFO: Open of '/tmp/export.geojson'
      using driver 'GeoJSON' successful.

Layer name: export
Geometry: Unknown (any)
Feature Count: 42
Extent: (12.0, 52.0) - (14.0, 53.0)
Layer SRS WKT:
GEOGCS["WGS 84",DATUM["WGS_1984"]]
FID Column = id
Geometry Column = geometry
@id: Integer64 (0.0)
name: String (0.0)
highway: String (0.0)
addr:street: String (0.0)
addr.housenumber: String (0.0)
population: Integer64 (0.0)
area: Real (0.0)
last_modified: DateTime (0.0)
created_date: Date (0.0)
"""


async def test_parses_well_formed_geojson_dump(fm, tmp_path):
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(_REALISTIC_OGRINFO)):
        fields = await fm._get_fields(tmp_path / "irrelevant.geojson")
    assert fields == [
        "name", "highway", "addr:street", "addr.housenumber",
        "population", "area", "last_modified", "created_date",
    ]


async def test_filters_out_at_id(fm, tmp_path):
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(_REALISTIC_OGRINFO)):
        fields = await fm._get_fields(tmp_path / "x")
    assert "@id" not in fields


async def test_ignores_geometry_unknown_header(fm, tmp_path):
    """'Geometry: Unknown (any)' must not be picked up — 'Unknown' is not a
    GDAL type, so the regex right-anchor protects against it."""
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(_REALISTIC_OGRINFO)):
        fields = await fm._get_fields(tmp_path / "x")
    assert "Geometry" not in fields


async def test_ignores_info_header(fm, tmp_path):
    """'INFO: Open of...' line must not match — 'Open' is not a GDAL type."""
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(_REALISTIC_OGRINFO)):
        fields = await fm._get_fields(tmp_path / "x")
    assert "INFO" not in fields


async def test_ignores_feature_count_line(fm, tmp_path):
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(_REALISTIC_OGRINFO)):
        fields = await fm._get_fields(tmp_path / "x")
    assert "Feature Count" not in fields
    assert "Feature" not in fields


async def test_recognizes_all_gdal_types(fm, tmp_path):
    """String, Integer, Integer64, Real, Date, DateTime, Time, Binary."""
    text = (
        "a_str: String (0.0)\n"
        "a_int: Integer (0.0)\n"
        "a_int64: Integer64 (0.0)\n"
        "a_real: Real (0.0)\n"
        "a_date: Date (0.0)\n"
        "a_datetime: DateTime (0.0)\n"
        "a_time: Time (0.0)\n"
        "a_binary: Binary (0.0)\n"
    )
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout(text)):
        fields = await fm._get_fields(tmp_path / "x")
    assert fields == [
        "a_str", "a_int", "a_int64", "a_real",
        "a_date", "a_datetime", "a_time", "a_binary",
    ]


async def test_empty_output_returns_empty_list(fm, tmp_path):
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout("")):
        assert await fm._get_fields(tmp_path / "x") == []


async def test_only_at_id_present_returns_empty(fm, tmp_path):
    """A layer with only @id (no real columns) → empty field list."""
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout("@id: Integer64 (0.0)\n")):
        assert await fm._get_fields(tmp_path / "x") == []


async def test_field_with_dot_in_name(fm, tmp_path):
    """Some OSM keys contain dots (e.g. 'addr.housenumber' after laundering)."""
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout("foo.bar: String (0.0)\n")):
        assert await fm._get_fields(tmp_path / "x") == ["foo.bar"]


async def test_field_with_hyphen_in_name(fm, tmp_path):
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout("addr-street: String (0.0)\n")):
        assert await fm._get_fields(tmp_path / "x") == ["addr-street"]


async def test_indented_lines_not_matched(fm, tmp_path):
    """Lines starting with whitespace must not match (regex is anchored ^)."""
    with patch("filter_manager.asyncio.create_subprocess_exec",
               return_value=_proc_with_stdout("    name: String (0.0)\n")):
        assert await fm._get_fields(tmp_path / "x") == []
