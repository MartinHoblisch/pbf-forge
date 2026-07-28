"""Tests for FilterManager job-lifecycle methods: clear_completed_jobs, cancel_job.

Bug class to prevent:
  - clear_completed_jobs accidentally drops a running job → user loses progress.
  - cancel_job kills the wrong process / fails silently / leaves stale state.
  - cancel_job called on an already-finished job mutates its outcome.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from filter_manager import FilterJob, FilterManager


def _job(job_id: str, status: str = "pending") -> FilterJob:
    j = FilterJob(
        id=job_id,
        source_files=["x.osm.pbf"],
        tags=["a"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["pbf"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    j.status = status
    return j


# ── clear_completed_jobs ─────────────────────────────────────────────────────


def test_clear_keeps_running_and_pending():
    fm = FilterManager(MagicMock())
    fm._jobs = {
        "a": _job("a", "running"),
        "b": _job("b", "pending"),
        "c": _job("c", "done"),
        "d": _job("d", "error"),
    }
    fm.clear_completed_jobs()
    assert set(fm._jobs.keys()) == {"a", "b"}


def test_clear_empty_manager_is_noop():
    fm = FilterManager(MagicMock())
    fm._jobs = {}
    fm.clear_completed_jobs()  # no raise
    assert fm._jobs == {}


def test_clear_all_done_results_in_empty():
    fm = FilterManager(MagicMock())
    fm._jobs = {
        "a": _job("a", "done"),
        "b": _job("b", "error"),
    }
    fm.clear_completed_jobs()
    assert fm._jobs == {}


# ── cancel_job ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_job_kills_proc_and_marks_error():
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job("running-id", "running")
    fm._jobs["running-id"] = job

    proc = MagicMock()
    fm._procs["running-id"] = proc

    result = await fm.cancel_job("running-id")

    assert result is True
    proc.kill.assert_called_once()
    assert job.status == "error"
    assert job.error == "Cancelled by user"
    # The error state is shared with a job that failed on its own; the flag is
    # what lets a client tell a stopped job from a failed one.
    assert job.cancelled is True
    assert job.to_dict()["cancelled"] is True
    ws.broadcast.assert_called()  # state update emitted


@pytest.mark.asyncio
async def test_a_job_that_was_not_cancelled_says_so():
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job("done-id", "done")
    fm._jobs["done-id"] = job

    assert job.cancelled is False
    assert job.to_dict()["cancelled"] is False


@pytest.mark.asyncio
async def test_cancel_unknown_id_returns_false():
    ws = AsyncMock()
    fm = FilterManager(ws)
    result = await fm.cancel_job("nonexistent")
    assert result is False
    ws.broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_already_done_job_does_not_overwrite_status():
    """Cancel on a job that already finished must not flip status to error."""
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job("done-id", "done")
    fm._jobs["done-id"] = job

    result = await fm.cancel_job("done-id")

    assert result is False
    assert job.status == "done"  # unchanged
    assert job.error is None
    ws.broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_swallows_process_lookup_error():
    """If the process already exited (race), kill() raises ProcessLookupError —
    cancel must still mark the job as cancelled, not propagate the error."""
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job("running-id", "running")
    fm._jobs["running-id"] = job

    proc = MagicMock()
    proc.kill.side_effect = ProcessLookupError("already gone")
    fm._procs["running-id"] = proc

    result = await fm.cancel_job("running-id")

    assert result is True
    assert job.status == "error"
    assert job.error == "Cancelled by user"


@pytest.mark.asyncio
async def test_cancel_running_without_proc_still_marks_error():
    """If somehow the proc isn't tracked but the job says running, still cancel."""
    ws = AsyncMock()
    fm = FilterManager(ws)
    job = _job("running-id", "running")
    fm._jobs["running-id"] = job
    # _procs intentionally empty

    result = await fm.cancel_job("running-id")

    assert result is True
    assert job.status == "error"


# ── Job queue ─────────────────────────────────────────────────────────────────


async def test_second_job_queues_when_at_capacity():
    """When max_parallel=1 and job1 holds the semaphore, job2 must be queued."""
    fm = FilterManager(AsyncMock())
    fm._max_parallel = 1
    fm._semaphore = asyncio.Semaphore(1)

    job1 = _job("job-1")
    job2 = _job("job-2")
    fm._jobs[job1.id] = job1
    fm._jobs[job2.id] = job2

    release_job1 = asyncio.Event()
    job1_running = asyncio.Event()

    async def mock_execute(job: FilterJob) -> None:
        if job.id == "job-1":
            job1_running.set()
            await release_job1.wait()

    with patch.object(fm, "_execute_job", side_effect=mock_execute):
        t1 = asyncio.create_task(fm.run_job(job1))
        await job1_running.wait()  # job1 holds semaphore

        t2 = asyncio.create_task(fm.run_job(job2))
        await asyncio.sleep(0)  # let job2 reach semaphore acquisition

        assert job2.status == "queued"
        assert job2.queue_position is not None

        release_job1.set()
        await asyncio.gather(t1, t2)

    assert fm._running_count == 0
