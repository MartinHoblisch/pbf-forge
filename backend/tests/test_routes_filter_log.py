"""Tests for GET /api/filter/jobs/{job_id}/log endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

import state
from filter_manager import FilterManager


def _seed_fm() -> FilterManager:
    fm = FilterManager(AsyncMock())
    state.filter_manager = fm
    return fm


def test_get_log_returns_content(client, tmp_config_dir):
    fm = _seed_fm()
    job = fm.create_job(
        source_files=["x.osm.pbf"],
        tags=["a"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["pbf"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    job.append_log("hello disk log\n")
    job.close_log()

    res = client.get(f"/api/filter/jobs/{job.id}/log")
    assert res.status_code == 200
    assert "hello disk log" in res.text
    assert res.headers["content-type"].startswith("text/plain")


def test_get_log_missing_returns_404(client, tmp_config_dir):
    _seed_fm()
    # UUID-like but no file
    res = client.get("/api/filter/jobs/00000000-0000-0000-0000-000000000000/log")
    assert res.status_code == 404


def test_get_log_invalid_id_returns_400(client, tmp_config_dir):
    _seed_fm()
    # FastAPI path matching prevents slashes from reaching the handler,
    # but local regex still rejects shell-special chars.
    res = client.get("/api/filter/jobs/bad..id/log")
    assert res.status_code == 400
