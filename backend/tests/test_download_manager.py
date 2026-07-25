"""Tests for DownloadManager: freshness checks, resume offsets, worker outcomes."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from download_manager import DownloadManager, FileState


def _make_dm(tmp_data_dir) -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


_SERVER_MTIME_OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
_SERVER_MTIME_NEW = datetime(2099, 1, 1, tzinfo=timezone.utc)


# ── Status transitions ────────────────────────────────────────────────────────


def test_initial_status_unknown(tmp_data_dir):
    (tmp_data_dir / "europe.osm.pbf").touch()
    dm = _make_dm(tmp_data_dir)
    with dm._lock:
        assert dm._files["europe.osm.pbf"].status == "unknown"


def test_check_file_not_downloaded(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping["test.osm.pbf"] = "https://example.com/test.osm.pbf"
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        dm.check_file("test.osm.pbf")
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "not_downloaded"


def test_check_file_up_to_date(tmp_data_dir):
    f = tmp_data_dir / "europe.osm.pbf"
    f.write_bytes(b"x" * 1000)
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping["europe.osm.pbf"] = "https://example.com/europe.osm.pbf"
    local_mtime_str = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
    with dm._lock:
        dm._files["europe.osm.pbf"].local_mtime = local_mtime_str
        dm._files["europe.osm.pbf"].local_size = 1000
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        dm.check_file("europe.osm.pbf")
    with dm._lock:
        assert dm._files["europe.osm.pbf"].status == "up_to_date"


def test_check_file_update_available(tmp_data_dir):
    f = tmp_data_dir / "europe.osm.pbf"
    f.write_bytes(b"x" * 1000)
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping["europe.osm.pbf"] = "https://example.com/europe.osm.pbf"
    with dm._lock:
        dm._files["europe.osm.pbf"].local_mtime = _SERVER_MTIME_OLD.isoformat()
        dm._files["europe.osm.pbf"].local_size = 1000
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_NEW)):
        dm.check_file("europe.osm.pbf")
    with dm._lock:
        assert dm._files["europe.osm.pbf"].status == "update_available"


def test_check_file_error_on_connection_error(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping["test.osm.pbf"] = "https://example.com/test.osm.pbf"
    with dm._lock:
        dm._files["test.osm.pbf"] = FileState(filename="test.osm.pbf")
    with patch.object(dm, "_head", side_effect=requests.ConnectionError("refused")):
        dm.check_file("test.osm.pbf")
    with dm._lock:
        assert dm._files["test.osm.pbf"].status == "error"


def test_start_download_sets_status_downloading(tmp_data_dir):
    (tmp_data_dir / "europe.osm.pbf").touch()
    dm = _make_dm(tmp_data_dir)
    dm._url_mapping["europe.osm.pbf"] = "https://example.com/europe.osm.pbf"
    dm._refresh_local_files()
    with patch.object(dm._executor, "submit"):
        dm.start_download("europe.osm.pbf")
    with dm._lock:
        assert dm._files["europe.osm.pbf"].status == "downloading"


def test_cancel_download_sets_cancel_flag(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    flag = threading.Event()
    with dm._lock:
        dm._cancel_flags["europe.osm.pbf"] = flag
    dm.cancel_download("europe.osm.pbf")
    assert flag.is_set()


# ── Resume logic ──────────────────────────────────────────────────────────────


def _setup_downloading(dm, filename, url="https://example.com/test.osm.pbf"):
    dm._url_mapping[filename] = url
    with dm._lock:
        dm._files[filename] = FileState(filename=filename, url=url, status="downloading")
    return threading.Event()


def _call_worker(dm, filename, cancel, head_return, do_download_side_effect=None):
    captured = {}

    def fake_do(url, dest, start_byte, size, tracker, state, c, session):
        captured["start_byte"] = start_byte
        if do_download_side_effect:
            do_download_side_effect(url, dest, start_byte, size, tracker, state, c, session)

    with patch.object(dm, "_head", return_value=head_return):
        with patch.object(dm, "_do_download", side_effect=fake_do):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker(filename, cancel)
    return captured


def test_no_local_file_start_byte_zero(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, "test.osm.pbf")
    captured = {}

    def fake_do(url, dest, start_byte, size, tracker, state, c, session):
        captured["start_byte"] = start_byte

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download", side_effect=fake_do):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker("test.osm.pbf", threading.Event())
    assert captured["start_byte"] == 0


def test_partial_download_start_byte_equals_local_size(tmp_data_dir):
    filename = "test.osm.pbf"
    # Resume reads from the .part file, not from the final destination.
    part = tmp_data_dir / (filename + ".part")
    part.write_bytes(b"x" * 500)

    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)

    captured = {}

    def fake_do(url, dest, start_byte, size, tracker, state, c, session):
        captured["start_byte"] = start_byte
        dest.write_bytes(b"x" * 1000)  # create part so os.replace succeeds

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download", side_effect=fake_do):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker(filename, threading.Event())
    assert captured["start_byte"] == 500


def test_same_version_becomes_up_to_date(tmp_data_dir):
    filename = "test.osm.pbf"
    # _do_download writes to .part; os.replace then moves it to dest.
    # Simulate a complete-file scenario by pre-creating the .part file so
    # the success path (os.replace + stat) works.
    part = tmp_data_dir / (filename + ".part")
    part.write_bytes(b"x" * 1000)

    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)

    # _do_download does nothing (simulates 416 scenario: complete .part → skip)
    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download"):
            with patch.object(dm, "_verify_checksum"):
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "up_to_date"


def test_http_5xx_exhausted_sets_status_error(tmp_data_dir):
    """Transient HTTP errors (5xx) exhaust MAX_RETRIES fast retries then mark error."""
    import download_manager as dm_module

    filename = "test.osm.pbf"
    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.reason = "Service Unavailable"
    mock_resp.headers = {}
    http_error = requests.HTTPError(response=mock_resp)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download", side_effect=http_error):
            with patch.object(dm_module.time, "sleep"):  # skip backoff sleeps
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "503" in dm._files[filename].error


def test_http_404_fails_immediately_no_retry(tmp_data_dir):
    """Permanent HTTP errors (404) fail on first attempt — no retries."""
    import download_manager as dm_module

    filename = "test.osm.pbf"
    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.reason = "Not Found"
    mock_resp.headers = {}
    http_error = requests.HTTPError(response=mock_resp)

    do_download_mock = MagicMock(side_effect=http_error)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download", do_download_mock):
            with patch.object(dm_module.time, "sleep"):
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "404" in dm._files[filename].error
        assert "permanent" in dm._files[filename].error
    assert do_download_mock.call_count == 1  # no retries


def test_connection_error_sets_waiting_retry_then_cancel_exits(tmp_data_dir):
    """ConnectionError triggers slow retry loop; cancel during wait ends cleanly."""
    filename = "test.osm.pbf"
    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)
    cancel = threading.Event()
    waiting_retry_seen = []

    original_broadcast = dm._broadcast

    def capture_broadcast(msg):
        if msg.get("type") == "file_update":
            s = msg["file"].get("status")
            if s == "waiting_retry":
                waiting_retry_seen.append(True)
        original_broadcast(msg)

    def fake_wait(timeout=None):
        # Simulate user cancelling while waiting for slow retry
        cancel.set()
        return True  # True = event was set (cancelled)

    with patch.object(dm, "_head", return_value=(1000, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download", side_effect=requests.ConnectionError("net down")):
            with patch.object(dm, "_broadcast", side_effect=capture_broadcast):
                with patch.object(cancel, "wait", side_effect=fake_wait):
                    dm._download_worker(filename, cancel)

    with dm._lock:
        assert dm._files[filename].status == "unknown"  # cancelled → unknown
    assert waiting_retry_seen, "status 'waiting_retry' was never broadcast"


# ── Concurrency ───────────────────────────────────────────────────────────────


def test_concurrent_downloads_have_own_cancel_flags(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    files = ["africa.osm.pbf", "europe.osm.pbf", "asia.osm.pbf"]
    for f in files:
        (tmp_data_dir / f).touch()
    dm._refresh_local_files()

    with patch.object(dm._executor, "submit"):
        for f in files:
            dm.start_download(f)

    with dm._lock:
        flags = [dm._cancel_flags.get(f) for f in files]
    assert all(isinstance(f, threading.Event) for f in flags)
    assert len(set(id(f) for f in flags)) == 3  # each is distinct


def test_cancel_only_affects_target(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    flag_a = threading.Event()
    flag_b = threading.Event()
    with dm._lock:
        dm._cancel_flags["a.osm.pbf"] = flag_a
        dm._cancel_flags["b.osm.pbf"] = flag_b
    dm.cancel_download("a.osm.pbf")
    assert flag_a.is_set()
    assert not flag_b.is_set()


def test_fourth_download_accepted_not_simultaneous(tmp_data_dir):
    dm = _make_dm(tmp_data_dir)
    files = [f"{c}.osm.pbf" for c in ["a", "b", "c", "d"]]
    for f in files:
        (tmp_data_dir / f).touch()
        dm._url_mapping[f] = f"https://example.com/{f}"
    dm._refresh_local_files()

    submitted = []

    def tracking_submit(fn, *args, **kwargs):
        submitted.append(args[0])  # filename
        # don't actually run
        return MagicMock()

    with patch.object(dm._executor, "submit", side_effect=tracking_submit):
        for f in files:
            dm.start_download(f)

    assert len(submitted) == 4


def test_register_and_start_skips_if_already_downloading(tmp_data_dir):
    """register_and_start must return False and not submit a second worker
    when the filename is already being downloaded."""
    dm = _make_dm(tmp_data_dir)
    (tmp_data_dir / "test.osm.pbf").touch()
    dm._refresh_local_files()

    submitted_count = 0

    def counting_submit(fn, *args, **kwargs):
        nonlocal submitted_count
        submitted_count += 1
        return MagicMock()

    with patch.object(dm._executor, "submit", side_effect=counting_submit):
        r1 = dm.register_and_start("https://example.com/test.osm.pbf", "test.osm.pbf")
        r2 = dm.register_and_start("https://example.com/test.osm.pbf", "test.osm.pbf")

    assert r1 is True
    assert r2 is False
    assert submitted_count == 1


# ── Checksum verification ─────────────────────────────────────────────────────


def _make_session_with_md5(hex_str: str, filename: str = "test.osm.pbf") -> MagicMock:
    """Return a mock requests.Session whose .get() returns a valid .md5 response."""
    resp = MagicMock()
    resp.text = f"{hex_str}  {filename}\n"
    resp.raise_for_status = MagicMock()
    session = MagicMock()
    session.get.return_value = resp
    return session


def test_verify_checksum_passes(tmp_data_dir):
    content = b"hello geofabrik"
    dest = tmp_data_dir / "test.osm.pbf"
    dest.write_bytes(content)
    expected = hashlib.md5(content).hexdigest()

    dm = _make_dm(tmp_data_dir)
    session = _make_session_with_md5(expected)
    # Should not raise
    dm._verify_checksum("https://example.com/test.osm.pbf", dest, session)
    session.get.assert_called_once_with("https://example.com/test.osm.pbf.md5", timeout=30)


def test_verify_checksum_mismatch_raises(tmp_data_dir):
    dest = tmp_data_dir / "test.osm.pbf"
    dest.write_bytes(b"corrupted data")
    wrong_hex = "a" * 32  # valid length, wrong value

    dm = _make_dm(tmp_data_dir)
    session = _make_session_with_md5(wrong_hex)
    with pytest.raises(RuntimeError, match="MD5 mismatch"):
        dm._verify_checksum("https://example.com/test.osm.pbf", dest, session)


def test_verify_checksum_network_error_raises(tmp_data_dir):
    dest = tmp_data_dir / "test.osm.pbf"
    dest.write_bytes(b"data")

    dm = _make_dm(tmp_data_dir)
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("refused")
    with pytest.raises(RuntimeError, match="Could not fetch checksum"):
        dm._verify_checksum("https://example.com/test.osm.pbf", dest, session)


def test_verify_checksum_bad_md5_content_raises(tmp_data_dir):
    dest = tmp_data_dir / "test.osm.pbf"
    dest.write_bytes(b"data")

    dm = _make_dm(tmp_data_dir)
    session = _make_session_with_md5("tooshort")
    with pytest.raises(RuntimeError, match="Unexpected .md5 content"):
        dm._verify_checksum("https://example.com/test.osm.pbf", dest, session)


def test_download_worker_checksum_failure_marks_error(tmp_data_dir):
    """Checksum mismatch after download → status error, not up_to_date."""
    filename = "test.osm.pbf"
    dest = tmp_data_dir / (filename + ".part")
    dest.write_bytes(b"x" * 100)

    dm = _make_dm(tmp_data_dir)
    _setup_downloading(dm, filename)

    with patch.object(dm, "_head", return_value=(100, _SERVER_MTIME_OLD)):
        with patch.object(dm, "_do_download"):
            with patch.object(
                dm,
                "_verify_checksum",
                side_effect=RuntimeError("MD5 mismatch for test.osm.pbf: expected aaa, got bbb"),
            ):
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "MD5 mismatch" in dm._files[filename].error


def test_urls_migrate_from_data_dir(tmp_path, monkeypatch):
    """Custom URLs in DATA_DIR are copied to CONFIG_DIR on first load."""
    import json

    import config as cfg
    import download_manager as dm

    tmp_data = tmp_path / "data"
    tmp_data.mkdir(exist_ok=True)
    tmp_cfg = tmp_path / "config"
    tmp_cfg.mkdir(exist_ok=True)

    old_file = tmp_data / ".osm_tool_urls.json"
    new_file = tmp_cfg / ".osm_tool_urls.json"
    old_file.write_text(
        json.dumps({"custom.osm.pbf": "https://example.com/custom.osm.pbf"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_data)
    monkeypatch.setattr(cfg, "URLS_FILE", new_file)
    monkeypatch.setattr(dm, "DATA_DIR", tmp_data)
    monkeypatch.setattr(dm, "URLS_FILE", new_file)

    manager = dm.DownloadManager(ws_manager=MagicMock())
    manager._load_url_mapping()

    assert new_file.exists()
    assert "custom.osm.pbf" in manager._url_mapping
    assert old_file.exists()  # original not deleted
