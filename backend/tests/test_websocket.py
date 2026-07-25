"""Tests for the /ws endpoint: initial snapshots, broadcast fan-out, disconnects."""

from __future__ import annotations

import state


def test_connect_first_message_is_files(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "files"


def test_connect_second_message_is_filter_jobs(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # files
        msg = ws.receive_json()
        assert msg["type"] == "filter_jobs"


def test_broadcast_received_by_client(client):
    import asyncio

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # files
        ws.receive_json()  # filter_jobs

        payload = {"type": "test", "value": 42}
        asyncio.run(state.ws_manager.broadcast(payload))
        msg = ws.receive_json()
        assert msg["type"] == "test"
        assert msg["value"] == 42


def test_dead_client_no_crash(client):
    # Connect then immediately close; broadcast must not raise
    import asyncio

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.receive_json()
    # ws is now closed — manager may still hold the dead socket
    asyncio.run(state.ws_manager.broadcast({"type": "ping"}))


def test_three_clients_all_receive_broadcast(client):
    import asyncio

    with client.websocket_connect("/ws") as ws1:
        ws1.receive_json()
        ws1.receive_json()
        with client.websocket_connect("/ws") as ws2:
            ws2.receive_json()
            ws2.receive_json()
            with client.websocket_connect("/ws") as ws3:
                ws3.receive_json()
                ws3.receive_json()

                payload = {"type": "multi", "n": 3}
                asyncio.run(state.ws_manager.broadcast(payload))
                for ws in (ws1, ws2, ws3):
                    msg = ws.receive_json()
                    assert msg["type"] == "multi"


def test_disconnect_removes_from_active_list(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        before = len(state.ws_manager._active)

    after = len(state.ws_manager._active)
    assert after < before
