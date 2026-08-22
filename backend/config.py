"""Paths, tuning constants and download limits shared across the backend.

Directory locations come from the environment so the same code runs inside the
container and against a local checkout.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# The release this build is. Reaches users through the download user agent, the
# provenance metadata embedded in every output file, and the report written
# beside it. Raising it here raises it everywhere.
VERSION = "1.1.0"

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
TEMP_DIR = Path(os.getenv("TEMP_DIR", str(DATA_DIR / "tmp")))

URLS_FILE = CONFIG_DIR / ".osm_tool_urls.json"
PRESETS_FILE = CONFIG_DIR / ".osm_tool_presets.json"

USER_CONFIG_FILE = CONFIG_DIR / "user-config.json"
STARTUP_TIME = time.time()

# Embedded in every output file. Names only what ODbL actually requires — the
# contributors, not the distributor. The host an extract came from varies per
# source and is recorded per output in the report instead.
ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)."

MAX_CONCURRENT_DOWNLOADS = 3
CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_RETRIES = 5

PERMANENT_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 405, 410, 451})
TRANSIENT_HTTP_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
SLOW_RETRY_INTERVAL_SECONDS = 600  # 10 minutes between network-error retries
MAX_RETRY_AFTER_SECONDS = 60  # cap on Retry-After header value

# Europe-latest ~30 GB. 100 GB leaves headroom but blocks pathological URLs.
MAX_DOWNLOAD_SIZE = int(os.getenv("MAX_DOWNLOAD_SIZE", str(100 * 1024 * 1024 * 1024)))  # 100 GB

# Buffer required free on disk after a download.
MIN_FREE_DISK_BUFFER = 500 * 1024 * 1024  # 500 MB

# Sent on all outbound HTTP requests, so whichever host is being asked can tell
# the traffic apart and has somewhere to complain.
USER_AGENT = f"pbf-forge/{VERSION} (+https://github.com/MartinHoblisch/pbf-forge)"

# Fallback URLs for eight filenames, never offered as a choice and never shown.
# They let a file that turns up without a recorded URL still be checked for
# updates, which happens when one is copied into the data directory by hand.
# Each name is keyed twice, under the "-latest" spelling the host serves and
# under the shortened one this tool writes to disk, so a lookup succeeds
# whichever it starts from. A URL the user set for the same name wins and is
# stored; see DownloadManager._save_url_mapping.
CONTINENTAL_URLS: dict[str, str] = {
    "africa.osm.pbf": "https://download.geofabrik.de/africa-latest.osm.pbf",
    "africa-latest.osm.pbf": "https://download.geofabrik.de/africa-latest.osm.pbf",
    "antarctica.osm.pbf": "https://download.geofabrik.de/antarctica-latest.osm.pbf",
    "antarctica-latest.osm.pbf": "https://download.geofabrik.de/antarctica-latest.osm.pbf",
    "asia.osm.pbf": "https://download.geofabrik.de/asia-latest.osm.pbf",
    "asia-latest.osm.pbf": "https://download.geofabrik.de/asia-latest.osm.pbf",
    "australia-oceania.osm.pbf": "https://download.geofabrik.de/australia-oceania-latest.osm.pbf",
    "australia-oceania-latest.osm.pbf": "https://download.geofabrik.de/australia-oceania-latest.osm.pbf",
    "central-america.osm.pbf": "https://download.geofabrik.de/central-america-latest.osm.pbf",
    "central-america-latest.osm.pbf": "https://download.geofabrik.de/central-america-latest.osm.pbf",
    "europe.osm.pbf": "https://download.geofabrik.de/europe-latest.osm.pbf",
    "europe-latest.osm.pbf": "https://download.geofabrik.de/europe-latest.osm.pbf",
    "north-america.osm.pbf": "https://download.geofabrik.de/north-america-latest.osm.pbf",
    "north-america-latest.osm.pbf": "https://download.geofabrik.de/north-america-latest.osm.pbf",
    "south-america.osm.pbf": "https://download.geofabrik.de/south-america-latest.osm.pbf",
    "south-america-latest.osm.pbf": "https://download.geofabrik.de/south-america-latest.osm.pbf",
}
