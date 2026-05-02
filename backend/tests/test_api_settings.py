from __future__ import annotations

import json

import routes.settings as settings_module


def test_get_settings_returns_200(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "configured" in data
    assert "startup_time" in data


def test_post_settings_writes_user_config(client, tmp_data_dir):
    resp = client.post("/api/settings", json={"host_data_dir": "H:\\mydata"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    cfg = json.loads(settings_module.USER_CONFIG_FILE.read_text(encoding="utf-8"))
    assert cfg["host_data_dir"] == "H:\\mydata"
    assert cfg["configured"] is True
    assert cfg["pending_restart"] is True


def test_post_settings_invalid_path_characters_returns_422(client):
    resp = client.post("/api/settings", json={"host_data_dir": "H:\\data; rm -rf /"})
    assert resp.status_code == 422


def test_post_settings_empty_path_returns_422(client):
    resp = client.post("/api/settings", json={"host_data_dir": ""})
    assert resp.status_code == 422
