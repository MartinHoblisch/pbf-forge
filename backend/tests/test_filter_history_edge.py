"""Edge-case tests for filter_history.FilterHistory beyond the happy paths in
test_filter_history.py.

Bug class to prevent:
  - Unbounded growth of the entries list (no _MAX_ENTRIES cap actually applied)
    → JSON file grows without limit, parse-time degrades, RSS bloats.
  - Disk-write failure during record() bubbles up and aborts the FilterManager
    phase loop — instead it must log+swallow.
  - _rename_corrupt() failing (read-only mount, antivirus lock) propagates and
    prevents the in-memory reset → user sees corrupt store re-loaded forever.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import filter_history as fh_module
from filter_history import FilterHistory

# ── _MAX_ENTRIES trim ────────────────────────────────────────────────────────


def test_record_trims_to_max_entries(tmp_path):
    h = FilterHistory(tmp_path / "fh.json")
    cap = fh_module._MAX_ENTRIES

    for i in range(cap + 1):  # one over the cap
        h.record(f"f{i}.osm.pbf", 1000, "filter", "pbf", 1.0)

    assert len(h._entries) == cap
    # Oldest entry was f0; after trim it should be gone, f1 is now first
    assert h._entries[0]["source"] == "f1.osm.pbf"
    assert h._entries[-1]["source"] == f"f{cap}.osm.pbf"


def test_record_persists_trim_to_disk(tmp_path):
    path = tmp_path / "fh.json"
    h = FilterHistory(path)
    cap = fh_module._MAX_ENTRIES

    for i in range(cap + 5):
        h.record(f"f{i}.osm.pbf", 1000, "filter", "pbf", 1.0)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert len(on_disk["entries"]) == cap


# ── save failure ──────────────────────────────────────────────────────────────


def test_save_failure_does_not_raise(tmp_path):
    """An OSError during tmp.write_text (e.g. quota, read-only fs) must be
    logged and swallowed — the in-memory entry is still present, callers
    don't see an exception."""
    path = tmp_path / "fh.json"
    h = FilterHistory(path)

    # Patch Path.write_text on the .tmp path to fail
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        h.record("f.osm.pbf", 1000, "filter", "pbf", 1.0)  # must not raise

    # Entry is in memory, but file was never written
    assert len(h._entries) == 1
    assert not path.exists()


def test_save_failure_cleans_up_tmp_file(tmp_path):
    """If the tmp file got created but os.replace fails, the unlink fallback
    must remove it (or swallow OSError if unlink also fails)."""
    path = tmp_path / "fh.json"
    h = FilterHistory(path)

    real_replace = __import__("os").replace
    with patch("os.replace", side_effect=OSError("rename failed")):
        h.record("f.osm.pbf", 1000, "filter", "pbf", 1.0)

    # tmp may or may not exist depending on whether write succeeded; the
    # contract is "no crash, no permanent .tmp left over"
    assert not (path.with_suffix(".tmp")).exists()
    # Restore for safety in case any later test mocks os.replace via this name
    assert __import__("os").replace is real_replace


# ── _rename_corrupt OSError swallowed ────────────────────────────────────────


def test_rename_corrupt_oserror_swallowed(tmp_path):
    """A corrupt history file that cannot be renamed (read-only fs, antivirus
    lock) must still result in an empty in-memory store — not a crash on
    FilterManager startup."""
    path = tmp_path / "fh.json"
    path.write_text("not json{{", encoding="utf-8")

    with patch("pathlib.Path.rename", side_effect=OSError("locked")):
        h = FilterHistory(path)  # _load → _rename_corrupt → OSError caught

    assert h._entries == []
