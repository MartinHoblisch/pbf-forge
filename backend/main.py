"""FastAPI application entry point: startup, routing, WebSocket, static files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import state
from config import CONFIG_DIR, DATA_DIR, TEMP_DIR, USER_CONFIG_FILE
from download_manager import DownloadManager
from filter_manager import FilterManager
from routes import downloads, presets
from routes import filesystem as filesystem_routes
from routes import filter as filter_routes
from routes import settings as settings_routes
from ws_manager import ConnectionManager

_log = logging.getLogger(__name__)
_STATIC = Path(__file__).parent.parent / "frontend"


def _clear_pending_restart() -> None:
    if not USER_CONFIG_FILE.exists():
        return
    try:
        cfg = json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
        if not cfg.get("pending_restart"):
            return
        # Only clear when the compose bind-mount matches the saved config.
        # HOST_DATA_DIR is injected by docker-compose at container creation time,
        # so a plain `docker restart` keeps the old value while `docker compose up`
        # sets the new one — making this a reliable signal.
        host_data_dir = cfg.get("host_data_dir", "")
        container_host_dir = os.environ.get("HOST_DATA_DIR", "")
        if host_data_dir and container_host_dir != host_data_dir:
            return
        cfg["pending_restart"] = False
        USER_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _log.warning("Could not clear pending_restart: %s", exc)


def _is_configured() -> bool:
    if not USER_CONFIG_FILE.exists():
        return False
    try:
        cfg = json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
        return bool(cfg.get("configured")) and not bool(cfg.get("pending_restart"))
    except Exception as exc:
        _log.warning("Could not read user config: %s", exc)
        return False


def _validate_dirs() -> None:
    for label, path in [("DATA_DIR", DATA_DIR), ("CONFIG_DIR", CONFIG_DIR)]:
        if not path.exists():
            raise RuntimeError(f"{label} does not exist: {path}")
        if not os.access(path, os.W_OK):
            raise RuntimeError(f"{label} is not writable: {path}")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_stale_temps() -> None:
    cutoff = time.time() - 86400  # 24 hours
    for d in TEMP_DIR.glob("tmp*"):
        if d.is_dir() and d.stat().st_mtime < cutoff:
            try:
                shutil.rmtree(d)
                _log.info("Removed stale temp dir: %s", d)
            except Exception as exc:
                _log.warning("Could not remove stale temp dir %s: %s", d, exc)


async def _delayed_check_all() -> None:
    await asyncio.sleep(0.5)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, state.download_manager.check_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_dirs()
    _cleanup_stale_temps()
    _clear_pending_restart()
    state.ws_manager = ConnectionManager()
    state.download_manager = DownloadManager(state.ws_manager)
    state.filter_manager = FilterManager(state.ws_manager)
    state.download_manager.set_loop(asyncio.get_running_loop())
    if _is_configured():
        asyncio.create_task(_delayed_check_all())
    yield


app = FastAPI(title="PBF Forge", lifespan=lifespan)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Check server logs for details."},
    )


app.include_router(downloads.router)
app.include_router(filter_routes.router)
app.include_router(presets.router)
app.include_router(settings_routes.router)
app.include_router(filesystem_routes.router)


_ALLOWED_WS_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_allowed_origin(origin: str | None) -> bool:
    """DNS-rebinding defense: only browser origins on loopback may open the WS.

    Absent Origin (non-browser clients like curl) is allowed — DNS-rebinding
    requires a browser, so the threat doesn't apply there.
    """
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname
    except Exception:
        return False
    return host in _ALLOWED_WS_HOSTS


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not _is_allowed_origin(ws.headers.get("origin")):
        await ws.close(code=1008)
        return
    await state.ws_manager.connect(ws)
    # Both snapshots stat the disk. Off the event loop, or a reconnect stalls the
    # progress broadcasts of every download already running.
    files = await run_in_threadpool(state.download_manager.list_files)
    jobs = await run_in_threadpool(state.filter_manager.list_jobs)
    await ws.send_json({"type": "files", "files": files})
    await ws.send_json({"type": "filter_jobs", "jobs": jobs})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.ws_manager.disconnect(ws)


# The frontend is a single HTML file that is replaced whenever the user pulls
# and rebuilds. Without Cache-Control a browser falls back to heuristic
# freshness — roughly 10% of the file's age when it was cached — and can keep
# serving the previous version for hours without ever contacting the server, so
# an update appears not to have arrived. "no-cache" still permits caching but
# forces revalidation on every load.
#
# StaticFiles answers that revalidation with a bodyless 304. The index route
# below returns a plain FileResponse, which has no conditional handling and so
# resends the file each time; at ~115 KB over loopback that is not worth
# restructuring the routing for.
_REVALIDATE = {"Cache-Control": "no-cache"}


class _RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that asks the browser to revalidate, as the index route does."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(_REVALIDATE)
        return response


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html", headers=_REVALIDATE)


# Serve frontend static files last so API routes take priority
if _STATIC.exists():
    app.mount("/", _RevalidatingStaticFiles(directory=str(_STATIC)), name="static")
