"""Checksum verification must run BEFORE os.utime; a mismatch quarantines the file."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

import download_manager as dm_mod
from download_manager import DownloadManager


class _StubWs:
    async def broadcast(self, data):  # pragma: no cover - never awaited in these tests
        pass


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _Session:
    """Fake requests.Session returning a fixed .md5 body."""

    def __init__(self, md5_text):
        self._md5_text = md5_text

    def get(self, url, timeout=30):
        return _Resp(self._md5_text)


@pytest.fixture
def dm(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dm_mod, "URLS_FILE", tmp_path / "urls.json")
    return DownloadManager(_StubWs())


def test_verify_runs_before_utime(dm, tmp_path, monkeypatch):
    filename = "berlin.osm.pbf"
    mtime = datetime(2026, 1, 1, tzinfo=timezone.utc)

    order: list[str] = []
    monkeypatch.setattr(dm, "_head", lambda url, session=None: (4, mtime))

    def fake_download(*a, **k):
        # a[1] is the dest path passed by the worker (now the .part file)
        a[1].write_bytes(b"data")
        order.append("download")

    monkeypatch.setattr(dm, "_do_download", fake_download)
    monkeypatch.setattr(
        dm,
        "_verify_checksum",
        lambda url, d, session: order.append("verify"),
    )
    monkeypatch.setattr(
        dm_mod.os,
        "utime",
        lambda path, times: order.append("utime"),
    )

    dm.register_url("http://example.com/berlin-latest.osm.pbf", filename)
    state = dm._files[filename]
    state.url = "http://example.com/berlin-latest.osm.pbf"
    state.status = "downloading"

    dm._download_worker(filename, threading.Event())

    assert order == ["download", "verify", "utime"]


def test_mismatch_overwrites_existing_quarantine(dm, tmp_path):
    """Policy: a newer corrupt download overwrites an older .corrupt file."""
    dest = tmp_path / "berlin.osm.pbf"
    dest.write_bytes(b"new corrupt content")
    old = tmp_path / "berlin.osm.pbf.corrupt"
    old.write_bytes(b"old corrupt content")
    session = _Session("0" * 32 + "  berlin.osm.pbf\n")

    with pytest.raises(RuntimeError, match="MD5 mismatch"):
        dm._verify_checksum("http://example.com/berlin.osm.pbf", dest, session)

    assert not dest.exists()
    assert old.read_bytes() == b"new corrupt content"


def test_mismatch_quarantines_file(dm, tmp_path):
    dest = tmp_path / "berlin.osm.pbf"
    dest.write_bytes(b"corrupt content")
    session = _Session("0" * 32 + "  berlin.osm.pbf\n")  # guaranteed mismatch

    with pytest.raises(RuntimeError, match="MD5 mismatch"):
        dm._verify_checksum("http://example.com/berlin.osm.pbf", dest, session)

    assert not dest.exists()
    assert (tmp_path / "berlin.osm.pbf.corrupt").exists()
