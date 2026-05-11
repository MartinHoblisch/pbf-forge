"""Tests for the G2 osmium-export sharing behaviour in run_job.

osmium export runs at most ONCE per source across all non-PBF formats that
need the export path (all/manual modes).  The other_tags+GPKG format uses the
GDAL OSM driver directly and never triggers osmium export.
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

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            export_call_count["n"] += 1
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[3])
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


# ── other_tags+GPKG never triggers osmium export ─────────────────────────────


async def test_other_tags_gpkg_skips_osmium_export(tmp_data_dir):
    """other_tags+gpkg uses GDAL OSM driver — no osmium export command."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"], columns_mode="other_tags")

    captured_cmds = []

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[3])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_embed_attribution"):
            with patch.object(fm, "_embed_provenance"):
                await fm.run_job(job)

    assert job.status == "done"
    assert not any(c[0] == "osmium" and "export" in c for c in captured_cmds)
    assert any(c[0] == "ogr2ogr" for c in captured_cmds)


# ── osmium export failure sets status=error ──────────────────────────────────


async def test_osmium_export_failure_sets_error(tmp_data_dir):
    """If osmium export returns non-zero, run_job sets status='error'."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"], columns_mode="all")

    async def fake_run_cmd(cmd, _job):
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

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[3])
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


# ── gpkg + manual goes direct via osmconf, skipping osmium export ────────────


async def test_gpkg_manual_uses_osmconf_and_skips_export(tmp_data_dir):
    """gpkg+manual reads PBF directly with OSM_CONFIG_FILE; no osmium export."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="manual",
        manual_keys=["name", "highway"],
    )

    captured_cmds = []

    async def fake_run_cmd(cmd, _job):
        captured_cmds.append(list(cmd))
        if cmd[0] == "ogr2ogr":
            # ogr2ogr <flags...> -f GPKG out.gpkg in.osm.pbf
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_embed_attribution"):
            with patch.object(fm, "_embed_provenance"):
                await fm.run_job(job)

    assert job.status == "done"
    assert not any(c[0] == "osmium" and "export" in c for c in captured_cmds)
    ogr_cmd = next(c for c in captured_cmds if c[0] == "ogr2ogr")
    assert "OSM_CONFIG_FILE" in ogr_cmd
    cfg_idx = ogr_cmd.index("OSM_CONFIG_FILE")
    cfg_path = Path(ogr_cmd[cfg_idx + 1])
    assert cfg_path.name.endswith("_osmconf.ini")
