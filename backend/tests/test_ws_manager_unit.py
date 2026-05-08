"""Unit tests for ws_manager.ConnectionManager — dead-client removal + disconnect race.

Bug class to prevent:
  - A WebSocket client disconnects (browser closed, network drop) while broadcast
    is iterating self._active. The naive approach (send to all, ignore failures)
    leaks dead refs that grow unbounded — eventually broadcasts spend most time
    failing on dead sockets and live clients see broadcast latency rise.
  - disconnect() called twice on the same ws (race between FastAPI cleanup and
    our explicit disconnect after broadcast failure) raises ValueError if not
    swallowed → tears down the ws_endpoint handler.

The existing test_websocket.py only smoke-tests the integrated stack via
TestClient and doesn't assert that dead clients are removed from _active.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ws_manager import ConnectionManager


def _live_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


def _dead_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock(side_effect=ConnectionError("client gone"))
    return ws


# ── disconnect ───────────────────────────────────────────────────────────────


def test_disconnect_unknown_ws_swallows_value_error():
    """disconnect() called on a ws not in _active must not raise — covers race
    between FastAPI's WebSocketDisconnect cleanup and our manual disconnect."""
    cm = ConnectionManager()
    ws = _live_ws()
    # ws was never added — disconnect must be a noop, not raise
    cm.disconnect(ws)


def test_disconnect_removes_known_ws():
    cm = ConnectionManager()
    ws = _live_ws()
    cm._active.append(ws)
    cm.disconnect(ws)
    assert ws not in cm._active


# ── broadcast ────────────────────────────────────────────────────────────────


async def test_broadcast_no_clients_is_noop():
    cm = ConnectionManager()
    # No registered clients — must not raise, must not call json.dumps
    await cm.broadcast({"type": "x", "n": 1})
    assert cm._active == []


async def test_broadcast_removes_dead_clients_keeps_alive():
    cm = ConnectionManager()
    alive = _live_ws()
    dead = _dead_ws()
    cm._active.extend([alive, dead])

    await cm.broadcast({"type": "ping"})

    alive.send_text.assert_awaited_once()
    dead.send_text.assert_awaited_once()
    assert alive in cm._active
    assert dead not in cm._active


async def test_broadcast_after_dead_removal_no_redundant_send():
    """First broadcast removes the dead ws; second broadcast must not invoke
    send_text on the already-removed dead ws."""
    cm = ConnectionManager()
    alive = _live_ws()
    dead = _dead_ws()
    cm._active.extend([alive, dead])

    await cm.broadcast({"type": "first"})
    await cm.broadcast({"type": "second"})

    # alive received both, dead only the first (after which it was removed)
    assert alive.send_text.await_count == 2
    assert dead.send_text.await_count == 1


async def test_broadcast_all_dead_clears_active_list():
    cm = ConnectionManager()
    d1, d2 = _dead_ws(), _dead_ws()
    cm._active.extend([d1, d2])

    await cm.broadcast({"type": "x"})

    assert cm._active == []
    # third broadcast on empty list is fine (no error)
    await cm.broadcast({"type": "y"})


async def test_disconnect_idempotent_after_broadcast_removal():
    """Broadcast already removed the dead ws via send-failure; an explicit
    disconnect() afterwards (e.g. fastapi calls our handler) must swallow
    the resulting ValueError."""
    cm = ConnectionManager()
    dead = _dead_ws()
    cm._active.append(dead)

    await cm.broadcast({"type": "x"})
    assert dead not in cm._active

    # now FastAPI's lifecycle calls disconnect — must not raise
    cm.disconnect(dead)
