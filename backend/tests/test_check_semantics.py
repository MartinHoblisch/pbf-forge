"""Tests for what a "Check" is allowed to do to a row.

A check refreshes the server-side figures of a file. It must not repurpose the
status column, because that column also drives the progress bar and the cancel
button, and the download worker only ever writes byte counters back to it. It
must also report the directory as it is right now rather than what this process
last remembered, and both entry points — one file or all of them — have to agree
on every value they produce.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

from download_manager import DownloadManager, FileState

_SERVER_MTIME = datetime(2024, 6, 1, tzinfo=timezone.utc)
_URL = "https://example.com/test.osm.pbf"


def _make_dm() -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


def _dm_with_transfer(tmp_data_dir, status: str) -> tuple[DownloadManager, FileState]:
    """A manager with one file mid-transfer: only a .part exists on disk."""
    (tmp_data_dir / "test.osm.pbf.part").write_bytes(b"x" * 400)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL
    with dm._lock:
        dm._files["test.osm.pbf"] = FileState(filename="test.osm.pbf", url=_URL, status=status)
        state = dm._files["test.osm.pbf"]
        state.downloaded_bytes = 400
        state.speed_bps = 1234.0
        state.eta_seconds = 42.0
    return dm, state


# ── A check must not disturb a running transfer ──────────────────────────────


def test_check_keeps_downloading_status_and_progress(tmp_data_dir):
    dm, state = _dm_with_transfer(tmp_data_dir, "downloading")

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert state.status == "downloading"
        assert state.downloaded_bytes == 400
        assert state.speed_bps == 1234.0
        assert state.eta_seconds == 42.0
        # The point of the check: the server columns are refreshed regardless.
        assert state.server_size == 1000
        assert state.server_mtime == _SERVER_MTIME.isoformat()


def test_check_keeps_waiting_retry_status(tmp_data_dir):
    dm, state = _dm_with_transfer(tmp_data_dir, "waiting_retry")

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert state.status == "waiting_retry"
        assert state.server_size == 1000


def test_check_never_broadcasts_a_non_downloading_status_mid_transfer(tmp_data_dir):
    """Guards the actual user-visible symptom: the progress bar is rendered only
    while the broadcast status is "downloading"."""
    dm, _ = _dm_with_transfer(tmp_data_dir, "downloading")

    with patch.object(dm, "_broadcast") as bc:
        with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
            dm.check_file("test.osm.pbf")

    updates = [c.args[0] for c in bc.call_args_list if c.args[0]["type"] == "file_update"]
    assert updates, "expected at least one file_update"
    assert all(u["file"]["status"] == "downloading" for u in updates)


def test_check_failure_during_transfer_does_not_raise_an_error_badge(tmp_data_dir):
    dm, state = _dm_with_transfer(tmp_data_dir, "downloading")

    with patch.object(dm, "_broadcast") as bc:
        with patch.object(dm, "_head", side_effect=requests.ConnectionError("refused")):
            dm.check_file("test.osm.pbf")

    with dm._lock:
        assert state.status == "downloading"
        assert state.error is None
    assert all(c.args[0]["file"]["status"] == "downloading" for c in bc.call_args_list)


def test_check_failure_while_idle_still_reports_error(tmp_data_dir):
    """The lenient path above must not swallow errors on files nobody is moving."""
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 500)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL

    with patch.object(dm, "_head", side_effect=requests.ConnectionError("refused")):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "error"
        assert "refused" in dm._files["test.osm.pbf"].error


def test_check_skips_status_when_download_starts_during_the_head(tmp_data_dir):
    """A HEAD may take up to 30 seconds. The status read before it is stale by
    the time the response arrives, so the write has to re-read it."""
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 500)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL

    def _head_then_start(url, session=None):
        with dm._lock:
            dm._files["test.osm.pbf"].status = "downloading"
        return 1000, _SERVER_MTIME

    with patch.object(dm, "_head", side_effect=_head_then_start):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        state = dm._files["test.osm.pbf"]
        assert state.status == "downloading"
        assert state.server_size == 1000


# ── A check must report the directory as it is now ───────────────────────────


def test_check_drops_the_row_when_the_file_was_deleted(tmp_data_dir):
    f = tmp_data_dir / "test.osm.pbf"
    f.write_bytes(b"x" * 500)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL
    assert "test.osm.pbf" in dm._files

    f.unlink()
    with patch.object(dm, "_broadcast") as bc:
        with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)) as head:
            dm.check_file("test.osm.pbf")

    assert "test.osm.pbf" not in dm._files
    head.assert_not_called()
    assert bc.call_args_list == [(({"type": "file_removed", "filename": "test.osm.pbf"},),)]


def test_check_picks_up_a_changed_file_without_a_full_refresh(tmp_data_dir):
    f = tmp_data_dir / "test.osm.pbf"
    f.write_bytes(b"x" * 500)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL

    f.write_bytes(b"x" * 900)
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert dm._files["test.osm.pbf"].local_size == 900


def test_check_one_and_check_all_agree_on_every_column(tmp_data_dir):
    """The two buttons used to read local state through different code paths,
    which made them disagree on the dates they showed."""
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 500)
    fields = ("local_size", "local_mtime", "server_size", "server_mtime", "url", "status")

    dm_one = _make_dm()
    dm_one._url_mapping["test.osm.pbf"] = _URL
    with patch.object(dm_one, "_head", return_value=(1000, _SERVER_MTIME)):
        dm_one.check_file("test.osm.pbf")

    dm_all = _make_dm()
    dm_all._url_mapping["test.osm.pbf"] = _URL
    with patch.object(dm_all, "_head", return_value=(1000, _SERVER_MTIME)):
        dm_all.check_all()

    one = dm_one._files["test.osm.pbf"].to_dict()
    every = dm_all._files["test.osm.pbf"].to_dict()
    assert {k: one[k] for k in fields} == {k: every[k] for k in fields}


def test_refresh_keeps_waiting_retry_entry_after_file_vanishes(tmp_data_dir):
    """A slow-retry loop owns its row just as much as an active transfer does;
    pruning it makes the download disappear from the table while it is alive."""
    f = tmp_data_dir / "retry.osm.pbf"
    f.write_bytes(b"x")
    dm = _make_dm()
    with dm._lock:
        dm._files["retry.osm.pbf"].status = "waiting_retry"

    f.unlink()
    dm._refresh_local_files()

    assert "retry.osm.pbf" in dm._files


def test_refresh_announces_removed_files(tmp_data_dir):
    f = tmp_data_dir / "ghost.osm.pbf"
    f.write_bytes(b"x")
    dm = _make_dm()

    f.unlink()
    with patch.object(dm, "_broadcast") as bc:
        dm._refresh_local_files()

    assert bc.call_args_list == [(({"type": "file_removed", "filename": "ghost.osm.pbf"},),)]


# ── Worker guards ────────────────────────────────────────────────────────────


def test_start_download_refuses_while_a_worker_still_holds_a_cancel_flag(tmp_data_dir):
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 500)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL
    with dm._lock:
        # Status drifted away from "downloading", but the worker is still alive.
        dm._files["test.osm.pbf"].status = "not_downloaded"
        dm._cancel_flags["test.osm.pbf"] = threading.Event()

    with patch.object(dm._executor, "submit") as submit:
        assert dm.start_download("test.osm.pbf") is False
    submit.assert_not_called()


def test_download_worker_survives_a_missing_last_modified(tmp_data_dir):
    filename = "nomtime.osm.pbf"
    (tmp_data_dir / (filename + ".part")).write_bytes(b"x" * 1000)
    dm = _make_dm()
    dm._url_mapping[filename] = "https://example.com/nomtime.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading"
        )

    with patch.object(dm, "_head", return_value=(1000, None)):
        with patch.object(dm, "_do_download"):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        state = dm._files[filename]
        assert state.status == "up_to_date"
        assert state.server_mtime is None
