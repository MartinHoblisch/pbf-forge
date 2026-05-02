from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import requests
from config import (
    CHUNK_SIZE,
    CONTINENTAL_URLS,
    DATA_DIR,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_DOWNLOAD_SIZE,
    MAX_RETRIES,
    MIN_FREE_DISK_BUFFER,
    URLS_FILE,
    USER_AGENT,
)

_log = logging.getLogger(__name__)


def url_to_filename(url: str) -> str:
    """Convert a Geofabrik URL to local filename without '-latest'.

    https://download.geofabrik.de/europe-latest.osm.pbf → europe.osm.pbf
    https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf → berlin.osm.pbf
    """
    import re

    last = url.rstrip("/").split("/")[-1]
    return re.sub(r"-latest(?=\.osm\.pbf$)", "", last)


@dataclass
class FileState:
    filename: str
    url: Optional[str] = None
    local_size: Optional[int] = None
    local_mtime: Optional[str] = None
    server_size: Optional[int] = None
    server_mtime: Optional[str] = None
    # unknown | not_downloaded | checking | up_to_date | update_available | downloading | error
    status: str = "unknown"
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "url": self.url,
            "local_size": self.local_size,
            "local_mtime": self.local_mtime,
            "server_size": self.server_size,
            "server_mtime": self.server_mtime,
            "status": self.status,
            "downloaded_bytes": self.downloaded_bytes,
            "speed_bps": self.speed_bps,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
        }


class _SpeedTracker:
    def __init__(self, window: int = 30) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._samples: collections.deque[tuple[float, int]] = collections.deque()
        self._total = 0
        self._last_broadcast_time: float = 0.0

    def add_bytes(self, n: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._total += n
            self._samples.append((now, self._total))
            cutoff = now - self._window
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()

    def speed_bps(self) -> float:
        with self._lock:
            if len(self._samples) < 2:
                return 0.0
            t0, b0 = self._samples[0]
            t1, b1 = self._samples[-1]
            dt = t1 - t0
            return (b1 - b0) / dt if dt > 0 else 0.0

    def reset(self, start: int = 0) -> None:
        with self._lock:
            self._total = start
            self._samples.clear()

    def should_broadcast(self) -> bool:
        now = time.monotonic()
        if now - self._last_broadcast_time >= 1.0:
            self._last_broadcast_time = now
            return True
        return False

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


class DownloadManager:
    def __init__(self, ws_manager) -> None:
        self._ws = ws_manager
        self._files: dict[str, FileState] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)
        self._check_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="check")
        self._cancel_flags: dict[str, threading.Event] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._url_mapping: dict[str, str] = {}
        self._load_url_mapping()
        self._refresh_local_files()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _load_url_mapping(self) -> None:
        self._url_mapping = dict(CONTINENTAL_URLS)
        if URLS_FILE.exists():
            try:
                stored = json.loads(URLS_FILE.read_text(encoding="utf-8"))
                self._url_mapping.update(stored)
            except Exception as exc:
                _log.warning("URL mapping could not be loaded: %s", exc)

    def _save_url_mapping(self) -> None:
        custom = {k: v for k, v in self._url_mapping.items() if k not in CONTINENTAL_URLS}
        try:
            URLS_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")
        except Exception as exc:
            _log.warning("URL mapping could not be saved: %s", exc)

    def _resolve_url(self, filename: str) -> Optional[str]:
        """Resolve Geofabrik URL for a filename, including date-stamped variants.

        Lookup order:
        1. Direct match (e.g. europe.osm.pbf, europe-latest.osm.pbf)
        2. Strip 6-digit date: africa-260427.osm.pbf -> africa.osm.pbf
        3. Strip -latest:      germany-latest.osm.pbf -> germany.osm.pbf
        """
        import re

        if url := self._url_mapping.get(filename):
            return url
        base = re.sub(r"-\d{6}(?=\.osm\.pbf$)", "", filename)
        if base != filename:
            if url := self._url_mapping.get(base):
                return url
        base2 = re.sub(r"-latest(?=\.osm\.pbf$)", "", filename)
        if base2 != filename:
            if url := self._url_mapping.get(base2):
                return url
        return None

    def _refresh_local_files(self) -> None:
        found: set[str] = set()
        for path in DATA_DIR.glob("*.osm.pbf"):
            fn = path.name
            found.add(fn)
            stat = path.stat()
            with self._lock:
                if fn not in self._files:
                    self._files[fn] = FileState(filename=fn)
                state = self._files[fn]
                state.local_size = stat.st_size
                state.local_mtime = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                state.url = self._resolve_url(fn)
                if state.status not in ("downloading", "checking", "error"):
                    state.status = "unknown"

        # Remove states for files no longer on disk (skip active downloads)
        with self._lock:
            gone = [
                k for k in self._files if k not in found and self._files[k].status != "downloading"
            ]
            for k in gone:
                del self._files[k]

    def list_files(self) -> list[dict]:
        self._refresh_local_files()
        with self._lock:
            return [s.to_dict() for s in self._files.values()]

    def check_file(self, filename: str) -> None:
        """HEAD-request one file against Geofabrik. Runs in a thread."""
        with self._lock:
            if filename not in self._files:
                self._files[filename] = FileState(filename=filename)
            state = self._files[filename]
            url = state.url or self._resolve_url(filename)

        if not url:
            return

        with self._lock:
            state.status = "checking"
        self._broadcast({"type": "file_update", "file": state.to_dict()})

        try:
            size, mtime = self._head(url)
            with self._lock:
                state.server_size = size
                state.server_mtime = mtime.isoformat()
                if state.local_size is None:
                    state.status = "not_downloaded"
                elif state.local_mtime and mtime > datetime.fromisoformat(state.local_mtime):
                    state.status = "update_available"
                elif state.local_size >= size:
                    state.status = "up_to_date"
                else:
                    state.status = "update_available"
                state.error = None
        except Exception as exc:
            with self._lock:
                state.status = "error"
                state.error = str(exc)

        self._broadcast({"type": "file_update", "file": state.to_dict()})

    def check_all(self) -> None:
        """Check every known file in parallel."""
        self._refresh_local_files()
        with self._lock:
            filenames = list(self._files.keys())
        list(self._check_executor.map(self.check_file, filenames))

    def start_download(self, filename: str) -> bool:
        """Queue a download. Returns False if already downloading or no URL."""
        with self._lock:
            if filename not in self._files:
                return False
            state = self._files[filename]
            if state.status == "downloading":
                return False
            url = state.url or self._resolve_url(filename)
            if not url:
                return False
            state.url = url
            state.status = "downloading"
            state.error = None
            state.downloaded_bytes = 0
            cancel = threading.Event()
            self._cancel_flags[filename] = cancel
            state_dict = state.to_dict()

        self._broadcast({"type": "file_update", "file": state_dict})
        self._executor.submit(self._download_worker, filename, cancel)
        return True

    def cancel_download(self, filename: str) -> None:
        with self._lock:
            flag = self._cancel_flags.get(filename)
        if flag:
            flag.set()

    def get_url_info(self, url: str) -> dict:
        filename = url_to_filename(url)
        size, mtime = self._head(url)
        return {
            "url": url,
            "filename": filename,
            "server_size": size,
            "server_mtime": mtime.isoformat(),
            "already_exists": (DATA_DIR / filename).exists(),
        }

    def register_url(self, url: str, filename: str) -> None:
        with self._lock:
            self._url_mapping[filename] = url
            dest = DATA_DIR / filename
            if filename not in self._files:
                state = FileState(filename=filename, url=url)
                if dest.exists():
                    stat = dest.stat()
                    state.local_size = stat.st_size
                    state.local_mtime = datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat()
                else:
                    state.status = "not_downloaded"
                self._files[filename] = state
            else:
                self._files[filename].url = url
        self._save_url_mapping()

    def _broadcast(self, data: dict) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._ws.broadcast(data), self._loop)

    @staticmethod
    def _new_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        return s

    def _head(self, url: str, session: Optional[requests.Session] = None) -> tuple[int, datetime]:
        def _do(s: requests.Session) -> tuple[int, datetime]:
            resp = s.head(url, allow_redirects=True, timeout=30)
            resp.raise_for_status()
            size = int(resp.headers.get("Content-Length", 0))
            raw_mtime = resp.headers.get("Last-Modified")
            mtime = parsedate_to_datetime(raw_mtime) if raw_mtime else datetime.now(timezone.utc)
            return size, mtime

        if session is not None:
            return _do(session)
        with self._new_session() as s:
            return _do(s)

    def _download_worker(self, filename: str, cancel: threading.Event) -> None:
        with self._lock:
            state = self._files[filename]
            url = state.url

        dest = DATA_DIR / filename
        tracker = _SpeedTracker()

        try:
            with self._new_session() as session:
                size, mtime = self._head(url, session=session)
                with self._lock:
                    state.server_size = size
                    state.server_mtime = mtime.isoformat()

                # Reject pathological downloads (wrong URL, hostile mirror, etc.)
                if size > MAX_DOWNLOAD_SIZE:
                    raise RuntimeError(
                        f"Refusing to download {size / 1e9:.1f} GB "
                        f"(cap is {MAX_DOWNLOAD_SIZE / 1e9:.0f} GB)"
                    )

                # Decide whether to resume or start fresh
                start_byte = 0
                if dest.exists():
                    local_mtime = (
                        datetime.fromisoformat(state.local_mtime) if state.local_mtime else None
                    )
                    if local_mtime and mtime <= local_mtime:
                        start_byte = dest.stat().st_size

                # Disk-space pre-check (avoid filling /data and crashing the host)
                needed = max(0, size - start_byte) + MIN_FREE_DISK_BUFFER
                try:
                    free = shutil.disk_usage(DATA_DIR).free
                except OSError:
                    free = None
                if free is not None and free < needed:
                    raise RuntimeError(
                        f"Insufficient disk space: need {needed / 1e9:.1f} GB, "
                        f"have {free / 1e9:.1f} GB free"
                    )

                for attempt in range(MAX_RETRIES):
                    if cancel.is_set():
                        break
                    try:
                        self._do_download(
                            url, dest, start_byte, size, tracker, state, cancel, session
                        )
                        break
                    except (requests.ConnectionError, requests.Timeout):
                        if attempt == MAX_RETRIES - 1:
                            raise
                        time.sleep(2**attempt)
                        start_byte = dest.stat().st_size if dest.exists() else start_byte

            if cancel.is_set():
                with self._lock:
                    state.status = "unknown"
                    state.speed_bps = 0.0
                    state.eta_seconds = 0.0
            else:
                ts = mtime.timestamp()
                os.utime(dest, (ts, ts))
                self._verify_checksum(url, dest, session)
                with self._lock:
                    state.status = "up_to_date"
                    state.local_size = dest.stat().st_size
                    state.local_mtime = mtime.isoformat()
                    state.speed_bps = 0.0
                    state.eta_seconds = 0.0
                    state.downloaded_bytes = 0

        except Exception as exc:
            with self._lock:
                state.status = "error"
                state.error = str(exc)
        finally:
            with self._lock:
                self._cancel_flags.pop(filename, None)

        self._broadcast({"type": "file_update", "file": state.to_dict()})

    def _verify_checksum(self, url: str, dest: Path, session: requests.Session) -> None:
        """Fetch <url>.md5 from Geofabrik and verify the local file matches.

        Fails closed: any error (network, parse, mismatch) raises RuntimeError
        so the download is marked error and osmium is never invoked on the file.
        """
        md5_url = url + ".md5"
        try:
            resp = session.get(md5_url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Could not fetch checksum from {md5_url}: {exc}") from exc

        # Geofabrik .md5 format: "<hex>  <filename>\n"
        raw = resp.text.strip()
        expected_hex = raw.split()[0] if raw else ""
        if len(expected_hex) != 32:
            raise RuntimeError(f"Unexpected .md5 content from {md5_url}: {raw!r}")

        h = hashlib.md5()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        actual_hex = h.hexdigest()

        if actual_hex != expected_hex:
            raise RuntimeError(
                f"MD5 mismatch for {dest.name}: " f"expected {expected_hex}, got {actual_hex}"
            )

    def _do_download(
        self,
        url: str,
        dest: Path,
        start_byte: int,
        total_size: int,
        tracker: _SpeedTracker,
        state: FileState,
        cancel: threading.Event,
        session: requests.Session,
    ) -> None:
        headers = {"Range": f"bytes={start_byte}-"} if start_byte > 0 else {}
        mode = "ab" if start_byte > 0 else "wb"
        tracker.reset(start_byte)

        with session.get(url, headers=headers, stream=True, timeout=60) as resp:
            if resp.status_code == 416:
                return
            resp.raise_for_status()
            if resp.status_code == 200 and start_byte > 0:
                mode = "wb"
                tracker.reset(0)

            with open(dest, mode) as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if cancel.is_set():
                        return
                    if chunk:
                        f.write(chunk)
                        tracker.add_bytes(len(chunk))
                        speed = tracker.speed_bps()
                        downloaded = tracker.total
                        remaining = max(0, total_size - downloaded)
                        eta = remaining / speed if speed > 0 else 0.0

                        with self._lock:
                            state.downloaded_bytes = downloaded
                            state.speed_bps = speed
                            state.eta_seconds = eta

                        if tracker.should_broadcast():
                            self._broadcast({"type": "file_update", "file": state.to_dict()})
