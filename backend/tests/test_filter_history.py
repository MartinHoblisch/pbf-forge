from __future__ import annotations

import json

import pytest
from filter_history import FilterHistory


def _history(tmp_path) -> FilterHistory:
    return FilterHistory(tmp_path / "filter_history.json")


# ── record + predict roundtrip ────────────────────────────────────────────────


def test_record_and_predict_same_size(tmp_path):
    h = _history(tmp_path)
    h.record("berlin.osm.pbf", 1_000_000, "filter", "pbf", 10.0)
    assert h.predict(1_000_000, "filter", "pbf") == pytest.approx(10.0)


# ── predict without history → None ───────────────────────────────────────────


def test_predict_no_history(tmp_path):
    assert _history(tmp_path).predict(1_000_000, "filter", "pbf") is None


# ── predict wrong step → None ────────────────────────────────────────────────


def test_predict_wrong_step(tmp_path):
    h = _history(tmp_path)
    h.record("berlin.osm.pbf", 1_000_000, "filter", "pbf", 10.0)
    assert h.predict(1_000_000, "export_convert", "pbf") is None


# ── predict wrong format → None ──────────────────────────────────────────────


def test_predict_wrong_format(tmp_path):
    h = _history(tmp_path)
    h.record("berlin.osm.pbf", 1_000_000, "filter", "pbf", 10.0)
    assert h.predict(1_000_000, "filter", "geojson") is None


# ── linear scaling with single entry ─────────────────────────────────────────


def test_predict_linear_scale_single_entry(tmp_path):
    h = _history(tmp_path)
    h.record("berlin.osm.pbf", 1_000_000, "filter", "pbf", 10.0)
    # size within ±30%: 1_200_000 = +20%
    result = h.predict(1_200_000, "filter", "pbf")
    assert result == pytest.approx(12.0, rel=1e-3)


# ── size outside ±30% tolerance → None ───────────────────────────────────────


def test_predict_size_out_of_tolerance(tmp_path):
    h = _history(tmp_path)
    h.record("berlin.osm.pbf", 1_000_000, "filter", "pbf", 10.0)
    # +100% — outside tolerance
    assert h.predict(2_000_000, "filter", "pbf") is None


# ── persistence across instances ─────────────────────────────────────────────


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "filter_history.json"
    FilterHistory(path).record("a.osm.pbf", 500_000, "filter", "pbf", 5.0)
    assert FilterHistory(path).predict(500_000, "filter", "pbf") == pytest.approx(5.0)


# ── corrupt JSON → empty store + file renamed ────────────────────────────────


def test_corrupt_file_resets_and_renames(tmp_path):
    path = tmp_path / "filter_history.json"
    path.write_text("not valid json{{", encoding="utf-8")

    h = FilterHistory(path)
    assert h.predict(1_000_000, "filter", "pbf") is None
    assert not path.exists()
    assert len(list(tmp_path.glob("filter_history.corrupt-*.json"))) == 1


# ── wrong schema version → reset ─────────────────────────────────────────────


def test_wrong_schema_version_resets(tmp_path):
    path = tmp_path / "filter_history.json"
    path.write_text(json.dumps({"v": 99, "entries": []}), encoding="utf-8")
    assert FilterHistory(path).predict(1_000_000, "filter", "pbf") is None


# ── atomic write: no .tmp file left behind ───────────────────────────────────


def test_no_tmp_file_after_save(tmp_path):
    h = _history(tmp_path)
    h.record("a.osm.pbf", 100_000, "filter", "pbf", 3.0)
    assert not (tmp_path / "filter_history.tmp").exists()


# ── predict uses median, outlier-resistant ───────────────────────────────────


def test_predict_uses_median(tmp_path):
    h = _history(tmp_path)
    for dur in [5.0, 10.0, 100.0]:
        h.record("a.osm.pbf", 1_000_000, "filter", "pbf", dur)
    # median([5,10,100]) = 10; exact size match → no scaling
    assert h.predict(1_000_000, "filter", "pbf") == pytest.approx(10.0, rel=1e-3)
