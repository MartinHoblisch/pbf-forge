from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import state
from download_manager import url_to_filename

_BLOCKED_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs allowed")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("Invalid URL: no hostname")
    if host.lower() in _BLOCKED_HOSTS:
        raise ValueError("URL to internal host not allowed")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass  # hostname, not a literal IP — allow
    else:
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError("URL to internal host not allowed")


router = APIRouter(prefix="/api")


class DownloadRequest(BaseModel):
    filenames: list[str]


class CancelRequest(BaseModel):
    filename: str


class CheckRequest(BaseModel):
    filenames: Optional[list[str]] = None


class UrlInfoRequest(BaseModel):
    url: str


class AddUrlRequest(BaseModel):
    url: str
    filename: Optional[str] = None


@router.get("/files")
def list_files():
    return state.download_manager.list_files()


@router.post("/check")
def check_files(req: CheckRequest, background_tasks: BackgroundTasks):
    dm = state.download_manager
    if req.filenames:
        for fn in req.filenames:
            background_tasks.add_task(dm.check_file, fn)
    else:
        background_tasks.add_task(dm.check_all)
    return {"status": "checking"}


@router.post("/download")
def start_downloads(req: DownloadRequest):
    dm = state.download_manager
    started = [fn for fn in req.filenames if dm.start_download(fn)]
    return {"started": started}


@router.post("/cancel")
def cancel_download(req: CancelRequest):
    state.download_manager.cancel_download(req.filename)
    return {"status": "cancelling"}


@router.post("/url-info")
def url_info(req: UrlInfoRequest):
    try:
        _validate_url(req.url)
        return state.download_manager.get_url_info(req.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/add-url")
def add_url(req: AddUrlRequest, background_tasks: BackgroundTasks):
    try:
        _validate_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = req.filename or url_to_filename(req.url)
    state.download_manager.register_url(req.url, filename)
    background_tasks.add_task(state.download_manager.start_download, filename)
    return {"filename": filename, "status": "queued"}
