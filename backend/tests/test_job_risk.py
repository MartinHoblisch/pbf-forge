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
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 6 * GB)
    risk = fm.assess_job_risk(["germany.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"
    assert risk["total_ram_bytes"] == 8 * GB


def test_no_risk_for_pbf_only(fm, monkeypatch):
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 30 * GB)
    assert fm.assess_job_risk(["europe.osm.pbf"], ["pbf"]) is None


def test_no_risk_for_small_source(fm, monkeypatch):
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 1 * GB)
    assert fm.assess_job_risk(["berlin.osm.pbf"], ["gpkg", "geojson"]) is None


def test_unknown_ram_yields_no_risk(fm, monkeypatch):
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: None)
    monkeypatch.setattr(fm, "_source_size", lambda s: 30 * GB)
    assert fm.assess_job_risk(["europe.osm.pbf"], ["gpkg"]) is None


def test_boundary_exactly_at_threshold_is_safe(fm, monkeypatch):
    # ram 8 GB, source exactly 4 GB (== ram * 0.5) — condition is strict >, so safe
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 4 * GB)
    assert fm.assess_job_risk(["x.osm.pbf"], ["gpkg"]) is None


def test_boundary_one_byte_over_threshold_is_high(fm, monkeypatch):
    # ram 8 GB, source 4 GB + 1 byte → just over 50% → high
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 4 * GB + 1)
    risk = fm.assess_job_risk(["x.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"


def test_multi_source_sum_crosses_threshold(fm, monkeypatch):
    # ram 8 GB, two sources at 3 GB each → sum 6 GB > 4 GB threshold → high
    monkeypatch.setattr(fm, "_memory_limit_bytes", lambda: 8 * GB)
    monkeypatch.setattr(fm, "_source_size", lambda s: 3 * GB)
    risk = fm.assess_job_risk(["a.osm.pbf", "b.osm.pbf"], ["gpkg"])
    assert risk is not None
    assert risk["level"] == "high"


class TestMemoryLimitDetection:
    """The ceiling must be the cgroup limit, not the host's MemTotal.

    Inside a container /proc/meminfo reports the host's memory. Sizing the
    queue or the pre-flight warning from it means the tool believes it has
    several times the memory the container is actually killed at.
    """

    @staticmethod
    def _fake_reader(files: dict[str, str]):
        real_read = fm_module.Path.read_text

        def read_text(self, *args, **kwargs):
            key = str(self).replace("\\", "/")
            if key in files:
                return files[key]
            if key in ("/proc/meminfo", "/sys/fs/cgroup/memory.max"):
                raise OSError("not present")
            return real_read(self, *args, **kwargs)

        return read_text

    def test_cgroup_v2_limit_wins_over_host_meminfo(self, monkeypatch):
        monkeypatch.setattr(
            fm_module.Path,
            "read_text",
            self._fake_reader(
                {
                    "/sys/fs/cgroup/memory.max": "4294967296\n",
                    "/proc/meminfo": "MemTotal:       33554432 kB\n",
                }
            ),
        )
        assert fm_module._detect_memory_limit_bytes() == 4 * GB

    def test_falls_back_to_meminfo_when_cgroup_is_unlimited(self, monkeypatch):
        monkeypatch.setattr(
            fm_module.Path,
            "read_text",
            self._fake_reader(
                {
                    "/sys/fs/cgroup/memory.max": "max\n",
                    "/proc/meminfo": "MemTotal:        8388608 kB\n",
                }
            ),
        )
        assert fm_module._detect_memory_limit_bytes() == 8 * GB

    def test_cgroup_v1_unlimited_sentinel_is_ignored(self, monkeypatch):
        monkeypatch.setattr(
            fm_module.Path,
            "read_text",
            self._fake_reader(
                {
                    "/sys/fs/cgroup/memory/memory.limit_in_bytes": "9223372036854771712\n",
                    "/proc/meminfo": "MemTotal:        8388608 kB\n",
                }
            ),
        )
        assert fm_module._detect_memory_limit_bytes() == 8 * GB

    def test_returns_none_when_nothing_is_readable(self, monkeypatch):
        monkeypatch.setattr(fm_module.Path, "read_text", self._fake_reader({}))
        assert fm_module._detect_memory_limit_bytes() is None


class TestQueueSizing:
    """max_parallel must respect a small container limit, not just core count."""

    def test_four_gb_container_runs_one_job_whatever_the_core_count(self, monkeypatch):
        monkeypatch.setattr(fm_module, "_detect_memory_limit_bytes", lambda: 4 * GB)
        monkeypatch.setattr(fm_module.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(
            fm_module.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError())
        )
        assert fm_module._compute_max_parallel() == 1

    def test_memory_caps_the_core_derived_number(self, monkeypatch):
        monkeypatch.setattr(fm_module, "_detect_memory_limit_bytes", lambda: 16 * GB)
        monkeypatch.setattr(fm_module.os, "cpu_count", lambda: 32)
        monkeypatch.setattr(
            fm_module.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError())
        )
        # cpu//4 == 8, ram_gb//8 == 2
        assert fm_module._compute_max_parallel() == 2

    def test_unknown_memory_falls_back_to_core_count(self, monkeypatch):
        monkeypatch.setattr(fm_module, "_detect_memory_limit_bytes", lambda: None)
        monkeypatch.setattr(fm_module.os, "cpu_count", lambda: 16)
        monkeypatch.setattr(
            fm_module.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError())
        )
        assert fm_module._compute_max_parallel() == 4


def test_malformed_cgroup_value_does_not_raise(monkeypatch):
    """FilterManager is constructed at startup, so a parse error here kills the app."""
    real_read = fm_module.Path.read_text

    def read_text(self, *args, **kwargs):
        key = str(self).replace("\\", "/")
        if key == "/sys/fs/cgroup/memory.max":
            return "not-a-number\n"
        if key == "/proc/meminfo":
            return "MemTotal:        8388608 kB\n"
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(fm_module.Path, "read_text", read_text)
    assert fm_module._detect_memory_limit_bytes() == 8 * GB
