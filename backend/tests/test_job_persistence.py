"""Tests for crash-visibility persistence in FilterManager.

Covers:
  - Manifest written on create + status changes
  - Log file written alongside in-memory log
  - Orphaned in-flight jobs marked as errored on restart
  - clear_completed_jobs removes log files and rewrites manifest
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from filter_manager import FilterManager


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _job_kwargs() -> dict:
    return dict(
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["pbf"],
        output_dir="/tmp/out",
        columns_mode="other_tags",
        manual_keys=[],
    )


def test_create_job_writes_manifest_and_assigns_log_path(tmp_config_dir):
    fm = _fm()
    job = fm.create_job(**_job_kwargs())

    manifest = tmp_config_dir / "jobs" / "manifest.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert len(data) == 1
    assert data[0]["id"] == job.id
    # log not embedded in manifest
    assert "log" not in data[0]
    assert data[0]["log_file"].endswith(f"{job.id}.log")
    assert job._log_path == tmp_config_dir / "jobs" / f"{job.id}.log"


def test_append_log_writes_to_disk(tmp_config_dir):
    fm = _fm()
    job = fm.create_job(**_job_kwargs())
    job.append_log("hello world\n")
    job.append_log("second line\n")
    job.close_log()  # flush + release handle

    log_path = tmp_config_dir / "jobs" / f"{job.id}.log"
    content = log_path.read_text(encoding="utf-8")
    assert "hello world" in content
    assert "second line" in content


def test_orphan_recovery_marks_running_jobs_as_errored(tmp_config_dir):
    fm = _fm()
    job = fm.create_job(**_job_kwargs())
    job.status = "running"
    fm._persist_jobs()

    # Simulate fresh process — new FilterManager reads manifest
    fm2 = _fm()
    recovered = fm2._jobs[job.id]
    assert recovered.status == "error"
    err = recovered.error or ""
    assert "Backend crashed" in err
    # Error must reveal the log file path so the user knows where to look
    assert f"{job.id}.log" in err
    # Recovery rewrites manifest with the new status
    data = json.loads((tmp_config_dir / "jobs" / "manifest.json").read_text())
    assert data[0]["status"] == "error"


def test_orphan_recovery_preserves_completed_jobs(tmp_config_dir):
    fm = _fm()
    job = fm.create_job(**_job_kwargs())
    job.status = "done"
    fm._persist_jobs()

    fm2 = _fm()
    recovered = fm2._jobs[job.id]
    assert recovered.status == "done"
    assert recovered.error is None


def test_orphan_recovery_handles_queued_status(tmp_config_dir):
    fm = _fm()
    job = fm.create_job(**_job_kwargs())
    job.status = "queued"
    fm._persist_jobs()

    fm2 = _fm()
    assert fm2._jobs[job.id].status == "error"


def test_clear_completed_jobs_removes_logs_and_manifest_entries(tmp_config_dir):
    fm = _fm()
    j_done = fm.create_job(**_job_kwargs())
    j_done.status = "done"
    j_done.append_log("done\n")
    j_done.close_log()
    j_run = fm.create_job(**_job_kwargs())
    j_run.status = "running"
    fm._persist_jobs()

    log_path = j_done._log_path
    assert log_path.exists()

    fm.clear_completed_jobs()

    assert j_done.id not in fm._jobs
    assert j_run.id in fm._jobs
    assert not log_path.exists()  # log file deleted
    data = json.loads((tmp_config_dir / "jobs" / "manifest.json").read_text())
    assert len(data) == 1
    assert data[0]["id"] == j_run.id


def test_manifest_corruption_does_not_crash_startup(tmp_config_dir):
    jobs_dir = tmp_config_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / "manifest.json").write_text("not json {")

    # Should not raise
    fm = _fm()
    assert fm._jobs == {}
