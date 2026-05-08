"""Tests for the validation guards and lifecycle endpoints in routes/filter.py.

Bug class to prevent:
  - A user submits a tag-filter request with an invalid suffix or empty
    output_formats and gets a 500 instead of a proper 422.
  - A malicious source filename ('../../etc/passwd.osm.pbf') reaches the
    osmium subprocess as a literal arg.
  - The job-listing endpoints (list, clear, cancel) regress and start
    leaking state across users or accepting unknown IDs as 200.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

_VALID = {
    "source_files": ["berlin.osm.pbf"],
    "tags": ["amenity"],
    "geometry_types": ["nodes"],
    "suffix": "test",
    "output_formats": ["gpkg"],
}


# ── suffix validator ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_suffix", ["foo bar", "foo@x", "foo!", "foo/bar", ""])
def test_invalid_suffix_returns_422(client, bad_suffix):
    body = {**_VALID, "suffix": bad_suffix}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 422


# ── output_formats validator ─────────────────────────────────────────────────


def test_empty_output_formats_returns_422(client):
    body = {**_VALID, "output_formats": []}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 422


# ── source_files filename guard ──────────────────────────────────────────────


@pytest.mark.parametrize("bad_filename", [
    "../etc.osm.pbf",
    "foo bar.osm.pbf",
    "foo.txt",
    "foo.osm",            # missing .pbf
    "foo.osm.pbf.bak",
    "/abs/path.osm.pbf",
])
def test_invalid_source_filename_returns_400(client, bad_filename):
    body = {**_VALID, "source_files": [bad_filename]}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 400


def test_check_endpoint_also_validates_source_filename(client):
    """Must apply the same guard to /check, otherwise the resolved path leaks
    into compute_output_paths and could surface secrets via 'would_overwrite'."""
    body = {**_VALID, "source_files": ["../sneaky.osm.pbf"]}
    resp = client.post("/api/filter/check", json=body)
    assert resp.status_code == 400


# ── /api/filter/files ────────────────────────────────────────────────────────


def test_list_filterable_files_returns_sorted_pbf_only(client, tmp_data_dir):
    (tmp_data_dir / "zeta.osm.pbf").touch()
    (tmp_data_dir / "alpha.osm.pbf").touch()
    (tmp_data_dir / "ignored.txt").touch()
    (tmp_data_dir / "ignored.osm.pbf.bak").touch()

    resp = client.get("/api/filter/files")
    assert resp.status_code == 200
    assert resp.json() == ["alpha.osm.pbf", "zeta.osm.pbf"]


# ── DELETE /api/filter/jobs ──────────────────────────────────────────────────


def test_clear_jobs_endpoint_purges_completed_keeps_active(client):
    """DELETE removes done/error jobs but preserves running/pending."""
    import state
    from filter_manager import FilterJob

    def _job(jid: str, status: str) -> FilterJob:
        j = FilterJob(
            id=jid, source_files=["x.osm.pbf"], tags=["a"], exclude_tags=[],
            geometry_types=["nodes"], suffix="t", output_formats=["pbf"],
            output_dir="/tmp", columns_mode="other_tags", manual_keys=[],
        )
        j.status = status
        return j

    state.filter_manager._jobs = {
        "a": _job("a", "running"),
        "b": _job("b", "pending"),
        "c": _job("c", "done"),
        "d": _job("d", "error"),
    }

    resp = client.delete("/api/filter/jobs")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert set(state.filter_manager._jobs.keys()) == {"a", "b"}


# ── POST /api/filter/cancel/{job_id} ─────────────────────────────────────────


def test_cancel_unknown_job_returns_404(client):
    resp = client.post("/api/filter/cancel/does-not-exist")
    assert resp.status_code == 404


def test_cancel_running_job_returns_cancelling(client):
    import state
    from filter_manager import FilterJob

    job = FilterJob(
        id="run-id", source_files=["x.osm.pbf"], tags=["a"], exclude_tags=[],
        geometry_types=["nodes"], suffix="t", output_formats=["pbf"],
        output_dir="/tmp", columns_mode="other_tags", manual_keys=[],
    )
    job.status = "running"
    state.filter_manager._jobs["run-id"] = job
    state.filter_manager._procs["run-id"] = MagicMock()

    resp = client.post("/api/filter/cancel/run-id")
    assert resp.status_code == 200
    assert resp.json() == {"status": "cancelling"}
    assert state.filter_manager._jobs["run-id"].status == "error"
