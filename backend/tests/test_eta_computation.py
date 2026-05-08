"""Tests for FilterManager._compute_eta — predictive ETA arithmetic.

Bug class: ETA is shown to the user as a countdown. Off-by-one or NaN values
make the UI lie about completion time. The function has six logical branches
that must each behave correctly:

  (a) job has no phases at all → None (caller must hide ETA)
  (b) all phases done (no remaining) → exactly 0.0
  (c) predicted_total == 0 (no usable history yet) → scale falls back to 1.0
  (d) remaining phase lacks prediction AND same-kind history → None
  (e) remaining phase lacks prediction but has same-kind median → use median
  (f) currently-running phase deducts elapsed time, capped at predicted duration
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import filter_manager as fm_module
from filter_manager import FilterJob, FilterManager, Phase


def _make_manager() -> FilterManager:
    return FilterManager(MagicMock())


def _job_with_phases(phases: list[Phase], **kwargs) -> FilterJob:
    defaults = dict(
        id="x",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["pbf"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(kwargs)
    j = FilterJob(**defaults)
    j.phases = phases
    return j


def _phase(label: str, source: str, step: str, fmt: str = "pbf",
           duration: float | None = None) -> Phase:
    return Phase(label=label, source=source, step=step, weight=1.0,
                 fmt=fmt, duration_seconds=duration)


# ── (a) No phases ─────────────────────────────────────────────────────────────


def test_eta_no_phases_returns_none():
    fm = _make_manager()
    job = _job_with_phases([])
    assert fm._compute_eta(job) is None


# ── (b) All phases done ───────────────────────────────────────────────────────


def test_eta_all_phases_completed_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    phases = [
        _phase("filter", "berlin.osm.pbf", "filter", "pbf", duration=5.0),
        _phase("export", "berlin.osm.pbf", "export_convert", "gpkg", duration=3.0),
    ]
    job = _job_with_phases(phases)
    job.current_phase_index = 2  # both done
    assert fm._compute_eta(job) == 0.0


# ── (c) No history yet → scale=1.0 fallback ──────────────────────────────────


def test_eta_without_history_falls_back_to_no_scale(tmp_path, monkeypatch):
    """First-ever job for this size: no predictions at all → only same-kind
    median (none) or fallback path. With *no* completed phases, scale is 1.0
    by definition; with predictions in remaining we use them as-is."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    # Inject a known prediction for the remaining phase
    fm._history = MagicMock()
    fm._history.predict.return_value = 7.5

    phases = [_phase("filter", "berlin.osm.pbf", "filter", "pbf")]
    job = _job_with_phases(phases)
    # No completed phases yet
    assert fm._compute_eta(job) == pytest.approx(7.5)


def test_eta_predicted_total_zero_uses_scale_one(tmp_path, monkeypatch):
    """If every completed-phase prediction is 0 (degenerate but possible with
    very fast phases), predicted_total stays 0 and scale must default to 1.0
    rather than DivisionByZero."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    # Completed phase prediction returns 0 (filtered out by `pred > 0` guard);
    # remaining phase prediction returns 4.0
    fm._history.predict.side_effect = [0.0, 4.0]

    phases = [
        _phase("filter", "berlin.osm.pbf", "filter", "pbf", duration=2.0),
        _phase("export", "berlin.osm.pbf", "export_convert", "gpkg"),
    ]
    job = _job_with_phases(phases)
    job.current_phase_index = 1
    # scale=1.0 (fallback) → eta = 4.0 * 1.0
    assert fm._compute_eta(job) == pytest.approx(4.0)


# ── (d) Remaining without prediction + no same-kind → None ───────────────────


def test_eta_no_prediction_no_same_kind_returns_none(tmp_path, monkeypatch):
    """If a remaining phase has no historical prediction AND no same-kind phase
    has completed *in this job*, ETA is unknown — return None."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    fm._history.predict.return_value = None  # no history at all

    phases = [_phase("export", "berlin.osm.pbf", "export_convert", "gpkg")]
    job = _job_with_phases(phases)
    assert fm._compute_eta(job) is None


# ── (e) Remaining without prediction but same-kind median ────────────────────


def test_eta_uses_same_kind_median_when_no_prediction(tmp_path, monkeypatch):
    """When library prediction is None, fall back to median of same step/fmt
    phases already completed *in this job*."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)
    (tmp_path / "hamburg.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    fm._history.predict.return_value = None  # disable history globally

    phases = [
        _phase("filter berlin", "berlin.osm.pbf", "filter", "pbf", duration=4.0),
        _phase("filter hamburg", "hamburg.osm.pbf", "filter", "pbf", duration=8.0),
        _phase("filter munich", "munich.osm.pbf", "filter", "pbf"),  # no duration
    ]
    job = _job_with_phases(phases)
    job.current_phase_index = 2
    # median([4.0, 8.0]) = 6.0 used for the remaining same-kind phase
    assert fm._compute_eta(job) == pytest.approx(6.0)


# ── (f) Mid-phase elapsed-time deduction ─────────────────────────────────────


def test_eta_mid_phase_deducts_elapsed_capped(tmp_path, monkeypatch):
    """While a phase is running, deduct elapsed time from ETA (capped at the
    phase's predicted duration so ETA never goes negative)."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    fm._history.predict.return_value = 10.0

    phases = [_phase("filter", "berlin.osm.pbf", "filter", "pbf")]
    job = _job_with_phases(phases)
    job.phase_started_at = 100.0  # phase started at t=100
    # Mock time.time so elapsed = 4.0 seconds
    with patch.object(fm_module.time, "time", return_value=104.0):
        eta = fm._compute_eta(job)
    # ETA = 10.0 (predicted) - min(4.0 elapsed, 10.0 cap) = 6.0
    assert eta == pytest.approx(6.0)


def test_eta_mid_phase_elapsed_exceeds_prediction_clamped_to_zero(tmp_path, monkeypatch):
    """Phase took longer than predicted → elapsed_capped = predicted, ETA = 0."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    fm._history.predict.return_value = 5.0

    phases = [_phase("filter", "berlin.osm.pbf", "filter", "pbf")]
    job = _job_with_phases(phases)
    job.phase_started_at = 100.0
    with patch.object(fm_module.time, "time", return_value=200.0):  # 100s elapsed
        eta = fm._compute_eta(job)
    # eta = 5.0 - min(100, 5.0) = 0.0 (clamped)
    assert eta == pytest.approx(0.0)


def test_eta_scale_applied_to_remaining(tmp_path, monkeypatch):
    """If completed phases ran 2x slower than predicted, remaining prediction
    is scaled by 2x as well."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _make_manager()
    fm._history = MagicMock()
    # Completed prediction = 5.0, actual = 10.0 → scale = 2.0
    # Remaining prediction = 3.0 → eta = 3.0 * 2.0
    fm._history.predict.side_effect = [5.0, 3.0]

    phases = [
        _phase("filter", "berlin.osm.pbf", "filter", "pbf", duration=10.0),
        _phase("export", "berlin.osm.pbf", "export_convert", "gpkg"),
    ]
    job = _job_with_phases(phases)
    job.current_phase_index = 1
    assert fm._compute_eta(job) == pytest.approx(6.0)
