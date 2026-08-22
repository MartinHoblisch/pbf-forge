"""Sorting the download list by status orders outdated files by their real age.

The status column groups the rows. Inside "update available" the order has to
follow how far behind each file is, and that only works on the timestamps: a
file five days behind is labelled "5 days" and one thirty-five days behind is
labelled "1 month", so anything derived from the label would sort the month
ahead of the days.

The functions are lifted out of frontend/index.html and run under node, so this
exercises the shipped code rather than a copy of it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed on this machine"
)


def _function(src: str, name: str) -> str:
    """The full source of a top-level `function name(...)`, braces balanced."""
    start = src.index(f"function {name}(")
    i = src.index("{", start)
    depth = 0
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1


def _sorted_filenames(files: list[dict], direction: str) -> list[str]:
    src = FRONTEND.read_text(encoding="utf-8")
    status_order = re.search(r"const STATUS_ORDER = \[[^\]]*\];", src)
    assert status_order, "STATUS_ORDER is no longer declared as a flat array"

    script = "\n".join(
        [
            status_order.group(0),
            _function(src, "ageDelta"),
            _function(src, "getSortedFiles"),
            f"const files = {json.dumps(files)};",
            f"const currentSort = {json.dumps({'col': 'status', 'dir': direction})};",
            "console.log(JSON.stringify(getSortedFiles().map(f => f.filename)));",
        ]
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def _files() -> list[dict]:
    """Three outdated files and one current one, deliberately out of order."""
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)

    def behind(days: int) -> dict:
        return {
            "server_mtime": now.isoformat(),
            "local_mtime": (now - timedelta(days=days)).isoformat(),
            "status": "update_available",
        }

    return [
        {"filename": "europe.osm.pbf", **behind(35)},  # labelled "1 month"
        {"filename": "berlin.osm.pbf", **behind(5)},  # labelled "5 days"
        {"filename": "germany.osm.pbf", **behind(400)},  # labelled "1 year"
        {
            "filename": "bremen.osm.pbf",
            "server_mtime": now.isoformat(),
            "local_mtime": now.isoformat(),
            "status": "up_to_date",
        },
    ]


def test_outdated_files_sort_by_age_within_their_status():
    assert _sorted_filenames(_files(), "asc") == [
        "bremen.osm.pbf",  # up_to_date sorts ahead of update_available
        "berlin.osm.pbf",  # 5 days
        "europe.osm.pbf",  # 35 days, labelled "1 month"
        "germany.osm.pbf",  # 400 days
    ]


def test_the_descending_direction_reverses_the_age_too():
    assert _sorted_filenames(_files(), "desc") == [
        "germany.osm.pbf",
        "europe.osm.pbf",
        "berlin.osm.pbf",
        "bremen.osm.pbf",
    ]
