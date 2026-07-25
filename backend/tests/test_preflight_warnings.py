"""Tests for the disk-space warning emitted at job start.

Disk space only. RAM risk is checked before the job starts, by
FilterManager.assess_job_risk — see test_job_risk.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from filter_manager import FilterJob, FilterManager


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _job(**overrides) -> FilterJob:
    defaults = dict(
        id="t",
        source_files=["x.osm.pbf"],
        tags=["a"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["geojson"],
        output_dir="/tmp/out",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(overrides)
    return FilterJob(**defaults)


def test_no_ram_warning_emitted_even_when_geojson_requested():
    """RAM warning is gone — large source + GeoJSON must not produce a RAM line."""
    fm = _fm()
    job = _job(output_formats=["geojson"])

    with patch("filter_manager.shutil.disk_usage") as du:
        du.return_value.free = 1000 * 1024**3  # plenty of disk
        fm._preflight_warnings(job, total_source_bytes=100 * 1024**3)

    assert "RAM" not in job.log
    assert "OOM" not in job.log


def test_low_disk_emits_warning():
    fm = _fm()
    job = _job(output_formats=["pbf"])

    with patch("filter_manager.shutil.disk_usage") as du:
        du.return_value.free = 100 * 1024 * 1024  # 100 MB free
        fm._preflight_warnings(job, total_source_bytes=10 * 1024**3)  # 10 GB

    assert "WARNING" in job.log
    assert "disk" in job.log.lower()


def test_sufficient_disk_no_warning():
    fm = _fm()
    job = _job(output_formats=["geojson"])

    with patch("filter_manager.shutil.disk_usage") as du:
        du.return_value.free = 100 * 1024**3
        fm._preflight_warnings(job, total_source_bytes=1 * 1024**3)

    assert job.log == ""


def test_disk_check_oserror_is_silent():
    """If shutil.disk_usage fails (e.g. permission, missing dir), preflight stays quiet."""
    fm = _fm()
    job = _job(output_formats=["pbf"])

    with patch("filter_manager.shutil.disk_usage", side_effect=OSError("boom")):
        fm._preflight_warnings(job, total_source_bytes=1 * 1024**3)

    assert job.log == ""
