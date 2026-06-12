"""D2: in-flight downloads use a .part suffix and are invisible to listings."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

import download_manager as dm_mod
from download_manager import DownloadManager


class _StubWs:
    async def broadcast(self, data):  # pragma: no cover
        pass


@pytest.fixture
def dm(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dm_mod, "URLS_FILE", tmp_path / "urls.json")
    return DownloadManager(_StubWs())


def _prep(dm, tmp_path, monkeypatch, server_mtime, captured):
    filename = "berlin.osm.pbf"
    url = "http://example.com/berlin-latest.osm.pbf"
    monkeypatch.setattr(dm, "_head", lambda u, session=None: (100, server_mtime))

    def fake_download(u, dest, start_byte, total, tracker, state, cancel, session):
        captured["dest"] = dest
        captured["start_byte"] = start_byte
        dest.write_bytes(b"x" * 100)

    monkeypatch.setattr(dm, "_do_download", fake_download)
    monkeypatch.setattr(dm, "_verify_checksum", lambda u, d, s: None)
    dm.register_url(url, filename)
    dm._files[filename].status = "downloading"
    return filename


def test_download_writes_part_then_renames(dm, tmp_path, monkeypatch):
    captured = {}
    mtime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    filename = _prep(dm, tmp_path, monkeypatch, mtime, captured)

    dm._download_worker(filename, threading.Event())

    assert captured["dest"].name == "berlin.osm.pbf.part"
    assert (tmp_path / "berlin.osm.pbf").exists()
    assert not (tmp_path / "berlin.osm.pbf.part").exists()


def test_resume_uses_part_size(dm, tmp_path, monkeypatch):
    captured = {}
    old_server_mtime = datetime.now(timezone.utc) - timedelta(days=2)
    filename = _prep(dm, tmp_path, monkeypatch, old_server_mtime, captured)
    (tmp_path / "berlin.osm.pbf.part").write_bytes(b"y" * 40)

    dm._download_worker(filename, threading.Event())

    assert captured["start_byte"] == 40


def test_stale_part_discarded_when_server_newer(dm, tmp_path, monkeypatch):
    captured = {}
    future_mtime = datetime.now(timezone.utc) + timedelta(days=1)
    filename = _prep(dm, tmp_path, monkeypatch, future_mtime, captured)
    (tmp_path / "berlin.osm.pbf.part").write_bytes(b"y" * 40)

    dm._download_worker(filename, threading.Event())

    assert captured["start_byte"] == 0


def test_416_without_part_raises_clean_error(dm, tmp_path, monkeypatch):
    """A 416 on a fresh download (no .part) must yield a clear error, not FileNotFoundError."""
    captured = {}
    mtime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    filename = _prep(dm, tmp_path, monkeypatch, mtime, captured)
    # simulate server 416 with nothing written: _do_download returns without writing
    monkeypatch.setattr(dm, "_do_download", lambda *a, **k: None)

    dm._download_worker(filename, threading.Event())

    state = dm._files[filename]
    assert state.status == "error"
    assert "produced no output" in (state.error or "")


def test_part_files_invisible_to_filter_and_downloads(dm, tmp_path, monkeypatch):
    (tmp_path / "berlin.osm.pbf.part").write_bytes(b"y" * 10)

    import filter_manager as fm_mod

    monkeypatch.setattr(fm_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fm_mod, "CONFIG_DIR", tmp_path / "cfg")
    from filter_manager import FilterManager

    fm = FilterManager(_StubWs())

    assert fm.list_pbf_files() == []
    assert all(f["filename"] != "berlin.osm.pbf.part" for f in dm.list_files())
