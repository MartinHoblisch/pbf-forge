"""Every finished output file gets a readable '<filename>.txt' sidecar report."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import config
from filter_manager import FilterJob, FilterManager, Phase, _fmt_duration

# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_pbf_header(monkeypatch):
    """Neutralise the PBF header read for every test in this file.

    The sources here are byte fillers, not real PBFs, and pyosmium is absent
    from the test environment — and stubbed in sys.modules by another test
    module, so what the header read returns depends on collection order. Tests
    that care about the timestamp patch this again with their own value.
    """
    monkeypatch.setattr("filter_manager._pbf_replication_timestamp", lambda _p: "")


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


async def test_report_names_the_job_log_file(tmp_data_dir):
    """A job created through create_job carries a log file, which the report cites."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    out_dir = tmp_data_dir / "out"
    job = fm.create_job(
        source_files=["a.osm.pbf"],
        tags=["railway=rail"],
        exclude_tags=[],
        geometry_types=["ways"],
        suffix="t",
        output_formats=["pbf"],
        output_dir=str(out_dir),
        columns_mode="other_tags",
        manual_keys=[],
    )

    await _run(fm, job)

    text = (out_dir / "pbf" / "a_t.osm.pbf.txt").read_text(encoding="utf-8")
    assert f"Job log           {job.id}.log" in text


# ── Layout, rendered directly ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0.0 s"),
        (9.44, "9.4 s"),
        (59.9, "59.9 s"),
        (60.0, "1m 00s"),
        (754.0, "12m 34s"),
        (3600.0, "1h 00m 00s"),
        (7354.0, "2h 02m 34s"),  # the realistic case for a continent-sized job
    ],
)
def test_fmt_duration(seconds, expected):
    assert _fmt_duration(seconds) == expected


def test_render_report_when_the_output_file_cannot_be_sized(tmp_data_dir):
    """A vanished or unreadable output still produces a report."""
    fm = _fm()
    _ensure_source(tmp_data_dir)
    text = fm._render_output_report(
        _job(tmp_data_dir),
        tmp_data_dir / "out" / "pbf" / "gone.osm.pbf",
        "a.osm.pbf",
        "pbf",
        finished_at=datetime(2026, 7, 26, 9, 42, 7, tzinfo=timezone.utc),
        duration_seconds=1.0,
        host_root="",
    )
    assert "Size              unknown" in text


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


async def test_writing_the_reports_is_a_counted_phase(tmp_data_dir):
    """It used to run past the last phase, which the UI rendered as "step 3 of 2"."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"])

    await _run(fm, job)

    assert [p.label for p in job.phases][-1] == "write reports"
    # Every phase closed, so the index lands on the count rather than past it.
    assert job.current_phase_index == len(job.phases)
    assert job.phases[-1].duration_seconds is not None
    n = len(job.phases)
    assert f"Phase {n}/{n}: write reports" in job.log
    assert f"Phase {n}/{n} done in" in job.log


async def test_publishing_an_output_counts_towards_its_export_phase(tmp_data_dir):
    """Embedding the metadata and moving the file into place produce that output,
    so the phase named after it must still be open while they run."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"])
    seen = {}

    def _record_index(*_args, **_kwargs):
        seen["at_embed"] = job.current_phase_index

    with patch.object(fm, "_run_cmd", side_effect=_fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution", side_effect=_record_index):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    export_index = next(i for i, p in enumerate(job.phases) if p.step == "export_convert")
    assert seen["at_embed"] == export_index


async def test_the_report_phase_records_no_filter_history(tmp_data_dir):
    """History estimates a step from its source's size; this phase has no source."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"])

    with patch.object(fm._history, "record") as record:
        await _run(fm, job)

    assert record.called
    assert all(call.args[0] for call in record.call_args_list), "no source-less entries"
    assert "report" not in [call.args[2] for call in record.call_args_list]


def test_report_lists_the_job_wide_report_phase(tmp_data_dir):
    """The phase that writes the reports belongs to no source, so it has to be
    listed for every output — and it is still running while it renders them."""
    fm = _fm()
    out_path = tmp_data_dir / "out" / "gpkg" / "a_t.gpkg"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"g" * 2048)

    job = _job(tmp_data_dir, output_formats=["gpkg"])
    job.phases = [
        Phase(
            label="a.osm.pbf · export gpkg",
            source="a.osm.pbf",
            step="export_convert",
            weight=1,
            fmt="gpkg",
        ),
        Phase(label="write reports", source="", step="report", weight=0.0, fmt="txt"),
    ]
    job.phases[0].duration_seconds = 9.4
    # In flight: this is the state the phase is in while the report is rendered.
    job.current_phase_index = 1
    job.phase_started_at = time.time() - 3.0

    text = fm._render_output_report(
        job,
        out_path,
        "a.osm.pbf",
        "gpkg",
        finished_at=datetime(2026, 7, 26, 9, 42, 7, tzinfo=timezone.utc),
        duration_seconds=754.0,
        host_root="",
    )

    phases_section = text.split("PHASES", 1)[1]
    assert "export gpkg" in phases_section
    assert "write reports" in phases_section
    # Its elapsed time stands in for a duration it cannot know yet.
    assert "3.0 s" in phases_section


def test_report_shows_a_dash_for_a_phase_that_never_ran(tmp_data_dir):
    fm = _fm()
    out_path = tmp_data_dir / "out" / "gpkg" / "a_t.gpkg"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"g")

    job = _job(tmp_data_dir, output_formats=["gpkg"])
    job.phases = [
        Phase(label="write reports", source="", step="report", weight=0.0, fmt="txt"),
    ]

    text = fm._render_output_report(
        job,
        out_path,
        "a.osm.pbf",
        "gpkg",
        finished_at=datetime(2026, 7, 26, 9, 42, 7, tzinfo=timezone.utc),
        duration_seconds=1.0,
        host_root="",
    )

    assert "write reports" in text.split("PHASES", 1)[1]
    assert "3.0 s" not in text


# ── source data timestamp ─────────────────────────────────────────────────────
# The report states how current the source data is, which is not the same as
# when the file was downloaded. _pbf_replication_timestamp is patched here so
# the tests do not need pyosmium or a real PBF; the header read itself is
# exercised against real osmium in the container.


def _render_input_section(fm: FilterManager, tmp_data_dir, source="a.osm.pbf") -> str:
    text = fm._render_output_report(
        _job(tmp_data_dir),
        tmp_data_dir / "out" / "pbf" / "a_t.osm.pbf",
        source,
        "pbf",
        finished_at=datetime(2026, 7, 26, 9, 42, 7, tzinfo=timezone.utc),
        duration_seconds=1.0,
        host_root="",
    )
    return text.split("INPUT", 1)[1].split("FILTER", 1)[0]


def test_report_shows_the_replication_timestamp_of_the_source(tmp_data_dir, monkeypatch):
    """The PBF header's replication timestamp is what the report prefers."""
    _ensure_source(tmp_data_dir)
    monkeypatch.setattr(
        "filter_manager._pbf_replication_timestamp", lambda _p: "2026-07-25T20:21:02Z"
    )

    section = _render_input_section(_fm(), tmp_data_dir)

    assert "  Source extract    a.osm.pbf" in section
    assert "  Data timestamp    2026-07-25 20:21:02+00:00  (OSM replication)" in section


def test_report_keeps_an_unparseable_replication_timestamp_verbatim(tmp_data_dir, monkeypatch):
    """A header value that is not ISO-8601 is reported as-is, not dropped."""
    _ensure_source(tmp_data_dir)
    monkeypatch.setattr("filter_manager._pbf_replication_timestamp", lambda _p: "last tuesday")

    assert "  Data timestamp    last tuesday  (OSM replication)" in _render_input_section(
        _fm(), tmp_data_dir
    )


@pytest.mark.parametrize(
    "header_result",
    [
        pytest.param(lambda _p: "", id="header_without_the_option"),
        pytest.param(
            lambda _p: (_ for _ in ()).throw(RuntimeError("not a PBF")), id="header_unreadable"
        ),
    ],
)
def test_report_falls_back_to_the_source_file_date(tmp_data_dir, monkeypatch, header_result):
    """Without a header timestamp, the file's own date is the next best answer.

    It is not the download time: DownloadManager stamps the server's
    Last-Modified onto the file.
    """
    _ensure_source(tmp_data_dir)
    published = datetime(2026, 7, 25, 20, 21, 2, tzinfo=timezone.utc).timestamp()
    os.utime(tmp_data_dir / "a.osm.pbf", (published, published))
    monkeypatch.setattr("filter_manager._pbf_replication_timestamp", header_result)

    section = _render_input_section(_fm(), tmp_data_dir)

    assert "  Data timestamp    2026-07-25 20:21:02+00:00  (source file date)" in section


def test_report_omits_the_timestamp_when_the_source_is_gone(tmp_data_dir, monkeypatch):
    """No source file, no date — the row is dropped rather than faked."""
    monkeypatch.setattr("filter_manager._pbf_replication_timestamp", lambda _p: "")

    section = _render_input_section(_fm(), tmp_data_dir, source="vanished.osm.pbf")

    assert "Source extract" in section
    assert "Data timestamp" not in section


# ── source URL ────────────────────────────────────────────────────────────────
# Any server that publishes a PBF next to an .md5 works, so the report names the
# host each source actually came from rather than assuming a single provider.


def test_report_names_the_host_a_source_was_downloaded_from(tmp_data_dir):
    """A built-in continental extract resolves to its download URL."""
    _ensure_source(tmp_data_dir, "europe.osm.pbf")

    section = _render_input_section(_fm(), tmp_data_dir, source="europe.osm.pbf")

    assert "  Source URL        https://download.geofabrik.de/europe-latest.osm.pbf" in section


def test_report_names_a_non_geofabrik_host(tmp_data_dir, tmp_config_dir):
    """The host is whatever the user pointed at — nothing assumes Geofabrik."""
    _ensure_source(tmp_data_dir, "planet.osm.pbf")
    (tmp_config_dir / ".osm_tool_urls.json").write_text(
        json.dumps(
            {"planet.osm.pbf": "https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf"}
        ),
        encoding="utf-8",
    )

    section = _render_input_section(_fm(), tmp_data_dir, source="planet.osm.pbf")

    assert (
        "  Source URL        https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf" in section
    )
    assert "geofabrik" not in section.lower()


def test_report_omits_the_url_for_a_hand_placed_file(tmp_data_dir):
    """A PBF copied into the data directory has no URL — the row is dropped."""
    _ensure_source(tmp_data_dir, "handmade.osm.pbf")

    section = _render_input_section(_fm(), tmp_data_dir, source="handmade.osm.pbf")

    assert "Source extract" in section
    assert "Source URL" not in section
