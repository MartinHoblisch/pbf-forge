"""Stall handling: silence warns but never auto-kills.

osmium tags-filter legitimately reads a 32-GB input up to 4 times before
writing any output, with no stdout/stderr when piped — total silence far
beyond any reasonable stall threshold. Liveness therefore comes from three
signals (output-file growth, stdout/stderr activity, /proc-IO counters);
prolonged silence produces a log warning, and only the absolute timeout
kills the process.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import filter_manager as fm_mod
from filter_manager import FilterJob, FilterManager


def _make_fm() -> FilterManager:
    return FilterManager(ws_manager=AsyncMock())


def _make_job(**kwargs) -> FilterJob:
    defaults = dict(
        id="stall-test-job",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="test",
        output_formats=["gpkg"],
        output_dir="/tmp/out",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(kwargs)
    return FilterJob(**defaults)


async def test_silent_process_is_warned_not_killed(tmp_path, monkeypatch):
    """A silent process (no stdout, no file growth, no proc-IO) gets a log
    warning after STALL_WARN_SECONDS but runs to completion."""
    monkeypatch.setattr(fm_mod, "STALL_WARN_SECONDS", 0.5)
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.1)
    monkeypatch.setattr(fm_mod, "_proc_io_bytes", lambda pid: None)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 60.0
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]

    rc = await fm._run_cmd(cmd, job, watch_path=tmp_path / "never_written.bin")

    assert rc == 0
    assert "no observable progress" in job.log


async def test_slow_but_growing_process_survives_without_warning(tmp_path, monkeypatch):
    """A process that writes steadily to the output file is neither killed
    nor warned about, even with no stdout."""
    monkeypatch.setattr(fm_mod, "STALL_WARN_SECONDS", 1.0)
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.2)
    monkeypatch.setattr(fm_mod, "_proc_io_bytes", lambda pid: None)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 60.0
    out = tmp_path / "grows.bin"
    # The process writes to the file but NOT to stdout — this specifically tests
    # the output-file growth signal (not stdout activity).
    script = (
        "import time\n"
        f"p = r'{out}'\n"
        "f = open(p, 'ab')\n"
        "for _ in range(12):\n"
        "    f.write(b'x'); f.flush(); time.sleep(0.25)\n"
    )
    cmd = [sys.executable, "-c", script]

    rc = await fm._run_cmd(cmd, job, watch_path=out)

    assert rc == 0
    assert job.output_bytes is not None and job.output_bytes > 0
    assert "no observable progress" not in job.log


async def test_proc_io_activity_suppresses_warning(tmp_path, monkeypatch):
    """Advancing /proc-IO counters count as liveness: a file-quiet, stdout-quiet
    process that is demonstrably reading input is not flagged as stalled."""
    monkeypatch.setattr(fm_mod, "STALL_WARN_SECONDS", 0.5)
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.1)
    counter = {"v": 0}

    def fake_io(pid: int) -> int:
        counter["v"] += 4096
        return counter["v"]

    monkeypatch.setattr(fm_mod, "_proc_io_bytes", fake_io)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 60.0
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]

    rc = await fm._run_cmd(cmd, job, watch_path=tmp_path / "never_written.bin")

    assert rc == 0
    assert "no observable progress" not in job.log
    assert job.bytes_read is not None and job.bytes_read > 0


async def test_absolute_timeout_still_kills(tmp_path, monkeypatch):
    """The absolute timeout remains the only auto-kill."""
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.1)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 1.0
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]

    with pytest.raises(RuntimeError, match="absolute limit"):
        await fm._run_cmd(cmd, job, watch_path=tmp_path / "x.bin")


async def test_wait_after_stdout_eof_is_bounded(tmp_path, monkeypatch):
    """A process that closes its pipes but never exits must not hang the job
    slot forever — the absolute timeout covers proc.wait() too."""
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.1)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 1.0
    cmd = [sys.executable, "-c", "import os, time; os.close(1); os.close(2); time.sleep(30)"]

    with pytest.raises(RuntimeError, match="absolute limit"):
        await fm._run_cmd(cmd, job, watch_path=tmp_path / "x.bin")


async def test_cr_progress_lines_are_ephemeral(monkeypatch):
    """\\r-terminated redraws (osmium --progress bar) land in job.progress_line,
    never in the permanent log; \\n-terminated lines go to the log as before."""
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.1)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 60.0
    # Percentages are computed in the child so the "$ <cmd>" log echo of this
    # script source does not itself contain the asserted strings.
    script = (
        "import sys\n"
        "for p in (10, 20):\n"
        "    sys.stdout.write('[==>     ] %d%% \\r' % p); sys.stdout.flush()\n"
        "sys.stdout.write('done\\n')\n"
    )

    rc = await fm._run_cmd([sys.executable, "-c", script], job, watch_path=None)

    assert rc == 0
    assert "10%" not in job.log
    assert "20%" not in job.log
    assert "done" in job.log
    assert job.progress_line is not None and "20%" in job.progress_line


def test_parse_proc_io():
    text = (
        "rchar: 323934931\n"
        "wchar: 323929600\n"
        "syscr: 632687\n"
        "syscw: 632675\n"
        "read_bytes: 0\n"
        "write_bytes: 323932160\n"
        "cancelled_write_bytes: 0\n"
    )
    assert fm_mod._parse_proc_io(text) == 323934931 + 323929600


async def test_osmium_and_ogr2ogr_commands_carry_progress_flags(tmp_data_dir):
    """osmium calls carry --verbose --progress (pass markers + copy-pass bar on
    stderr), ogr2ogr carries -progress (tick output on stdout)."""
    (tmp_data_dir / "berlin.osm.pbf").write_bytes(b"x" * 1000)
    fm = _make_fm()
    job = _make_job(
        output_formats=["pbf", "gpkg"],
        output_dir=str(tmp_data_dir / "out"),
    )
    captured: list[list[str]] = []

    async def fake_run(cmd, job_, watch_path=None):
        captured.append(cmd)
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"d")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_bytes(b"d")
        return 0

    with (
        patch.object(fm, "_run_cmd", fake_run),
        patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])),
    ):
        await fm.run_job(job)

    osmium_cmds = [c for c in captured if c[0] == "osmium"]
    assert osmium_cmds, f"no osmium commands captured: {captured}"
    for c in osmium_cmds:
        assert "--progress" in c and "--verbose" in c, c
    ogr_cmds = [c for c in captured if c[0] == "ogr2ogr"]
    assert ogr_cmds, f"no ogr2ogr commands captured: {captured}"
    for c in ogr_cmds:
        assert "-progress" in c, c
