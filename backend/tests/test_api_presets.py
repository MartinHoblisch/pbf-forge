"""API contract tests for the /api/presets CRUD endpoints."""

from __future__ import annotations

_VALID_BODY = {
    "name": "Test Preset",
    "tags": ["amenity=cafe"],
    "geometry_types": ["nodes"],
}


def test_get_presets_returns_default_list(client):
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_post_preset_valid_returns_preset_with_id(client):
    resp = client.post("/api/presets", json=_VALID_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert len(data["id"]) == 36
    assert data["name"] == "Test Preset"


def test_post_preset_missing_name_returns_422(client):
    body = {k: v for k, v in _VALID_BODY.items() if k != "name"}
    resp = client.post("/api/presets", json=body)
    assert resp.status_code == 422


def test_put_preset_updates_fields(client):
    create_resp = client.post("/api/presets", json=_VALID_BODY)
    preset_id = create_resp.json()["id"]

    updated = {**_VALID_BODY, "name": "Updated Name"}
    resp = client.put(f"/api/presets/{preset_id}", json=updated)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["id"] == preset_id


def test_put_preset_unknown_id_returns_404(client):
    resp = client.put("/api/presets/nonexistent-id", json=_VALID_BODY)
    assert resp.status_code == 404


def test_delete_preset_removes_it(client):
    create_resp = client.post("/api/presets", json=_VALID_BODY)
    preset_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/presets/{preset_id}")
    assert del_resp.status_code == 200

    # Gone from list
    presets = client.get("/api/presets").json()
    assert not any(p["id"] == preset_id for p in presets)


def test_delete_preset_unknown_id_returns_404(client):
    resp = client.delete("/api/presets/nonexistent-id")
    assert resp.status_code == 404
