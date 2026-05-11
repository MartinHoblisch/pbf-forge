from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_MAX_ENTRIES = 200


class FilterHistory:
    """Persistent store of per-phase durations for history-based analysis."""

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
