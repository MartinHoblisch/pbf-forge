"""Additional _compute_eta sub-branches not covered by test_eta_computation.py.

Bug class:
  - Completed phase whose duration_seconds is None (race: phase finished but
    record was lost) breaks the scale calculation if not skipped → ETA
    explodes or stays None forever.
  - Running phase has no library prediction AND no same-kind completed
    phase yet → must fall through to elapsed-as-cap so ETA still ticks down,
    not stays NaN.
  - Running phase has no library prediction but a same-kind completed phase
    exists → use its median duration as cap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import filter_manager as fm_module
from filter_manager import FilterJob, FilterManager, Phase


def _fm() -> FilterManager:
    return FilterManager(MagicMock())


def _job(phases, **overrides) -> FilterJob:
    defaults = dict(
        id="x",
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
    defaults.update(overrides)
    j = FilterJob(**defaults)
    j.phases = phases
    return j


def _phase(label="p", source="berlin.osm.pbf", step="filter", fmt="pbf", duration=None) -> Phase:
    return Phase(
        label=label, source=source, step=step, weight=1.0, fmt=fmt, duration_seconds=duration
    )


# ── Completed phase with no recorded duration ────────────────────────────────


def test_eta_completed_with_none_duration_is_skipped(tmp_path, monkeypatch):
    """If a completed phase has duration_seconds=None (e.g. _finish_phase
    raised mid-record), it must be skipped in the scale calculation, not
    treated as actual=0 which would zero out the entire scale."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _fm()
    fm._history = MagicMock()
    # Two completed phases: first has no duration (skipped), second has
    # duration=10 with predicted=5 → scale=2.0.
    # Remaining predicted=3 → eta=3*2=6.
    fm._history.predict.side_effect = [5.0, 3.0]
    # completed[0] skipped before predict (duration is None) → no call.
    # completed[1] predicted=5 used. remaining[0] predicted=3 used.
    # Plus: mid-phase elapsed-deduction would call predict again, but
    # phase_started_at is None here so that block is skipped.

    phases = [
        _phase(step="filter", duration=None),
        _phase(step="filter", duration=10.0),
        _phase(step="export_convert", fmt="gpkg"),
    ]
    job = _job(phases)
    job.current_phase_index = 2
    assert fm._compute_eta(job) == pytest.approx(6.0)


# ── Mid-phase, prediction=None, same-kind median fallback ────────────────────


def test_eta_mid_phase_uses_same_kind_median_when_prediction_none(tmp_path, monkeypatch):
    """While running phase has no library prediction but a previous same-kind
    phase did finish — use its duration as the cap so elapsed deduction works."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "a.osm.pbf").write_bytes(b"x" * 1000)
    (tmp_path / "b.osm.pbf").write_bytes(b"x" * 1000)

    fm = _fm()
    fm._history = MagicMock()
    fm._history.predict.return_value = None  # no library data at all

    phases = [
        _phase(label="a-filter", source="a.osm.pbf", step="filter", duration=8.0),
        _phase(label="b-filter", source="b.osm.pbf", step="filter"),  # running, no prediction
    ]
    job = _job(phases)
    job.current_phase_index = 1
    job.phase_started_at = 100.0

    with patch.object(fm_module.time, "time", return_value=103.0):  # elapsed = 3.0
        eta = fm._compute_eta(job)

    # Same-kind median = 8.0 (single observation), cap=8, elapsed=3 → eta = 8 - 3 = 5
    assert eta == pytest.approx(5.0)


# ── Mid-phase, prediction=None, no same-kind completed → cap=elapsed_current ─


def test_eta_mid_phase_no_prediction_no_same_kind_caps_at_elapsed(tmp_path, monkeypatch):
    """Mid-phase, no library prediction, no same-kind completed → can't cap
    at predicted; cap defaults to elapsed_current → eta floor is 0."""
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_path)
    (tmp_path / "berlin.osm.pbf").write_bytes(b"x" * 1000)

    fm = _fm()
    fm._history = MagicMock()
    fm._history.predict.return_value = None

    phases = [
        # Need a same-kind completed phase for the function to reach the
        # mid-phase deduction at all (otherwise it returns None at the
        # earlier "no same_kind" branch). Use a single same-kind phase to
        # populate the median.
        _phase(label="warm", step="filter", fmt="pbf", duration=4.0),
        _phase(label="running", step="filter", fmt="pbf"),  # current
    ]
    job = _job(phases)
    job.current_phase_index = 1
    job.phase_started_at = 100.0

    # elapsed > median → eta clamped to 0
    with patch.object(fm_module.time, "time", return_value=200.0):
        eta = fm._compute_eta(job)

    assert eta == pytest.approx(0.0)
