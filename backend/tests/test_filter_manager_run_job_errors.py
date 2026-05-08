"""Tests for FilterManager.run_job error branches and pipeline arms.

The 200-line run_job is the heart of the app. The existing tests cover
sub-functions (build_expressions, build_phases, run_cmd) but never the
orchestration error paths:

  - mkdir failure on output dir
  - source file missing
  - filter rc != 0
  - exclude pass rc != 0
  - reduce pass rc != 0
  - non-pbf-only path (intermediate filter into tmp)
  - exclude on non-pbf path
  - manual column-mode dispatch
  - GPKG-other_tags routing through _build_ogr_cmd
  - attribution + provenance embedded after success
  - phase_started_at cleared on error so the FE elapsed-ticker stops
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import filter_manager as fm_module
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
        output_formats=["pbf"],
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


# ── mkdir failure ────────────────────────────────────────────────────────────


async def test_run_job_output_dir_mkdir_failure_marks_error(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir)

    with patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
        await fm.run_job(job)

    assert job.status == "error"
    assert "Could not create output directory" in (job.error or "")
    assert job.eta_seconds is None


# ── source file missing ──────────────────────────────────────────────────────


async def test_run_job_source_file_not_found_marks_error(tmp_data_dir):
    fm = _fm()
    # Don't create the source — run_job must catch FileNotFoundError
    job = _job(tmp_data_dir, source_files=["never_existed.osm.pbf"])

    await fm.run_job(job)

    assert job.status == "error"
    assert "never_existed.osm.pbf" in (job.error or "")
    assert "not found" in (job.error or "")


# ── no expressions ───────────────────────────────────────────────────────────


async def test_run_job_no_expressions_marks_error(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, tags=[], geometry_types=[])

    await fm.run_job(job)

    assert job.status == "error"
    assert "No filter expressions" in (job.error or "")


# ── filter rc != 0 ───────────────────────────────────────────────────────────


async def test_run_job_filter_rc_nonzero_raises_runtime_error(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir)

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=1)):
        await fm.run_job(job)

    assert job.status == "error"
    assert "osmium exited with code 1" in (job.error or "")


# ── exclude pass rc != 0 ─────────────────────────────────────────────────────


async def test_run_job_exclude_pass_rc_nonzero_raises(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, exclude_tags=["foo=bar"])

    # First call (filter) succeeds, second (exclude) fails
    rc_seq = AsyncMock(side_effect=[0, 1])
    with patch.object(fm, "_run_cmd", rc_seq):
        await fm.run_job(job)

    assert job.status == "error"
    assert "exclude pass exited with code 1" in (job.error or "")


# ── invalid tag inside run_job ───────────────────────────────────────────────


async def test_run_job_invalid_tag_with_dash_prefix_raises(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, tags=["-malicious"])

    await fm.run_job(job)

    assert job.status == "error"
    assert "Invalid tag expression" in (job.error or "")


# ── error path side-effects ──────────────────────────────────────────────────


async def test_run_job_clears_phase_started_on_error(tmp_data_dir):
    """phase_started_at must be reset to None on error so the frontend's
    elapsed-second ticker stops."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir)

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=1)):
        await fm.run_job(job)

    assert job.phase_started_at is None


async def test_run_job_error_appends_to_log(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir)

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=1)):
        await fm.run_job(job)

    assert "ERROR:" in job.log


# ── reduce phase invocation success path ─────────────────────────────────────


async def test_run_job_invokes_reduce_for_manual_with_keys(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, columns_mode="manual", manual_keys=["name"])

    reduce_mock = AsyncMock(return_value=0)
    with patch.object(fm, "_run_cmd", AsyncMock(return_value=0)):
        with patch.object(fm, "_reduce_pbf_tags", reduce_mock):
            await fm.run_job(job)

    reduce_mock.assert_awaited_once()
    assert job.status == "done"


async def test_run_job_reduce_failure_marks_error(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, columns_mode="manual", manual_keys=["name"])

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=0)):
        with patch.object(fm, "_reduce_pbf_tags", AsyncMock(return_value=1)):
            await fm.run_job(job)

    assert job.status == "error"
    assert "PBF tag reduction failed" in (job.error or "")


# ── non-pbf-only branch ──────────────────────────────────────────────────────


async def test_run_job_geojson_only_filters_to_temp_then_exports(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"])

    run_cmd = AsyncMock(return_value=0)
    export_convert = AsyncMock(return_value=0)

    def make_output(*args, **kwargs):
        # Simulate _osmium_export_convert creating the output file
        out_file = args[2]  # (src, fmt, out_file, job, tmp)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return 0

    export_convert.side_effect = make_output

    with patch.object(fm, "_run_cmd", run_cmd):
        with patch.object(fm, "_osmium_export_convert", export_convert):
            await fm.run_job(job)

    assert job.status == "done"
    # No PBF output file (only geojson was requested)
    assert all(not p.endswith(".osm.pbf") for p in job.output_files)
    assert any(p.endswith(".geojson") for p in job.output_files)
    # filter ran in tmp (1 call); export_convert ran (1 call)
    assert run_cmd.await_count == 1
    assert export_convert.await_count == 1


async def test_run_job_geojson_with_exclude_runs_two_filter_passes(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"], exclude_tags=["x=y"])

    run_cmd = AsyncMock(return_value=0)
    export_convert = AsyncMock(return_value=0)

    def make_output(*args, **kwargs):
        out_file = args[2]
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("{}", encoding="utf-8")
        return 0

    export_convert.side_effect = make_output

    with patch.object(fm, "_run_cmd", run_cmd):
        with patch.object(fm, "_osmium_export_convert", export_convert):
            await fm.run_job(job)

    # filter + invert-match → 2 _run_cmd calls
    assert run_cmd.await_count == 2
    assert job.status == "done"


# ── GPKG other_tags routing through _build_ogr_cmd ───────────────────────────


async def test_run_job_gpkg_other_tags_uses_build_ogr_cmd_path(tmp_data_dir):
    """columns_mode='other_tags' + format='gpkg' → uses _build_ogr_cmd
    (GDAL OSM driver), not _osmium_export_convert."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"], columns_mode="other_tags")

    run_cmd = AsyncMock(return_value=0)
    export_convert = AsyncMock(return_value=0)

    def make_run_output(cmd, job):
        # When ogr2ogr-style cmd is invoked (the second _run_cmd call),
        # produce the output file so embed_* doesn't error.
        if "ogr2ogr" in cmd[0]:
            out = Path(cmd[3])
            out.parent.mkdir(parents=True, exist_ok=True)
            # Minimal valid GPKG (sqlite header) — tests don't open it; just exists
            out.write_bytes(b"")
        return 0

    run_cmd.side_effect = make_run_output

    with patch.object(fm, "_run_cmd", run_cmd):
        with patch.object(fm, "_osmium_export_convert", export_convert):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    # _osmium_export_convert NOT called for the other_tags+gpkg combo
    export_convert.assert_not_awaited()
    # ogr2ogr was invoked via _run_cmd (second call after filter)
    cmds = [call.args[0] for call in run_cmd.await_args_list]
    assert any(c[0] == "ogr2ogr" for c in cmds)


# ── attribution + provenance embedded after success ──────────────────────────


async def test_run_job_embeds_attribution_and_provenance(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"])

    def make_output(*args, **kwargs):
        out_file = args[2]
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=0)):
        with patch.object(fm, "_osmium_export_convert", AsyncMock(side_effect=make_output)):
            with patch.object(fm, "_embed_attribution") as embed_a:
                with patch.object(fm, "_embed_provenance") as embed_p:
                    await fm.run_job(job)

    embed_a.assert_called_once()
    embed_p.assert_called_once()
    # Verify call shape: (path, fmt, source, tags, exclude_tags, geometry_types)
    args = embed_p.call_args.args
    assert args[1] == "geojson"
    assert args[2] == "berlin.osm.pbf"
    assert args[3] == ["amenity"]


# ── PBF-only path: no export, no embed ───────────────────────────────────────


async def test_run_job_pbf_only_no_export_or_embed(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["pbf"])

    with patch.object(fm, "_run_cmd", AsyncMock(return_value=0)):
        with patch.object(fm, "_osmium_export_convert", AsyncMock(return_value=0)) as conv:
            with patch.object(fm, "_embed_attribution") as embed_a:
                with patch.object(fm, "_embed_provenance") as embed_p:
                    await fm.run_job(job)

    assert job.status == "done"
    conv.assert_not_awaited()
    embed_a.assert_not_called()
    embed_p.assert_not_called()
