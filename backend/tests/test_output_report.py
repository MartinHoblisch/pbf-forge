"""Every finished output file gets a readable '<filename>.txt' sidecar report."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import config
from filter_manager import FilterJob, FilterManager, Phase

# ── helpers ───────────────────────────────────────────────────────────────────


def _job(tmp_data_dir, **overrides) -> FilterJob:
    out_dir = overrides.pop("output_dir", None) or str(tmp_data_dir / "out")
    defaults = dict(
        id="job-1",
        source_files=["a.osm.pbf"],
        tags=["railway=rail"],
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


async def _fake_run_cmd(cmd, _job, **_):
    """Succeed at every pipeline step, writing a plausible file each time."""
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
        out_path = Path(cmd[cmd.index("-f") + 2])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"gpkg-data")
        return 0
    return 0


async def _run(fm: FilterManager, job: FilterJob) -> None:
    with patch.object(fm, "_run_cmd", side_effect=_fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)


# ── Reports are written next to every output ──────────────────────────────────


async def test_report_written_beside_every_output(tmp_data_dir):
    """One report per output file, named '<full output filename>.txt'."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(tmp_data_dir, output_formats=["pbf", "gpkg"], output_dir=str(out_dir))

    await _run(fm, job)

    assert job.status == "done", f"Expected done, got {job.status!r}: {job.error}"
    # The double extension must be kept, not replaced.
    assert (out_dir / "pbf" / "a_t.osm.pbf.txt").exists()
    assert (out_dir / "gpkg" / "a_t.gpkg.txt").exists()


async def test_report_contains_sources_filters_and_completion(tmp_data_dir):
    """The report names the source, every filter, the timestamp and the duration."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(
        tmp_data_dir,
        tags=["railway=rail", "railway=narrow_gauge"],
        exclude_tags=["railway:traffic_mode=passenger"],
        geometry_types=["ways"],
        suffix="barge",
        output_formats=["gpkg"],
        output_dir=str(out_dir),
    )

    await _run(fm, job)

    text = (out_dir / "gpkg" / "a_barge.gpkg.txt").read_text(encoding="utf-8")
    assert "a.osm.pbf" in text
    assert "railway=rail" in text
    assert "railway=narrow_gauge" in text
    assert "railway:traffic_mode=passenger" in text
    assert "ways" in text
    assert "barge" in text
    assert "job-1" in text
    assert "a_barge.gpkg" in text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text), "needs date and time"
    assert "Job duration" in text
    assert "PBF Forge v1.0.0" in text
    assert "OpenStreetMap contributors" in text


async def test_report_is_per_source_and_per_format(tmp_data_dir):
    """Each report describes its own source and its own format only."""
    _ensure_source(tmp_data_dir, "a.osm.pbf")
    _ensure_source(tmp_data_dir, "b.osm.pbf")
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(
        tmp_data_dir,
        source_files=["a.osm.pbf", "b.osm.pbf"],
        output_formats=["pbf", "gpkg", "geojson"],
        output_dir=str(out_dir),
    )

    await _run(fm, job)

    assert len(sorted(out_dir.rglob("*.txt"))) == 6  # 2 sources x 3 formats

    a_gpkg = (out_dir / "gpkg" / "a_t.gpkg.txt").read_text(encoding="utf-8")
    b_gpkg = (out_dir / "gpkg" / "b_t.gpkg.txt").read_text(encoding="utf-8")
    a_pbf = (out_dir / "pbf" / "a_t.osm.pbf.txt").read_text(encoding="utf-8")

    # The INPUT section names exactly one source.
    assert "Source extract    a.osm.pbf" in a_gpkg
    assert "Source extract    b.osm.pbf" in b_gpkg

    # Phase lists never leak another format's export or another source's passes.
    assert "export gpkg" in a_gpkg
    assert "export geojson" not in a_gpkg
    assert "export" not in a_pbf
    assert "b.osm.pbf" not in a_gpkg.split("PHASES", 1)[1]


async def test_report_overwrites_existing_file(tmp_data_dir):
    """A stale report of the same name is replaced, not appended to."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    (out_dir / "pbf").mkdir(parents=True)
    stale = out_dir / "pbf" / "a_t.osm.pbf.txt"
    stale.write_text("STALE" * 500, encoding="utf-8")

    await _run(fm, _job(tmp_data_dir, output_dir=str(out_dir)))

    assert "STALE" not in stale.read_text(encoding="utf-8")


async def test_report_omits_empty_rows_and_keeps_layout_width(tmp_data_dir):
    """Rows without a value are dropped; the layout itself stays 80 columns wide.

    A long path or tag expression is printed intact rather than wrapped, because
    a wrapped path cannot be copied into a file manager. Only the parts the
    layout controls — the rules and the phase table — are width-checked.
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"

    await _run(fm, _job(tmp_data_dir, exclude_tags=[], output_dir=str(out_dir)))

    text = (out_dir / "pbf" / "a_t.osm.pbf.txt").read_text(encoding="utf-8")
    assert "Exclude tags" not in text

    lines = text.splitlines()
    rules = [line for line in lines if set(line) in ({"="}, {"-"})]
    assert len(rules) == 3 and all(len(line) == 80 for line in rules)
    assert not [line for line in lines if line != line.rstrip()], "no trailing whitespace"

    phase_lines = lines[lines.index("PHASES (this output)") + 1 :]
    over = [line for line in phase_lines if len(line) > 80]
    assert not over, f"phase table exceeds 80 columns: {over}"


async def test_report_is_not_listed_as_a_job_output(tmp_data_dir):
    """Reports are a side product: they never appear as job outputs."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["pbf", "gpkg"])

    await _run(fm, job)

    assert not any(p.endswith(".txt") for p in job.output_files)
    assert ".txt" not in job.log


async def test_report_shows_host_path(tmp_data_dir):
    """The folder line uses the user's host path, not the container path."""
    config.USER_CONFIG_FILE.write_text(json.dumps({"host_data_dir": "H:\\hostdata"}))
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"

    await _run(fm, _job(tmp_data_dir, output_dir=str(out_dir)))

    text = (out_dir / "pbf" / "a_t.osm.pbf.txt").read_text(encoding="utf-8")
    assert "H:\\hostdata\\out\\pbf" in text


# ── A report must never cost the job ──────────────────────────────────────────


async def test_report_failure_does_not_fail_the_job(tmp_data_dir):
    """A broken report leaves a finished job finished."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(tmp_data_dir, output_dir=str(out_dir))

    with patch.object(fm, "_render_output_report", side_effect=OSError("boom")):
        await _run(fm, job)

    assert job.status == "done", f"Expected done, got {job.status!r}: {job.error}"
    assert not (out_dir / "pbf" / "a_t.osm.pbf.txt").exists()


async def test_no_reports_for_a_failed_job(tmp_data_dir):
    """A job that errors out writes no reports at all."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = _job(tmp_data_dir, output_formats=["gpkg"], output_dir=str(out_dir))

    async def failing_ogr(cmd, _job, **kwargs):
        if cmd[0] == "ogr2ogr":
            return 1
        return await _fake_run_cmd(cmd, _job, **kwargs)

    with patch.object(fm, "_run_cmd", side_effect=failing_ogr):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            await fm.run_job(job)

    assert job.status == "error"
    assert sorted(out_dir.rglob("*.txt")) == []


# ── Layout, rendered directly ─────────────────────────────────────────────────


def test_render_output_report_layout(tmp_data_dir):
    """Sections appear in order, keys are aligned, phase durations are shown."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_path = tmp_data_dir / "out" / "gpkg" / "a_t.gpkg"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"g" * 2048)

    job = _job(tmp_data_dir, output_formats=["gpkg"])
    job.phases = [
        Phase(label="a.osm.pbf · filter", source="a.osm.pbf", step="filter", weight=1),
        Phase(
            label="a.osm.pbf · export gpkg",
            source="a.osm.pbf",
            step="export_convert",
            weight=1,
            fmt="gpkg",
        ),
    ]
    job.phases[0].duration_seconds = 492.0
    job.phases[1].duration_seconds = 9.4

    text = fm._render_output_report(
        job,
        out_path,
        "a.osm.pbf",
        "gpkg",
        finished_at=datetime(2026, 7, 26, 9, 42, 7, tzinfo=timezone.utc),
        duration_seconds=754.0,
        host_root="/home/u/osm-data",
    )

    positions = [text.index(s) for s in ("OUTPUT", "INPUT", "FILTER", "JOB", "PHASES")]
    assert positions == sorted(positions), "sections must appear in order"
    assert "  File              a_t.gpkg" in text
    assert "  Format            GPKG (GeoPackage)" in text
    assert "  Size              2.0 KB" in text
    assert "  Folder            /home/u/osm-data/out/gpkg" in text
    assert "  Completed         2026-07-26 09:42:07+00:00" in text
    assert "12m 34s" in text  # job duration
    assert "8m 12s" in text  # filter phase
    assert "9.4 s" in text  # export phase
