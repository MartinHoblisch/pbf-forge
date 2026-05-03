from __future__ import annotations

from unittest.mock import MagicMock

import filter_manager as fm_module
import pytest
from filter_manager import FilterJob, FilterManager


def _make_job(**kwargs) -> FilterJob:
    defaults = dict(
        id="test-id",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        geometry_types=["nodes"],
        suffix="test",
        output_formats=["pbf"],
        output_dir="/tmp/out",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(kwargs)
    return FilterJob(**defaults)


def _make_manager() -> FilterManager:
    return FilterManager(MagicMock())


# ── 1 source, pbf + geojson + gpkg ───────────────────────────────────────────


def test_single_source_three_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    job = _make_job(output_formats=["pbf", "geojson", "gpkg"])
    phases = _make_manager()._build_phases(job)

    assert [p.step for p in phases] == ["filter", "export_convert", "export_convert"]
    assert phases[0].label == "berlin.osm.pbf · filter"
    assert "geojson" in phases[1].label
    assert "gpkg" in phases[2].label

    expected_weight = 1000 * (1.0 + 0.5 + 0.5)
    assert abs(sum(p.weight for p in phases) - expected_weight) < 1e-6


# ── 2 sources × 2 formats ─────────────────────────────────────────────────────


def test_two_sources_two_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 500)
    (tmp_path / "hamburg.osm.pbf").write_bytes(b"x" * 800)

    job = _make_job(
        source_files=["berlin.osm.pbf", "hamburg.osm.pbf"],
        output_formats=["pbf", "geojson"],
    )
    phases = _make_manager()._build_phases(job)

    assert len(phases) == 4
    assert len([p for p in phases if p.source == "berlin.osm.pbf"]) == 2
    assert len([p for p in phases if p.source == "hamburg.osm.pbf"]) == 2


# ── pbf only ──────────────────────────────────────────────────────────────────


def test_pbf_only(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 200)

    phases = _make_manager()._build_phases(_make_job(output_formats=["pbf"]))

    assert len(phases) == 1
    assert phases[0].step == "filter"


# ── reduce phase for pbf + manual + manual_keys ───────────────────────────────


def test_reduce_phase_added_for_manual_pbf(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 400)

    job = _make_job(output_formats=["pbf"], columns_mode="manual", manual_keys=["name"])
    phases = _make_manager()._build_phases(job)

    assert [p.step for p in phases] == ["filter", "reduce"]


# ── no reduce without manual_keys ────────────────────────────────────────────


def test_no_reduce_without_manual_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 400)

    job = _make_job(output_formats=["pbf"], columns_mode="manual", manual_keys=[])
    phases = _make_manager()._build_phases(job)

    assert all(p.step != "reduce" for p in phases)


# ── geojson only (no pbf in formats) ─────────────────────────────────────────


def test_geojson_only(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 600)

    phases = _make_manager()._build_phases(_make_job(output_formats=["geojson"]))

    assert [p.step for p in phases] == ["filter", "export_convert"]


# ── weights scale with file size ──────────────────────────────────────────────


def test_weights_scale_with_file_size(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "small.osm.pbf").write_bytes(b"x" * 100)
    (tmp_path / "large.osm.pbf").write_bytes(b"x" * 1000)

    mgr = _make_manager()
    w_small = mgr._build_phases(_make_job(source_files=["small.osm.pbf"]))[0].weight
    w_large = mgr._build_phases(_make_job(source_files=["large.osm.pbf"]))[0].weight

    assert abs(w_large / w_small - 10.0) < 1e-6


# ── missing file → fallback weight 1 ─────────────────────────────────────────


def test_missing_file_fallback_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)

    phases = _make_manager()._build_phases(_make_job(source_files=["ghost.osm.pbf"]))

    assert len(phases) == 1
    assert phases[0].weight == pytest.approx(1.0)
