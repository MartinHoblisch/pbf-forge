"""Tests for POST /api/shutdown — the Quit button's backend.

Bug class to prevent:
  Any page the user has open in another tab POSTs to http://127.0.0.1:8000 and
  kills the server, taking a running filter job with it. The endpoint must
  reject cross-origin callers before scheduling the signal.

Every test that reaches the endpoint replaces _request_stop. The real one is
scheduled on a timer, so an unpatched test would deliver SIGTERM to the test
runner after the assertions have already passed.
"""

from __future__ import annotations

import signal

import pytest

import main


@pytest.fixture
def stops(client, monkeypatch) -> list:
    """Records shutdown requests instead of signalling the process."""
    calls: list = []
    monkeypatch.setattr(main, "_request_stop", lambda: calls.append("stop"))
    return calls


# ── Allow path ────────────────────────────────────────────────────────────────


def test_shutdown_is_accepted_from_the_local_page(client, stops):
    resp = client.post("/api/shutdown", headers={"Origin": "http://localhost:8000"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "shutting_down"}


def test_shutdown_allows_a_client_that_sends_no_origin(client, stops):
    """curl and friends stay usable, matching the WebSocket guard."""
    assert client.post("/api/shutdown").status_code == 200


def test_the_stop_is_deferred_so_the_response_can_be_delivered(client, stops):
    """Signalling inline would cut the connection before the reply lands."""
    client.post("/api/shutdown", headers={"Origin": "http://localhost:8000"})

    assert stops == [], "the stop must be scheduled, not run during the request"
    assert main._SHUTDOWN_GRACE_SECONDS > 0


def test_request_stop_sends_sigterm_so_uvicorn_stops_gracefully(monkeypatch):
    """SIGKILL would strand the lifespan teardown; SIGTERM is what uvicorn hooks."""
    signalled: list = []
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    main._request_stop()

    assert signalled == [(main.os.getpid(), signal.SIGTERM)]


# ── Deny path ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost.evil.example", "http://192.168.1.5:8000"],
)
def test_shutdown_rejects_a_cross_origin_caller(client, stops, origin):
    resp = client.post("/api/shutdown", headers={"Origin": origin})

    assert resp.status_code == 403
    assert stops == []
