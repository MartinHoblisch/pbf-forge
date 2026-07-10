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
  - attribution + provenance embedded after success
  - phase_started_at cleared on error so the FE elapsed-ticker stops
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

    # F4: tags-filter now writes to pbf_work (tmp), not the final path.
    # The mock must write the -o target so shutil.move(pbf_work → pbf_out) succeeds.
    async def fake_run_cmd(cmd, _j, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x")
        return 0

    reduce_mock = AsyncMock(return_value=0)
    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
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

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            # Create the shared geojson so _get_fields can be called
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    # No PBF output file (only geojson was requested)
    assert all(not p.endswith(".osm.pbf") for p in job.output_files)
    assert any(p.endswith(".geojson") for p in job.output_files)
    # filter (1) + osmium export (2) + other_tags fold (3) + ogr2ogr (4)
    assert len(captured_cmds) == 4
    assert captured_cmds[0][1] == "tags-filter"
    assert captured_cmds[1][1] == "export"
    assert "geojsonseq_fold.py" in captured_cmds[2][1]
    assert captured_cmds[3][0] == "ogr2ogr"


async def test_run_job_geojson_with_exclude_runs_two_filter_passes(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"], exclude_tags=["x=y"])

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    # filter + invert-match + osmium export + ogr2ogr = 4 _run_cmd calls
    filter_cmds = [c for c in captured_cmds if c[0] == "osmium" and c[1] == "tags-filter"]
    assert len(filter_cmds) == 2
    assert job.status == "done"


# ── attribution + provenance embedded after success ──────────────────────────


async def test_run_job_embeds_attribution_and_provenance(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"])

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
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

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        # F4: tags-filter now writes to pbf_work (tmp); write the -o target so
        # shutil.move(pbf_work → pbf_out) succeeds without a real osmium binary.
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_embed_attribution") as embed_a:
            with patch.object(fm, "_embed_provenance") as embed_p:
                await fm.run_job(job)

    assert job.status == "done"
    # PBF-only: no osmium export, no ogr2ogr, no embed
    assert not any(c[0] == "osmium" and "export" in c for c in captured_cmds)
    assert not any(c[0] == "ogr2ogr" for c in captured_cmds)
    embed_a.assert_not_called()
    embed_p.assert_not_called()
