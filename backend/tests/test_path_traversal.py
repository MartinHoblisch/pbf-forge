"""Tests that user-supplied paths cannot escape the allowed directories.

Covers output_dir on filter requests and the host-drive browser, including
symlinks pointing outside the browsable root.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

_VALID_BODY = {
    "source_files": ["berlin.osm.pbf"],
    "tags": ["amenity"],
    "geometry_types": ["nodes"],
    "suffix": "test",
    "output_formats": ["gpkg"],
}


# ── /api/filter/run output_dir traversal ─────────────────────────────────────


def test_traversal_dotdot_rejected(client):
    body = {**_VALID_BODY, "output_dir": "../../../etc"}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 400


def test_traversal_dotslash_rejected(client):
    body = {**_VALID_BODY, "output_dir": "./../../outside"}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "evil_dir",
    [
        "../escape",
        "subdir/../../etc",
        "a;b",
        "subdir;rm -rf /",
        "$(whoami)",
        "<script>",
        "subdir\x00null",
        "abs/../../../root",
        "with space",
        ".hidden",
    ],
)
def test_output_dir_allowlist_rejects_unsafe_chars(client, evil_dir):
    body = {**_VALID_BODY, "output_dir": evil_dir}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 400


def test_valid_subdir_accepted(client, tmp_data_dir):
    body = {**_VALID_BODY, "output_dir": "valid/subdir"}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code != 400
    assert "job_id" in resp.json()


# ── /api/fs/browse path traversal ────────────────────────────────────────────


@pytest.fixture
def mock_host_drives(tmp_path):
    host_drives = tmp_path / "host_drives"
    host_drives.mkdir()
    with patch("routes.filesystem._HOST_DRIVES", host_drives):
        yield host_drives


def test_fs_browse_outside_allowed_returns_error(client, mock_host_drives):
    resp = client.get("/api/fs/browse", params={"path": "../../../etc"})
    assert resp.status_code == 200
    assert "error" in resp.json()


@pytest.mark.parametrize(
    "evil_path",
    [
        "..",
        "h/../..",
        "a;b",
        "$(whoami)",
        "<script>",
        "h\x00null",
        "with space",
        "h/../etc",
    ],
)
def test_fs_browse_allowlist_rejects_unsafe_chars(client, mock_host_drives, evil_path):
    resp = client.get("/api/fs/browse", params={"path": evil_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("error") == "Invalid path"
    assert body["dirs"] == []


@pytest.mark.posix
def test_fs_browse_symlink_outside_returns_error(client, mock_host_drives, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = mock_host_drives / "evil_link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported")

    resp = client.get("/api/fs/browse", params={"path": "evil_link/../../../etc"})
    assert resp.status_code == 200
    assert "error" in resp.json()
