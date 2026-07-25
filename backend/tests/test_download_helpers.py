"""Additional download_manager tests focused on guard behaviors and helpers
not covered by test_download_manager.py:

  - _retry_delay: Retry-After header parsing (numeric, http-date, missing)
  - MAX_DOWNLOAD_SIZE cap (refusal of pathologically large downloads)
  - Disk-space pre-flight guard
  - register_url branches (new file, existing file, already-known filename)
  - _refresh_local_files cleanup of vanished files (active downloads protected)
  - _load_url_mapping with corrupt URLS_FILE
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import config as cfg_module
import download_manager as dm_module
from download_manager import DownloadManager, FileState, _retry_delay

_OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _make_dm() -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


# ── _retry_delay ─────────────────────────────────────────────────────────────


def test_retry_delay_no_response_uses_exponential_backoff():
    assert _retry_delay(None, 0) == 1.0  # 2^0
    assert _retry_delay(None, 3) == 8.0  # 2^3


def test_retry_delay_numeric_header_used_directly():
    resp = MagicMock()
    resp.headers = {"Retry-After": "15"}
    assert _retry_delay(resp, 0) == 15.0


def test_retry_delay_numeric_header_capped_at_max():
    resp = MagicMock()
    resp.headers = {"Retry-After": "9999"}
    # Cap is MAX_RETRY_AFTER_SECONDS (60s)
    assert _retry_delay(resp, 0) == float(cfg_module.MAX_RETRY_AFTER_SECONDS)


def test_retry_delay_http_date_header_falls_back_to_backoff():
    """HTTP-date format ('Wed, 21 Oct 2026 07:28:00 GMT') triggers ValueError
    on float() — guard must catch it and fall through to exponential backoff."""
    resp = MagicMock()
    resp.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert _retry_delay(resp, 2) == 4.0  # 2^2


def test_retry_delay_missing_header_uses_backoff():
    resp = MagicMock()
    resp.headers = {}
    assert _retry_delay(resp, 1) == 2.0  # 2^1


# ── MAX_DOWNLOAD_SIZE cap ────────────────────────────────────────────────────


def test_download_worker_refuses_oversized_file(tmp_data_dir):
    """A hostile mirror reporting Content-Length > 100 GB → status=error,
    osmium never invoked, no partial file written."""
    filename = "huge.osm.pbf"
    dm = _make_dm()
    dm._url_mapping[filename] = "https://evil.example.com/huge.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading"
        )
    oversized = cfg_module.MAX_DOWNLOAD_SIZE + 1

    with patch.object(dm, "_head", return_value=(oversized, _OLD)):
        with patch.object(dm, "_do_download") as do_dl:
            with patch.object(dm, "_verify_checksum") as verify:
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "Refusing to download" in dm._files[filename].error
    do_dl.assert_not_called()
    verify.assert_not_called()


# ── Disk-space guard ─────────────────────────────────────────────────────────


def test_download_worker_refuses_when_disk_full(tmp_data_dir):
    """Pre-flight: shutil.disk_usage.free < (size + buffer) → status=error,
    download never attempted."""
    filename = "big.osm.pbf"
    dm = _make_dm()
    dm._url_mapping[filename] = "https://example.com/big.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading"
        )

    fake_disk = MagicMock()
    fake_disk.free = 100  # bytes — definitely not enough
    with patch.object(dm, "_head", return_value=(1024 * 1024 * 1024, _OLD)):  # 1 GB
        with patch.object(dm_module.shutil, "disk_usage", return_value=fake_disk):
            with patch.object(dm, "_do_download") as do_dl:
                dm._download_worker(filename, threading.Event())

    with dm._lock:
        assert dm._files[filename].status == "error"
        assert "Insufficient disk space" in dm._files[filename].error
    do_dl.assert_not_called()


def test_download_worker_proceeds_when_disk_check_oserrors(tmp_data_dir):
    """If shutil.disk_usage raises OSError (e.g. mount unavailable on weird
    filesystems), the guard must log/skip rather than block legitimate
    downloads. `free` becomes None → check is skipped."""
    filename = "ok.osm.pbf"
    # _do_download is mocked; pre-create the .part file so os.replace
    # (part → dest) succeeds in the success path.
    part = tmp_data_dir / (filename + ".part")
    part.write_bytes(b"x" * 1000)

    dm = _make_dm()
    dm._url_mapping[filename] = "https://example.com/ok.osm.pbf"
    with dm._lock:
        dm._files[filename] = FileState(
            filename=filename, url=dm._url_mapping[filename], status="downloading"
        )

    with patch.object(dm, "_head", return_value=(1000, _OLD)):
        with patch.object(dm_module.shutil, "disk_usage", side_effect=OSError("nope")):
            with patch.object(dm, "_do_download") as do_dl:
                with patch.object(dm, "_verify_checksum"):
                    dm._download_worker(filename, threading.Event())

    do_dl.assert_called_once()
    with dm._lock:
        assert dm._files[filename].status == "up_to_date"


# ── register_url branches ────────────────────────────────────────────────────


def test_register_url_new_filename_no_local_file(tmp_data_dir):
    dm = _make_dm()
    dm.register_url("https://example.com/foo.osm.pbf", "foo.osm.pbf")

    with dm._lock:
        state = dm._files["foo.osm.pbf"]
    assert state.url == "https://example.com/foo.osm.pbf"
    assert state.status == "not_downloaded"
    assert state.local_size is None


def test_register_url_new_filename_with_existing_local_file(tmp_data_dir):
    """If the file already exists on disk, populate local_size/local_mtime
    so the user sees correct status immediately."""
    (tmp_data_dir / "foo.osm.pbf").write_bytes(b"x" * 500)

    dm = _make_dm()
    dm.register_url("https://example.com/foo.osm.pbf", "foo.osm.pbf")

    with dm._lock:
        state = dm._files["foo.osm.pbf"]
    assert state.local_size == 500
    assert state.local_mtime is not None


def test_register_url_existing_filename_updates_only_url(tmp_data_dir):
    """Re-registering an already-known filename must update URL but preserve
    other state (status, local_size)."""
    dm = _make_dm()
    with dm._lock:
        dm._files["foo.osm.pbf"] = FileState(
            filename="foo.osm.pbf",
            url="https://OLD.example.com/foo.osm.pbf",
            status="up_to_date",
            local_size=999,
        )

    dm.register_url("https://NEW.example.com/foo.osm.pbf", "foo.osm.pbf")

    with dm._lock:
        state = dm._files["foo.osm.pbf"]
    assert state.url == "https://NEW.example.com/foo.osm.pbf"
    assert state.status == "up_to_date"  # not reset
    assert state.local_size == 999


def test_register_url_persists_to_urls_file(tmp_data_dir):
    """Custom (non-CONTINENTAL) URL is saved to URLS_FILE on disk."""
    dm = _make_dm()
    dm.register_url("https://example.com/custom.osm.pbf", "custom.osm.pbf")

    stored = json.loads(cfg_module.URLS_FILE.read_text(encoding="utf-8"))
    assert stored["custom.osm.pbf"] == "https://example.com/custom.osm.pbf"


def test_register_url_does_not_persist_continental_defaults(tmp_data_dir):
    """CONTINENTAL_URLS entries shouldn't pollute the on-disk custom-URLs file."""
    dm = _make_dm()
    dm.register_url(
        "https://download.geofabrik.de/europe-latest.osm.pbf",
        "europe.osm.pbf",
    )
    if cfg_module.URLS_FILE.exists():
        stored = json.loads(cfg_module.URLS_FILE.read_text(encoding="utf-8"))
        assert "europe.osm.pbf" not in stored


# ── _refresh_local_files cleanup ─────────────────────────────────────────────


def test_refresh_removes_vanished_idle_files(tmp_data_dir):
    f = tmp_data_dir / "ghost.osm.pbf"
    f.write_bytes(b"x")

    dm = _make_dm()
    assert "ghost.osm.pbf" in dm._files

    f.unlink()
    dm._refresh_local_files()

    assert "ghost.osm.pbf" not in dm._files


def test_refresh_keeps_active_download_state_after_file_vanishes(tmp_data_dir):
    """A racing rm during an active download must not drop the state — the
    worker thread still owns it."""
    f = tmp_data_dir / "active.osm.pbf"
    f.write_bytes(b"x")

    dm = _make_dm()
    with dm._lock:
        dm._files["active.osm.pbf"].status = "downloading"

    f.unlink()
    dm._refresh_local_files()

    # Still tracked because status="downloading"
    assert "active.osm.pbf" in dm._files


# ── _load_url_mapping with corrupt JSON ──────────────────────────────────────


def test_load_url_mapping_with_corrupt_file_falls_back_to_continental(tmp_data_dir):
    cfg_module.URLS_FILE.write_text("{this is not json", encoding="utf-8")

    dm = _make_dm()  # __init__ calls _load_url_mapping

    # Continental defaults still present despite corrupt file
    assert "europe.osm.pbf" in dm._url_mapping
    # Corrupt file did not get parsed into mapping
    assert "this" not in dm._url_mapping
