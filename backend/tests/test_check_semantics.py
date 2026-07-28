"""Tests for what a "Check" is allowed to do to a row.

A check refreshes the server-side figures of a file. It must not repurpose the
status column, because that column also drives the progress bar and the cancel
button, and the download worker only ever writes byte counters back to it. It
must also report the directory as it is right now rather than what this process
last remembered, and both entry points — one file or all of them — have to agree
on every value they produce.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

from download_manager import PART_SUFFIX, DownloadManager, FileState

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


# ── A cancelled transfer keeps its row so it can be resumed ──────────────────


def _paused_by_cancel(tmp_data_dir, written: int = 400) -> tuple[DownloadManager, FileState]:
    """Run a worker that writes some bytes and is then cancelled."""
    filename = "test.osm.pbf"
    part = tmp_data_dir / (filename + PART_SUFFIX)
    dm = _make_dm()
    dm._url_mapping[filename] = _URL
    with dm._lock:
        dm._files[filename] = FileState(filename=filename, url=_URL, status="downloading")
        state = dm._files[filename]

    def _write_then_cancel(url, dest, start_byte, size, tracker, st, c, session):
        part.write_bytes(b"x" * written)
        c.set()

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        with patch.object(dm, "_do_download", side_effect=_write_then_cancel):
            dm._download_worker(filename, threading.Event())
    return dm, state


def test_cancel_leaves_a_paused_row_carrying_its_partial(tmp_data_dir):
    dm, state = _paused_by_cancel(tmp_data_dir)

    assert "test.osm.pbf" in dm._files
    with dm._lock:
        assert state.status == "paused"
        assert state.partial_bytes == 400
        # The partial must never masquerade as a usable local file.
        assert state.local_size is None


def test_refresh_keeps_a_row_whose_only_artifact_is_a_part_file(tmp_data_dir):
    dm, _ = _paused_by_cancel(tmp_data_dir)

    dm._refresh_local_files()

    assert "test.osm.pbf" in dm._files
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "paused"
        assert dm._files["test.osm.pbf"].partial_bytes == 400


def test_refresh_discovers_a_part_file_with_nothing_in_memory(tmp_data_dir):
    """The restart case: the process is gone but the resume base is still there."""
    (tmp_data_dir / ("europe.osm.pbf" + PART_SUFFIX)).write_bytes(b"x" * 700)

    dm = _make_dm()  # __init__ runs _refresh_local_files

    assert "europe.osm.pbf" in dm._files
    with dm._lock:
        state = dm._files["europe.osm.pbf"]
        assert state.status == "paused"
        assert state.partial_bytes == 700
        assert state.local_size is None
        # Resolved from the bundled continental URLs, so it stays resumable.
        assert state.url == "https://download.geofabrik.de/europe-latest.osm.pbf"


def test_check_keeps_a_paused_row_paused(tmp_data_dir):
    dm, state = _paused_by_cancel(tmp_data_dir)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert state.status == "paused"  # not "not_downloaded"
        assert state.partial_bytes == 400
        assert state.server_size == 1000


def test_deleting_the_part_file_drops_the_paused_row(tmp_data_dir):
    dm, _ = _paused_by_cancel(tmp_data_dir)
    (tmp_data_dir / ("test.osm.pbf" + PART_SUFFIX)).unlink()

    with patch.object(dm, "_broadcast") as bc:
        with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)) as head:
            dm.check_file("test.osm.pbf")

    assert "test.osm.pbf" not in dm._files
    head.assert_not_called()
    assert bc.call_args_list == [(({"type": "file_removed", "filename": "test.osm.pbf"},),)]


def test_paused_row_can_be_resumed_from_its_partial(tmp_data_dir):
    dm, _ = _paused_by_cancel(tmp_data_dir)
    captured = {}

    def _capture(url, dest, start_byte, size, tracker, st, c, session):
        captured["start_byte"] = start_byte

    assert dm.start_download("test.osm.pbf") is True
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        with patch.object(dm, "_do_download", side_effect=_capture):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker("test.osm.pbf", threading.Event())

    assert captured["start_byte"] == 400  # picked up where the cancel left off
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "up_to_date"


def test_completing_a_download_clears_the_partial(tmp_data_dir):
    filename = "test.osm.pbf"
    (tmp_data_dir / (filename + PART_SUFFIX)).write_bytes(b"x" * 1000)
    dm = _make_dm()
    dm._url_mapping[filename] = _URL
    with dm._lock:
        dm._files[filename] = FileState(filename=filename, url=_URL, status="downloading")
        dm._files[filename].partial_bytes = 1000

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        with patch.object(dm, "_do_download"):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        state = dm._files[filename]
        assert state.status == "up_to_date"
        assert state.partial_bytes is None
        assert state.local_size == 1000


# ── A paused update survives a check ─────────────────────────────────────────
#
# Cancelling an update leaves the previous complete file next to the .part of
# the new one. Judging that row by the complete file alone reports it as
# update_available and takes the progress bar away, even though the partial is
# exactly the progress towards that update.

_OLD_BUILD = datetime(2024, 1, 1, tzinfo=timezone.utc)
_PART_WRITTEN = datetime(2024, 6, 2, tzinfo=timezone.utc)  # after _SERVER_MTIME


def _stamp(path, when: datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


def _cancelled_update(tmp_data_dir, part_written: datetime = _PART_WRITTEN):
    """An outdated complete file plus the .part of the update that was cancelled."""
    complete = tmp_data_dir / "test.osm.pbf"
    complete.write_bytes(b"x" * 800)
    _stamp(complete, _OLD_BUILD)

    part = tmp_data_dir / ("test.osm.pbf" + PART_SUFFIX)
    part.write_bytes(b"x" * 300)
    _stamp(part, part_written)

    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL
    with dm._lock:
        dm._files["test.osm.pbf"] = FileState(filename="test.osm.pbf", url=_URL, status="paused")
    return dm


def test_check_keeps_a_cancelled_update_paused(tmp_data_dir):
    dm = _cancelled_update(tmp_data_dir)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        state = dm._files["test.osm.pbf"]
        assert state.status == "paused"
        assert state.partial_bytes == 300  # what the progress bar is drawn from


def test_check_all_keeps_a_cancelled_update_paused(tmp_data_dir):
    dm = _cancelled_update(tmp_data_dir)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_all()

    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "paused"


def test_a_directory_scan_keeps_a_cancelled_update_paused(tmp_data_dir):
    """Listing the files must not undo it either — no server figures are involved."""
    dm = _cancelled_update(tmp_data_dir)

    rows = {row["filename"]: row for row in dm.list_files()}

    assert rows["test.osm.pbf"]["status"] == "paused"
    assert rows["test.osm.pbf"]["partial_bytes"] == 300


def test_check_gives_up_on_a_partial_the_server_has_built_past(tmp_data_dir):
    """A newer build on the server is what ends the resume, and only that."""
    dm = _cancelled_update(tmp_data_dir, part_written=datetime(2024, 5, 1, tzinfo=timezone.utc))

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "update_available"


def test_a_scan_stops_reporting_paused_once_the_partial_is_gone(tmp_data_dir):
    dm = _cancelled_update(tmp_data_dir)
    (tmp_data_dir / ("test.osm.pbf" + PART_SUFFIX)).unlink()

    dm.list_files()

    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "unknown"


# ── A scan keeps what a check established ────────────────────────────────────
#
# The verdict of a check is a function of four figures the row already carries.
# Throwing it away on every directory scan reset each checked row to "unknown"
# on every page load, while its own local and server columns went on showing
# the result of the check that supposedly never happened.


_CURRENT_BUILD = datetime(2024, 7, 1, tzinfo=timezone.utc)  # after _SERVER_MTIME


def _checked(tmp_data_dir, local_bytes: int, local_written: datetime, server=(1000, None)):
    complete = tmp_data_dir / "test.osm.pbf"
    complete.write_bytes(b"x" * local_bytes)
    _stamp(complete, local_written)

    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL
    size, mtime = server[0], server[1] or _SERVER_MTIME
    with patch.object(dm, "_head", return_value=(size, mtime)):
        dm.check_file("test.osm.pbf")
    return dm


def test_a_scan_keeps_an_up_to_date_verdict(tmp_data_dir):
    dm = _checked(tmp_data_dir, 1000, _CURRENT_BUILD)
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "up_to_date"

    rows = {row["filename"]: row for row in dm.list_files()}

    assert rows["test.osm.pbf"]["status"] == "up_to_date"


def test_a_scan_keeps_an_update_available_verdict(tmp_data_dir):
    dm = _checked(tmp_data_dir, 800, datetime(2023, 1, 1, tzinfo=timezone.utc))
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "update_available"

    rows = {row["filename"]: row for row in dm.list_files()}

    assert rows["test.osm.pbf"]["status"] == "update_available"


def test_a_scan_leaves_a_row_that_was_never_checked_unknown(tmp_data_dir):
    """Nothing to re-derive from — inventing a verdict would be worse."""
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 1000)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL

    rows = {row["filename"]: row for row in dm.list_files()}

    assert rows["test.osm.pbf"]["status"] == "unknown"


def test_a_scan_re_derives_the_verdict_rather_than_repeating_it(tmp_data_dir):
    """The point is the figures, not the label: a file that shrank on disk is
    no longer up to date, and the scan has to say so without a second check."""
    dm = _checked(tmp_data_dir, 1000, _CURRENT_BUILD)
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 400)

    rows = {row["filename"]: row for row in dm.list_files()}

    assert rows["test.osm.pbf"]["status"] == "update_available"


def test_a_complete_file_beside_a_stray_part_is_not_paused(tmp_data_dir):
    (tmp_data_dir / "test.osm.pbf").write_bytes(b"x" * 1000)
    (tmp_data_dir / ("test.osm.pbf" + PART_SUFFIX)).write_bytes(b"x" * 50)
    dm = _make_dm()
    dm._url_mapping["test.osm.pbf"] = _URL

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME)):
        dm.check_file("test.osm.pbf")

    with dm._lock:
        state = dm._files["test.osm.pbf"]
        assert state.status == "up_to_date"  # the finished file wins
        assert state.local_size == 1000
        assert state.partial_bytes == 50  # still reported, just not decisive
