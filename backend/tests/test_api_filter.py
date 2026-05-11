from __future__ import annotations

_VALID_RUN = {
    "source_files": ["berlin.osm.pbf"],
    "tags": ["amenity"],
    "geometry_types": ["nodes"],
    "suffix": "test",
    "output_formats": ["gpkg"],
}


def test_filter_run_valid_returns_job_id(client):
    resp = client.post("/api/filter/run", json=_VALID_RUN)
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_filter_run_missing_tags_returns_422(client):
    body = {k: v for k, v in _VALID_RUN.items() if k != "tags"}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 422


def test_filter_run_missing_source_files_returns_422(client):
    body = {k: v for k, v in _VALID_RUN.items() if k != "source_files"}
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 422


def test_filter_check_with_conflicts(client, tmp_data_dir):
    # output_dir=None → resolves to DATA_DIR; pre-create source + expected output file
    (tmp_data_dir / "berlin.osm.pbf").touch()
    out_dir = tmp_data_dir / "gpkg"
    out_dir.mkdir()
    (out_dir / "berlin_test.gpkg").touch()

    resp = client.post("/api/filter/check", json=_VALID_RUN)
    assert resp.status_code == 200
    data = resp.json()
    assert "would_overwrite" in data
    assert len(data["would_overwrite"]) > 0


def test_filter_jobs_list(client):
    # Create a job first
    client.post("/api/filter/run", json=_VALID_RUN)
    resp = client.get("/api/filter/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


def test_filter_job_detail_by_id(client):
    run_resp = client.post("/api/filter/run", json=_VALID_RUN)
    job_id = run_resp.json()["job_id"]
    resp = client.get(f"/api/filter/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_filter_job_unknown_id_returns_404(client):
    resp = client.get("/api/filter/jobs/does-not-exist")
    assert resp.status_code == 404


def test_filter_run_with_exclude_tags(client):
    body = {
        **_VALID_RUN,
        "exclude_tags": ["railway:traffic_mode=passenger"],
    }
    resp = client.post("/api/filter/run", json=body)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Verify exclude_tags is in job detail response
    resp = client.get(f"/api/filter/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["exclude_tags"] == ["railway:traffic_mode=passenger"]
