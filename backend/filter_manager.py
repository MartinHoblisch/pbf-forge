"""Filter job execution: the osmium/ogr2ogr pipeline and the bookkeeping around it.

A job filters one or more PBF sources by tag and geometry type, then writes the
result in the requested formats. Work happens in subprocesses whose output is
streamed to the client; jobs survive a backend restart via a JSON manifest.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from config import (
    ATTRIBUTION,
    CONFIG_DIR,
    DATA_DIR,
    TEMP_DIR,
    USER_CONFIG_FILE,
)
from filter_history import FilterHistory
from host_paths import host_data_dir, to_host_path

_log = logging.getLogger(__name__)

_FMT_EXT = {
    "pbf": ".osm.pbf",
    "geojson": ".geojson",
    "gpkg": ".gpkg",
}

# OSM tag expressions passed to osmium tags-filter must not start with '-'
# (which would be interpreted as a CLI flag) or contain shell-special chars.
_VALID_TAG = re.compile(r"^[^-\s\n\r\x00][^\n\r\x00]*$")

# Gate 0 is the pre-start risk check (assess_job_risk): before any work begins,
# a job whose sources look too large for the machine's RAM raises a confirmation
# dialog in the UI. It flags non-PBF jobs whose total source size exceeds this
# fraction of system RAM. PBF-only jobs stream end-to-end and are never flagged.
RISK_RAM_FACTOR = 0.5

# Stall handling: prolonged silence produces a WARNING, never a kill.
# Silence is not evidence of a hang — osmium tags-filter reads its input up to
# four times to resolve references before writing any output, and stays quiet
# while doing so when its streams are piped. On a 32-GB source that is hours of
# healthy work. Liveness is therefore judged from stdout/stderr activity,
# output-file growth, and /proc-IO counters; only ABSOLUTE_TIMEOUT_SECONDS kills.
STALL_CHECK_INTERVAL = 2.0
STALL_WARN_SECONDS = 300.0
ABSOLUTE_TIMEOUT_SECONDS = 24 * 3600.0
# How long to wait for a killed subprocess to be reaped before giving up on it.
KILL_REAP_SECONDS = 10.0


def _ts() -> str:
    return datetime.now().strftime("[%H:%M:%S] ")


def _parse_proc_io(text: str) -> int:
    """Sum rchar+wchar from /proc/<pid>/io content."""
    total = 0
    for line in text.splitlines():
        if line.startswith(("rchar:", "wchar:")):
            total += int(line.split(":", 1)[1])
    return total


def _proc_io_bytes(pid: int) -> int | None:
    """Total syscall-level IO bytes (rchar+wchar) of pid, or None if unavailable.

    rchar/wchar, NOT read_bytes/write_bytes: the block-layer counters stay
    flat on FUSE/virtiofs bind mounts (Docker Desktop) — exactly where the
    signal is needed. Returns None off Linux, after process exit, or when
    the kernel lacks IO accounting.
    """
    try:
        return _parse_proc_io(Path(f"/proc/{pid}/io").read_text())
    except (OSError, ValueError):
        return None


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _export_is_empty(path: Path) -> bool:
    """True when an osmium GeoJSONSeq export contains no feature.

    A tag expression that matches nothing yields a zero-byte export. The leading
    chunk is inspected as well so that a stray record separator or newline is not
    mistaken for a feature.
    """
    try:
        if path.stat().st_size == 0:
            return True
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return not head.strip(b"\x1e \t\r\n")


def _fmt_duration(seconds: float) -> str:
    """Human-readable duration: '9.4 s', '12m 34s', '1h 02m 34s'."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


# ── Output report ─────────────────────────────────────────────────────────────
# Every finished output file gets a plain-text sidecar describing how it was
# made. Laid out for reading, not parsing: the machine-readable copy of the same
# facts is embedded in the file itself by _embed_provenance().

_REPORT_WIDTH = 80
_REPORT_KEY_WIDTH = 18

_FMT_LABEL = {
    "pbf": "PBF (OSM protocol buffer)",
    "geojson": "GeoJSON (RFC 7946)",
    "gpkg": "GPKG (GeoPackage)",
}


def _report_row(key: str, values: list[str]) -> list[str]:
    """A 'key    value' line, with any further values aligned underneath."""
    indent = " " * (2 + _REPORT_KEY_WIDTH)
    return [
        f"  {key.ljust(_REPORT_KEY_WIDTH)}{values[0]}",
        *(indent + v for v in values[1:]),
    ]


def _pbf_replication_timestamp(path: Path) -> str:
    """The header's osmosis_replication_timestamp, or '' when the file has none.

    Only the header block is parsed, so the cost does not scale with file size.
    """
    import osmium.io  # imported here to keep module-level clean

    with osmium.io.Reader(str(path)) as reader:
        return reader.header().get("osmosis_replication_timestamp", "")


def _attributes_description(job: FilterJob, fmt: str) -> str:
    """How the attribute mode actually affected this output format."""
    manual = job.columns_mode == "manual" and job.manual_keys
    if fmt == "pbf":
        # For PBF the column modes do not apply: the format has no schema, so
        # tags are copied verbatim unless the manual reduction pass ran.
        if manual:
            return "reduced to: " + ", ".join(job.manual_keys)
        return "all original OSM tags preserved"
    if manual:
        return "manual columns: " + ", ".join(job.manual_keys)
    if job.columns_mode == "all":
        return "one column per tag key"
    return "curated keys as columns, remaining tags folded into other_tags"


@dataclass
class Phase:
    label: str
    source: str
    step: str  # filter | reduce | export_convert
    weight: float
    fmt: str = "pbf"  # output format associated with this phase
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "source": self.source,
            "step": self.step,
            "weight": self.weight,
            "fmt": self.fmt,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class FilterJob:
    id: str
    source_files: list[str]
    tags: list[str]
    exclude_tags: list[str]
    geometry_types: list[str]
    suffix: str
    output_formats: list[str]  # ["pbf", "geojson", "gpkg"]
    output_dir: str
    columns_mode: str  # other_tags | all | manual
    manual_keys: list[str]
    status: str = "pending"
    finished_at: Optional[str] = None
    output_files: list[str] = field(default_factory=list)
    error: Optional[str] = None
    phases: list[Phase] = field(default_factory=list)
    current_phase_index: int = 0
    phase_started_at: float | None = None
    job_started_at: float | None = None
    timeout_seconds: float | None = None
    queue_position: int | None = None
    output_bytes: int | None = None
    bytes_read: int | None = None
    progress_line: str | None = None
    last_activity_at: float | None = None
    _log_parts: list[str] = field(default_factory=list, init=False, repr=False)
    _proc_env: dict = field(default_factory=dict, init=False, repr=False)
    _nice_level: int = field(default=0, init=False, repr=False)
    _log_path: Optional[Path] = field(default=None, init=False, repr=False)
    _log_fh: Optional[object] = field(default=None, init=False, repr=False)

    @property
    def log(self) -> str:
        return "".join(self._log_parts)

    @property
    def log_file(self) -> Optional[str]:
        return str(self._log_path) if self._log_path else None

    def append_log(self, text: str) -> None:
        self._log_parts.append(text)
        if self._log_path is not None:
            try:
                if self._log_fh is None:
                    self._log_fh = open(self._log_path, "a", encoding="utf-8", buffering=1)
                self._log_fh.write(text)
            except OSError as exc:
                # Log to stderr but never crash the pipeline on disk errors.
                _log.warning("Failed to write job log to %s: %s", self._log_path, exc)

    def close_log(self) -> None:
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None

    def to_dict(self) -> dict:
        # Host paths are derived, never stored: the user may repoint the data
        # directory, and restored jobs must then show the current mapping.
        host_root = host_data_dir() if self.output_files else ""
        return {
            "id": self.id,
            "source_files": self.source_files,
            "tags": self.tags,
            "exclude_tags": self.exclude_tags,
            "geometry_types": self.geometry_types,
            "suffix": self.suffix,
            "output_formats": self.output_formats,
            "output_dir": self.output_dir,
            "columns_mode": self.columns_mode,
            "manual_keys": self.manual_keys,
            "status": self.status,
            "finished_at": self.finished_at,
            "log": self.log,
            "log_file": self.log_file,
            "output_files": self.output_files,
            "output_files_host": [to_host_path(p, host_root) for p in self.output_files],
            "error": self.error,
            "phases": [p.to_dict() for p in self.phases],
            "current_phase_index": self.current_phase_index,
            "phase_started_at": self.phase_started_at,
            "job_started_at": self.job_started_at,
            "queue_position": self.queue_position,
            "output_bytes": self.output_bytes,
            "bytes_read": self.bytes_read,
            "progress_line": self.progress_line,
            "last_activity_at": self.last_activity_at,
        }

    def to_manifest_dict(self) -> dict:
        """Persist-friendly snapshot: drop in-memory log (lives on disk separately)."""
        d = self.to_dict()
        d.pop("log", None)
        d.pop("output_files_host", None)  # derived from current config, never stored
        return d


def _compute_max_parallel() -> int:
    """Return max parallel jobs: max(1, min(cpu//4, ram_gb//8))."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        cpu = os.cpu_count() or 1 if quota == "max" else max(1, int(int(quota) / int(period)))
    except Exception:
        cpu = os.cpu_count() or 1
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) // (1024 * 1024)
                break
        else:
            ram_gb = 0
    except Exception:
        ram_gb = 0
    cap = max(1, cpu // 4)
    if ram_gb >= 8:
        cap = min(cap, ram_gb // 8)
    return max(1, cap)


class FilterManager:
    def __init__(self, ws_manager) -> None:
        self._ws = ws_manager
        self._jobs: dict[str, FilterJob] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # Dedicated thread for pyosmium: keeps it off the default pool so
        # CPU-bound PBF reduction doesn't starve other async operations.
        self._pyosmium_executor = ThreadPoolExecutor(max_workers=1)
        self._max_parallel: int = _compute_max_parallel()
        self._semaphore = asyncio.Semaphore(self._max_parallel)
        self._running_count: int = 0
        _log.info("Job queue: max_parallel=%d", self._max_parallel)
        _old_history = DATA_DIR / ".filter_history.json"
        _new_history = CONFIG_DIR / ".filter_history.json"
        if not _new_history.exists() and _old_history.exists():
            shutil.copy(_old_history, _new_history)
        self._history = FilterHistory(_new_history)
        # Resolved at construction so monkeypatch-on-CONFIG_DIR (tests) works.
        self._jobs_dir: Path = CONFIG_DIR / "jobs"
        self._manifest_file: Path = self._jobs_dir / "manifest.json"
        try:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log.warning("Could not create jobs dir %s: %s", self._jobs_dir, exc)
        self._recover_orphan_jobs()

    def _recover_orphan_jobs(self) -> None:
        """Load manifest from disk; mark in-flight jobs as crashed."""
        if not self._manifest_file.exists():
            return
        try:
            data = json.loads(self._manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read job manifest: %s", exc)
            return
        in_flight = {"running", "pending", "queued"}
        recovered = 0
        for entry in data:
            try:
                # Reconstruct minimal FilterJob; in-flight ones are marked errored.
                job = FilterJob(
                    id=entry["id"],
                    source_files=entry.get("source_files", []),
                    tags=entry.get("tags", []),
                    exclude_tags=entry.get("exclude_tags", []),
                    geometry_types=entry.get("geometry_types", []),
                    suffix=entry.get("suffix", ""),
                    output_formats=entry.get("output_formats", []),
                    output_dir=entry.get("output_dir", ""),
                    columns_mode=entry.get("columns_mode", "other_tags"),
                    manual_keys=entry.get("manual_keys", []),
                )
                job.status = entry.get("status", "error")
                job.finished_at = entry.get("finished_at")
                job.output_files = entry.get("output_files", [])
                job.error = entry.get("error")
                job.current_phase_index = entry.get("current_phase_index", 0)
                lf = entry.get("log_file")
                if lf:
                    job._log_path = Path(lf)
                if job.status in in_flight:
                    job.status = "error"
                    job.finished_at = datetime.now().strftime("%H:%M")
                    log_hint = str(job._log_path) if job._log_path else "(no log file)"
                    job.error = (
                        f"Backend crashed or restarted mid-job. "
                        f"Log: {log_hint} "
                        f"(in Docker: matches ./config/jobs/ on host via compose bind-mount). "
                        f"Likely cause: OOM — verify with `dmesg | grep -i kill`."
                    )
                    job.phase_started_at = None
                    recovered += 1
                self._jobs[job.id] = job
            except (KeyError, TypeError) as exc:
                _log.warning("Skipping malformed manifest entry: %s", exc)
        if recovered:
            _log.warning("Recovered %d orphaned job(s) as errored after restart", recovered)
            self._persist_jobs()

    def _persist_jobs(self) -> None:
        """Write atomic snapshot of all jobs to manifest.json."""
        try:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)
            payload = [j.to_manifest_dict() for j in self._jobs.values()]
            tmp = self._manifest_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._manifest_file)
        except OSError as exc:
            _log.warning("Could not persist job manifest: %s", exc)

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in reversed(list(self._jobs.values()))]

    def clear_completed_jobs(self) -> None:
        keep = {"running", "pending"}
        removed = [j for jid, j in self._jobs.items() if j.status not in keep]
        self._jobs = {jid: j for jid, j in self._jobs.items() if j.status in keep}
        for j in removed:
            j.close_log()
            if j._log_path is not None:
                try:
                    j._log_path.unlink(missing_ok=True)
                except OSError as exc:
                    _log.warning("Could not delete job log %s: %s", j._log_path, exc)
        self._persist_jobs()

    def get_job(self, job_id: str) -> Optional[dict]:
        j = self._jobs.get(job_id)
        return j.to_dict() if j else None

    def create_job(self, **kwargs) -> FilterJob:
        if "manual_keys" in kwargs:
            kwargs["manual_keys"] = [k.strip() for k in kwargs["manual_keys"] if k.strip()]
        job = FilterJob(id=str(uuid.uuid4()), **kwargs)
        try:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)
            job._log_path = self._jobs_dir / f"{job.id}.log"
        except OSError as exc:
            _log.warning("Could not set up job log path: %s", exc)
        self._jobs[job.id] = job
        self._persist_jobs()
        return job

    def list_pbf_files(self) -> list[str]:
        return sorted(p.name for p in DATA_DIR.glob("*.osm.pbf"))

    def compute_output_paths(
        self,
        source_files: list[str],
        suffix: str,
        output_formats: list[str],
        output_dir: str,
    ) -> list[Path]:
        out_dir = Path(output_dir)
        paths = []
        for source in source_files:
            base = source.removesuffix(".osm.pbf")
            stem = f"{base}_{suffix}"
            for fmt in output_formats:
                ext = _FMT_EXT.get(fmt, "")
                paths.append(out_dir / fmt / f"{stem}{ext}")
        return paths

    def check_would_overwrite(
        self,
        source_files: list[str],
        suffix: str,
        output_formats: list[str],
        output_dir: str,
    ) -> list[str]:
        return [
            str(p)
            for p in self.compute_output_paths(source_files, suffix, output_formats, output_dir)
            if p.exists()
        ]

    def assess_job_risk(self, source_files: list[str], output_formats: list[str]) -> dict | None:
        """Gate 0: cheap pre-job RAM-risk estimate.

        The GeoJSON conversion step loads the whole filtered GeoJSON into RAM
        (10-20x its size, verified). The filtered size is unknown before the
        job, so the source size serves as an upper-bound proxy: big source +
        non-PBF format on a small-RAM machine is the combination that OOMs in
        practice. PBF-only jobs stream end-to-end and are always safe.

        RAM is measured as MemTotal (not MemAvailable) — a deliberate choice.
        Using MemTotal gives a deterministic, load-independent structural bound
        that makes the pre-flight warning reproducible regardless of current
        system load. This is consistent with how _compute_max_parallel() sizes
        the job queue. The key is therefore named total_ram_bytes, not
        available_ram_bytes.
        """
        non_pbf = [f for f in output_formats if f != "pbf"]
        if not non_pbf:
            return None
        ram = self._meminfo_total_bytes()
        if ram is None:
            return None
        total = sum(self._source_size(s) for s in source_files)
        if total > ram * RISK_RAM_FACTOR:
            return {
                "level": "high",
                "source_bytes": total,
                "total_ram_bytes": ram,
                "formats": non_pbf,
            }
        return None

    @staticmethod
    def _meminfo_total_bytes() -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    async def run_job(self, job: FilterJob) -> None:
        if self._running_count >= self._max_parallel:
            job.status = "queued"
            job.queue_position = sum(1 for j in self._jobs.values() if j.status == "queued")
            self._persist_jobs()
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        async with self._semaphore:
            self._running_count += 1
            try:
                await self._execute_job(job)
            finally:
                self._running_count -= 1
                job.close_log()
                self._persist_jobs()

    async def _execute_job(self, job: FilterJob) -> None:
        job.status = "running"
        job.queue_position = None
        job.phases = self._build_phases(job)
        job.current_phase_index = 0
        job.job_started_at = time.time()
        job.last_activity_at = job.job_started_at
        job.phase_started_at = job.job_started_at  # ticker starts immediately
        self._persist_jobs()
        total_bytes = sum(self._source_size(s) for s in job.source_files)
        job.timeout_seconds = ABSOLUTE_TIMEOUT_SECONDS

        # Resource limits — computed once, carried on job for subprocess use
        threads, nice = self._resource_limits()
        job._proc_env = {
            **os.environ,
            "OSMIUM_POOL_THREADS": str(threads),
            "GDAL_NUM_THREADS": str(threads),
        }
        job._nice_level = nice

        # Startup header
        lines = [f"{_ts()}=== Job started ==="]
        for s in job.source_files:
            lines.append(f"{_ts()}Sources  : {s} ({_fmt_size(self._source_size(s))})")
        lines.append(f"{_ts()}Formats  : {', '.join(f.upper() for f in job.output_formats)}")
        lines.append(f"{_ts()}Tags     : {', '.join(job.tags)}")
        if job.exclude_tags:
            lines.append(f"{_ts()}Exclude  : {', '.join(job.exclude_tags)}")
        if job.geometry_types:
            lines.append(f"{_ts()}Geometry : {', '.join(job.geometry_types)}")
        lines.append(
            f"{_ts()}Watchdog : warn after {int(STALL_WARN_SECONDS)}s without observable "
            f"progress (no auto-kill); absolute limit "
            f"{int(ABSOLUTE_TIMEOUT_SECONDS / 3600)}h"
        )
        lines.append(f"{_ts()}Resources: {threads} threads, nice {nice}")
        job.append_log("\n".join(lines) + "\n")

        self._preflight_warnings(job, total_bytes)

        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        out_dir = Path(job.output_dir)
        try:
            for fmt in job.output_formats:
                (out_dir / fmt).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            job.status = "error"
            job.error = f"Could not create output directory: {exc}"
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            return

        # Which source and which format produced each published file. Recorded
        # at publish time because both are in scope there; recovering them
        # afterwards from job.output_files would mean parsing filenames.
        published: list[tuple[Path, str, str]] = []
        # Sources whose filter expression matched nothing. Kept per source so a
        # batch in which only some sources come up empty still publishes the rest.
        empty_sources: list[str] = []

        try:
            with tempfile.TemporaryDirectory(dir=TEMP_DIR) as _tmp:
                tmp = Path(_tmp)
                for source in job.source_files:
                    source_path = DATA_DIR / source
                    if not source_path.exists():
                        raise FileNotFoundError(f"'{source}' not found in data directory.")

                    exprs = self._build_expressions(job)
                    if not exprs:
                        raise ValueError(
                            "No filter expressions. Please specify geometry types and tags."
                        )

                    base = source.removesuffix(".osm.pbf")
                    stem = f"{base}_{job.suffix}"

                    needs_pbf = "pbf" in job.output_formats
                    non_pbf = [f for f in job.output_formats if f != "pbf"]

                    if needs_pbf:
                        pbf_out = out_dir / "pbf" / f"{stem}.osm.pbf"
                        pbf_work = tmp / f"{stem}_work.osm.pbf"
                        await self._start_phase(job)
                        cmd = [
                            "osmium",
                            "tags-filter",
                            # --verbose: pass-boundary markers on stderr (the
                            # only sign of life during the up-to-3 silent
                            # reference-finding passes); --progress: percent
                            # bar during the final copy pass even when piped.
                            "--verbose",
                            "--progress",
                            str(source_path),
                            *exprs,
                            "-o",
                            str(pbf_work),
                            "--overwrite",
                        ]
                        rc = await self._run_cmd(cmd, job, watch_path=pbf_work)
                        if rc != 0:
                            raise RuntimeError(f"osmium exited with code {rc}")
                        await self._finish_phase(job)

                        if job.exclude_tags:
                            excl_exprs = self._build_expressions(job, kind="exclude")
                            excl_tmp = tmp / f"{stem}_excl.osm.pbf"
                            await self._start_phase(job)
                            excl_cmd = [
                                "osmium",
                                "tags-filter",
                                "--verbose",
                                "--progress",
                                str(pbf_work),
                                *excl_exprs,
                                "--invert-match",
                                "-o",
                                str(excl_tmp),
                                "--overwrite",
                            ]
                            rc = await self._run_cmd(excl_cmd, job, watch_path=excl_tmp)
                            if rc != 0:
                                raise RuntimeError(f"osmium exclude pass exited with code {rc}")
                            shutil.move(str(excl_tmp), str(pbf_work))
                            await self._finish_phase(job)

                        if job.columns_mode == "manual" and job.manual_keys:
                            await self._start_phase(job)
                            rc = await self._reduce_pbf_tags(pbf_work, job)
                            if rc != 0:
                                raise RuntimeError("PBF tag reduction failed")
                            await self._finish_phase(job)

                        # Every phase above wrote into tmp, so only a complete
                        # run reaches the final path. A failure leaves nothing
                        # there that a user could mistake for a finished result.
                        shutil.move(str(pbf_work), str(pbf_out))
                        job.output_files.append(str(pbf_out))
                        published.append((pbf_out, source, "pbf"))
                        intermediate = pbf_out
                    elif non_pbf:
                        intermediate = tmp / f"{stem}.osm.pbf"
                        await self._start_phase(job)
                        cmd = [
                            "osmium",
                            "tags-filter",
                            # --verbose: pass-boundary markers on stderr (the
                            # only sign of life during the up-to-3 silent
                            # reference-finding passes); --progress: percent
                            # bar during the final copy pass even when piped.
                            "--verbose",
                            "--progress",
                            str(source_path),
                            *exprs,
                            "-o",
                            str(intermediate),
                            "--overwrite",
                        ]
                        rc = await self._run_cmd(cmd, job, watch_path=intermediate)
                        if rc != 0:
                            raise RuntimeError(f"osmium exited with code {rc}")
                        await self._finish_phase(job)

                        if job.exclude_tags:
                            excl_exprs = self._build_expressions(job, kind="exclude")
                            excl_tmp = tmp / f"{stem}_excl.osm.pbf"
                            await self._start_phase(job)
                            excl_cmd = [
                                "osmium",
                                "tags-filter",
                                "--verbose",
                                "--progress",
                                str(intermediate),
                                *excl_exprs,
                                "--invert-match",
                                "-o",
                                str(excl_tmp),
                                "--overwrite",
                            ]
                            rc = await self._run_cmd(excl_cmd, job, watch_path=excl_tmp)
                            if rc != 0:
                                raise RuntimeError(f"osmium exclude pass exited with code {rc}")
                            intermediate = excl_tmp
                            await self._finish_phase(job)
                    else:
                        continue

                    # osmium export runs at most once per source; every selected
                    # output format reuses the result.
                    shared_geojson: Path | None = None
                    shared_fields: list[str] = []

                    for fmt in non_pbf:
                        if fmt == "geojson":
                            out_file = out_dir / "geojson" / f"{stem}.geojson"
                        else:
                            out_file = out_dir / "gpkg" / f"{stem}.gpkg"

                        work_file = tmp / out_file.name
                        work_file.unlink(missing_ok=True)
                        await self._start_phase(job)

                        # Single export route for every non-PBF format: osmium
                        # export with a disk-backed node index, then ogr2ogr.
                        # Routing gpkg through GDAL's OSM driver instead would
                        # be faster but yields a different column set, so the
                        # same filter would produce two schemas depending on
                        # which formats happened to be selected together.
                        if shared_geojson is None:
                            shared_geojson = tmp / f"{stem}_export.geojsonseq"
                            # Disk-backed node index keeps RAM bounded on
                            # large sources (Europe-scale fits in ~1 GB RSS
                            # instead of 5–10 GB with flex_mem default).
                            # GeoJSONSeq (not GeoJSON): the GDAL GeoJSONSeq
                            # driver streams, while the classic GeoJSON driver
                            # loads the whole file into RAM (10-20x its size).
                            # NOTE: the option is --output-format; a bare
                            # --format=X prefix-matches osmium's no-op
                            # --format-option and is silently ignored.
                            rc = await self._run_cmd(
                                [
                                    "osmium",
                                    "export",
                                    "--verbose",
                                    "--progress",
                                    "--output-format=geojsonseq",
                                    "--attributes",
                                    "id",
                                    "--index-type=sparse_file_array,sparse_file_array",
                                    "-o",
                                    str(shared_geojson),
                                    str(intermediate),
                                    "--overwrite",
                                ],
                                job,
                                watch_path=shared_geojson,
                            )
                            if rc != 0:
                                raise RuntimeError(f"osmium export exited with code {rc}")

                            # An expression that matches nothing leaves an empty
                            # export. ogr2ogr cannot open an empty datasource and
                            # answers with a list of every driver it knows plus
                            # exit code 1 — nothing a user could act on. The
                            # cause is reported here instead, before conversion.
                            if _export_is_empty(shared_geojson):
                                job.append_log(
                                    f"{_ts()}{source}: no features matched this filter — "
                                    f"nothing to convert\n"
                                )
                                empty_sources.append(source)
                                await self._finish_phase(job)
                                shared_geojson.unlink(missing_ok=True)
                                shared_geojson = None
                                break

                            if job.columns_mode == "other_tags":
                                # Standard mode: fold every non-curated tag
                                # into a compact other_tags JSON column. This
                                # is what the UI promises — and expanding all
                                # keys is impossible anyway (SQLite caps a
                                # table at 2000 columns; Europe-scale extracts
                                # carry more distinct tag keys).
                                curated = self._curated_keys(job)
                                folded = tmp / f"{stem}_folded.geojsonseq"
                                fold_cmd = [
                                    sys.executable,
                                    str(Path(__file__).parent / "geojsonseq_fold.py"),
                                    str(shared_geojson),
                                    str(folded),
                                    "--keep",
                                    ",".join(curated),
                                ]
                                rc = await self._run_cmd(fold_cmd, job, watch_path=folded)
                                if rc != 0:
                                    raise RuntimeError(f"other_tags fold exited with code {rc}")
                                # Eager cleanup: raw export and folded copy
                                # would otherwise coexist at full size.
                                shared_geojson.unlink(missing_ok=True)
                                shared_geojson = folded
                                # Schema is static — fold emits every curated
                                # key (null when absent) plus other_tags, so
                                # no ogrinfo field scan is needed.
                                shared_fields = [*curated, "other_tags"]
                            else:
                                shared_fields = await self._get_fields(shared_geojson)

                        sql = self._build_export_sql(shared_geojson.stem, job, shared_fields)
                        ogr_fmt = "GeoJSON" if fmt == "geojson" else "GPKG"
                        if ogr_fmt == "GPKG":
                            # SQLite caps a table at 2000 columns; fid + geom +
                            # osm_id occupy 3. Europe-scale extracts can carry
                            # >2000 distinct tag keys — fail before the
                            # expensive conversion, not inside sqlite3_exec.
                            if job.columns_mode == "manual" and job.manual_keys:
                                available = set(shared_fields)
                                n_cols = sum(1 for k in job.manual_keys if k in available)
                            else:
                                n_cols = len(shared_fields)
                            if n_cols > 1997:
                                raise RuntimeError(
                                    f"GeoPackage supports at most 2000 columns, "
                                    f"but this extract has {n_cols} distinct tag "
                                    f"keys. Use 'Select manually' to pick the "
                                    f"columns you need."
                                )
                        cmd = [
                            "ogr2ogr",
                            # Tick output on stdout ("0...10...done") keeps the
                            # liveness signal fed during long feature copies.
                            "-progress",
                            "-f",
                            ogr_fmt,
                            str(work_file),
                            str(shared_geojson),
                            "-sql",
                            sql,
                        ]
                        if fmt == "gpkg":
                            cmd += [
                                "-nln",
                                out_file.stem,
                                "-a_srs",
                                "EPSG:4326",
                                "-gt",
                                "65536",
                                "--config",
                                "OGR_SQLITE_SYNCHRONOUS",
                                "OFF",
                            ]
                        rc = await self._run_cmd(cmd, job, watch_path=work_file)

                        if rc != 0:
                            raise RuntimeError(f"Conversion exited with code {rc}")
                        await self._finish_phase(job)
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, self._embed_attribution, work_file, fmt)
                        await loop.run_in_executor(
                            None,
                            self._embed_provenance,
                            work_file,
                            fmt,
                            source,
                            job.tags,
                            job.exclude_tags,
                            job.geometry_types,
                        )
                        # Conversion and metadata embedding both succeeded, so
                        # the finished file can take the final path. A failure
                        # anywhere above leaves that path untouched.
                        out_file.unlink(missing_ok=True)
                        shutil.move(str(work_file), str(out_file))
                        job.output_files.append(str(out_file))
                        published.append((out_file, source, fmt))
                        gc.collect()

                    # Eager cleanup: free disk space from shared export temp.
                    if shared_geojson is not None:
                        shared_geojson.unlink(missing_ok=True)

            # Output inventory
            total_out = 0
            inv_lines = [f"{_ts()}=== Output files ==="]
            for f in job.output_files:
                try:
                    sz = Path(f).stat().st_size
                except OSError:
                    sz = 0
                total_out += sz
                inv_lines.append(f"{_ts()}  {f} ({_fmt_size(sz)})")
            job_dur = time.time() - (job.job_started_at or time.time())
            inv_lines.append(
                f"{_ts()}Total: {_fmt_size(total_out)}  |  Job duration: {job_dur:.1f}s"
            )
            job.append_log("\n".join(inv_lines) + "\n")

            if empty_sources and not job.output_files:
                # Narrowing a filter until nothing matches is ordinary use, not a
                # failure — so this is not an error. It still gets a status of its
                # own, because reporting "done" with no files would read as a
                # silent success.
                job.status = "no_matches"
                job.append_log(
                    f"{_ts()}No features matched this filter. No output files were written.\n"
                )
            else:
                job.status = "done"
            finished_dt = datetime.now().astimezone()
            job.finished_at = finished_dt.strftime("%H:%M")
            self._write_output_reports(job, published, finished_dt, job_dur)

        except Exception as exc:
            job.status = "error"
            job.finished_at = datetime.now().strftime("%H:%M")
            job.error = str(exc)
            job.phase_started_at = None  # prevent stale elapsed ticker on client
            job.append_log(f"\nERROR: {exc}\n")
            self._append_kernel_log_snapshot(job)

        self._persist_jobs()
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

    _STEP_FACTORS: dict[str, float] = {
        "filter": 1.0,
        "exclude": 1.0,
        "reduce": 0.3,
        "export_convert": 0.5,
    }

    def _source_size(self, source: str) -> int:
        try:
            return (DATA_DIR / source).stat().st_size
        except OSError:
            return 1

    def _source_data_timestamp(self, source: str) -> Optional[str]:
        """How current the source extract's data is, or None if undeterminable.

        Prefers the replication timestamp in the PBF header: the moment the
        extract was cut from the OSM database, which is the figure a downstream
        consumer needs and is unrelated to when the file was fetched.

        Falls back to the file's mtime, which is not the download time either —
        DownloadManager stamps the server's Last-Modified onto the file, so for
        a Geofabrik extract this is its publication time.
        """
        path = DATA_DIR / source
        try:
            stamp = _pbf_replication_timestamp(path)
        except Exception:
            # Sources that are not readable PBF, or a runtime without pyosmium.
            # The mtime fallback still says something useful.
            _log.debug("Could not read the PBF header of %s", source, exc_info=True)
            stamp = ""
        if stamp:
            with contextlib.suppress(ValueError):
                stamp = datetime.fromisoformat(stamp).isoformat(sep=" ", timespec="seconds")
            return f"{stamp}  (OSM replication)"
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            return None
        return f"{mtime.isoformat(sep=' ', timespec='seconds')}  (source file date)"

    def _preflight_warnings(self, job: FilterJob, total_source_bytes: int) -> None:
        """Append disk-space warning to job log. Never blocks the job.

        Disk space only. RAM risk is handled before the job starts, by
        assess_job_risk() (Gate 0), which can still ask the user to confirm;
        a warning written here would arrive too late to act on.
        """
        warnings: list[str] = []

        # Disk space check at TEMP_DIR — intermediate PBF + GeoJSON live there.
        try:
            free = shutil.disk_usage(TEMP_DIR).free
            required = int(total_source_bytes * 1.5)
            if free < required:
                warnings.append(
                    f"{_ts()}WARNING: Free disk at {TEMP_DIR} ({_fmt_size(free)}) "
                    f"is below estimated need ({_fmt_size(required)}). "
                    f"Pipeline may fail mid-run on disk-full."
                )
        except OSError:
            pass

        if warnings:
            job.append_log("\n".join(warnings) + "\n")

    def _resource_limits(self) -> tuple[int, int]:
        """Return (thread_count, nice_level) from user config + presets."""
        try:
            cfg = json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cpu = os.cpu_count() or 1
        mode = cfg.get("resource_mode", "full")
        preset_threads = max(1, cpu // 2) if mode == "background" else cpu
        preset_nice = 10 if mode == "background" else 0
        raw_threads = cfg.get("osmium_threads_override")
        raw_nice = cfg.get("nice_override")
        threads = int(raw_threads) if raw_threads else preset_threads
        nice = int(raw_nice) if raw_nice is not None else preset_nice
        return max(1, min(cpu, threads)), max(0, min(19, nice))

    async def _start_phase(self, job: FilterJob) -> None:
        if job.current_phase_index >= len(job.phases):
            return
        job.phase_started_at = time.time()
        job.output_bytes = None
        n, m = job.current_phase_index + 1, len(job.phases)
        label = job.phases[job.current_phase_index].label
        job.append_log(f"{_ts()}--- Phase {n}/{m}: {label} ---\n")
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

    async def _finish_phase(self, job: FilterJob) -> None:
        idx = job.current_phase_index
        if idx >= len(job.phases) or job.phase_started_at is None:
            return
        phase = job.phases[idx]
        duration = max(0.0, time.time() - job.phase_started_at)
        phase.duration_seconds = duration
        size = self._source_size(phase.source)
        try:
            self._history.record(phase.source, size, phase.step, phase.fmt, duration)
        except Exception as exc:
            _log.warning("filter_history.record failed: %s", exc)
        n, m = idx + 1, len(job.phases)
        job.append_log(f"{_ts()}Phase {n}/{m} done in {duration:.1f}s\n")
        job.current_phase_index += 1
        job.phase_started_at = None
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

    def _build_phases(self, job: FilterJob) -> list[Phase]:
        phases: list[Phase] = []
        for source in job.source_files:
            source_path = DATA_DIR / source
            try:
                size = source_path.stat().st_size
            except OSError:
                size = 1  # fallback weight if file not yet accessible

            needs_pbf = "pbf" in job.output_formats
            non_pbf = [f for f in job.output_formats if f != "pbf"]

            if not needs_pbf and not non_pbf:
                continue  # no outputs for this source — run_job will also skip it

            phases.append(
                Phase(
                    label=f"{source} · filter",
                    source=source,
                    step="filter",
                    weight=size * self._STEP_FACTORS["filter"],
                    fmt="pbf",
                )
            )

            if job.exclude_tags:
                phases.append(
                    Phase(
                        label=f"{source} · exclude",
                        source=source,
                        step="exclude",
                        weight=size * self._STEP_FACTORS["exclude"],
                        fmt="pbf",
                    )
                )

            if needs_pbf and job.columns_mode == "manual" and job.manual_keys:
                phases.append(
                    Phase(
                        label=f"{source} · reduce",
                        source=source,
                        step="reduce",
                        weight=size * self._STEP_FACTORS["reduce"],
                        fmt="pbf",
                    )
                )

            for fmt in non_pbf:
                phases.append(
                    Phase(
                        label=f"{source} · export {fmt}",
                        source=source,
                        step="export_convert",
                        weight=size * self._STEP_FACTORS["export_convert"],
                        fmt=fmt,
                    )
                )

        return phases

    def _build_expressions(
        self, job: FilterJob, *, kind: Literal["include", "exclude"] = "include"
    ) -> list[str]:
        prefix_map = {"nodes": "n", "ways": "w", "relations": "r"}
        exprs = []
        tags = job.tags if kind == "include" else job.exclude_tags
        for tag in tags:
            tag = tag.strip()
            if not tag:
                continue
            if not _VALID_TAG.match(tag):
                raise ValueError(f"Invalid tag expression: {tag!r}")
            for gtype in job.geometry_types:
                p = prefix_map.get(gtype)
                if p is None:
                    raise ValueError(f"Unknown geometry type: {gtype}")
                exprs.append(f"{p}/{tag}")
        return exprs

    async def _get_fields(self, path: Path) -> list[str]:
        """Return field names from a GeoJSON layer via ogrinfo, excluding @id."""
        proc = await asyncio.create_subprocess_exec(
            "ogrinfo",
            "-al",
            "-so",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        # Require a known GDAL type on the right side to avoid matching header
        # lines like "INFO: Open of ..." and "Geometry: Unknown (any)".
        _gdal_type = re.compile(
            r"^([A-Za-z_@][\w:.\-]*): " r"(String|Integer(?:64)?|Real|Date(?:Time)?|Time|Binary)"
        )
        fields = []
        for line in stdout.decode().splitlines():
            m = _gdal_type.match(line)
            if m and m.group(1) != "@id":
                fields.append(m.group(1))
        return fields

    def _curated_keys(self, job: FilterJob) -> list[str]:
        """Standard-mode column set: name plus the keys of the include tags.

        Everything else stays queryable inside the other_tags JSON column.
        """
        keys = ["name"]
        for tag in job.tags:
            key = tag.strip().split("=", 1)[0]
            if key and key not in keys:
                keys.append(key)
        return keys

    def _build_export_sql(self, layer: str, job: FilterJob, fields: list[str]) -> str:
        """Build SELECT SQL prepending fid and osm_id before user-selected or all fields."""

        def q(f: str) -> str:
            return f'"{f}"'

        if job.columns_mode == "manual" and job.manual_keys:
            available = set(fields)
            col_list = ", ".join(q(k) for k in job.manual_keys if k in available)
        else:
            col_list = ", ".join(q(f) for f in fields)

        suffix = f", {col_list}" if col_list else ""
        return f'SELECT "@id" AS osm_id{suffix} FROM {q(layer)}'

    async def _reduce_pbf_tags(self, pbf_path: Path, job: FilterJob) -> int:
        """Use pyosmium to strip all tags not in job.manual_keys from a PBF file in-place."""
        from pbf_tag_reducer import reduce_tags  # imported here to keep module-level clean

        keep = set(job.manual_keys)
        tmp_out = pbf_path.with_suffix(".tmp.osm.pbf")
        job.append_log(f"\n$ [pyosmium] reduce PBF tags → keep={sorted(keep)}\n")
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._pyosmium_executor,
                reduce_tags,
                str(pbf_path),
                str(tmp_out),
                keep,
            )
            tmp_out.replace(pbf_path)
            return 0
        except Exception as exc:
            job.append_log(f"ERROR in tag reduction: {exc}\n")
            tmp_out.unlink(missing_ok=True)
            return 1

    def _embed_attribution(self, path: Path, fmt: str) -> None:
        if fmt == "gpkg":
            self._embed_attribution_gpkg(path)
        elif fmt == "geojson":
            self._embed_attribution_geojson(path)

    def _embed_attribution_gpkg(self, path: Path) -> None:
        try:
            # closing() around connect(): the connection's own context manager
            # commits the transaction but does not close the handle, leaving it
            # to the garbage collector.
            with closing(sqlite3.connect(str(path))) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gpkg_metadata (
                        id INTEGER PRIMARY KEY ASC NOT NULL,
                        md_scope TEXT NOT NULL DEFAULT 'dataset',
                        md_standard_uri TEXT NOT NULL,
                        mime_type TEXT NOT NULL DEFAULT 'text/xml',
                        metadata TEXT NOT NULL DEFAULT ''
                    )
                """
                )
                conn.execute(
                    "INSERT INTO gpkg_metadata (md_scope, md_standard_uri, mime_type, metadata)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        "dataset",
                        "http://www.opengis.net/spec/GeoPackage/1.0/opt/metadata/1.0",
                        "text/plain",
                        ATTRIBUTION,
                    ),
                )
        except Exception as exc:
            _log.warning("Failed to embed attribution in GPkg: %s", exc)

    def _embed_attribution_geojson(self, path: Path) -> None:
        try:
            self._stream_inject_geojson_keys(path, {"attribution": ATTRIBUTION})
        except Exception as exc:
            _log.warning("Failed to embed attribution in GeoJSON: %s", exc)

    def _stream_inject_geojson_keys(self, path: Path, extras: dict) -> None:
        """Insert top-level keys before the `"features"` array.

        Streams 64-KB chunks to a sibling tmp file then atomically replaces the
        original. Peak RAM ~128 KB regardless of file size — critical for
        multi-GB GeoJSON outputs where json.loads() blows the heap.
        """
        if not extras:
            return
        fragment = json.dumps(extras, ensure_ascii=False)[1:-1].encode("utf-8") + b","
        target = b'"features"'
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        chunk_size = 64 * 1024
        try:
            with open(path, "rb") as src, open(tmp_path, "wb") as dst:
                injected = False
                carry = b""
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    buf = carry + chunk
                    if not injected:
                        idx = buf.find(target)
                        if idx >= 0:
                            dst.write(buf[:idx])
                            dst.write(fragment)
                            dst.write(buf[idx:])
                            injected = True
                            carry = b""
                        else:
                            keep = len(target) - 1
                            if len(buf) > keep:
                                dst.write(buf[:-keep])
                                carry = buf[-keep:]
                            else:
                                carry = buf
                    else:
                        dst.write(chunk)
                if carry:
                    dst.write(carry)
                if not injected:
                    raise ValueError(f'"features" key not found in {path}')
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _embed_provenance(
        self,
        path: Path,
        fmt: str,
        source_file: str,
        tags: list[str],
        exclude_tags: list[str],
        geometry_types: list[str],
    ) -> None:
        """Embed provenance metadata (source, filter params, timestamp) into output files.

        GPKG: second row in gpkg_metadata with JSON payload.
        GeoJSON: top-level 'provenance' key on the FeatureCollection.
        PBF: no-op (binary format, no metadata container).
        """
        provenance = {
            "generated_by": "PBF Forge v1.0.0",
            "source": source_file,
            "tags": tags,
            "exclude_tags": exclude_tags,
            "geometry_types": geometry_types,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if fmt == "gpkg":
            self._embed_provenance_gpkg(path, provenance)
        elif fmt == "geojson":
            self._embed_provenance_geojson(path, provenance)

    def _embed_provenance_gpkg(self, path: Path, provenance: dict) -> None:
        try:
            # See _embed_attribution_gpkg: connect() alone never closes.
            with closing(sqlite3.connect(str(path))) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gpkg_metadata (
                        id INTEGER PRIMARY KEY ASC NOT NULL,
                        md_scope TEXT NOT NULL DEFAULT 'dataset',
                        md_standard_uri TEXT NOT NULL,
                        mime_type TEXT NOT NULL DEFAULT 'text/xml',
                        metadata TEXT NOT NULL DEFAULT ''
                    )
                """
                )
                conn.execute(
                    "INSERT INTO gpkg_metadata (md_scope, md_standard_uri, mime_type, metadata)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        "dataset",
                        "http://www.opengis.net/spec/GeoPackage/1.0/opt/metadata/1.0",
                        "application/json",
                        json.dumps(provenance, ensure_ascii=False),
                    ),
                )
        except Exception as exc:
            _log.warning("Failed to embed provenance in GPkg: %s", exc)

    def _embed_provenance_geojson(self, path: Path, provenance: dict) -> None:
        try:
            self._stream_inject_geojson_keys(path, {"provenance": provenance})
        except Exception as exc:
            _log.warning("Failed to embed provenance in GeoJSON: %s", exc)

    def _write_output_reports(
        self,
        job: FilterJob,
        published: list[tuple[Path, str, str]],
        finished_at: datetime,
        duration_seconds: float,
    ) -> None:
        """Write a '<output filename>.txt' report beside every published file.

        Best-effort, like the metadata embedding above: the report is a
        convenience, so a disk error here must not turn a finished job into a
        failed one. An existing report of the same name is overwritten.
        """
        host_root = host_data_dir()
        for out_path, source, fmt in published:
            # with_name, not with_suffix: 'berlin_barge.osm.pbf' must become
            # 'berlin_barge.osm.pbf.txt', not 'berlin_barge.osm.txt'.
            report_path = out_path.with_name(out_path.name + ".txt")
            try:
                text = self._render_output_report(
                    job,
                    out_path,
                    source,
                    fmt,
                    finished_at=finished_at,
                    duration_seconds=duration_seconds,
                    host_root=host_root,
                )
                report_path.write_text(text, encoding="utf-8")
            except Exception as exc:
                _log.warning("Could not write output report %s: %s", report_path, exc)

    def _render_output_report(
        self,
        job: FilterJob,
        out_path: Path,
        source: str,
        fmt: str,
        *,
        finished_at: datetime,
        duration_seconds: float,
        host_root: str,
    ) -> str:
        """Render one report body. Touches disk only to size and date the files."""
        try:
            out_size = _fmt_size(out_path.stat().st_size)
        except OSError:
            out_size = "unknown"

        lines = ["=" * _REPORT_WIDTH, "  PBF FORGE - OUTPUT REPORT", "=" * _REPORT_WIDTH, ""]

        lines.append("OUTPUT")
        lines += _report_row("File", [out_path.name])
        lines += _report_row("Folder", [to_host_path(str(out_path.parent), host_root)])
        lines += _report_row("Format", [_FMT_LABEL.get(fmt, fmt.upper())])
        lines += _report_row("Size", [out_size])
        lines.append("")

        lines.append("INPUT")
        source_size = _fmt_size(self._source_size(source))
        lines += _report_row("Source extract", [f"{source}  ({source_size})"])
        data_timestamp = self._source_data_timestamp(source)
        if data_timestamp:
            lines += _report_row("Data timestamp", [data_timestamp])
        lines.append("")

        lines.append("FILTER")
        if job.tags:
            lines += _report_row("Include tags", list(job.tags))
        if job.exclude_tags:
            lines += _report_row("Exclude tags", list(job.exclude_tags))
        if job.geometry_types:
            lines += _report_row("Geometry types", [", ".join(job.geometry_types)])
        lines += _report_row("Attributes", [_attributes_description(job, fmt)])
        if job.suffix:
            lines += _report_row("Filename suffix", [job.suffix])
        lines.append("")

        lines.append("JOB")
        lines += _report_row("Job ID", [job.id])
        lines += _report_row("Completed", [finished_at.isoformat(sep=" ", timespec="seconds")])
        lines += _report_row(
            "Job duration",
            [f"{_fmt_duration(duration_seconds)}  (whole job, all output files)"],
        )
        if job.log_file:
            lines += _report_row("Job log", [Path(job.log_file).name])
        lines.append("")

        # Phases that actually fed this file: the shared filter/exclude/reduce
        # passes for its source, plus its own export pass — never another
        # format's export.
        phases = [
            p
            for p in job.phases
            if p.source == source and (p.step != "export_convert" or p.fmt == fmt)
        ]
        if phases:
            lines.append("PHASES (this output)")
            for phase in phases:
                label = phase.label.split(" · ", 1)[-1]
                dur = (
                    "-" if phase.duration_seconds is None else _fmt_duration(phase.duration_seconds)
                )
                lines.append(f"  {label}".ljust(_REPORT_WIDTH - len(dur)) + dur)
            lines.append("")

        lines += ["-" * _REPORT_WIDTH, "Generated by PBF Forge v1.0.0", ATTRIBUTION, ""]
        return "\n".join(lines)

    async def cancel_job(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        job = self._jobs.get(job_id)
        if proc:
            try:
                proc.kill()
            except OSError:
                pass
        if job and job.status == "running":
            job.status = "error"
            job.error = "Cancelled by user"
            self._persist_jobs()
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            return True
        return False

    def _append_kernel_log_snapshot(self, job: FilterJob) -> None:
        """Best-effort: tail kernel log to capture OOM-killer messages.

        Tries `journalctl -k` first (works rootless on most systemd distros),
        falls back to `dmesg`. Both are Linux-only; silently no-ops elsewhere.
        """
        import subprocess

        for cmd in (
            ["journalctl", "-k", "--since", "5 min ago", "--no-pager", "-n", "50"],
            ["dmesg", "-T"],
        ):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                continue
            out = (result.stdout or "").strip()
            if not out:
                continue
            tail = "\n".join(out.splitlines()[-50:])
            job.append_log(f"\n--- kernel log tail ({cmd[0]}) ---\n{tail}\n--- end ---\n")
            return

    async def _run_cmd(self, cmd: list[str], job: FilterJob, watch_path: Path | None = None) -> int:
        if job._nice_level > 0:
            cmd = ["nice", "-n", str(job._nice_level), *cmd]
        job.append_log(f"\n$ {' '.join(cmd)}\n")
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=job._proc_env or None,
        )
        if proc.stdout is None:
            raise RuntimeError("subprocess stdout is None despite PIPE")

        self._procs[job.id] = proc
        job.last_activity_at = time.time()
        job.progress_line = None

        def _safe_size(path: Path) -> int:
            try:
                return path.stat().st_size
            except OSError:
                return -1

        async def _heartbeat() -> None:
            last_size = -1
            last_io = -1
            warned = False
            loop = asyncio.get_running_loop()
            stat_fut = None
            try:
                while True:
                    await asyncio.sleep(STALL_CHECK_INTERVAL)
                    activity = False
                    if watch_path is not None:
                        # stat() can block indefinitely on a hung bind mount —
                        # keep it off the event loop, at most one in flight.
                        if stat_fut is None:
                            stat_fut = loop.run_in_executor(None, _safe_size, watch_path)
                        if stat_fut.done():
                            size = stat_fut.result()
                            stat_fut = None
                            if size > last_size:
                                last_size = size
                                job.output_bytes = size
                                activity = True
                    if proc.returncode is None:
                        io_bytes = _proc_io_bytes(proc.pid)
                        if io_bytes is not None and io_bytes > last_io:
                            last_io = io_bytes
                            job.bytes_read = io_bytes
                            activity = True
                    if activity:
                        job.last_activity_at = time.time()
                    quiet = time.time() - (job.last_activity_at or 0)
                    if quiet <= STALL_WARN_SECONDS:
                        warned = False
                    elif not warned:
                        warned = True
                        job.append_log(
                            f"{_ts()}WARNING: no observable progress for "
                            f"{int(quiet)}s (no log output, no output-file "
                            f"growth, no process IO). The job keeps running — "
                            f"it is only stopped at the absolute "
                            f"{int(ABSOLUTE_TIMEOUT_SECONDS / 3600)}h limit. "
                            f"Cancel manually if you believe it is stuck.\n"
                        )
                    await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            except asyncio.CancelledError:
                pass

        async def _read_output() -> None:
            loop = asyncio.get_running_loop()
            last_broadcast = loop.time()
            buf = b""
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                job.last_activity_at = time.time()
                parts = re.split(rb"(\r\n|\r|\n)", buf)
                buf = parts[-1]
                for i in range(0, len(parts) - 1, 2):
                    text = parts[i].decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    if parts[i + 1] == b"\r":
                        # \r-terminated redraws (osmium --progress bar) are
                        # ephemeral: keep only the latest, never in the log.
                        job.progress_line = text
                    else:
                        job.append_log(text + "\n")
                now = loop.time()
                if now - last_broadcast >= 0.5:
                    await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
                    last_broadcast = now
            if buf:
                job.append_log(buf.decode("utf-8", errors="replace") + "\n")

        async def _consume_and_wait() -> None:
            # proc.wait() must sit under the same absolute budget: a process
            # that closes its pipes but never exits would otherwise hang the
            # job slot forever.
            await _read_output()
            await proc.wait()

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if job.timeout_seconds is not None:
                await asyncio.wait_for(_consume_and_wait(), timeout=job.timeout_seconds)
            else:
                await _consume_and_wait()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except OSError:
                pass
            # Reap the killed process. Without this the child is never awaited,
            # so asyncio keeps its stdout pipe transport alive with a read still
            # in flight — an unclosed transport and a lingering process handle
            # for every job that hits the absolute limit.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=KILL_REAP_SECONDS)
            raise RuntimeError(
                f"Subprocess exceeded absolute limit of {job.timeout_seconds / 3600:.0f}h"
            )
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._procs.pop(job.id, None)

        rc = proc.returncode
        if rc is not None and rc < 0:
            sig = -rc
            sig_names = {9: "SIGKILL (likely OOM)", 15: "SIGTERM", 11: "SIGSEGV", 6: "SIGABRT"}
            label = sig_names.get(sig, f"signal {sig}")
            job.append_log(
                f"\n*** Subprocess killed by {label}. "
                f"Inspect: `dmesg | grep -i 'killed process'` or "
                f"`journalctl -k --since '5 min ago'` ***\n"
            )
        return rc
