"""F4: a failing phase must not leave anything at the final output paths.

Tests:
1. test_failed_export_leaves_no_final_file   — export (gpkg) fails → no gpkg at final path
2. test_failed_exclude_leaves_no_final_pbf   — exclude pass fails → no PBF at final path
                                               (pre-fix: pre-exclude result sits at final path)
3. test_successful_job_publishes_outputs     — happy path → both finals exist, paths correct
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from filter_manager import FilterJob, FilterManager

# ── helpers ───────────────────────────────────────────────────────────────────


def _job(tmp_data_dir, **overrides) -> FilterJob:
    out_dir = overrides.pop("output_dir", None) or str(tmp_data_dir / "out")
    defaults = dict(
        id="t",
        source_files=["a.osm.pbf"],
        tags=["highway"],
        exclude_tags=[],
        geometry_types=["ways"],
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


def _ensure_source(tmp_data_dir, name="a.osm.pbf", size=1000):
    (tmp_data_dir / name).write_bytes(b"x" * size)


# ── Test 1: failed export (gpkg) must not leave final gpkg ───────────────────


async def test_failed_export_leaves_no_final_file(tmp_data_dir):
    """tags-filter succeeds (writes -o file), ogr2ogr fails.
    Final gpkg must NOT exist at out_dir/gpkg/a_t.gpkg.
    (The successful PBF phase publishes normally — we check gpkg specifically.)
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(
        tmp_data_dir,
        output_formats=["pbf", "gpkg"],
        output_dir=str(out_dir),
    )

    async def fake_run_cmd(cmd, _job, **_):
        # tags-filter: write the -o file and succeed
        if cmd[0] == "osmium" and "tags-filter" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"pbf")
            return 0
        # osmium export: write shared geojson and succeed
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            return 0
        # ogr2ogr: fail (no file written)
        if cmd[0] == "ogr2ogr":
            return 1
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "error"
    # The failing export must not leave a file at the final gpkg path.
    assert not (
        out_dir / "gpkg" / "a_t.gpkg"
    ).exists(), "ogr2ogr failure must not leave a truncated file at the final gpkg path"


# ── Test 2: failed exclude must not leave pre-exclude PBF at final path ───────


async def test_failed_exclude_leaves_no_final_pbf(tmp_data_dir):
    """tags-filter succeeds (writes -o file to pbf_work / pbf_out),
    exclude pass (--invert-match) fails.
    Final PBF must NOT exist at out_dir/pbf/a_t.osm.pbf.

    Pre-fix: filter writes directly to pbf_out; exclude failure left that
    pre-exclude file at the final path — indistinguishable from valid output.
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(
        tmp_data_dir,
        output_formats=["pbf"],
        exclude_tags=["access=no"],
        suffix="t",
        output_dir=str(out_dir),
    )

    async def fake_run_cmd(cmd, _job, **_):
        # tags-filter: write the -o file and succeed (whether pbf_work or pbf_out)
        if cmd[0] == "osmium" and "tags-filter" in cmd and "--invert-match" not in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"pre-exclude-pbf")
            return 0
        # exclude pass (--invert-match): fail
        if "--invert-match" in cmd:
            return 1
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        await fm.run_job(job)

    assert job.status == "error"
    final_pbf = out_dir / "pbf" / "a_t.osm.pbf"
    assert not final_pbf.exists(), (
        "exclude failure must not leave the pre-exclude PBF at the final output path; "
        f"found: {final_pbf}"
    )


# ── Test 3: successful job publishes all outputs to their final paths ─────────


async def test_successful_job_publishes_outputs(tmp_data_dir):
    """All commands succeed; both pbf and gpkg finals must exist at their
    canonical paths, and job.output_files must reference the FINAL paths
    (not tmp paths).
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(
        tmp_data_dir,
        output_formats=["pbf", "gpkg"],
        output_dir=str(out_dir),
    )

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "osmium" and "tags-filter" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"pbf-data")
            return 0
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
            return 0
        if cmd[0] == "ogr2ogr":
            # ogr2ogr writes to cmd[3] (the destination)
            out_path = Path(cmd[3])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"gpkg-data")
            return 0
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done", f"Expected done, got {job.status!r}: {job.error}"

    # Both final files must exist at their canonical locations.
    final_pbf = out_dir / "pbf" / "a_t.osm.pbf"
    final_gpkg = out_dir / "gpkg" / "a_t.gpkg"
    assert final_pbf.exists(), f"Final PBF missing at {final_pbf}"
    assert final_gpkg.exists(), f"Final GPKG missing at {final_gpkg}"

    # job.output_files must reference the FINAL paths, not tmp paths.
    tmp_marker = str(tmp_data_dir / "tmp")
    for path_str in job.output_files:
        assert not path_str.startswith(
            tmp_marker
        ), f"output_files must contain final paths, not tmp: {path_str}"

    # Both formats present in output_files.
    assert any(p.endswith(".osm.pbf") for p in job.output_files), "PBF must be in output_files"
    assert any(p.endswith(".gpkg") for p in job.output_files), "GPKG must be in output_files"
