"""Tests for the G2 osmium-export sharing behaviour in run_job.

osmium export runs at most ONCE per source across all non-PBF formats.
All non-PBF formats (geojson, gpkg) always go through the osmium-export path
regardless of columns_mode (audit F1: one route = one schema).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from filter_manager import FilterJob, FilterManager


def _job(tmp_data_dir, **overrides) -> FilterJob:
    out_dir = overrides.pop("output_dir", None) or str(tmp_data_dir / "out")
    defaults = dict(
        id="t",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["gpkg"],
        output_dir=out_dir,
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(overrides)
    return FilterJob(**defaults)


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _ensure_source(tmp_data_dir, name="berlin.osm.pbf", size=1000):
    (tmp_data_dir / name).write_bytes(b"x" * size)


# ── G2: osmium export shared across geojson + gpkg (manual mode) ─────────────


async def test_g2_osmium_export_runs_once_for_multiple_formats(tmp_data_dir):
    """geojson + gpkg with manual columns_mode → osmium export called exactly once."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["geojson", "gpkg"],
        columns_mode="manual",
        manual_keys=["name"],
    )

    export_call_count = {"n": 0}
    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            export_call_count["n"] += 1
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    assert export_call_count["n"] == 1, "osmium export must run exactly once (G2)"
    # Two ogr2ogr calls — one per format
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert len(ogr_cmds) == 2
    # osmium export must carry the disk-backed node index flag (RAM cap)
    export_cmd = next(c for c in captured_cmds if c[0] == "osmium" and "export" in c)
    assert "--index-type=sparse_file_array,sparse_file_array" in export_cmd


# ── osmium export failure sets status=error ──────────────────────────────────


async def test_osmium_export_failure_sets_error(tmp_data_dir):
    """If osmium export returns non-zero, run_job sets status='error'."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"], columns_mode="all")

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "osmium" and "export" in cmd:
            return 1  # export fails
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            await fm.run_job(job)

    assert job.status == "error"
    assert "osmium export" in (job.error or "")


# ── gpkg export path includes -gt and OGR_SQLITE_SYNCHRONOUS ────────────────


async def test_gpkg_export_path_includes_perf_flags(tmp_data_dir):
    """ogr2ogr for GPKG (all mode) must carry -gt and OGR_SQLITE_SYNCHRONOUS."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"], columns_mode="all")

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    ogr_cmd = next(c for c in captured_cmds if c[0] == "ogr2ogr")
    assert "-gt" in ogr_cmd
    assert ogr_cmd[ogr_cmd.index("-gt") + 1] == "65536"
    assert "OGR_SQLITE_SYNCHRONOUS" in ogr_cmd


# ── gpkg + geojson must reuse shared geojson (no GDAL OSM driver OOM) ────────


async def test_gpkg_plus_geojson_reuses_shared_geojson_for_gpkg(tmp_data_dir):
    """When GeoJSON is also requested, GPKG must read from shared geojson, not
    direct from PBF. GDAL OSM driver has no disk-backed index and OOMs on
    Europe-scale sources with limited RAM."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["geojson", "gpkg"],
        columns_mode="manual",
        manual_keys=["name"],
    )

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    # osmium export must run (creates shared geojson)
    export_cmds = [c for c in captured_cmds if c[0] == "osmium" and "export" in c]
    assert len(export_cmds) == 1
    # GPKG ogr2ogr must NOT carry OSM_CONFIG_FILE (would mean direct-PBF path)
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert all("OSM_CONFIG_FILE" not in c for c in ogr_cmds)
    # Both ogr2ogr calls read from shared geojson (-sql path)
    assert all("-sql" in c for c in ogr_cmds)


# ── F1: gpkg-only always uses osmium export path ──────────────────────────────


async def test_gpkg_only_uses_osmium_export_path(tmp_data_dir):
    """F1: gpkg-only jobs must use the same osmium-export route as gpkg+geojson.

    Before F1 fix: gpkg-only with other_tags or manual took the GDAL-OSM-driver
    path (gpkg_direct), skipping osmium export entirely.  After fix: every
    non-PBF format — including gpkg-only — always runs osmium export first and
    then ogr2ogr on the shared geojson.  One route = one schema.
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="other_tags",
    )

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    # Must have called osmium export (new unified route)
    export_cmds = [c for c in captured_cmds if c[0] == "osmium" and "export" in c]
    assert len(export_cmds) == 1, "gpkg-only must use osmium export (F1)"
    # osmium export carries disk-backed node index
    assert "--index-type=sparse_file_array,sparse_file_array" in export_cmds[0]
    # ogr2ogr reads from shared geojson, NOT from PBF
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert len(ogr_cmds) == 1
    assert "-sql" in ogr_cmds[0], "ogr2ogr must read from shared geojson via -sql"
    # No OSM_CONFIG_FILE (GDAL driver path must be gone)
    assert "OSM_CONFIG_FILE" not in ogr_cmds[0], "OSM_CONFIG_FILE must not appear (F1)"


# ── GPKG column-count guard (SQLite max 2000 columns) ────────────────────────


async def test_gpkg_column_guard_fails_fast_over_limit(tmp_data_dir):
    """Europe-scale extracts can carry >2000 distinct tag keys; SQLite caps a
    table at 2000 columns. The job must fail with a clear message BEFORE the
    ogr2ogr conversion is attempted."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"])
    many_fields = [f"key_{i}" for i in range(2300)]
    ogr_calls = []

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "ogr2ogr":
            ogr_calls.append(cmd)
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            await fm.run_job(job)

    assert job.status == "error"
    assert "2000" in (job.error or "")
    assert "2300" in (job.error or "")
    assert not ogr_calls, "ogr2ogr must not be attempted over the column limit"


async def test_gpkg_column_guard_ignores_geojson(tmp_data_dir):
    """GeoJSON has no column limit — same oversized field list must pass."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"])
    many_fields = [f"key_{i}" for i in range(2300)]

    async def fake_run_cmd(cmd, _job, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"


async def test_gpkg_column_guard_counts_manual_selection(tmp_data_dir):
    """Manual mode counts only the selected keys — a small manual pick from an
    oversized extract must pass."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="manual",
        manual_keys=["key_1", "key_2"],
    )
    many_fields = [f"key_{i}" for i in range(2300)]

    async def fake_run_cmd(cmd, _job, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
