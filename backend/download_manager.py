"""Downloading and freshness tracking for Geofabrik PBF extracts.

Transfers run in worker threads, resume from a .part file, and are checksummed
against Geofabrik's .md5 before they count as complete. Per-file state is pushed
to connected clients as it changes.
"""

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
from datetime import datetime, timedelta, timezone
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
    MAX_RETRY_AFTER_SECONDS,
    MIN_FREE_DISK_BUFFER,
    PERMANENT_HTTP_STATUSES,
    SLOW_RETRY_INTERVAL_SECONDS,
    TRANSIENT_HTTP_STATUSES,
    URLS_FILE,
    USER_AGENT,
)

_log = logging.getLogger(__name__)

# In-progress transfers are written to <name>.osm.pbf.part and only renamed into
# place once the checksum matches, so a stray .part is a resumable remnant.
PART_SUFFIX = ".part"

# Statuses that mean a worker thread is alive and owns this file's row. While a
# transfer is running only a .part file exists on disk, so anything that reads
# the directory must neither reset such a row's status nor prune it.
_ACTIVE_STATUSES = ("downloading", "waiting_retry")

# Statuses that a directory scan must leave alone on top of the active ones:
# an in-flight check owns the row just as much, and an error is a result the
# user still needs to see.
_PRESERVED_STATUSES = _ACTIVE_STATUSES + ("checking", "error")


def _stat_or_none(path: Path) -> Optional[os.stat_result]:
    """stat() a path, or None if it is not there (or unreadable)."""
    try:
        return path.stat()
    except OSError:
        return None


def _basename(url: str) -> str:
    """Last path segment of a URL — the name of the file it serves."""
    return url.rstrip("/").split("/")[-1]


def _is_superseded(server_mtime: Optional[datetime], written_at: Optional[datetime]) -> bool:
    """True when the server published a build after *written_at*.

    One rule with two users: it decides whether a partial may still be resumed,
    and whether a paused row should still be offering that resume. Unknown
    timestamps count as not superseded — the checksum is the backstop.
    """
    return server_mtime is not None and written_at is not None and server_mtime > written_at


def _freshness(state: "FileState", server_size: int, server_mtime: Optional[datetime]) -> str:
    """The status a check reports for a row no worker owns.

    Judges the complete file first, then lets a .part override that verdict —
    but only where a resume would still achieve something. A partial next to a
    file that is already current is a remnant, not progress; a partial the
    server has built past is worthless, and the row falls back to what the
    complete file alone deserves.
    """
    if state.local_size is None:
        verdict = "not_downloaded"
    elif (
        server_mtime
        and state.local_mtime
        and server_mtime > datetime.fromisoformat(state.local_mtime)
    ):
        verdict = "update_available"
    elif state.local_size >= server_size:
        verdict = "up_to_date"
    else:
        verdict = "update_available"

    if verdict == "up_to_date" or not state.partial_bytes:
        return verdict
    written_at = datetime.fromisoformat(state.partial_mtime) if state.partial_mtime else None
    return verdict if _is_superseded(server_mtime, written_at) else "paused"


def _scanned_status(state: "FileState", partial: Optional[os.stat_result]) -> str:
    """The status a directory scan reports for a row no worker owns.

    The verdict of a check is a function of four figures the row already
    carries — the local size and date, and the published ones — so a scan can
    re-derive it rather than throw it away. Discarding it reset every checked
    row to "unknown" on each page load, which read as though the check had
    never happened while its own columns still showed the result.

    Only a row that has never been checked has nothing to derive from.
    """
    if state.server_size is None:
        return "paused" if partial else "unknown"
    server_mtime = datetime.fromisoformat(state.server_mtime) if state.server_mtime else None
    return _freshness(state, state.server_size, server_mtime)


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    """Return seconds to wait before next fast retry.

    Honors Retry-After header on 429 responses (capped); otherwise exponential backoff.
    """
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass  # HTTP-date form — ignore, use backoff
    return float(2**attempt)


def _lookup_url(mapping: dict[str, str], filename: str) -> Optional[str]:
    """Resolve the download URL of a local extract, including renamed variants.

    Lookup order:
    1. Direct match (e.g. europe.osm.pbf, europe-latest.osm.pbf)
    2. Strip 6-digit date: africa-260427.osm.pbf -> africa.osm.pbf
    3. Strip -latest:      germany-latest.osm.pbf -> germany.osm.pbf
    """
    import re

    if url := mapping.get(filename):
        return url
    base = re.sub(r"-\d{6}(?=\.osm\.pbf$)", "", filename)
    if base != filename:
        if url := mapping.get(base):
            return url
    base2 = re.sub(r"-latest(?=\.osm\.pbf$)", "", filename)
    if base2 != filename:
        if url := mapping.get(base2):
            return url
    return None


def source_url(filename: str) -> Optional[str]:
    """Where a local extract was downloaded from, or None if it was placed by hand.

    Reads the stored mapping rather than taking it from a running
    DownloadManager, so a caller outside the download subsystem — the output
    report — can name the real host of each source instead of assuming one.
    """
    mapping = dict(CONTINENTAL_URLS)
    if URLS_FILE.exists():
        try:
            mapping.update(json.loads(URLS_FILE.read_text(encoding="utf-8")))
        except Exception as exc:
            _log.warning("URL mapping could not be read: %s", exc)
    return _lookup_url(mapping, filename)


def url_to_filename(url: str) -> str:
    """Convert a Geofabrik URL to local filename without '-latest'.

    https://download.geofabrik.de/europe-latest.osm.pbf → europe.osm.pbf
    https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf → berlin.osm.pbf
    """
    import re

    return re.sub(r"-latest(?=\.osm\.pbf$)", "", _basename(url))


@dataclass
class FileState:
    filename: str
    url: Optional[str] = None
    local_size: Optional[int] = None
    local_mtime: Optional[str] = None
    server_size: Optional[int] = None
    server_mtime: Optional[str] = None
    # unknown | not_downloaded | checking | up_to_date | update_available
    # downloading | paused | waiting_retry | error
    status: str = "unknown"
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: float = 0.0
    error: Optional[str] = None
    retry_at: Optional[str] = None  # ISO timestamp of next slow retry
    retry_attempt: Optional[int] = None  # slow retry attempt counter
    # Bytes sitting in the .part file. Reported separately from local_size so a
    # half-finished transfer can never be mistaken for a usable local file.
    partial_bytes: Optional[int] = None
    # When those bytes were last written. Not reported to clients — it exists so
    # a check can tell a partial that is still resumable from one the server has
    # already built past.
    partial_mtime: Optional[str] = None

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
            "retry_at": self.retry_at,
            "retry_attempt": self.retry_attempt,
            "partial_bytes": self.partial_bytes,
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
        # The URL mapping used to live in the data volume. Copy it into the
        # config volume once, so installs created before the split keep their
        # custom URLs instead of silently starting over.
        _old = DATA_DIR / ".osm_tool_urls.json"
        if not URLS_FILE.exists() and _old.exists():
            shutil.copy(_old, URLS_FILE)
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
        return _lookup_url(self._url_mapping, filename)

    def _sync_local_state(self, filename: str) -> bool:
        """Re-read one file from disk into its FileState.

        Every path that reports on a file goes through here, so a single check
        and a check-all always see the same size, timestamp and URL.

        A lone .part file counts as present: it is the resume base for a paused
        transfer, and dropping the row would leave the user no way to continue it.

        Returns False when nothing is left on disk and the row was dropped.
        """
        complete = _stat_or_none(DATA_DIR / filename)
        partial = _stat_or_none(DATA_DIR / (filename + PART_SUFFIX))

        if complete is None and partial is None:
            with self._lock:
                state = self._files.get(filename)
                # Nothing tracked, or a worker is about to create the .part file.
                if state is None or state.status in _ACTIVE_STATUSES:
                    return True
                del self._files[filename]
            self._broadcast({"type": "file_removed", "filename": filename})
            return False

        with self._lock:
            if filename not in self._files:
                self._files[filename] = FileState(filename=filename)
            state = self._files[filename]
            state.local_size = complete.st_size if complete else None
            state.local_mtime = (
                datetime.fromtimestamp(complete.st_mtime, tz=timezone.utc).isoformat()
                if complete
                else None
            )
            state.partial_bytes = partial.st_size if partial else None
            state.partial_mtime = (
                datetime.fromtimestamp(partial.st_mtime, tz=timezone.utc).isoformat()
                if partial
                else None
            )
            # Keep a previously known URL when the mapping no longer resolves:
            # dropping it would strand the row with nothing left to check against.
            if url := self._resolve_url(filename):
                state.url = url
            if state.status not in _PRESERVED_STATUSES:
                state.status = _scanned_status(state, partial)
        return True

    def _refresh_local_files(self) -> None:
        # A .part file on its own is enough to keep a row: that is what makes a
        # paused download survive a restart instead of leaking disk space unseen.
        found = {path.name for path in DATA_DIR.glob("*.osm.pbf")}
        found |= {
            path.name[: -len(PART_SUFFIX)] for path in DATA_DIR.glob("*.osm.pbf" + PART_SUFFIX)
        }
        for filename in sorted(found):
            self._sync_local_state(filename)

        # Remove states for files no longer on disk (skip active transfers)
        with self._lock:
            gone = [
                k
                for k in self._files
                if k not in found and self._files[k].status not in _ACTIVE_STATUSES
            ]
            for k in gone:
                del self._files[k]
        for filename in gone:
            self._broadcast({"type": "file_removed", "filename": filename})

    def list_files(self) -> list[dict]:
        self._refresh_local_files()
        with self._lock:
            return [s.to_dict() for s in self._files.values()]

    def check_file(self, filename: str) -> None:
        """HEAD-request one file against Geofabrik. Runs in a thread."""
        # Read the disk first, so a single check reports what is actually there
        # rather than whatever this process last remembered.
        if not self._sync_local_state(filename):
            return  # File is gone; its row has already been dropped.

        with self._lock:
            if filename not in self._files:
                self._files[filename] = FileState(filename=filename)
            state = self._files[filename]
            url = state.url or self._resolve_url(filename)

        if not url:
            return

        # A running transfer owns the status column: the progress bar and the
        # cancel button are rendered from it, and the worker only writes byte
        # counters back. Refresh the server figures around it, never over it.
        with self._lock:
            active = state.status in _ACTIVE_STATUSES
            if not active:
                state.status = "checking"
        if not active:
            self._broadcast({"type": "file_update", "file": state.to_dict()})

        try:
            size, mtime = self._head(url)
        except Exception as exc:
            with self._lock:
                active = state.status in _ACTIVE_STATUSES
                if not active:
                    state.status = "error"
                    state.error = str(exc)
            if active:
                # Surfacing this would replace a live progress bar with a red
                # badge over a transfer that is still perfectly healthy.
                _log.warning("Check of %s failed during transfer: %s", filename, exc)
            else:
                self._broadcast({"type": "file_update", "file": state.to_dict()})
            return

        with self._lock:
            state.server_size = size
            state.server_mtime = mtime.isoformat() if mtime else None
            # Re-read the status instead of trusting the snapshot taken before
            # the request: a HEAD may take up to 30 seconds, long enough for a
            # download to have started in the meantime.
            if state.status not in _ACTIVE_STATUSES:
                state.status = _freshness(state, size, mtime)
                state.error = None

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
            # The cancel flag outlives the status: the worker pops it in its
            # finally block. Checking it too keeps a second worker from ever
            # appending to the same .part file if the status drifts.
            if state.status == "downloading" or filename in self._cancel_flags:
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
            "server_mtime": mtime.isoformat() if mtime else None,
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

    def register_and_start(self, url: str, filename: str) -> bool:
        """Atomically register URL and start download. Returns False if already downloading."""
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
                state = self._files[filename]
                state.url = url
            if state.status == "downloading" or filename in self._cancel_flags:
                return False
            state.status = "downloading"
            state.error = None
            state.downloaded_bytes = 0
            cancel = threading.Event()
            self._cancel_flags[filename] = cancel
            state_dict = state.to_dict()
        self._save_url_mapping()
        self._broadcast({"type": "file_update", "file": state_dict})
        self._executor.submit(self._download_worker, filename, cancel)
        return True

    def _broadcast(self, data: dict) -> None:
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.broadcast(data), self._loop)
            except RuntimeError:
                pass

    @staticmethod
    def _new_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        return s

    def _head(
        self, url: str, session: Optional[requests.Session] = None
    ) -> tuple[int, Optional[datetime]]:
        def _do(s: requests.Session) -> tuple[int, Optional[datetime]]:
            resp = s.head(
                url, allow_redirects=True, timeout=30, headers={"Cache-Control": "no-cache"}
            )
            resp.raise_for_status()
            size = int(resp.headers.get("Content-Length", 0))
            raw_mtime = resp.headers.get("Last-Modified")
            mtime = parsedate_to_datetime(raw_mtime) if raw_mtime else None
            return size, mtime

        if session is not None:
            return _do(session)
        with self._new_session() as s:
            return _do(s)

    def _effective_url(self, url: str, session: requests.Session) -> str:
        """Resolve redirects to the concrete build an alias points at.

        Falls back to *url* when the redirect cannot be resolved, leaving the
        caller with what it already had.
        """
        try:
            resp = session.head(
                url, allow_redirects=True, timeout=30, headers={"Cache-Control": "no-cache"}
            )
            resp.raise_for_status()
            return resp.url or url
        except Exception as exc:
            _log.warning("Could not resolve %s to its final URL: %s", url, exc)
            return url

    @staticmethod
    def _resume_offset(part: Path, server_mtime: Optional[datetime]) -> int:
        """Bytes of *part* a resume may build on, discarding it if it is outdated.

        Hosts rebuild their extracts daily. A Range request is answered from
        whatever the URL serves at that moment, so resuming a partial that was
        started before the current build splices bytes from two different files
        together. When the published timestamp is newer than the last write to
        the .part, the partial belongs to an older build and is dropped.
        """
        st = _stat_or_none(part)
        if st is None:
            return 0
        part_mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if _is_superseded(server_mtime, part_mtime):
            part.unlink(missing_ok=True)
            return 0
        return st.st_size

    def _refresh_server_state(
        self,
        url: str,
        session: requests.Session,
        state: FileState,
        fallback: tuple[int, Optional[datetime]],
    ) -> tuple[int, Optional[datetime]]:
        """Re-read the published size and timestamp before resuming a transfer.

        A retry can happen minutes or days after the transfer began — the slow
        loop waits for the network to come back for as long as it takes — so
        the figures taken when the worker started may describe a build that is
        no longer being served.

        Keeps *fallback* when the server cannot be reached: the retry is about
        to run into the same outage, and the checksum still has the last word.
        """
        try:
            size, mtime = self._head(url, session=session)
        except Exception as exc:
            _log.warning("Could not refresh server state for %s: %s", url, exc)
            return fallback
        with self._lock:
            state.server_size = size
            state.server_mtime = mtime.isoformat() if mtime else None
        return size, mtime

    def _download_worker(self, filename: str, cancel: threading.Event) -> None:
        with self._lock:
            state = self._files[filename]
            url = state.url

        dest = DATA_DIR / filename
        part = dest.with_name(dest.name + PART_SUFFIX)
        tracker = _SpeedTracker()

        try:
            with self._new_session() as session:
                size, mtime = self._head(url, session=session)
                with self._lock:
                    state.server_size = size
                    state.server_mtime = mtime.isoformat() if mtime else None

                # Reject pathological downloads (wrong URL, hostile mirror, etc.)
                if size > MAX_DOWNLOAD_SIZE:
                    raise RuntimeError(
                        f"Refusing to download {size / 1e9:.1f} GB "
                        f"(cap is {MAX_DOWNLOAD_SIZE / 1e9:.0f} GB)"
                    )

                start_byte = self._resume_offset(part, mtime)

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
                            url, part, start_byte, size, tracker, state, cancel, session
                        )
                        break
                    except requests.HTTPError as exc:
                        code = exc.response.status_code if exc.response is not None else 0
                        reason = exc.response.reason if exc.response is not None else ""
                        is_permanent = code in PERMANENT_HTTP_STATUSES or (
                            400 <= code < 500 and code not in TRANSIENT_HTTP_STATUSES
                        )
                        if is_permanent:
                            raise RuntimeError(
                                f"HTTP {code} {reason} — permanent error, not retrying"
                            ) from exc
                        if attempt == MAX_RETRIES - 1:
                            raise RuntimeError(
                                f"HTTP {code} {reason} — failed after {MAX_RETRIES} retries"
                            ) from exc
                        time.sleep(_retry_delay(exc.response, attempt))
                        size, mtime = self._refresh_server_state(url, session, state, (size, mtime))
                        start_byte = self._resume_offset(part, mtime)
                    except requests.exceptions.SSLError as exc:
                        raise RuntimeError(f"SSL error — not retrying: {exc}") from exc
                    except (
                        requests.ConnectionError,
                        requests.Timeout,
                        requests.exceptions.ChunkedEncodingError,
                    ):
                        # Network unreachable — slow retry loop (10-min interval, until cancelled)
                        slow_attempt = 0
                        while not cancel.is_set():
                            slow_attempt += 1
                            retry_at = datetime.now(timezone.utc) + timedelta(
                                seconds=SLOW_RETRY_INTERVAL_SECONDS
                            )
                            with self._lock:
                                state.status = "waiting_retry"
                                state.retry_at = retry_at.isoformat()
                                state.retry_attempt = slow_attempt
                            self._broadcast({"type": "file_update", "file": state.to_dict()})
                            # Block until cancelled or timeout expires — no busy-spin
                            cancel.wait(timeout=SLOW_RETRY_INTERVAL_SECONDS)
                            if cancel.is_set():
                                break
                            size, mtime = self._refresh_server_state(
                                url, session, state, (size, mtime)
                            )
                            start_byte = self._resume_offset(part, mtime)
                            with self._lock:
                                state.status = "downloading"
                                state.retry_at = None
                            self._broadcast({"type": "file_update", "file": state.to_dict()})
                            try:
                                self._do_download(
                                    url,
                                    part,
                                    start_byte,
                                    size,
                                    tracker,
                                    state,
                                    cancel,
                                    session,
                                )
                                break  # success — exit slow retry loop
                            except (
                                requests.ConnectionError,
                                requests.Timeout,
                                requests.exceptions.ChunkedEncodingError,
                            ):
                                continue  # still offline — wait again
                            except Exception:
                                raise  # other error — bubble up
                        break  # exit fast retry loop (slow loop handled everything)

            if cancel.is_set():
                # .part intentionally kept on disk — resume base for the next
                # attempt. Report it as paused so the row stays and offers a
                # resume instead of vanishing on the next directory scan.
                leftover = _stat_or_none(part)
                with self._lock:
                    state.status = "paused" if leftover else "unknown"
                    state.partial_bytes = leftover.st_size if leftover else None
                    state.speed_bps = 0.0
                    state.eta_seconds = 0.0
            else:
                if not part.exists():
                    raise RuntimeError(
                        f"Download of {filename!r} produced no output "
                        f"(server returned 416 with no partial file present)"
                    )
                # Order matters. Verify the .part file first, then rename it
                # into place, and only stamp the server mtime last: a corrupt
                # download that already carried the server timestamp would look
                # up_to_date after a restart and never be fetched again.
                self._verify_checksum(url, part, session)
                os.replace(part, dest)
                if mtime:
                    ts = mtime.timestamp()
                    os.utime(dest, (ts, ts))
                with self._lock:
                    state.status = "up_to_date"
                    state.local_size = dest.stat().st_size
                    state.local_mtime = mtime.isoformat() if mtime else None
                    state.partial_bytes = None  # .part was renamed into place
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

    @staticmethod
    def _fetch_checksum(md5_url: str, session: requests.Session) -> tuple[str, Optional[str]]:
        """Return (hex digest, name of the file it describes) from a .md5 sidecar.

        Sidecar format: "<hex>  <filename>\\n". The filename is optional —
        only the digest has to be there.
        """
        try:
            resp = session.get(md5_url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Could not fetch checksum from {md5_url}: {exc}") from exc

        fields = resp.text.strip().split()
        expected_hex = fields[0] if fields else ""
        if len(expected_hex) != 32:
            raise RuntimeError(f"Unexpected .md5 content from {md5_url}: {resp.text.strip()!r}")
        return expected_hex, fields[1] if len(fields) > 1 else None

    def _verify_checksum(self, url: str, dest: Path, session: requests.Session) -> None:
        """Fetch <url>.md5 and verify the local file matches.

        Fails closed: any error (network, parse, mismatch) raises RuntimeError
        so the download is marked error and osmium is never invoked on the file.
        Caller must ensure *dest* exists.
        """
        md5_url = url + ".md5"
        expected_hex, listed_name = self._fetch_checksum(md5_url, session)
        remote_name = _basename(url)

        # A sidecar names the build it describes. Hosts publish
        # <region>-latest.osm.pbf as a redirect to a dated build and keep a
        # .md5 beside both, but the one next to the alias is not always in
        # step: it can lag months behind, or run ahead of the redirect while
        # the nightly rotation is in progress. When the names disagree, follow
        # the alias and take the checksum from the build that was served.
        if listed_name and listed_name != remote_name:
            resolved = self._effective_url(url, session)
            if resolved != url:
                md5_url = resolved + ".md5"
                expected_hex, listed_name = self._fetch_checksum(md5_url, session)
                remote_name = _basename(resolved)
            if listed_name and listed_name != remote_name:
                raise RuntimeError(
                    f"Checksum at {md5_url} describes {listed_name}, not {remote_name} — "
                    f"the published checksum belongs to a different build. "
                    f"{dest.name} was kept; try again later."
                )

        h = hashlib.md5()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        actual_hex = h.hexdigest()

        if actual_hex != expected_hex:
            quarantine = dest.with_name(dest.name + ".corrupt")
            try:
                dest.replace(quarantine)
                hint = f"File quarantined as {quarantine.name}."
            except OSError:
                hint = "File left in place — delete it manually."
            raise RuntimeError(
                f"MD5 mismatch for {dest.name}: expected {expected_hex}, got {actual_hex}. {hint}"
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
