"""Real-method tests for download_manager._do_download (HTTP protocol behavior).

Existing test_download_manager.py mocks _do_download wholesale and thus never
exercises:
  - Range-header generation for resume
  - 416 (Range Not Satisfiable) early-return path
  - Server returning 200 even though we asked for a partial range → must
    restart the download fresh, NOT append to the existing partial file
  - Cancel flag honored mid-stream (chunks after cancel must not be written)
  - State updates (downloaded_bytes, speed_bps, eta_seconds) during stream
  - Cancel between fast-retry attempts
  - Slow-retry success after the network returns

The bug class: a corrupted resume that silently doubles the file, or a runaway
slow-retry that never ends, is invisible to the existing tests.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

import download_manager as dm_module
from download_manager import DownloadManager, FileState, _SpeedTracker

_OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _make_dm(tmp_data_dir) -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


def _state(filename: str) -> FileState:
    return FileState(filename=filename, url="https://example.com/x.osm.pbf",
                     status="downloading")


def _streaming_response(status_code: int, chunks: list[bytes]) -> MagicMock:
    """Build a context-manager mock that mimics `session.get(..., stream=True)`."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=iter(chunks))

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=None)
    return cm


def _session_returning(*responses: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(side_effect=list(responses))
    return session


# ── _SpeedTracker.reset (extension to test_speed_tracker.py) ─────────────────


def test_speed_tracker_reset_clears_samples_and_sets_total():
    tracker = _SpeedTracker()
    tracker.add_bytes(1000)
    tracker.add_bytes(500)
    assert tracker.total == 1500

    tracker.reset(start=200)
    assert tracker.total == 200
    # Samples cleared → speed back to 0.0 with no history
    assert tracker.speed_bps() == 0.0


# ── _do_download fresh start ─────────────────────────────────────────────────


def test_do_download_starts_fresh_without_range_header(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    state = _state("x.osm.pbf")
    tracker = _SpeedTracker()
    cancel = threading.Event()

    session = _session_returning(_streaming_response(200, [b"hello"]))
    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=0, total_size=5,
                    tracker=tracker, state=state, cancel=cancel, session=session)

    # No Range header on fresh start
    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs["headers"] == {}
    assert dest.read_bytes() == b"hello"


# ── _do_download resume ──────────────────────────────────────────────────────


def test_do_download_resume_sets_range_header_and_appends(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    dest.write_bytes(b"AAAAA")  # existing partial
    state = _state("x.osm.pbf")
    tracker = _SpeedTracker()

    session = _session_returning(_streaming_response(206, [b"BBB"]))
    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=5, total_size=8,
                    tracker=tracker, state=state, cancel=threading.Event(),
                    session=session)

    assert session.get.call_args.kwargs["headers"] == {"Range": "bytes=5-"}
    assert dest.read_bytes() == b"AAAAABBB"


# ── _do_download 416 early return ────────────────────────────────────────────


def test_do_download_416_returns_early_no_write(tmp_data_dir):
    """416 = file is already complete on disk. Must NOT touch the file."""
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    dest.write_bytes(b"COMPLETE")
    original = dest.read_bytes()

    session = _session_returning(_streaming_response(416, [b"ignored"]))
    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=8, total_size=8,
                    tracker=_SpeedTracker(), state=_state("x.osm.pbf"),
                    cancel=threading.Event(), session=session)

    assert dest.read_bytes() == original


# ── _do_download 200 during resume → restart fresh ───────────────────────────


def test_do_download_200_during_resume_resets_to_full_write(tmp_data_dir):
    """Server ignored Range and returned 200 + full body. Must overwrite,
    NOT append, otherwise the file ends up double-prefixed and corrupt."""
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    dest.write_bytes(b"OLDCONTENT")
    tracker = _SpeedTracker()

    session = _session_returning(_streaming_response(200, [b"FRESH"]))
    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=10, total_size=5,
                    tracker=tracker, state=_state("x.osm.pbf"),
                    cancel=threading.Event(), session=session)

    assert dest.read_bytes() == b"FRESH"
    # tracker reset to 0 on the 200 path (verify via internal total)
    assert tracker.total == 5  # only the new chunk's bytes


# ── _do_download cancel mid-stream ───────────────────────────────────────────


def test_do_download_cancel_mid_stream_stops_writing(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    cancel = threading.Event()

    chunks = [b"AAA", b"BBB", b"CCC"]

    def gated_iter(chunk_size):
        yield chunks[0]
        cancel.set()  # user clicks cancel
        yield chunks[1]  # _do_download must check the flag and return
        yield chunks[2]  # never reached

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(side_effect=gated_iter)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=None)
    session = _session_returning(cm)

    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=0, total_size=9,
                    tracker=_SpeedTracker(), state=_state("x.osm.pbf"),
                    cancel=cancel, session=session)

    # Only the first chunk was written
    assert dest.read_bytes() == b"AAA"


# ── _do_download state updates ───────────────────────────────────────────────


def test_do_download_updates_state_during_stream(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dest = tmp_data_dir / "x.osm.pbf"
    state = _state("x.osm.pbf")
    tracker = _SpeedTracker()

    session = _session_returning(_streaming_response(200, [b"X" * 100]))
    dm._do_download("https://example.com/x.osm.pbf", dest,
                    start_byte=0, total_size=100,
                    tracker=tracker, state=state,
                    cancel=threading.Event(), session=session)

    assert state.downloaded_bytes == 100


# ── Cancel between fast-retry attempts ───────────────────────────────────────


def test_fast_retry_loop_breaks_when_cancelled_between_attempts(tmp_data_dir):
    """503 → backoff sleep → cancel during sleep → loop exits without 2nd
    attempt. Covers the `if cancel.is_set(): break` at line 405-406."""
    filename = "test.osm.pbf"
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping[filename] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading")
    cancel = threading.Event()

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.reason = "Service Unavailable"
    mock_resp.headers = {}
    http_error = requests.HTTPError(response=mock_resp)

    do_download_mock = MagicMock(side_effect=http_error)

    def cancel_during_sleep(_seconds):
        cancel.set()

    with patch.object(dm, "_head", return_value=(1000, _OLD)):
        with patch.object(dm, "_do_download", do_download_mock):
            with patch.object(dm_module.time, "sleep", side_effect=cancel_during_sleep):
                dm._download_worker(filename, cancel)

    # 1 attempt happened, then sleep ran (cancel set), then loop broke
    assert do_download_mock.call_count == 1
    with dm._lock:
        # Cancelled mid-retry → status becomes "unknown" via the cancel path,
        # OR remains "downloading" depending on timing. Worst case "error" must
        # NOT happen because cancel preempted.
        status = dm._files[filename].status
    assert status in ("unknown", "downloading")


# ── Slow-retry recovery after network returns ────────────────────────────────


def test_slow_retry_recovers_after_network_returns(tmp_data_dir):
    """First _do_download raises ConnectionError → enters slow retry loop →
    waits → second attempt succeeds → status flips to up_to_date."""
    filename = "test.osm.pbf"
    dest = tmp_data_dir / filename
    dest.write_bytes(b"")  # _do_download is mocked; touch dest so os.utime works

    dm = _make_dm(tmp_data_dir)
    dm._url_mapping[filename] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading")

    waiting_retry_seen = []
    original_broadcast = dm._broadcast

    def capture(msg):
        if msg.get("type") == "file_update":
            if msg["file"].get("status") == "waiting_retry":
                waiting_retry_seen.append(True)
        original_broadcast(msg)

    call_count = {"n": 0}

    def fake_do_download(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.ConnectionError("offline")
        # Second call: success — no raise

    def fake_wait(timeout=None):
        return False  # event was NOT set → wait expired → retry

    with patch.object(dm, "_head", return_value=(0, _OLD)):
        with patch.object(dm, "_do_download", side_effect=fake_do_download):
            with patch.object(dm, "_verify_checksum"):
                with patch.object(dm, "_broadcast", side_effect=capture):
                    cancel = threading.Event()
                    with patch.object(cancel, "wait", side_effect=fake_wait):
                        dm._download_worker(filename, cancel)

    assert call_count["n"] == 2  # initial fail + recovery
    assert waiting_retry_seen, "status 'waiting_retry' was never broadcast"
    with dm._lock:
        assert dm._files[filename].status == "up_to_date"
        assert dm._files[filename].retry_at is None


# ── Slow-retry: still offline → continue (covers lines 468-473) ──────────────


def test_slow_retry_continues_when_still_offline(tmp_data_dir):
    """First fast attempt fails with ConnectionError → enters slow loop. The
    first slow attempt ALSO fails → must continue waiting; second slow
    attempt succeeds."""
    filename = "test.osm.pbf"
    dest = tmp_data_dir / filename
    dest.write_bytes(b"")

    dm = _make_dm(tmp_data_dir)
    dm._url_mapping[filename] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading")

    call_count = {"n": 0}

    def fake_do_download(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            # First (fast loop) and first slow attempt both fail
            raise requests.ConnectionError("offline")
        # Third attempt succeeds

    with patch.object(dm, "_head", return_value=(0, _OLD)):
        with patch.object(dm, "_do_download", side_effect=fake_do_download):
            with patch.object(dm, "_verify_checksum"):
                cancel = threading.Event()
                with patch.object(cancel, "wait", return_value=False):  # always wake up to retry
                    dm._download_worker(filename, cancel)

    assert call_count["n"] == 3  # 1 fast + 2 slow
    with dm._lock:
        assert dm._files[filename].status == "up_to_date"


# ── Slow-retry: non-network exception bubbles up (covers lines 474-475) ──────


def test_slow_retry_bubbles_other_exception(tmp_data_dir):
    """Inside the slow loop, _do_download raises a non-network exception
    (e.g. unexpected ValueError). Must bubble up to the outer handler and
    mark status=error — NOT continue spinning the slow loop forever."""
    filename = "test.osm.pbf"
    dest = tmp_data_dir / filename
    dest.write_bytes(b"")

    dm = _make_dm(tmp_data_dir)
    dm._url_mapping[filename] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading")

    call_count = {"n": 0}

    def fake_do_download(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.ConnectionError("offline")  # enters slow loop
        # Second call (inside slow loop) — non-network failure
        raise ValueError("unexpected programming error")

    with patch.object(dm, "_head", return_value=(0, _OLD)):
        with patch.object(dm, "_do_download", side_effect=fake_do_download):
            cancel = threading.Event()
            with patch.object(cancel, "wait", return_value=False):
                dm._download_worker(filename, cancel)

    assert call_count["n"] == 2
    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "unexpected programming error" in dm._files[filename].error
