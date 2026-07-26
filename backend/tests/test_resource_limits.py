"""Resource limits from the user config reach the subprocess environment.

The UI promises that the Full Power / Background presets and the thread and nice
overrides "take effect on the next job start". These tests hold that promise to
the point where it matters: the environment and nice level handed to osmium.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import config
from filter_manager import FilterJob, FilterManager


@pytest.fixture
def cpu8(monkeypatch):
    """Pin the core count so expected thread numbers are machine-independent."""
    monkeypatch.setattr(os, "cpu_count", lambda: 8)


def _write_config(**values) -> None:
    config.USER_CONFIG_FILE.write_text(json.dumps(values), encoding="utf-8")


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, (8, 0)),
        ({"resource_mode": "full"}, (8, 0)),
        ({"resource_mode": "background"}, (4, 10)),
        ({"resource_mode": "background", "osmium_threads_override": 3}, (3, 10)),
        ({"osmium_threads_override": 2, "nice_override": 5}, (2, 5)),
        # An override of 0 is falsy and must fall back to the preset, not to zero
        # threads, which would leave osmium with no worker pool at all.
        ({"osmium_threads_override": 0}, (8, 0)),
        # nice 0 is a legitimate value, so it must NOT fall back to the preset.
        ({"resource_mode": "background", "nice_override": 0}, (4, 0)),
        # Out-of-range values are clamped rather than rejected.
        ({"osmium_threads_override": 99}, (8, 0)),
        ({"nice_override": 25}, (8, 19)),
        ({"nice_override": -5}, (8, 0)),
    ],
)
def test_resource_limits_from_config(cpu8, cfg, expected):
    _write_config(**cfg)
    assert FilterManager(AsyncMock())._resource_limits() == expected


def test_resource_limits_without_a_config_file(cpu8):
    """No config yet: full-power defaults, no crash."""
    assert not config.USER_CONFIG_FILE.exists()
    assert FilterManager(AsyncMock())._resource_limits() == (8, 0)


def test_resource_limits_with_a_corrupt_config_file(cpu8):
    config.USER_CONFIG_FILE.write_text("{not json", encoding="utf-8")
    assert FilterManager(AsyncMock())._resource_limits() == (8, 0)


def test_background_preset_keeps_at_least_one_thread(monkeypatch):
    """On a single-core host, halving the core count must not reach zero."""
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    _write_config(resource_mode="background")
    assert FilterManager(AsyncMock())._resource_limits() == (1, 10)


async def test_limits_reach_the_job_environment(cpu8, tmp_data_dir):
    """End to end: the configured values land on the job that runs osmium."""
    _write_config(resource_mode="background", osmium_threads_override=3, nice_override=7)
    (tmp_data_dir / "a.osm.pbf").write_bytes(b"x" * 1000)
    fm = FilterManager(AsyncMock())
    out_dir = tmp_data_dir / "out"
    job = FilterJob(
        id="j1",
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

    async def fake_run_cmd(cmd, _job, **_):
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"pbf")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        await fm.run_job(job)

    assert job.status == "done", f"Expected done, got {job.status!r}: {job.error}"
    assert job._proc_env["OSMIUM_POOL_THREADS"] == "3"
    assert job._proc_env["GDAL_NUM_THREADS"] == "3"
    assert job._nice_level == 7
