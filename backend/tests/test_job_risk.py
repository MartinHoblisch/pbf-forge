"""Gate 0: pre-job RAM risk assessment."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import filter_manager as fm_module

GB = 1024**3


@pytest.fixture
def fm(reset_state):
    """Construct a FilterManager with a mock ws_manager."""
    return fm_module.FilterManager(ws_manager=MagicMock())


def test_high_risk_for_big_source_nonpbf(fm, monkeypatch):
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 6 * GB)
    risk = fm.assess_job_risk(["germany.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"
    assert risk["total_ram_bytes"] == 8 * GB


def test_no_risk_for_pbf_only(fm, monkeypatch):
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 30 * GB)
    assert fm.assess_job_risk(["europe.osm.pbf"], ["pbf"]) is None


def test_no_risk_for_small_source(fm, monkeypatch):
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 1 * GB)
    assert fm.assess_job_risk(["berlin.osm.pbf"], ["gpkg", "geojson"]) is None


def test_unknown_ram_yields_no_risk(fm, monkeypatch):
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: None)
    monkeypatch.setattr(fm, "_source_size", lambda s: 30 * GB)
    assert fm.assess_job_risk(["europe.osm.pbf"], ["gpkg"]) is None


def test_boundary_exactly_at_threshold_is_safe(fm, monkeypatch):
    # ram 8 GB, source exactly 4 GB (== ram * 0.5) — condition is strict >, so safe
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 4 * GB)
    assert fm.assess_job_risk(["x.osm.pbf"], ["gpkg"]) is None


def test_boundary_one_byte_over_threshold_is_high(fm, monkeypatch):
    # ram 8 GB, source 4 GB + 1 byte → just over 50% → high
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 4 * GB + 1)
    risk = fm.assess_job_risk(["x.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"


def test_multi_source_sum_crosses_threshold(fm, monkeypatch):
    # ram 8 GB, two sources at 3 GB each → sum 6 GB > 4 GB threshold → high
    monkeypatch.setattr(fm, "_meminfo_total_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 3 * GB)
    risk = fm.assess_job_risk(["a.osm.pbf", "b.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"
