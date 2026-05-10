"""Tests for routes/filesystem.py: _to_windows_path and /api/fs/browse branches.

Covers behaviors not exercised by the existing path-traversal tests:
  - _to_windows_path: drive-letter detection, nested-prefix stripping, edge cases.
  - browse_fs: missing /host_drives, non-directory target, hidden-file filtering,
    parent-path computation, /api/platform endpoint.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import routes.filesystem as fs_routes
from routes.filesystem import _is_visible, _to_windows_path

# ── _to_windows_path ──────────────────────────────────────────────────────────


def test_to_windows_path_empty_returns_empty():
    assert _to_windows_path("") == ""


def test_to_windows_path_single_drive_letter():
    assert _to_windows_path("h") == "H:\\"


def test_to_windows_path_drive_letter_uppercased():
    assert _to_windows_path("c") == "C:\\"


def test_to_windows_path_drive_with_subdirs():
    assert _to_windows_path("h/foo/bar") == "H:\\foo\\bar"


def test_to_windows_path_nested_prefix_stripped():
    """Some Docker Desktop setups nest drives under a prefix dir."""
    assert _to_windows_path("host/h/foo") == "H:\\foo"
    assert _to_windows_path("host_drives/d/data") == "D:\\data"


def test_to_windows_path_no_drive_letter_returns_empty():
    assert _to_windows_path("foo/bar/baz") == ""


def test_to_windows_path_digit_segment_not_drive():
    """Drive letter must be alphabetic; digits don't qualify."""
    assert _to_windows_path("1/foo") == ""


def test_to_windows_path_multichar_segment_not_drive():
    """First single-letter alpha segment wins; multichar segments are ignored."""
    assert _to_windows_path("foo/h") == "H:\\"


# ── _is_visible ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("regular_dir", True),
        ("Documents", True),
        (".git", False),
        (".hidden", False),
        ("$Recycle.Bin", False),
        ("$SysReset", False),
        ("normal.txt", True),
    ],
)
def test_is_visible_filter(name: str, expected: bool):
    assert _is_visible(name) is expected


# ── /api/platform endpoint ────────────────────────────────────────────────────


def test_platform_info_reports_existence(client, tmp_path):
    """Endpoint reflects whether /host_drives mount is present."""
    fake = tmp_path / "host_drives"
    fake.mkdir()
    with patch.object(fs_routes, "_HOST_DRIVES", fake):
        resp = client.get("/api/platform")
    assert resp.status_code == 200
    assert resp.json() == {"windows_host": True}


def test_platform_info_reports_missing(client, tmp_path):
    missing = tmp_path / "nope"  # never created
    with patch.object(fs_routes, "_HOST_DRIVES", missing):
        resp = client.get("/api/platform")
    assert resp.status_code == 200
    assert resp.json() == {"windows_host": False}


# ── browse_fs branches ────────────────────────────────────────────────────────


@pytest.fixture
def host_drives(tmp_path):
    d = tmp_path / "host_drives"
    d.mkdir()
    with patch.object(fs_routes, "_HOST_DRIVES", d):
        yield d


def test_browse_fs_unavailable_when_host_drives_missing(client, tmp_path):
    """No /host_drives → endpoint returns descriptive error, not 500."""
    missing = tmp_path / "no-host-drives"
    with patch.object(fs_routes, "_HOST_DRIVES", missing):
        resp = client.get("/api/fs/browse", params={"path": "h/foo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] == "Directory browser not available (only in Docker Desktop for Windows)"
    assert body["dirs"] == []


def test_browse_fs_root_lists_drive_letters(client, host_drives):
    (host_drives / "h").mkdir()
    (host_drives / "c").mkdir()
    resp = client.get("/api/fs/browse", params={"path": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["dirs"]) == ["c", "h"]
    assert body["parent"] is None


def test_browse_fs_root_shows_dotted_drive_letters(client, host_drives):
    """At root level, even drive letters starting with '.' are shown
    (drive-letter dirs created by Docker Desktop sometimes start with '.')."""
    (host_drives / ".docker_drive").mkdir()
    (host_drives / "h").mkdir()
    resp = client.get("/api/fs/browse", params={"path": ""})
    body = resp.json()
    assert ".docker_drive" in body["dirs"]
    assert "h" in body["dirs"]


def test_browse_fs_subdir_filters_hidden(client, host_drives):
    drive = host_drives / "h"
    drive.mkdir()
    (drive / ".git").mkdir()
    (drive / "$Recycle.Bin").mkdir()
    (drive / "Documents").mkdir()
    resp = client.get("/api/fs/browse", params={"path": "h"})
    body = resp.json()
    assert body["dirs"] == ["Documents"]


def test_browse_fs_target_is_file_returns_not_a_directory(client, host_drives, tmp_path):
    (host_drives / "h").mkdir()
    (host_drives / "h" / "regular_file").write_text("data")
    resp = client.get("/api/fs/browse", params={"path": "h/regular_file"})
    body = resp.json()
    assert body["error"] == "Not a directory"
    assert body["dirs"] == []


def test_browse_fs_parent_for_top_level_is_empty_string(client, host_drives):
    (host_drives / "h").mkdir()
    resp = client.get("/api/fs/browse", params={"path": "h"})
    body = resp.json()
    # parent of a top-level dir (one segment) is "", not None
    assert body["parent"] == ""


def test_browse_fs_parent_for_nested_path(client, host_drives):
    nested = host_drives / "h" / "Users" / "Estac"
    nested.mkdir(parents=True)
    resp = client.get("/api/fs/browse", params={"path": "h/Users/Estac"})
    body = resp.json()
    assert body["parent"] == str(Path("h/Users"))


def test_browse_fs_only_dirs_listed(client, host_drives):
    drive = host_drives / "h"
    drive.mkdir()
    (drive / "subdir").mkdir()
    (drive / "regular_file.txt").write_text("x")
    resp = client.get("/api/fs/browse", params={"path": "h"})
    assert resp.json()["dirs"] == ["subdir"]


def test_browse_fs_windows_path_returned(client, host_drives):
    drive = host_drives / "h"
    drive.mkdir()
    (drive / "Data").mkdir()
    resp = client.get("/api/fs/browse", params={"path": "h/Data"})
    body = resp.json()
    assert body["windows_path"] == "H:\\Data"
