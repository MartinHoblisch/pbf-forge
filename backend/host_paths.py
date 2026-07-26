"""Translation of container paths into the paths the user sees on their machine.

The backend only ever knows `/data`, because that is where the data directory is
bind-mounted inside the container. The user picked a very different path during
onboarding — `H:\\pbf-forge\\data` on Windows, `/home/me/osm-data` on Linux — and
that is the path they need in order to find a result in their file manager.
Everything printed for the user goes through here first.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import config

# A Windows path always carries a backslash or a drive letter. The container is
# Linux, so os.sep cannot be used to tell the two flavours apart.
_WIN_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")


@lru_cache(maxsize=1)
def host_data_dir() -> str:
    """Host path of the data directory, or "" if unset or unreadable.

    Cached for the lifetime of the process, which is both cheaper and more
    accurate. Changing the data directory writes the new path to the config but
    also sets `pending_restart`, because the container's bind mount can only be
    repointed by restarting through start.sh. Until that restart happens, `/data`
    still resolves to the old host directory — so the value read at startup is
    the one that actually describes where files are, and re-reading the file
    would start reporting paths that nothing lives under yet.

    Callers that legitimately need a re-read (tests) use `cache_clear()`.
    """
    try:
        cfg = json.loads(config.USER_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = cfg.get("host_data_dir")
    return value if isinstance(value, str) else ""


def to_host_path(container_path: str, host_root: str) -> str:
    """Map a container path below DATA_DIR onto its host equivalent.

    `host_root` stays a plain string on purpose: on Linux a `Path` treats
    "H:\\pbf-forge\\data" as one long filename, so joining it with "/" would
    produce "H:\\pbf-forge\\data/gpkg/x.gpkg". Separators are chosen explicitly.

    Returns `container_path` unchanged when the host path is unknown or the path
    lies outside DATA_DIR — a correct container path beats a fabricated host one.
    """
    if not host_root:
        return container_path
    try:
        rel = Path(container_path).relative_to(config.DATA_DIR)
    except ValueError:
        return container_path
    windows = "\\" in host_root or bool(_WIN_DRIVE.match(host_root))
    sep = "\\" if windows else "/"
    base = (host_root.replace("/", "\\") if windows else host_root).rstrip(sep)
    if not rel.parts:
        return base or sep
    return sep.join([base, *rel.parts])
