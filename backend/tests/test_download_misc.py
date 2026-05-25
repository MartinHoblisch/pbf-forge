"""Misc download_manager edge cases not covered elsewhere:
- get_url_info shape + already_exists flag
- SSL error during download (must not retry)
- _save_url_mapping disk failure (must log+swallow)
- check_file early return when no URL resolvable
- check_file partial-local-size → update_available
- start_download early returns (already downloading, no URL)
- register_url branch where file is on disk but not yet tracked
- _head: real method behavior (Content-Length/Last-Modified parsing)
- /api/check with explicit filenames dispatches per-file (routes/downloads:67-68)
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import requests

from download_manager import DownloadManager, FileState

_OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _make_dm() -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


# ── get_url_info ─────────────────────────────────────────────────────────────


def test_get_url_info_returns_structured_dict(tmp_data_dir):
    dm = _make_dm()
    url = "https://download.geofabrik.de/europe-latest.osm.pbf"
    with patch.object(dm, "_head", return_value=(123_456_789, _OLD)):
        info = dm.get_url_info(url)

    assert info["url"] == url
    assert info["filename"] == "europe.osm.pbf"
    assert info["server_size"] == 123_456_789
    assert info["server_mtime"] == _OLD.isoformat()
    assert info["already_exists"] is False


def test_get_url_info_already_exists_true_when_local_file_present(tmp_data_dir):
    (tmp_data_dir / "europe.osm.pbf").write_bytes(b"x")
    dm = _make_dm()
    with patch.object(dm, "_head", return_value=(1, _OLD)):
        info = dm.get_url_info("https://download.geofabrik.de/europe-latest.osm.pbf")
    assert info["already_exists"] is True


# ── SSL error path ───────────────────────────────────────────────────────────


def test_ssl_error_during_download_marks_error_no_retry(tmp_data_dir):
    """SSLError is non-retryable (cert mismatch, expired CA): must fail
    immediately without burning the retry budget."""
    filename = "test.osm.pbf"
    dm = _make_dm()
    dm._url_mapping[filename] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading"
        )

    do_download_mock = MagicMock(side_effect=requests.exceptions.SSLError("bad cert"))

    with patch.object(dm, "_head", return_value=(1000, _OLD)):
        with patch.object(dm, "_do_download", do_download_mock):
            dm._download_worker(filename, threading.Event())

    assert do_download_mock.call_count == 1  # no retries
    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "SSL" in dm._files[filename].error


# ── _save_url_mapping disk failure ───────────────────────────────────────────


def test_save_url_mapping_oserror_does_not_raise(tmp_data_dir):
    """If URLS_FILE write fails (read-only mount), the in-memory mapping is
    still updated and no exception bubbles up to the caller."""
    dm = _make_dm()
    with patch("pathlib.Path.write_text", side_effect=OSError("read-only fs")):
        # Should not raise
        dm.register_url("https://example.com/foo.osm.pbf", "foo.osm.pbf")

    assert dm._url_mapping["foo.osm.pbf"] == "https://example.com/foo.osm.pbf"


# ── check_file ───────────────────────────────────────────────────────────────


def test_check_file_skips_when_no_url_resolvable(tmp_data_dir):
    """A filename with no entry in CONTINENTAL_URLS or _url_mapping must
    not even hit the network."""
    dm = _make_dm()
    with patch.object(dm, "_head") as head:
        dm.check_file("totally-unknown-region.osm.pbf")
    head.assert_not_called()


def test_check_file_partial_local_size_marks_update_available(tmp_data_dir):
    """local_size=500 < server_size=1000 with same mtime → update_available
    (covers the final else-branch in check_file)."""
    f = tmp_data_dir / "europe.osm.pbf"
    f.write_bytes(b"x" * 500)

    dm = _make_dm()
    dm._url_mapping["europe.osm.pbf"] = "https://example.com/europe.osm.pbf"
    with dm._lock:
        # local_mtime equal to server_mtime forces fall-through to size check
        dm._files["europe.osm.pbf"].local_mtime = _OLD.isoformat()
        dm._files["europe.osm.pbf"].local_size = 500

    with patch.object(dm, "_head", return_value=(1000, _OLD)):
        dm.check_file("europe.osm.pbf")

    with dm._lock:
        assert dm._files["europe.osm.pbf"].status == "update_available"


# ── start_download early returns ─────────────────────────────────────────────


def test_start_download_returns_false_when_already_downloading(tmp_data_dir):
    dm = _make_dm()
    dm._url_mapping["x.osm.pbf"] = "https://example.com/x.osm.pbf"
    with dm._lock:
        dm._files["x.osm.pbf"] = FileState(filename="x.osm.pbf", status="downloading")

    with patch.object(dm._executor, "submit") as submit:
        assert dm.start_download("x.osm.pbf") is False
    submit.assert_not_called()


def test_start_download_returns_false_when_no_url_resolvable(tmp_data_dir):
    dm = _make_dm()
    with dm._lock:
        # File is tracked but has no URL — and no mapping entry to fall back to
        dm._files["mystery.osm.pbf"] = FileState(filename="mystery.osm.pbf")

    with patch.object(dm._executor, "submit") as submit:
        assert dm.start_download("mystery.osm.pbf") is False
    submit.assert_not_called()


# ── register_url with file on disk but not tracked ───────────────────────────


def test_register_url_populates_size_when_on_disk_but_not_tracked(tmp_data_dir):
    """File exists in DATA_DIR but is somehow not in _files (e.g. user
    manually dropped a file after server start). register_url must populate
    local_size/local_mtime."""
    (tmp_data_dir / "manual.osm.pbf").write_bytes(b"x" * 777)

    dm = _make_dm()
    # _refresh_local_files in __init__ would have picked it up; remove to
    # simulate the "not yet tracked" branch
    with dm._lock:
        dm._files.pop("manual.osm.pbf", None)

    dm.register_url("https://example.com/manual.osm.pbf", "manual.osm.pbf")

    with dm._lock:
        state = dm._files["manual.osm.pbf"]
    assert state.local_size == 777
    assert state.local_mtime is not None


# ── _head real method ────────────────────────────────────────────────────────


def test_head_parses_content_length_and_last_modified(tmp_data_dir):
    dm = _make_dm()
    expected_mtime = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    resp = MagicMock()
    resp.headers = {
        "Content-Length": "12345",
        "Last-Modified": format_datetime(expected_mtime, usegmt=True),
    }
    resp.raise_for_status = MagicMock()

    session = MagicMock()
    session.head = MagicMock(return_value=resp)

    size, mtime = dm._head("https://example.com/x.osm.pbf", session=session)
    assert size == 12345
    assert mtime == expected_mtime


def test_head_uses_default_session_when_none_passed(tmp_data_dir):
    dm = _make_dm()
    resp = MagicMock()
    resp.headers = {"Content-Length": "100"}
    resp.raise_for_status = MagicMock()

    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=session_cm)
    session_cm.__exit__ = MagicMock(return_value=None)
    session_cm.head = MagicMock(return_value=resp)

    with patch.object(dm, "_new_session", return_value=session_cm):
        size, mtime = dm._head("https://example.com/x.osm.pbf")

    assert size == 100
    assert mtime is None  # no Last-Modified header → None, not a fallback datetime


def test_head_returns_none_when_no_last_modified(tmp_data_dir):
    """Server omits Last-Modified → mtime is None, not a fallback datetime."""
    dm = _make_dm()
    resp = MagicMock()
    resp.headers = {"Content-Length": "1"}
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.head = MagicMock(return_value=resp)

    _, mtime = dm._head("https://example.com/x.osm.pbf", session=session)
    assert mtime is None


# ── /api/check with explicit filenames ───────────────────────────────────────


def test_post_check_with_explicit_filenames_dispatches_per_file(client):
    """POST /api/check with {"filenames": [...]} must call dm.check_file once
    per filename via background_tasks, NOT call check_all (covers
    routes/downloads.py:67-68)."""
    import state

    with patch.object(state.download_manager, "check_file") as per_file:
        with patch.object(state.download_manager, "check_all") as all_files:
            resp = client.post("/api/check", json={"filenames": ["a.osm.pbf", "b.osm.pbf"]})

    assert resp.status_code == 200
    assert per_file.call_count == 2
    all_files.assert_not_called()
