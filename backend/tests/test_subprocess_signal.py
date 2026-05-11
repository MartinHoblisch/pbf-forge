"""Tests for signal-aware subprocess error reporting in _run_cmd."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from filter_manager import FilterJob, FilterManager


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _job() -> FilterJob:
    return FilterJob(
        id="t",
        source_files=["x.osm.pbf"],
        tags=[],
        exclude_tags=[],
        geometry_types=[],
        suffix="t",
        output_formats=[],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )


async def test_run_cmd_negative_returncode_logs_sigkill_hint():
    fm = _fm()
    job = _job()

    # Build a mock proc: -9 returncode, empty stdout stream
    proc = MagicMock()
    proc.returncode = -9
    stdout_mock = MagicMock()
    stdout_mock.read = AsyncMock(return_value=b"")
    proc.stdout = stdout_mock
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

    with patch("filter_manager.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        rc = await fm._run_cmd(["echo", "x"], job)

    assert rc == -9
    log = job.log
    assert "SIGKILL" in log
    assert "OOM" in log


async def test_run_cmd_returncode_zero_no_kill_message():
    fm = _fm()
    job = _job()

    proc = MagicMock()
    proc.returncode = 0
    stdout_mock = MagicMock()
    stdout_mock.read = AsyncMock(return_value=b"")
    proc.stdout = stdout_mock
    proc.wait = AsyncMock()

    with patch("filter_manager.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        rc = await fm._run_cmd(["echo", "x"], job)

    assert rc == 0
    assert "SIGKILL" not in job.log
    assert "killed by" not in job.log


async def test_run_cmd_positive_returncode_no_signal_message():
    """Non-zero positive rc = ordinary failure, not a kill signal."""
    fm = _fm()
    job = _job()

    proc = MagicMock()
    proc.returncode = 1
    stdout_mock = MagicMock()
    stdout_mock.read = AsyncMock(return_value=b"")
    proc.stdout = stdout_mock
    proc.wait = AsyncMock()

    with patch("filter_manager.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        rc = await fm._run_cmd(["echo", "x"], job)

    assert rc == 1
    assert "killed by" not in job.log
