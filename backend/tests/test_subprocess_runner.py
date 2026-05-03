from __future__ import annotations

from unittest.mock import AsyncMock, patch

from filter_manager import FilterJob, FilterManager


def _make_job(**kwargs) -> FilterJob:
    defaults = dict(
        id="test-job",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
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
        self._iter = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


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
