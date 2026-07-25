"""API contract tests for the download endpoints under /api."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

_MTIME = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_get_files_returns_200_with_list(client):
    resp = client.get("/api/files")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_post_check_returns_200(client):
    resp = client.post("/api/check", json={})
    assert resp.status_code == 200


def test_post_download_known_name_returns_200(client, tmp_data_dir):
    (tmp_data_dir / "europe.osm.pbf").touch()
    import state

    state.download_manager._url_mapping["europe.osm.pbf"] = "https://example.com/europe.osm.pbf"
    state.download_manager._refresh_local_files()

    with patch.object(state.download_manager._executor, "submit"):
        resp = client.post("/api/download", json={"filenames": ["europe.osm.pbf"]})
    assert resp.status_code == 200
    assert "europe.osm.pbf" in resp.json()["started"]


def test_post_download_unknown_name_not_in_started(client):
    resp = client.post("/api/download", json={"filenames": ["does-not-exist.osm.pbf"]})
    assert resp.status_code == 200
    assert "does-not-exist.osm.pbf" not in resp.json()["started"]


def test_post_cancel_returns_200(client):
    resp = client.post("/api/cancel", json={"filename": "europe.osm.pbf"})
    assert resp.status_code == 200


def test_post_url_info_valid_url_returns_size_and_filename(client):
    import state

    with patch.object(
        state.download_manager,
        "get_url_info",
        return_value={
            "url": "https://example.com/test-latest.osm.pbf",
            "filename": "test.osm.pbf",
            "server_size": 12345,
            "server_mtime": _MTIME.isoformat(),
            "already_exists": False,
        },
    ):
        resp = client.post("/api/url-info", json={"url": "https://example.com/test-latest.osm.pbf"})

    assert resp.status_code == 200
    data = resp.json()
    assert "server_size" in data
    assert "filename" in data


def test_post_url_info_invalid_url_returns_400(client):
    resp = client.post("/api/url-info", json={"url": "ftp://example.com/test.osm.pbf"})
    assert resp.status_code == 400
