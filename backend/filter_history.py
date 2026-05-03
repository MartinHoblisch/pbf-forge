from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from statistics import median
from typing import Any

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_MAX_ENTRIES = 200
_PREDICT_N = 20
_SIZE_TOLERANCE = 0.30  # ±30%


class FilterHistory:
    """Persistent store of per-phase durations used for ETA prediction."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: list[dict[str, Any]] = []
        self._load()

    def record(
        self,
        source: str,
        source_size: int,
        step: str,
        fmt: str,
        duration_seconds: float,
    ) -> None:
        self._entries.append(
            {
                "source": source,
                "source_size": source_size,
                "step": step,
                "format": fmt,
                "duration_seconds": duration_seconds,
                "timestamp": time.time(),
            }
        )
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]
        self._save()

    def predict(self, source_size: int, step: str, fmt: str) -> float | None:
        """Return predicted duration in seconds, or None if insufficient data."""
        lo = source_size * (1 - _SIZE_TOLERANCE)
        hi = source_size * (1 + _SIZE_TOLERANCE)
        candidates = [
            e
            for e in self._entries
            if e["step"] == step and e["format"] == fmt and lo <= e["source_size"] <= hi
        ]
        recent = candidates[-_PREDICT_N:]
        if not recent:
            return None
        ref_size = median(e["source_size"] for e in recent)
        ref_duration = median(e["duration_seconds"] for e in recent)
        if ref_size == 0:
            return ref_duration
        return ref_duration * (source_size / ref_size)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("v") != _SCHEMA_VERSION:
                raise ValueError("schema mismatch")
            self._entries = data.get("entries", [])
        except Exception as exc:
            _log.warning("filter_history: corrupt store (%s), resetting", exc)
            self._rename_corrupt()
            self._entries = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps({"v": _SCHEMA_VERSION, "entries": self._entries}, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except Exception as exc:
            _log.error("filter_history: failed to save (%s)", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _rename_corrupt(self) -> None:
        ts = int(time.time())
        corrupt = self._path.with_name(f"{self._path.stem}.corrupt-{ts}.json")
        try:
            self._path.rename(corrupt)
        except OSError:
            pass
