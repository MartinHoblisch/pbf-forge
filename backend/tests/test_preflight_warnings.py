"""Tests for memory/disk preflight warnings emitted at job start."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import filter_manager as fm_module
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


def test_low_memory_emits_warning_when_geojson_requested():
    fm = _fm()
    job = _job(output_formats=["geojson"])

    # 1 GB available, claim 100 GB source → estimated peak 40 GB > 1 GB → warning
    with patch.object(fm_module, "_mem_available_bytes", return_value=1 * 1024**3):
        fm._preflight_warnings(job, total_source_bytes=100 * 1024**3)

    assert "WARNING" in job.log
    assert "RAM" in job.log


def test_no_warning_when_geojson_not_requested():
    fm = _fm()
    job = _job(output_formats=["gpkg"])

    with patch.object(fm_module, "_mem_available_bytes", return_value=1 * 1024**3):
        fm._preflight_warnings(job, total_source_bytes=100 * 1024**3)

    # Disk check may still trigger; the RAM warning specifically must not.
    assert "RAM" not in job.log


def test_no_warning_when_memory_sufficient():
    fm = _fm()
    job = _job(output_formats=["geojson"])

    # 16 GB available, 1 GB source → estimated peak 400 MB → no warning
    with patch.object(fm_module, "_mem_available_bytes", return_value=16 * 1024**3):
        with patch("filter_manager.shutil.disk_usage") as du:
            du.return_value.free = 100 * 1024**3
            fm._preflight_warnings(job, total_source_bytes=1 * 1024**3)

    assert job.log == ""


def test_low_disk_emits_warning():
    fm = _fm()
    job = _job(output_formats=["pbf"])

    with patch.object(fm_module, "_mem_available_bytes", return_value=None):
        with patch("filter_manager.shutil.disk_usage") as du:
            du.return_value.free = 100 * 1024 * 1024  # 100 MB free
            fm._preflight_warnings(job, total_source_bytes=10 * 1024**3)  # 10 GB

    assert "WARNING" in job.log
    assert "disk" in job.log.lower()


def test_non_linux_mem_check_does_not_crash():
    """_mem_available_bytes returns None on non-Linux; preflight still runs."""
    fm = _fm()
    job = _job(output_formats=["geojson"])

    with patch.object(fm_module, "_mem_available_bytes", return_value=None):
        with patch("filter_manager.shutil.disk_usage") as du:
            du.return_value.free = 100 * 1024**3
            fm._preflight_warnings(job, total_source_bytes=1 * 1024**3)

    # Should complete cleanly — no RAM warning when MemAvailable unknown
    assert "RAM" not in job.log
