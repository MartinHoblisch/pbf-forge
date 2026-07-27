"""Tests for main._is_configured and the WS-endpoint Origin guard at the
endpoint level (not just the pure-function level).

Bug class to prevent:
  - Lifespan triggers _delayed_check_all → check_all whether or not the user
    has finished the setup wizard. If _is_configured returns the wrong answer,
    the WS endpoint may broadcast partial state before files exist, or the
    setup wizard never lets the user finish.
  - A corrupt user config (hand-edited bad JSON) crashes lifespan startup.
  - WebSocket endpoint accepts an evil-Origin browser handshake despite
    _is_allowed_origin returning False — the close-1008 path must be live.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main as main_module
from main import _is_configured, app


def _write_config(payload: dict | str) -> None:
    if isinstance(payload, str):
        main_module.USER_CONFIG_FILE.write_text(payload, encoding="utf-8")
    else:
        main_module.USER_CONFIG_FILE.write_text(json.dumps(payload), encoding="utf-8")


# ── _is_configured ───────────────────────────────────────────────────────────


def test_is_configured_no_file_returns_false(tmp_config_dir):
    # USER_CONFIG_FILE patched to non-existent path by reset_state
    assert _is_configured() is False


def test_is_configured_corrupt_file_returns_false(tmp_config_dir):
    """Hand-edited bad JSON must not crash lifespan startup — log warning,
    return False so check_all is skipped."""
    _write_config("{not json")
    assert _is_configured() is False


def test_is_configured_true_when_configured_no_pending(tmp_config_dir):
    _write_config({"configured": True, "host_data_dir": "H:\\d", "pending_restart": False})
    assert _is_configured() is True


def test_is_configured_false_when_pending_restart(tmp_config_dir):
    """User changed host_data_dir → setup not yet effective → check_all skipped."""
    _write_config({"configured": True, "host_data_dir": "H:\\d", "pending_restart": True})
    assert _is_configured() is False


def test_is_configured_false_when_configured_false(tmp_config_dir):
    """Fresh install — user hasn't run setup yet."""
    _write_config({"configured": False, "host_data_dir": "", "pending_restart": False})
    assert _is_configured() is False


def test_is_configured_false_when_field_missing(tmp_config_dir):
    """Partial config (no `configured` key) → defaults to False."""
    _write_config({"host_data_dir": "H:\\d"})
    assert _is_configured() is False


# ── /ws endpoint Origin guard ────────────────────────────────────────────────


def test_ws_endpoint_rejects_evil_origin(client):
    """A browser with Origin: http://evil.com tries to open /ws — must close
    immediately with 1008 before any state leak."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws", headers={"origin": "http://evil.com"}) as ws:
            ws.receive_json()  # would block forever if accepted; close fires first
    assert exc_info.value.code == 1008


def test_ws_endpoint_accepts_loopback_origin(client):
    """Origin: http://localhost — must connect normally and send the initial
    files+filter_jobs frames."""
    with client.websocket_connect("/ws", headers={"origin": "http://localhost:5000"}) as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["type"] == "files"
    assert second["type"] == "filter_jobs"


def test_ws_endpoint_accepts_missing_origin(client):
    """Non-browser clients send no Origin header → must connect (covers the
    `if not origin` branch in _is_allowed_origin via the endpoint)."""
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
    assert first["type"] == "files"


# ── / index route ────────────────────────────────────────────────────────────


def test_index_route_returns_html(client):
    """GET / must return the SPA shell — verifies the route is registered
    *before* the StaticFiles mount which otherwise would catch '/'."""
    resp = client.get("/")
    assert resp.status_code == 200
    # FileResponse sets text/html for .html
    assert "text/html" in resp.headers.get("content-type", "")


def test_frontend_is_served_with_revalidation(client):
    """The single-page frontend must never be served from cache unchecked.

    Without Cache-Control a browser applies heuristic freshness — about 10% of
    the file's age — and can keep showing the previous build for hours after an
    update without ever asking the server, which looks like the update never
    arrived.
    """
    for path in ("/", "/index.html"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers.get("cache-control") == "no-cache", path


def test_static_frontend_revalidation_is_cheap(client):
    """A revalidated static asset comes back as a bodyless 304, not a resend."""
    etag = client.get("/index.html").headers["etag"]
    resp = client.get("/index.html", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.content == b""


# ── lifespan: _delayed_check_all triggered when configured ───────────────────


def test_lifespan_runs_real_delayed_check_all_when_configured(reset_state, tmp_config_dir):
    """End-to-end: lifespan dispatches the REAL _delayed_check_all (no patch
    on the function itself), only asyncio.sleep is shortened so the test
    doesn't take 500ms. This exercises the actual body lines (sleep →
    get_running_loop → run_in_executor → check_all) — not just the wiring."""
    import asyncio
    import threading
    from unittest.mock import patch

    _write_config({"configured": True, "host_data_dir": "H:\\d", "pending_restart": False})

    called = threading.Event()

    def fake_check_all(self_dm):
        called.set()

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        # Only short-circuit the 0.5s startup delay; let other awaits flow
        await real_sleep(0)

    with patch("download_manager.DownloadManager.check_all", fake_check_all):
        with patch("main.asyncio.sleep", fast_sleep):
            with TestClient(app):
                assert called.wait(timeout=2.0), "check_all was never invoked"


def test_lifespan_does_not_check_all_when_not_configured(reset_state, tmp_config_dir):
    """Inverse: setup wizard not done → check_all must NOT run, otherwise
    download_manager would hit Geofabrik before the user has chosen a data
    dir, surface confusing errors, and waste bandwidth."""
    import asyncio
    import threading
    from unittest.mock import patch

    # No config file → _is_configured() returns False
    called = threading.Event()

    def fake_check_all(self_dm):
        called.set()

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await real_sleep(0)

    with patch("download_manager.DownloadManager.check_all", fake_check_all):
        with patch("main.asyncio.sleep", fast_sleep):
            with TestClient(app):
                # Give the loop a moment to NOT schedule the task
                pass

    assert not called.is_set(), "check_all ran despite is_configured=False"
