from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filter_manager import FilterJob, FilterManager


def _make_job(**kwargs) -> FilterJob:
    defaults = dict(
        id="test-job",
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


class _AsyncLines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self._pos = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._pos >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._pos]
        self._pos += 1
        return line

    async def read(self, n: int) -> bytes:  # noqa: ARG002
        if self._pos >= len(self._lines):
            return b""
        chunk = self._lines[self._pos]
        self._pos += 1
        return chunk


def _make_proc(returncode: int, lines: list[bytes]) -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.stdout = _AsyncLines(lines)
    proc.wait = AsyncMock(return_value=returncode)
    return proc


async def test_exit_code_zero_returns_zero():
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    proc = _make_proc(0, [b"output line\n"])

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        rc = await fm._run_cmd(["echo", "hello"], job)

    assert rc == 0
    assert "output line" in job.log


async def test_exit_code_one_returns_one():
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    proc = _make_proc(1, [])

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        rc = await fm._run_cmd(["false"], job)

    assert rc == 1


async def test_non_utf8_bytes_no_crash():
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    bad_bytes = b"\xff\xfe invalid utf-8\n"
    proc = _make_proc(0, [bad_bytes])

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        rc = await fm._run_cmd(["cmd"], job)

    assert rc == 0
    assert len(job.log) > 0  # something was appended (replaced chars)


async def test_each_line_appended_to_job_log():
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    lines = [b"line one\n", b"line two\n", b"line three\n"]
    proc = _make_proc(0, lines)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        await fm._run_cmd(["cmd"], job)

    assert "line one" in job.log
    assert "line two" in job.log
    assert "line three" in job.log


async def test_output_file_simulated_via_touch(tmp_path):
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    output_path = tmp_path / "result.gpkg"

    def fake_exec(*args, **kwargs):
        output_path.touch()
        return _make_proc(0, [b"done\n"])

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc = await fm._run_cmd(["osmium", "export", "-o", str(output_path)], job)

    assert rc == 0
    assert output_path.exists()


async def test_timeout_kills_process_and_raises():
    fm = FilterManager(ws_manager=AsyncMock())
    job = _make_job()
    job.timeout_seconds = 0.05  # 50 ms — fires before stdout ever closes

    class _HangingStdout:
        async def read(self, n: int) -> bytes:  # noqa: ARG002
            await asyncio.sleep(10)
            return b""

    proc = AsyncMock()
    proc.stdout = _HangingStdout()
    proc.kill = MagicMock()  # kill() is synchronous on asyncio subprocess
    proc.wait = AsyncMock(return_value=-9)
    proc.returncode = -9

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="timed out"):
            await fm._run_cmd(["sleep", "10"], job)

    proc.kill.assert_called_once()
