from __future__ import annotations

import json
import logging

from fastapi import WebSocket

_log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._active.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, data: dict) -> None:
        if not self._active:
            return
        msg = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._active):
            try:
                await ws.send_text(msg)
            except Exception as exc:
                _log.debug("WebSocket send failed, removing client: %s", exc)
                dead.append(ws)
        for ws in dead:
            if ws in self._active:
                self._active.remove(ws)
