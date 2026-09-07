"""Discarding a .part file: the way out of a partial that cannot complete."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import download_manager as dm_mod
from download_manager import PART_SUFFIX, DownloadManager


class _StubWs:
    async def broadcast(self, data):  # pragma: no cover - never awaited here
        pass


@pytest.fixture
def dm(tmp_path, monkeypatch):
    monkeypatch.setattr(dm_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dm_mod, "URLS_FILE", tmp_path / "urls.json")
    return DownloadManager(_StubWs())


def _row_with_partial(dm, tmp_path, status, size=700):
    filename = "berlin.osm.pbf"
    (tmp_path / (filename + PART_SUFFIX)).write_bytes(b"x" * size)
    dm.register_url("http://example.com/berlin-latest.osm.pbf", filename)
    dm._refresh_local_files()
    dm._files[filename].status = status
    return filename


def test_discard_removes_the_part_file(dm, tmp_path):
    filename = _row_with_partial(dm, tmp_path, "paused")

    with patch.object(dm, "_broadcast"):
        assert dm.discard_partial(filename) is True

    assert not (tmp_path / (filename + PART_SUFFIX)).exists()
    # Nothing is left on disk, so the row goes the same way a deleted file's
    # row goes: the list mirrors the directory.
    assert filename not in dm._files


def test_discard_clears_the_error_that_asked_for_it(dm, tmp_path):
    """An unreadable partial leaves an error row; discarding it ends the loop."""
    filename = _row_with_partial(dm, tmp_path, "error")
    dm._files[filename].error = "Could not read berlin.osm.pbf.part: [Errno 5] Input/output error"
    (dm_mod.DATA_DIR / filename).write_bytes(b"y" * 400)  # an older complete file

    with patch.object(dm, "_broadcast"):
        assert dm.discard_partial(filename) is True

    assert dm._files[filename].error is None
    assert dm._files[filename].partial_bytes is None
    assert dm._files[filename].status != "error"


def test_discard_refuses_while_a_transfer_owns_the_row(dm, tmp_path):
    """The .part of a running transfer is the file that transfer writes to."""
    filename = _row_with_partial(dm, tmp_path, "downloading")

    assert dm.discard_partial(filename) is False
    assert (tmp_path / (filename + PART_SUFFIX)).exists()


def test_discard_of_an_unknown_file_reports_nothing_done(dm):
    assert dm.discard_partial("nowhere.osm.pbf") is False


def test_discarding_the_last_trace_tells_the_clients(dm, tmp_path):
    """The row is gone from the list, and every open tab hears about it."""
    filename = _row_with_partial(dm, tmp_path, "paused")

    with patch.object(dm, "_broadcast") as bc:
        dm.discard_partial(filename)

    assert bc.call_args_list == [(({"type": "file_removed", "filename": filename},),)]


def test_unreadable_partial_is_not_told_to_resume(dm, tmp_path):
    """Resuming appends to the file that failed to read — it cannot help."""
    part = tmp_path / ("berlin.osm.pbf" + PART_SUFFIX)
    part.write_bytes(b"x" * 10)
    url = "http://example.com/berlin-latest.osm.pbf"

    with patch.object(dm_mod, "_md5_of", side_effect=OSError("[Errno 5] Input/output error")):
        with pytest.raises(RuntimeError) as exc:
            dm._verify_checksum(url, part, session=None)

    assert "discard it and download again" in str(exc.value)
    assert "start it again to resume" not in str(exc.value)
