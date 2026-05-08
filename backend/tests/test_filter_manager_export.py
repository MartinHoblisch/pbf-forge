"""Tests for FilterManager._osmium_export_convert orchestration.

This method bridges osmium-export → ogrinfo → ogr2ogr SQL projection. It is
fragile because:
  - tmp_geojson cleanup must always run (try/finally) — otherwise tmp dir
    fills up across many jobs
  - osmium-export failure must short-circuit the ogr2ogr call
  - ogr2ogr command shape differs by output format (GPKG needs -nln/-a_srs,
    GeoJSON does not)
  - the SELECT statement built from _get_fields output must reach ogr2ogr
    unchanged so column ordering is preserved

_get_fields itself is fully covered by test_ogrinfo_parser.py — not
duplicated here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filter_manager import FilterJob, FilterManager


def _job(**overrides) -> FilterJob:
    defaults = dict(
        id="t",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["gpkg"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(overrides)
    return FilterJob(**defaults)


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


# ── geojson: no -nln, no -a_srs ──────────────────────────────────────────────


async def test_osmium_export_convert_geojson_no_nln_or_srs(tmp_path):
    fm = _fm()
    job = _job()

    captured_cmds = []

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            rc = await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="geojson",
                out_file=tmp_path / "out.geojson",
                job=job,
                tmp=tmp_path,
            )

    assert rc == 0
    # Second cmd is ogr2ogr — verify shape
    ogr_cmd = captured_cmds[1]
    assert ogr_cmd[0] == "ogr2ogr"
    assert "-f" in ogr_cmd
    assert ogr_cmd[ogr_cmd.index("-f") + 1] == "GeoJSON"
    assert "-nln" not in ogr_cmd
    assert "-a_srs" not in ogr_cmd


# ── gpkg: -nln <stem> + -a_srs EPSG:4326 ─────────────────────────────────────


async def test_osmium_export_convert_gpkg_includes_nln_and_srs(tmp_path):
    fm = _fm()
    job = _job(output_formats=["gpkg"])

    captured_cmds = []

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        return 0

    out_file = tmp_path / "result.gpkg"
    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["highway"])):
            rc = await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="gpkg",
                out_file=out_file,
                job=job,
                tmp=tmp_path,
            )

    assert rc == 0
    ogr_cmd = captured_cmds[1]
    assert ogr_cmd[ogr_cmd.index("-f") + 1] == "GPKG"
    assert ogr_cmd[ogr_cmd.index("-nln") + 1] == out_file.stem
    assert ogr_cmd[ogr_cmd.index("-a_srs") + 1] == "EPSG:4326"


# ── osmium failure short-circuits ─────────────────────────────────────────────


async def test_osmium_export_convert_returns_early_on_osmium_failure(tmp_path):
    fm = _fm()
    job = _job()

    call_count = {"n": 0}

    async def fake_run_cmd(cmd, _job):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 1  # osmium fails
        return 0  # ogr2ogr — must NOT reach this

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])) as gf:
            rc = await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="geojson",
                out_file=tmp_path / "out.geojson",
                job=job,
                tmp=tmp_path,
            )

    assert rc == 1
    assert call_count["n"] == 1  # ogr2ogr never invoked
    gf.assert_not_called()  # _get_fields also skipped


# ── tmp_geojson cleanup on success ───────────────────────────────────────────


async def test_osmium_export_convert_cleans_tmp_geojson_on_success(tmp_path):
    fm = _fm()
    job = _job()
    out_file = tmp_path / "out.geojson"
    expected_tmp = tmp_path / f"{out_file.stem}_export.geojson"

    async def fake_run_cmd(cmd, _job):
        # Simulate osmium creating the tmp file on first call
        if "osmium" in cmd[0]:
            expected_tmp.write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="geojson",
                out_file=out_file,
                job=job,
                tmp=tmp_path,
            )

    assert not expected_tmp.exists(), "tmp_geojson must be cleaned up on success"


# ── tmp_geojson cleanup on ogr2ogr failure ───────────────────────────────────


async def test_osmium_export_convert_cleans_tmp_geojson_on_ogr2ogr_failure(tmp_path):
    fm = _fm()
    job = _job()
    out_file = tmp_path / "out.gpkg"
    expected_tmp = tmp_path / f"{out_file.stem}_export.geojson"

    async def fake_run_cmd(cmd, _job):
        if "osmium" in cmd[0]:
            expected_tmp.write_text("{}", encoding="utf-8")
            return 0
        return 1  # ogr2ogr failure

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            rc = await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="gpkg",
                out_file=out_file,
                job=job,
                tmp=tmp_path,
            )

    assert rc == 1
    assert not expected_tmp.exists(), "tmp_geojson must be cleaned up even on failure"


# ── _get_fields output reaches ogr2ogr SQL ───────────────────────────────────


async def test_osmium_export_convert_uses_get_fields_output_in_sql(tmp_path):
    fm = _fm()
    job = _job()

    captured_cmds = []

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name", "highway"])):
            await fm._osmium_export_convert(
                src=tmp_path / "src.osm.pbf",
                fmt="geojson",
                out_file=tmp_path / "out.geojson",
                job=job,
                tmp=tmp_path,
            )

    ogr_cmd = captured_cmds[1]
    # SQL is the value after -sql
    sql = ogr_cmd[ogr_cmd.index("-sql") + 1]
    assert '"name"' in sql
    assert '"highway"' in sql
    assert '"@id" AS osm_id' in sql
