"""Tests for FilterManager._start_phase, _finish_phase guard branches and
_build_phases stat() failure path.

Bug class:
  - _start_phase / _finish_phase called when current_phase_index has already
    advanced past the phase list (race or logic bug) must be no-ops, not
    crash. Otherwise an off-by-one in run_job tears down the entire WS.
  - _finish_phase records to history; if record raises, the warning must
    be logged but the index advance must still happen — otherwise the
    job hangs on the same phase forever.
  - _build_phases reads file size; if the file vanishes between job creation
    and phase building (race with a concurrent re-download), use weight=1.0
    instead of crashing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import filter_manager as fm_module
from filter_manager import FilterJob, FilterManager, Phase


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _job(phases=None) -> FilterJob:
    j = FilterJob(
        id="t",
        source_files=["berlin.osm.pbf"],
        tags=["a"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["pbf"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    if phases is not None:
        j.phases = phases
    return j


def _phase(step="filter", fmt="pbf") -> Phase:
    return Phase(label="p", source="berlin.osm.pbf", step=step, weight=1.0, fmt=fmt)


# ── _start_phase index overflow ──────────────────────────────────────────────


async def test_start_phase_noop_when_index_past_end():
    fm = _fm()
    job = _job([_phase()])
    job.current_phase_index = 5  # way past the 1-phase list

    await fm._start_phase(job)

    # No broadcast issued because the function returned early
    fm._ws.broadcast.assert_not_called()


# ── _finish_phase guards ─────────────────────────────────────────────────────


async def test_finish_phase_noop_when_started_at_none():
    """Idempotency: calling _finish_phase without a matching _start_phase
    must not double-increment the index or broadcast spurious updates."""
    fm = _fm()
    job = _job([_phase()])
    job.phase_started_at = None

    await fm._finish_phase(job)

    assert job.current_phase_index == 0  # unchanged
    fm._ws.broadcast.assert_not_called()


async def test_finish_phase_noop_when_index_past_end():
    fm = _fm()
    job = _job([_phase()])
    job.current_phase_index = 99
    job.phase_started_at = 100.0  # would otherwise pass the start_at guard

    await fm._finish_phase(job)

    fm._ws.broadcast.assert_not_called()


async def test_finish_phase_history_record_exception_logged_not_raised(tmp_data_dir):
    """If history.record raises (disk full, JSON corruption mid-write), the
    warning is logged but the index still advances — otherwise the job
    livelocks on this phase."""
    (tmp_data_dir / "berlin.osm.pbf").write_bytes(b"x" * 100)

    fm = _fm()
    fm._history = MagicMock()
    fm._history.record.side_effect = OSError("disk full")

    job = _job([_phase()])
    job.phase_started_at = 100.0

    with patch.object(fm_module.time, "time", return_value=110.0):
        await fm._finish_phase(job)  # must not raise

    assert job.current_phase_index == 1
    assert job.phase_started_at is None


# ── _build_phases stat() OSError fallback ────────────────────────────────────


def test_build_phases_source_stat_oserror_uses_fallback_weight(tmp_data_dir):
    """Source file vanished between job creation and phase building (race
    with concurrent download/re-download). Must NOT crash _build_phases —
    fall back to weight=1.0 so ETA can still be computed."""
    fm = _fm()
    job = _job()
    job.source_files = ["ghost.osm.pbf"]  # never created

    phases = fm._build_phases(job)

    assert len(phases) == 1
    assert phases[0].weight == pytest.approx(1.0)
