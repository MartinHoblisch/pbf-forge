"""Tests for routes/settings._read_config fallback when USER_CONFIG_FILE is
missing or corrupt.

Bug class to prevent: a user with a hand-edited (or partially-written, e.g.
power-loss-truncated) user-config.json hits GET /api/settings and gets a 500
instead of the safe defaults that would let them re-run the setup wizard.
"""

from __future__ import annotations


def test_get_settings_no_config_file_returns_defaults(client):
    # USER_CONFIG_FILE doesn't exist (reset_state patches to a non-existent path)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["host_data_dir"] == ""
    assert body["pending_restart"] is False
    assert "startup_time" in body  # always set


def test_get_settings_corrupt_config_returns_defaults(client):
    """Bad JSON in USER_CONFIG_FILE → log warning, fall through to defaults,
    serve a 200 so the frontend can guide the user back into setup."""
    import routes.settings as rs

    rs.USER_CONFIG_FILE.write_text("{not json", encoding="utf-8")

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["host_data_dir"] == ""
    assert body["pending_restart"] is False
