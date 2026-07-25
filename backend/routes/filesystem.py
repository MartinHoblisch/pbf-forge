"""Host-directory browser backing the data-directory picker.

Only works on Docker Desktop for Windows, which exposes the host's drives to the
container. Elsewhere the endpoints report that browsing is unavailable and the
user types the path instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api")

_HOST_DRIVES = Path("/host_drives")
_SAFE_BROWSE_PATH = re.compile(r"^[a-zA-Z0-9_\-/]*$")


def _to_windows_path(rel: str) -> str:
    """Convert container-relative path to Windows path.

    Searches for the first single-letter alphabetic segment (the drive letter),
    which may be nested under a prefix directory on some Docker Desktop setups.

    "h"                        → "H:\\"
    "h/foo/bar"                → "H:\\foo\\bar"
    "host/h/foo/bar"           → "H:\\foo\\bar"
    """
    if not rel:
        return ""
    parts = Path(rel).parts
    drive_idx = next(
        (i for i, p in enumerate(parts) if len(p) == 1 and p.isalpha()),
        None,
    )
    if drive_idx is None:
        return ""
    drive = parts[drive_idx].upper()
    rest = "\\".join(parts[drive_idx + 1 :])
    return f"{drive}:\\{rest}" if rest else f"{drive}:\\"


def _is_visible(name: str) -> bool:
    return not name.startswith((".", "$"))


@router.get("/platform")
def platform_info() -> dict:
    return {"windows_host": _HOST_DRIVES.exists()}


@router.get("/fs/browse")
def browse_fs(path: str = "") -> dict:
    """List subdirectories for the folder browser.

    path=""    → list available drive letters from /host_drives
    path="h"   → list root of H:
    path="h/f" → list H:\\f
    """
    if not _HOST_DRIVES.exists():
        return {
            "path": path,
            "windows_path": "",
            "dirs": [],
            "parent": None,
            "error": "Directory browser not available (only in Docker Desktop for Windows)",
        }

    if not _SAFE_BROWSE_PATH.match(path) or ".." in path.split("/"):
        return {"path": "", "windows_path": "", "dirs": [], "parent": None, "error": "Invalid path"}

    target = (_HOST_DRIVES / path) if path else _HOST_DRIVES
    # Resolve to catch symlinks, then guard against traversal
    try:
        resolved = target.resolve()
        resolved.relative_to(_HOST_DRIVES.resolve())
    except (ValueError, OSError):
        return {"path": "", "windows_path": "", "dirs": [], "parent": None, "error": "Invalid path"}

    if not resolved.is_dir():
        return {
            "path": path,
            "windows_path": _to_windows_path(path),
            "dirs": [],
            "parent": None,
            "error": "Not a directory",
        }

    try:
        dirs = sorted(
            e.name for e in resolved.iterdir() if e.is_dir() and (not path or _is_visible(e.name))
        )
    except PermissionError:
        dirs = []

    parent: str | None
    if not path:
        parent = None
    else:
        p = str(Path(path).parent)
        parent = "" if p == "." else p

    return {
        "path": path,
        "windows_path": _to_windows_path(path),
        "dirs": dirs,
        "parent": parent,
    }
