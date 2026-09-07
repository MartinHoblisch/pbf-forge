"""The request that opens a transfer waits out an outage like the transfer does."""

from __future__ import annotations

import socket
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

import download_manager as dm_mod
from download_manager import PART_SUFFIX, DownloadManager

_SERVER_MTIME = datetime(2026, 9, 7, tzinfo=timezone.utc)


@pytest.fixture
def dm(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dm_mod, "URLS_FILE", tmp_path / "urls.json")
    monkeypatch.setattr(dm_mod, "SLOW_RETRY_INTERVAL_SECONDS", 0)
    manager = DownloadManager(MagicMock())
    manager.register_url("https://example.com/berlin-latest.osm.pbf", "berlin.osm.pbf")
    return manager


def _run_worker(dm, cancel=None):
    filename = "berlin.osm.pbf"
    dm._files[filename].status = "downloading"
    dm._download_worker(filename, cancel or threading.Event())
    return dm._files[filename]


def _http_error(code: int) -> requests.HTTPError:
    response = MagicMock(status_code=code, reason="Bad Gateway")
    return requests.HTTPError(response=response)


def test_a_timed_out_host_is_waited_for_not_reported_as_a_failure(dm, tmp_path):
    """The same requests.Timeout mid-transfer starts a slow retry; so must this one."""
    seen_status = []
    heads = [requests.Timeout("Read timed out. (read timeout=30)"), (700, _SERVER_MTIME)]

    def _head(url, session=None):
        result = heads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _fake_download(url, part, start, size, tracker, state, cancel, session):
        seen_status.append(state.status)
        part.write_bytes(b"x" * size)

    with patch.object(dm, "_head", side_effect=_head):
        with patch.object(dm, "_do_download", side_effect=_fake_download):
            with patch.object(dm, "_verify_checksum"):
                state = _run_worker(dm)

    assert seen_status == ["downloading"]  # the wait ended and the transfer ran
    assert state.status == "up_to_date"
    assert state.error is None


def test_an_overloaded_host_is_waited_for(dm):
    """502/503 while a host is struggling is not a reason to give up either."""
    heads = [_http_error(502), (700, _SERVER_MTIME)]

    def _head(url, session=None):
        result = heads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(dm, "_head", side_effect=_head):
        with patch.object(dm, "_do_download", side_effect=lambda *a, **k: None):
            with patch.object(dm, "_verify_checksum"):
                (dm_mod.DATA_DIR / ("berlin.osm.pbf" + PART_SUFFIX)).write_bytes(b"x" * 700)
                state = _run_worker(dm)

    assert state.status == "up_to_date"


def test_a_missing_file_still_fails_immediately(dm):
    """A 404 will not get better by waiting — the row must say so at once."""
    with patch.object(dm, "_head", side_effect=_http_error(404)):
        state = _run_worker(dm)

    assert state.status == "error"


def test_cancelling_the_wait_leaves_a_row_not_an_error(dm):
    """Cancel is the user's answer to a wait, and it is not a failure."""
    cancel = threading.Event()

    def _head(url, session=None):
        cancel.set()  # the user hits Cancel while the wait is running
        raise requests.ConnectionError("host unreachable")

    with patch.object(dm, "_head", side_effect=_head):
        state = _run_worker(dm, cancel)

    assert state.status == "unknown"  # nothing on disk to resume from
    assert state.error is None


def test_a_hostname_nobody_can_resolve_fails_at_once(dm):
    """A typo in the URL is not an outage — waiting for it would wait forever."""

    def _head(url, session=None):
        try:
            raise socket.gaierror(-2, "Name or service not known")
        except socket.gaierror as cause:
            raise requests.ConnectionError("Failed to resolve host") from cause

    with patch.object(dm, "_head", side_effect=_head):
        state = _run_worker(dm)

    assert state.status == "error"
    assert "resolve" in state.error
