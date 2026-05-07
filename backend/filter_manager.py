from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Literal, Optional

from config import ATTRIBUTION, CONFIG_DIR, DATA_DIR
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

# Column names written into osmconf INI files must be safe identifiers.
_VALID_KEY = re.compile(r"^[a-zA-Z0-9_:.\-]+$")


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
    eta_seconds: float | None = None
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
            "eta_seconds": self.eta_seconds,
        }


class FilterManager:
    def __init__(self, ws_manager) -> None:
        self._ws = ws_manager
        self._jobs: dict[str, FilterJob] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        _old_history = DATA_DIR / ".filter_history.json"
        _new_history = CONFIG_DIR / ".filter_history.json"
        if not _new_history.exists() and _old_history.exists():
            shutil.copy(_old_history, _new_history)
        self._history = FilterHistory(_new_history)

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in reversed(list(self._jobs.values()))]

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
            base = source.replace(".osm.pbf", "")
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
        job.status = "running"
        job.phases = self._build_phases(job)
        job.current_phase_index = 0
        job.job_started_at = time.time()
        job.eta_seconds = self._compute_eta(job)
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

        out_dir = Path(job.output_dir)
        try:
            for fmt in job.output_formats:
                (out_dir / fmt).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            job.status = "error"
            job.error = f"Could not create output directory: {exc}"
            job.eta_seconds = None
            await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
            return

        try:
            with tempfile.TemporaryDirectory() as _tmp:
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

                    base = source.replace(".osm.pbf", "")
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

                    for fmt in non_pbf:
                        if fmt == "geojson":
                            out_file = out_dir / "geojson" / f"{stem}.geojson"
                        else:
                            out_file = out_dir / "gpkg" / f"{stem}.gpkg"

                        out_file.unlink(missing_ok=True)
                        await self._start_phase(job)
                        if fmt == "geojson" and job.columns_mode in ("other_tags", "all"):
                            # GeoJSON: Standard and Expand-all are identical (all tags exported).
                            # Route through _osmium_export_convert so @id is renamed to osm_id.
                            rc = await self._osmium_export_convert(
                                intermediate, fmt, out_file, job, tmp
                            )
                        elif fmt == "gpkg" and job.columns_mode == "other_tags":
                            # GPKG Standard: GDAL OSM driver with other_tags HSTORE column.
                            ogr_cmd = self._build_ogr_cmd(
                                fmt, str(out_file), str(intermediate), job, tmp
                            )
                            rc = await self._run_cmd(ogr_cmd, job)
                        else:
                            # Expand-all+GPKG and manual+GeoJSON/GPKG: osmium export → SELECT.
                            rc = await self._osmium_export_convert(
                                intermediate, fmt, out_file, job, tmp
                            )
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

            job.status = "done"
            job.finished_at = datetime.now().strftime("%H:%M")
            job.eta_seconds = 0.0

        except Exception as exc:
            job.status = "error"
            job.finished_at = datetime.now().strftime("%H:%M")
            job.error = str(exc)
            job.eta_seconds = None
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
        job.eta_seconds = self._compute_eta(job)
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
        job.current_phase_index += 1
        job.phase_started_at = None
        job.eta_seconds = self._compute_eta(job)
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})

    def _compute_eta(self, job: FilterJob) -> float | None:
        """Sum predicted durations for remaining phases, scaled by actual-vs-predicted
        ratio of completed phases. Returns None if any remaining phase has no
        prediction and no same-kind phase has completed in this job yet."""
        if not job.phases:
            return None
        completed = job.phases[: job.current_phase_index]
        remaining = job.phases[job.current_phase_index :]
        if not remaining:
            return 0.0

        actual_total = 0.0
        predicted_total = 0.0
        for p in completed:
            if p.duration_seconds is None:
                continue
            pred = self._history.predict(self._source_size(p.source), p.step, p.fmt)
            if pred is not None and pred > 0:
                actual_total += p.duration_seconds
                predicted_total += pred
        scale = (actual_total / predicted_total) if predicted_total > 0 else 1.0

        eta = 0.0
        for p in remaining:
            pred = self._history.predict(self._source_size(p.source), p.step, p.fmt)
            if pred is not None:
                eta += pred * scale
                continue
            same_kind = [
                c.duration_seconds
                for c in completed
                if c.step == p.step and c.fmt == p.fmt and c.duration_seconds is not None
            ]
            if same_kind:
                eta += median(same_kind)
            else:
                return None

        if job.phase_started_at is not None and remaining:
            current_phase = remaining[0]
            current_pred = self._history.predict(
                self._source_size(current_phase.source), current_phase.step, current_phase.fmt
            )
            if current_pred is None:
                same = [
                    c.duration_seconds
                    for c in completed
                    if c.step == current_phase.step
                    and c.fmt == current_phase.fmt
                    and c.duration_seconds is not None
                ]
                current_pred = median(same) if same else None
            elapsed_current = time.time() - job.phase_started_at
            cap = current_pred * scale if current_pred is not None else elapsed_current
            eta = max(0.0, eta - min(elapsed_current, cap))
        return eta

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

    async def _osmium_export_convert(
        self, src: Path, fmt: str, out_file: Path, job: FilterJob, tmp: Path
    ) -> int:
        """osmium export → ogrinfo → ogr2ogr SQL to ensure fid/osm_id come first."""
        tmp_geojson = tmp / f"{out_file.stem}_export.geojson"
        try:
            rc = await self._run_cmd(
                [
                    "osmium",
                    "export",
                    "--format=geojson",
                    "--attributes",
                    "id",
                    "-o",
                    str(tmp_geojson),
                    str(src),
                    "--overwrite",
                ],
                job,
            )
            if rc != 0:
                return rc

            fields = await self._get_fields(tmp_geojson)
            sql = self._build_export_sql(tmp_geojson.stem, job, fields)

            ogr_fmt = "GeoJSON" if fmt == "geojson" else "GPKG"
            cmd = ["ogr2ogr", "-f", ogr_fmt, str(out_file), str(tmp_geojson), "-sql", sql]
            if fmt == "gpkg":
                cmd += ["-nln", out_file.stem, "-a_srs", "EPSG:4326"]
            return await self._run_cmd(cmd, job)
        finally:
            tmp_geojson.unlink(missing_ok=True)

    async def _osmium_export_direct(self, src: Path, out_file: Path, job: FilterJob) -> int:
        """Export PBF to GeoJSON via osmium export without any column filtering."""
        return await self._run_cmd(
            [
                "osmium",
                "export",
                "--format=geojson",
                "--attributes",
                "id",
                "-o",
                str(out_file),
                str(src),
                "--overwrite",
            ],
            job,
        )

    async def _reduce_pbf_tags(self, pbf_path: Path, job: FilterJob) -> int:
        """Use pyosmium to strip all tags not in job.manual_keys from a PBF file in-place."""
        from pbf_tag_reducer import reduce_tags  # imported here to keep module-level clean

        keep = set(job.manual_keys)
        tmp_out = pbf_path.with_suffix(".tmp.osm.pbf")
        job.append_log(f"\n$ [pyosmium] reduce PBF tags → keep={sorted(keep)}\n")
        await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, reduce_tags, str(pbf_path), str(tmp_out), keep)
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
            cmd += ["-a_srs", "EPSG:4326"]
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
            except ProcessLookupError:
                pass
        if job and job.status == "running":
            job.status = "error"
            job.error = "Cancelled by user"
            job.eta_seconds = None
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
        try:
            loop = asyncio.get_running_loop()
            last_broadcast = loop.time()
            async for line in proc.stdout:
                job.append_log(line.decode("utf-8", errors="replace"))
                now = loop.time()
                if now - last_broadcast >= 0.5:
                    await self._ws.broadcast({"type": "filter_update", "job": job.to_dict()})
                    last_broadcast = now
        finally:
            self._procs.pop(job.id, None)

        await proc.wait()
        return proc.returncode
