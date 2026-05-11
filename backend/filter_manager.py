from __future__ import annotations

import asyncio
import collections
import gc
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from config import (
    ATTRIBUTION,
    CONFIG_DIR,
    DATA_DIR,
    TEMP_DIR,
)
from filter_history import FilterHistory

_log = logging.getLogger(__name__)

_FMT_EXT = {
    "pbf": ".osm.pbf",
    "geojson": ".geojson",
    "gpkg": ".gpkg",
}

# OSM tag expressions passed to osmium tags-filter must not start with '-'
# (which would be interpreted as a CLI flag) or contain shell-special chars.
_VALID_TAG = re.compile(r"^[^-\s\n\r\x00][^\n\r\x00]*$")

# Matches osmium/ogr2ogr progress lines such as "[======>   ] 45%" or "45%"
_PROGRESS_RE = re.compile(r"\]\s*(\d{1,3})%|^(\d{1,3})%\s*$")

# Column names written into osmconf INI files must be safe identifiers.
_VALID_KEY = re.compile(r"^[a-zA-Z0-9_:.\-]+$")


def _ts() -> str:
    return datetime.now().strftime("[%H:%M:%S] ")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@dataclass
class Phase:
    label: str
    source: str
    step: str  # filter | reduce | export_convert
    weight: float
    fmt: str = "pbf"  # output format associated with this phase (for ETA grouping)
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
    phase_percent: int | None = None
    speed_bps: float | None = None
    timeout_seconds: float | None = None
    queue_position: int | None = None
    _log_parts: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def log(self) -> str:
        return "".join(self._log_parts)

    def append_log(self, text: str) -> None:
        self._log_parts.append(text)

    def to_dict(self) -> dict:
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
            "output_files": self.output_files,
            "error": self.error,
            "phases": [p.to_dict() for p in self.phases],
            "current_phase_index": self.current_phase_index,
            "phase_started_at": self.phase_started_at,
            "job_started_at": self.job_started_at,
            "phase_percent": self.phase_percent,
            "speed_bps": self.speed_bps,
            "queue_position": self.queue_position,
        }


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

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in reversed(list(self._jobs.values()))]

    def clear_completed_jobs(self) -> None:
        keep = {"running", "pending"}
        self._jobs = {jid: j for jid, j in self._jobs.items() if j.status in keep}

    def get_job(self, job_id: str) -> Optional[dict]:
        j = self._jobs.get(job_id)
        return j.to_dict() if j else None

    def create_job(self, **kwargs) -> FilterJob:
        if "manual_keys" in kwargs:
            kwargs["manual_keys"] = [k.strip() for k in kwargs["manual_keys"] if k.strip()]
        job = FilterJob(id=str(uuid.uuid4()), **kwargs)
        self._jobs[job.id] = job
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

    async def run_job(self, job: FilterJob) -> None:
        if self._running_count >= self._max_parallel:
            job.status = "queued"
            job.queue_position = sum(1 for j in self._jobs.values() if j.status == "queued")
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        async with self._semaphore:
            self._running_count += 1
            try:
                await self._execute_job(job)
            finally:
                self._running_count -= 1

    async def _execute_job(self, job: FilterJob) -> None:
        job.status = "running"
        job.queue_position = None
        job.phases = self._build_phases(job)
        job.current_phase_index = 0
        job.job_started_at = time.time()
        job.phase_started_at = job.job_started_at  # ticker starts immediately
        total_bytes = sum(self._source_size(s) for s in job.source_files)
        job.timeout_seconds = max(300.0, total_bytes / (10 * 1024 * 1024))

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
        lines.append(f"{_ts()}Timeout  : {int(job.timeout_seconds)}s")
        job.append_log("\n".join(lines) + "\n")

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
                        await self._start_phase(job)
                        cmd = [
                            "osmium",
                            "tags-filter",
                            str(source_path),
                            *exprs,
                            "-o",
                            str(pbf_out),
                            "--overwrite",
                        ]
                        rc = await self._run_cmd(cmd, job)
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
                                str(pbf_out),
                                *excl_exprs,
                                "--invert-match",
                                "-o",
                                str(excl_tmp),
                                "--overwrite",
                            ]
                            rc = await self._run_cmd(excl_cmd, job)
                            if rc != 0:
                                raise RuntimeError(f"osmium exclude pass exited with code {rc}")
                            shutil.move(str(excl_tmp), str(pbf_out))
                            await self._finish_phase(job)

                        if job.columns_mode == "manual" and job.manual_keys:
                            await self._start_phase(job)
                            rc = await self._reduce_pbf_tags(pbf_out, job)
                            if rc != 0:
                                raise RuntimeError("PBF tag reduction failed")
                            await self._finish_phase(job)
                        job.output_files.append(str(pbf_out))
                        intermediate = pbf_out
                    elif non_pbf:
                        intermediate = tmp / f"{stem}.osm.pbf"
                        await self._start_phase(job)
                        cmd = [
                            "osmium",
                            "tags-filter",
                            str(source_path),
                            *exprs,
                            "-o",
                            str(intermediate),
                            "--overwrite",
                        ]
                        rc = await self._run_cmd(cmd, job)
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
                                str(intermediate),
                                *excl_exprs,
                                "--invert-match",
                                "-o",
                                str(excl_tmp),
                                "--overwrite",
                            ]
                            rc = await self._run_cmd(excl_cmd, job)
                            if rc != 0:
                                raise RuntimeError(f"osmium exclude pass exited with code {rc}")
                            intermediate = excl_tmp
                            await self._finish_phase(job)
                    else:
                        continue

                    # G2: run osmium export at most once per source, share across formats.
                    shared_geojson: Path | None = None
                    shared_fields: list[str] = []

                    for fmt in non_pbf:
                        if fmt == "geojson":
                            out_file = out_dir / "geojson" / f"{stem}.geojson"
                        else:
                            out_file = out_dir / "gpkg" / f"{stem}.gpkg"

                        out_file.unlink(missing_ok=True)
                        await self._start_phase(job)

                        if fmt == "gpkg" and job.columns_mode == "other_tags":
                            # GPKG Standard: GDAL OSM driver with other_tags HSTORE column.
                            ogr_cmd = self._build_ogr_cmd(
                                fmt, str(out_file), str(intermediate), job, tmp
                            )
                            rc = await self._run_cmd(ogr_cmd, job)
                        else:
                            # osmium export path (all non-PBF formats except other_tags+GPKG).
                            # Run export once; reuse shared_geojson for subsequent formats.
                            if shared_geojson is None:
                                shared_geojson = tmp / f"{stem}_export.geojson"
                                rc = await self._run_cmd(
                                    [
                                        "osmium",
                                        "export",
                                        "--format=geojson",
                                        "--attributes",
                                        "id",
                                        "-o",
                                        str(shared_geojson),
                                        str(intermediate),
                                        "--overwrite",
                                    ],
                                    job,
                                )
                                if rc != 0:
                                    raise RuntimeError(f"osmium export exited with code {rc}")
                                shared_fields = await self._get_fields(shared_geojson)

                            sql = self._build_export_sql(shared_geojson.stem, job, shared_fields)
                            ogr_fmt = "GeoJSON" if fmt == "geojson" else "GPKG"
                            cmd = [
                                "ogr2ogr",
                                "-f",
                                ogr_fmt,
                                str(out_file),
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
                            rc = await self._run_cmd(cmd, job)

                        if rc != 0:
                            raise RuntimeError(f"Conversion exited with code {rc}")
                        await self._finish_phase(job)
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, self._embed_attribution, out_file, fmt)
                        await loop.run_in_executor(
                            None,
                            self._embed_provenance,
                            out_file,
                            fmt,
                            source,
                            job.tags,
                            job.exclude_tags,
                            job.geometry_types,
                        )
                        job.output_files.append(str(out_file))
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

            job.status = "done"
            job.finished_at = datetime.now().strftime("%H:%M")

        except Exception as exc:
            job.status = "error"
            job.finished_at = datetime.now().strftime("%H:%M")
            job.error = str(exc)
            job.phase_started_at = None  # prevent stale elapsed ticker on client
            job.append_log(f"\nERROR: {exc}\n")

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

    async def _start_phase(self, job: FilterJob) -> None:
        if job.current_phase_index >= len(job.phases):
            return
        job.phase_started_at = time.time()
        job.phase_percent = None
        job.speed_bps = None
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
        job.speed_bps = None
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

    def _build_ogr_cmd(
        self, fmt: str, out_file: str, src: str, job: FilterJob, tmp: Path
    ) -> list[str]:
        """Build ogr2ogr command for other_tags+GPKG: GDAL OSM driver default schema.

        The GDAL OSM driver reads PBF and creates up to five layers by geometry
        type: points, lines, multilinestrings, multipolygons, other_relations.
        Each layer is stored as a separate table inside the GeoPackage.
        CRS is declared as EPSG:4326 (WGS 84) via -a_srs.
        """
        ogr_fmt = "GeoJSON" if fmt == "geojson" else "GPKG"
        cmd = ["ogr2ogr", "-f", ogr_fmt, out_file, src]
        if fmt == "gpkg":
            cmd += [
                "-a_srs",
                "EPSG:4326",
                "-gt",
                "65536",
                "--config",
                "OGR_SQLITE_SYNCHRONOUS",
                "OFF",
            ]
        return cmd

    def _osmconf(self, keys: list[str]) -> str:
        for key in keys:
            if not _VALID_KEY.match(key):
                raise ValueError(f"Invalid column name: {key!r}")
        keys_str = ",".join(keys)
        layers = ["points", "lines", "multilinestrings", "multipolygons", "other_relations"]
        sections = ["[general]\nattribute_name_laundering=yes\n"]
        for layer in layers:
            sections.append(f"[{layer}]\nosm_id=yes\nattributes={keys_str}\nother_tags=yes\n")
        return "\n".join(sections)

    def _embed_attribution(self, path: Path, fmt: str) -> None:
        if fmt == "gpkg":
            self._embed_attribution_gpkg(path)
        elif fmt == "geojson":
            self._embed_attribution_geojson(path)

    def _embed_attribution_gpkg(self, path: Path) -> None:
        try:
            with sqlite3.connect(str(path)) as conn:
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
            data = json.loads(path.read_text(encoding="utf-8"))
            data["attribution"] = ATTRIBUTION
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            _log.warning("Failed to embed attribution in GeoJSON: %s", exc)

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
            with sqlite3.connect(str(path)) as conn:
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
            data = json.loads(path.read_text(encoding="utf-8"))
            data["provenance"] = provenance
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            _log.warning("Failed to embed provenance in GeoJSON: %s", exc)

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
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            return True
        return False

    async def _run_cmd(self, cmd: list[str], job: FilterJob) -> int:
        job.append_log(f"\n$ {' '.join(cmd)}\n")
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if proc.stdout is None:
            raise RuntimeError("subprocess stdout is None despite PIPE")

        self._procs[job.id] = proc

        async def _heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(2)
                    await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            except asyncio.CancelledError:
                pass

        async def _read_output() -> None:
            loop = asyncio.get_running_loop()
            last_broadcast = loop.time()
            buf = b""
            total_bytes = sum(self._source_size(s) for s in job.source_files)
            # 10-second sliding window: (monotonic_time, bytes_processed)
            speed_window: collections.deque[tuple[float, float]] = collections.deque()
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                segments = re.split(rb"[\r\n]", buf)
                buf = segments[-1]
                for seg in segments[:-1]:
                    text = seg.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    job.append_log(text + "\n")
                    m = _PROGRESS_RE.search(text)
                    if m:
                        job.phase_percent = int(m.group(1) or m.group(2))
                        if total_bytes > 0:
                            now_mono = loop.time()
                            processed = (job.phase_percent / 100) * total_bytes
                            speed_window.append((now_mono, processed))
                            cutoff = now_mono - 10.0
                            while speed_window and speed_window[0][0] < cutoff:
                                speed_window.popleft()
                            if len(speed_window) >= 2:
                                t0, b0 = speed_window[0]
                                t1, b1 = speed_window[-1]
                                dt = t1 - t0
                                job.speed_bps = (b1 - b0) / dt if dt > 0 else None
                now = loop.time()
                if now - last_broadcast >= 0.5:
                    await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
                    last_broadcast = now
            if buf:
                job.append_log(buf.decode("utf-8", errors="replace") + "\n")

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if job.timeout_seconds is not None:
                await asyncio.wait_for(_read_output(), timeout=job.timeout_seconds)
            else:
                await _read_output()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except OSError:
                pass
            raise RuntimeError(f"Subprocess timed out after {job.timeout_seconds:.0f}s")
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._procs.pop(job.id, None)

        await proc.wait()
        return proc.returncode
