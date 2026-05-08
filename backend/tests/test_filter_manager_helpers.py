"""Tests for FilterManager helpers: list_pbf_files, _build_ogr_cmd, and the
_run_cmd guards (stdout=None and broadcast-throttling).

Bug class:
  - list_pbf_files leaks non-PBF entries → user picks an invalid source and
    the osmium subprocess fails with cryptic stderr.
  - _build_ogr_cmd misses -a_srs for GPKG → output GeoPackage opens with no
    CRS in QGIS / ArcGIS, the user thinks PBF Forge is broken.
  - _run_cmd doesn't notice proc.stdout=None (asyncio quirk on some platforms)
    and silently drops the entire job log.
  - _run_cmd never throttles broadcasts → WS spam during a 30-min export.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filter_manager import FilterJob, FilterManager


def _job(**overrides) -> FilterJob:
    defaults = dict(
        id="t",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["gpkg"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(overrides)
    return FilterJob(**defaults)


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


# ── list_pbf_files ───────────────────────────────────────────────────────────


def test_list_pbf_files_returns_sorted_pbf_filenames(tmp_data_dir):
    (tmp_data_dir / "zeta.osm.pbf").touch()
    (tmp_data_dir / "alpha.osm.pbf").touch()
    assert _fm().list_pbf_files() == ["alpha.osm.pbf", "zeta.osm.pbf"]


def test_list_pbf_files_excludes_non_pbf(tmp_data_dir):
    (tmp_data_dir / "real.osm.pbf").touch()
    (tmp_data_dir / "fake.gpkg").touch()
    (tmp_data_dir / "fake.osm").touch()
    (tmp_data_dir / "fake.txt").touch()
    assert _fm().list_pbf_files() == ["real.osm.pbf"]


def test_list_pbf_files_empty_when_no_pbf(tmp_data_dir):
    assert _fm().list_pbf_files() == []


# ── _build_ogr_cmd ───────────────────────────────────────────────────────────


def test_build_ogr_cmd_gpkg_includes_a_srs_epsg_4326(tmp_path):
    fm = _fm()
    cmd = fm._build_ogr_cmd("gpkg", "/out/x.gpkg", "/in/x.pbf", _job(), tmp_path)
    assert "-a_srs" in cmd
    idx = cmd.index("-a_srs")
    assert cmd[idx + 1] == "EPSG:4326"
    assert cmd[:5] == ["ogr2ogr", "-f", "GPKG", "/out/x.gpkg", "/in/x.pbf"]


def test_build_ogr_cmd_geojson_omits_a_srs(tmp_path):
    fm = _fm()
    cmd = fm._build_ogr_cmd("geojson", "/out/x.geojson", "/in/x.pbf", _job(), tmp_path)
    assert "-a_srs" not in cmd
    assert cmd == ["ogr2ogr", "-f", "GeoJSON", "/out/x.geojson", "/in/x.pbf"]


# ── _run_cmd: proc.stdout=None ───────────────────────────────────────────────


async def test_run_cmd_raises_when_proc_stdout_none():
    """Defensive guard: if asyncio.create_subprocess_exec returns a proc with
    .stdout=None (platform quirk, FD exhaustion), raise immediately rather
    than passing the bug downstream."""
    fm = _fm()
    job = _job()

    proc = AsyncMock()
    proc.stdout = None
    proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="subprocess stdout is None"):
            await fm._run_cmd(["echo", "x"], job)


# ── _run_cmd broadcast throttle ──────────────────────────────────────────────


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


async def test_run_cmd_broadcasts_throttled_at_500ms_boundary():
    """Verify the exact throttling contract:
      - 1 initial broadcast on entry (line 782)
      - 1 broadcast per stdout chunk where (now - last_broadcast) >= 0.5s
      - chunks below 0.5s diff produce no broadcast

    With time samples [0.0, 0.1, 0.7, 1.4, 2.1] and 4 stdout lines, the
    expected sequence is:
      init     → 1 broadcast
      line1: now=0.1, diff=0.1 → skip
      line2: now=0.7, diff=0.7 → broadcast (last=0.7)
      line3: now=1.4, diff=0.7 → broadcast (last=1.4)
      line4: now=2.1, diff=0.7 → broadcast (last=2.1)
    Total = 4 broadcasts, exactly. A weaker assertion would mask off-by-one
    bugs in the throttle logic.
    """
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job()

    fake_loop = MagicMock()
    times = iter([0.0, 0.1, 0.7, 1.4, 2.1])
    fake_loop.time = MagicMock(side_effect=lambda: next(times))

    proc = AsyncMock()
    proc.stdout = _AsyncLines([b"line1\n", b"line2\n", b"line3\n", b"line4\n"])
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("filter_manager.asyncio.get_running_loop", return_value=fake_loop):
            await fm._run_cmd(["x"], job)

    assert ws.broadcast.await_count == 4


async def test_run_cmd_no_broadcast_when_all_chunks_within_500ms():
    """All time diffs < 0.5s → only the initial broadcast fires, no
    mid-stream broadcasts. Pins the lower bound of the throttle contract."""
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job()

    fake_loop = MagicMock()
    # diffs: 0.1, 0.1, 0.1 — all below threshold
    times = iter([0.0, 0.1, 0.2, 0.3])
    fake_loop.time = MagicMock(side_effect=lambda: next(times))

    proc = AsyncMock()
    proc.stdout = _AsyncLines([b"a\n", b"b\n", b"c\n"])
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("filter_manager.asyncio.get_running_loop", return_value=fake_loop):
            await fm._run_cmd(["x"], job)

    assert ws.broadcast.await_count == 1  # initial only


# ── _reduce_pbf_tags real body ───────────────────────────────────────────────
#
# Use a fake module created from scratch (not the real pbf_tag_reducer, which
# would require pyosmium). Save and restore any pre-existing sys.modules entry
# so this test is order-independent vs. test_pbf_tag_reducer.py.


def _swap_pbf_tag_reducer_module(reduce_fn):
    """Return a context manager that installs a fake pbf_tag_reducer module
    exposing the given reduce_tags fn, then restores the original on exit."""
    import sys
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        original = sys.modules.get("pbf_tag_reducer")
        fake = type(sys)("pbf_tag_reducer")
        fake.reduce_tags = reduce_fn
        sys.modules["pbf_tag_reducer"] = fake
        try:
            yield
        finally:
            if original is not None:
                sys.modules["pbf_tag_reducer"] = original
            else:
                sys.modules.pop("pbf_tag_reducer", None)

    return _ctx()


async def test_reduce_pbf_tags_success_replaces_pbf_returns_zero(tmp_path):
    """Happy path: reduce_tags() succeeds → tmp_out replaces pbf_path,
    return code 0."""
    fm = _fm()
    pbf_path = tmp_path / "in.osm.pbf"
    pbf_path.write_bytes(b"original")
    job = _job(columns_mode="manual", manual_keys=["name"])

    def fake_reduce(src, dst, keep):
        from pathlib import Path as _P

        _P(dst).write_bytes(b"reduced")

    with _swap_pbf_tag_reducer_module(fake_reduce):
        rc = await fm._reduce_pbf_tags(pbf_path, job)

    assert rc == 0
    assert pbf_path.read_bytes() == b"reduced"
    assert not (tmp_path / "in.tmp.osm.pbf").exists()


async def test_reduce_pbf_tags_failure_returns_one_and_cleans_tmp(tmp_path):
    """Reducer raises (e.g. corrupt PBF) → return 1, .tmp file cleaned up,
    error logged in job.log, original PBF untouched."""
    fm = _fm()
    pbf_path = tmp_path / "in.osm.pbf"
    pbf_path.write_bytes(b"original")
    job = _job(columns_mode="manual", manual_keys=["name"])

    def fake_reduce(src, dst, keep):
        from pathlib import Path as _P

        _P(dst).write_bytes(b"partial-then-fail")
        raise RuntimeError("osmium parse error")

    with _swap_pbf_tag_reducer_module(fake_reduce):
        rc = await fm._reduce_pbf_tags(pbf_path, job)

    assert rc == 1
    assert "ERROR in tag reduction" in job.log
    assert pbf_path.read_bytes() == b"original"
    assert not (tmp_path / "in.tmp.osm.pbf").exists()
