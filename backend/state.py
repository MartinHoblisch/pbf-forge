"""Module-level singletons set during app startup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from download_manager import DownloadManager
    from filter_manager import FilterManager
    from ws_manager import ConnectionManager

ws_manager: "ConnectionManager | None" = None
download_manager: "DownloadManager | None" = None
filter_manager: "FilterManager | None" = None
