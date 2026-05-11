from __future__ import annotations

import os
import time
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/app/config"))
TEMP_DIR = Path(os.getenv("TEMP_DIR", str(DATA_DIR / "tmp")))

URLS_FILE = CONFIG_DIR / ".osm_tool_urls.json"
PRESETS_FILE = CONFIG_DIR / ".osm_tool_presets.json"

USER_CONFIG_FILE = CONFIG_DIR / "user-config.json"
STARTUP_TIME = time.time()

ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0). Data sourced via Geofabrik."

MAX_CONCURRENT_DOWNLOADS = 3
CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_RETRIES = 5

PERMANENT_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 405, 410, 451})
TRANSIENT_HTTP_STATUSES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
SLOW_RETRY_INTERVAL_SECONDS = 600  # 10 minutes between network-error retries
MAX_RETRY_AFTER_SECONDS = 60  # cap on Retry-After header value

# Europe-latest ~30 GB. 100 GB leaves headroom but blocks pathological URLs.
MAX_DOWNLOAD_SIZE = int(os.getenv("MAX_DOWNLOAD_SIZE", str(100 * 1024 * 1024 * 1024)))  # 100 GB

# pyosmium apply_file buffer: larger = fewer Python callbacks, more RSS.
# 512 MB default is a reasonable balance for large PBF files.
PYOSMIUM_BUFFER_SIZE = int(os.getenv("PYOSMIUM_BUFFER_SIZE", str(512 * 1024 * 1024)))

# Source files larger than this use a disk-backed osmium index (sparse_file_array)
# to reduce RAM usage during osmium tags-filter.  4 GB covers germany and below.
OSMIUM_INDEX_THRESHOLD = int(os.getenv("OSMIUM_INDEX_THRESHOLD", str(4 * 1024**3)))

# Buffer required free on disk after a download.
MIN_FREE_DISK_BUFFER = 500 * 1024 * 1024  # 500 MB

# Sent on all outbound HTTP requests so Geofabrik can identify traffic source.
USER_AGENT = "pbf-forge/1.0.0 (+https://github.com/martinhoblisch/pbf-forge)"

# Known continental PBF files — both old (-latest) and new naming conventions
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
