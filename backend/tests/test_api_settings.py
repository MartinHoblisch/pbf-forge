"""API contract tests for the /api/settings endpoints."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import config
import routes.settings as settings_module
import update_check


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


# ── Update check ──────────────────────────────────────────────────────────────


def test_get_update_check_reports_the_running_version(client, monkeypatch):
    monkeypatch.setattr(update_check.requests, "get", MagicMock(side_effect=OSError("offline")))

    data = client.get("/api/update-check").json()

    assert data["enabled"] is True
    assert data["current"] == config.VERSION
    assert data["update_available"] is False


def test_update_check_can_be_switched_off(client, monkeypatch):
    get = MagicMock(side_effect=AssertionError("a disabled check must not ask GitHub"))
    monkeypatch.setattr(update_check.requests, "get", get)

    assert client.post("/api/update-check", json={"enabled": False}).status_code == 200

    cfg = json.loads(settings_module.USER_CONFIG_FILE.read_text(encoding="utf-8"))
    assert cfg["update_check"] is False
    assert client.get("/api/update-check").json()["enabled"] is False


def test_update_check_can_be_switched_back_on(client, monkeypatch):
    monkeypatch.setattr(update_check.requests, "get", MagicMock(side_effect=OSError("offline")))
    client.post("/api/update-check", json={"enabled": False})

    client.post("/api/update-check", json={"enabled": True})

    assert client.get("/api/update-check").json()["enabled"] is True


def test_post_update_check_rejects_a_missing_flag(client):
    assert client.post("/api/update-check", json={}).status_code == 422
