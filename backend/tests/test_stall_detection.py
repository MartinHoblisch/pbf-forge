"""F3: subprocesses are killed on stall (no output growth, no log), not on a wall-clock rate."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock

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


async def test_silent_stalled_process_is_killed(tmp_path, monkeypatch):
    """A silent process (no stdout, no output-file growth) is killed after STALL_KILL_SECONDS."""
    monkeypatch.setattr(fm_mod, "STALL_KILL_SECONDS", 1.0)
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.2)
    fm = _make_fm()
    job = _make_job()
    job.timeout_seconds = 60.0
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]

    with pytest.raises(RuntimeError, match="[Nn]o progress"):
        await fm._run_cmd(cmd, job, watch_path=tmp_path / "never_written.bin")


async def test_slow_but_growing_process_survives(tmp_path, monkeypatch):
    """A process that writes steadily to the output file is NOT killed even with no stdout."""
    monkeypatch.setattr(fm_mod, "STALL_KILL_SECONDS", 1.0)
    monkeypatch.setattr(fm_mod, "STALL_CHECK_INTERVAL", 0.2)
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
