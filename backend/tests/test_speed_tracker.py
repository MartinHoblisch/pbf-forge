"""Tests for the rolling download-speed tracker and its broadcast throttle."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from download_manager import _SpeedTracker


def test_speed_bps_zero_with_no_samples():
    tracker = _SpeedTracker()
    assert tracker.speed_bps() == 0.0


def test_speed_bps_zero_with_one_sample():
    tracker = _SpeedTracker()
    tracker.add_bytes(1000)
    assert tracker.speed_bps() == 0.0


def test_speed_bps_rolling_window():
    tracker = _SpeedTracker()
    times = iter([10.0, 12.0])  # t0=10, t1=12 → dt=2s
    with patch("download_manager.time.monotonic", side_effect=lambda: next(times)):
        tracker.add_bytes(0)  # t=10.0, total=0
        tracker.add_bytes(200)  # t=12.0, total=200
    # speed = (200 - 0) / (12.0 - 10.0) = 100 bps
    assert tracker.speed_bps() == pytest.approx(100.0)


def test_speed_bps_discards_old_samples():
    window = 5
    tracker = _SpeedTracker(window=window)
    # t=0 is evicted at t=10 (cutoff=5); t=6 and t=10 remain
    times = iter([0.0, 6.0, 10.0])
    with patch("download_manager.time.monotonic", side_effect=lambda: next(times)):
        tracker.add_bytes(0)  # t=0.0, total=0   → evicted (0 < cutoff 5)
        tracker.add_bytes(600)  # t=6.0, total=600 → kept
        tracker.add_bytes(200)  # t=10.0, total=800 → kept
    # remaining: [(6.0, 600), (10.0, 800)] → (800-600)/(10-6) = 50 bps
    assert tracker.speed_bps() == pytest.approx(50.0)


def test_add_bytes_threadsafe():
    tracker = _SpeedTracker()
    n_threads = 10
    chunk = 1000

    def worker():
        for _ in range(100):
            tracker.add_bytes(chunk)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.total == n_threads * 100 * chunk


def test_should_broadcast_true_after_interval():
    tracker = _SpeedTracker()
    times = iter([2.0, 2.5, 3.5])
    with patch("download_manager.time.monotonic", side_effect=lambda: next(times)):
        assert tracker.should_broadcast() is True  # 2.0 - 0.0 >= 1.0
        assert tracker.should_broadcast() is False  # 2.5 - 2.0 < 1.0
        assert tracker.should_broadcast() is True  # 3.5 - 2.0 >= 1.0
