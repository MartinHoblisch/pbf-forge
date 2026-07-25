"""Tests that run_job shares one osmium export across all non-PBF formats.

osmium export runs at most ONCE per source, and every non-PBF format (geojson,
gpkg) goes through it regardless of columns_mode — one route, one schema.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from filter_manager import FilterJob, FilterManager


def _job(tmp_data_dir, **overrides) -> FilterJob:
    out_dir = overrides.pop("output_dir", None) or str(tmp_data_dir / "out")
    defaults = dict(
        id="t",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="t",
        output_formats=["gpkg"],
        output_dir=out_dir,
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(overrides)
    return FilterJob(**defaults)


def _fm() -> FilterManager:
    return FilterManager(AsyncMock())


def _ensure_source(tmp_data_dir, name="berlin.osm.pbf", size=1000):
    (tmp_data_dir / name).write_bytes(b"x" * size)


# ── osmium export shared across geojson + gpkg (manual mode) ─────────────────


async def test_osmium_export_runs_once_for_multiple_formats(tmp_data_dir):
    """geojson + gpkg with manual columns_mode → osmium export called exactly once."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["geojson", "gpkg"],
        columns_mode="manual",
        manual_keys=["name"],
    )

    export_call_count = {"n": 0}
    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            export_call_count["n"] += 1
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    assert export_call_count["n"] == 1, "osmium export must run exactly once per source"
    # Two ogr2ogr calls — one per format
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert len(ogr_cmds) == 2
    # osmium export must carry the disk-backed node index flag (RAM cap)
    export_cmd = next(c for c in captured_cmds if c[0] == "osmium" and "export" in c)
    assert "--index-type=sparse_file_array,sparse_file_array" in export_cmd


# ── osmium export failure sets status=error ──────────────────────────────────


async def test_osmium_export_failure_sets_error(tmp_data_dir):
    """If osmium export returns non-zero, run_job sets status='error'."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"], columns_mode="all")

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "osmium" and "export" in cmd:
            return 1  # export fails
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            await fm.run_job(job)

    assert job.status == "error"
    assert "osmium export" in (job.error or "")


# ── gpkg export path includes -gt and OGR_SQLITE_SYNCHRONOUS ────────────────


async def test_gpkg_export_path_includes_perf_flags(tmp_data_dir):
    """ogr2ogr for GPKG (all mode) must carry -gt and OGR_SQLITE_SYNCHRONOUS."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"], columns_mode="all")

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=[])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    ogr_cmd = next(c for c in captured_cmds if c[0] == "ogr2ogr")
    assert "-gt" in ogr_cmd
    assert ogr_cmd[ogr_cmd.index("-gt") + 1] == "65536"
    assert "OGR_SQLITE_SYNCHRONOUS" in ogr_cmd


# ── gpkg + geojson must reuse shared geojson (no GDAL OSM driver OOM) ────────


async def test_gpkg_plus_geojson_reuses_shared_geojson_for_gpkg(tmp_data_dir):
    """When GeoJSON is also requested, GPKG must read from shared geojson, not
    direct from PBF. GDAL OSM driver has no disk-backed index and OOMs on
    Europe-scale sources with limited RAM."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["geojson", "gpkg"],
        columns_mode="manual",
        manual_keys=["name"],
    )

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    # osmium export must run (creates shared geojson)
    export_cmds = [c for c in captured_cmds if c[0] == "osmium" and "export" in c]
    assert len(export_cmds) == 1
    # GPKG ogr2ogr must NOT carry OSM_CONFIG_FILE (would mean direct-PBF path)
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert all("OSM_CONFIG_FILE" not in c for c in ogr_cmds)
    # Both ogr2ogr calls read from shared geojson (-sql path)
    assert all("-sql" in c for c in ogr_cmds)


# ── gpkg-only always uses the osmium export path ──────────────────────────────


async def test_gpkg_only_uses_osmium_export_path(tmp_data_dir):
    """gpkg-only jobs use the same osmium-export route as gpkg+geojson.

    GDAL's OSM driver could write a GPKG straight from the PBF, which is faster
    but produces a different column set. Taking that shortcut when gpkg is the
    only selected format would mean the same filter yields two different schemas
    depending on what else the user ticked. So every non-PBF format runs osmium
    export first, then ogr2ogr over the shared GeoJSON.
    """
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="other_tags",
    )

    captured_cmds = []

    async def fake_run_cmd(cmd, _job, **_):
        captured_cmds.append(list(cmd))
        if cmd[0] == "osmium" and "export" in cmd:
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            out = Path(cmd[cmd.index("-f") + 2])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=["name"])):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"
    export_cmds = [c for c in captured_cmds if c[0] == "osmium" and "export" in c]
    assert len(export_cmds) == 1, "gpkg-only must still run osmium export"
    # osmium export carries disk-backed node index
    assert "--index-type=sparse_file_array,sparse_file_array" in export_cmds[0]
    # ogr2ogr reads from shared geojson, NOT from PBF
    ogr_cmds = [c for c in captured_cmds if c[0] == "ogr2ogr"]
    assert len(ogr_cmds) == 1
    assert "-sql" in ogr_cmds[0], "ogr2ogr must read from shared geojson via -sql"
    assert "OSM_CONFIG_FILE" not in ogr_cmds[0], (
        "OSM_CONFIG_FILE means ogr2ogr took GDAL's OSM driver instead of the export"
    )


# ── GPKG column-count guard (SQLite max 2000 columns) ────────────────────────


async def test_gpkg_column_guard_fails_fast_over_limit(tmp_data_dir):
    """Europe-scale extracts can carry >2000 distinct tag keys; SQLite caps a
    table at 2000 columns. The job must fail with a clear message BEFORE the
    ogr2ogr conversion is attempted."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    # columns_mode="all": Standard mode has a static curated schema and can
    # never exceed the cap — only expand-all/manual still hit the guard.
    job = _job(tmp_data_dir, output_formats=["gpkg"], columns_mode="all")
    many_fields = [f"key_{i}" for i in range(2300)]
    ogr_calls = []

    async def fake_run_cmd(cmd, _job, **_):
        if cmd[0] == "ogr2ogr":
            ogr_calls.append(cmd)
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            await fm.run_job(job)

    assert job.status == "error"
    assert "2000" in (job.error or "")
    assert "2300" in (job.error or "")
    assert not ogr_calls, "ogr2ogr must not be attempted over the column limit"


async def test_gpkg_column_guard_ignores_geojson(tmp_data_dir):
    """GeoJSON has no column limit — same oversized field list must pass."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["geojson"])
    many_fields = [f"key_{i}" for i in range(2300)]

    async def fake_run_cmd(cmd, _job, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"


async def test_gpkg_column_guard_counts_manual_selection(tmp_data_dir):
    """Manual mode counts only the selected keys — a small manual pick from an
    oversized extract must pass."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="manual",
        manual_keys=["key_1", "key_2"],
    )
    many_fields = [f"key_{i}" for i in range(2300)]

    async def fake_run_cmd(cmd, _job, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_text("{}", encoding="utf-8")
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", AsyncMock(return_value=many_fields)):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done"


# ── Standard mode: real other_tags via geojsonseq fold ───────────────────────


async def test_standard_mode_folds_to_other_tags(tmp_data_dir):
    """columns_mode=other_tags routes through the fold subprocess and builds
    the SQL from the static curated schema — no ogrinfo field scan."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"], tags=["waterway=canal", "waterway=river"])
    captured = []

    async def fake_run_cmd(cmd, _job, **_):
        captured.append(list(cmd))
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_bytes(b"d")
        return 0

    get_fields = AsyncMock(return_value=["should_not_be_used"])
    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", get_fields):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done", job.error
    # osmium export must use the REAL flag: --format=X prefix-matches the
    # no-op --format-option and is silently ignored by osmium.
    export_cmd = next(c for c in captured if c[0] == "osmium" and "export" in c)
    assert "--output-format=geojsonseq" in export_cmd
    assert not any("--format=geojson" in a for a in export_cmd)
    # fold subprocess with curated keys (name + include-tag keys, deduped)
    fold_cmd = next(c for c in captured if "geojsonseq_fold.py" in c[1])
    assert fold_cmd[2].endswith("_export.geojsonseq")
    assert fold_cmd[3].endswith("_folded.geojsonseq")
    assert fold_cmd[fold_cmd.index("--keep") + 1] == "name,waterway"
    # ogr2ogr must consume the FOLDED file, not the raw export
    ogr_src = next(c for c in captured if c[0] == "ogr2ogr")
    assert any(str(a).endswith("_folded.geojsonseq") for a in ogr_src)
    # SQL built from the static schema; ogrinfo never consulted
    ogr_cmd = next(c for c in captured if c[0] == "ogr2ogr")
    sql = ogr_cmd[ogr_cmd.index("-sql") + 1]
    assert '"other_tags"' in sql and '"name"' in sql and '"waterway"' in sql
    assert "should_not_be_used" not in sql
    get_fields.assert_not_awaited()


async def test_standard_mode_fold_failure_marks_error(tmp_data_dir):
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(tmp_data_dir, output_formats=["gpkg"])

    async def fake_run_cmd(cmd, _job, **_):
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if "geojsonseq_fold.py" in str(cmd[1]):
            return 1
        return 0

    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        await fm.run_job(job)

    assert job.status == "error"
    assert "fold exited with code 1" in (job.error or "")


async def test_manual_mode_still_scans_fields_from_seq(tmp_data_dir):
    """Non-standard modes keep the ogrinfo scan and must NOT run the fold."""
    _ensure_source(tmp_data_dir)
    fm = _fm()
    job = _job(
        tmp_data_dir,
        output_formats=["gpkg"],
        columns_mode="manual",
        manual_keys=["name"],
    )
    captured = []

    async def fake_run_cmd(cmd, _job, **_):
        captured.append(list(cmd))
        if "-o" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text("{}", encoding="utf-8")
        if cmd[0] == "ogr2ogr":
            Path(cmd[cmd.index("-f") + 2]).write_bytes(b"d")
        return 0

    get_fields = AsyncMock(return_value=["name", "waterway"])
    with patch.object(fm, "_run_cmd", side_effect=fake_run_cmd):
        with patch.object(fm, "_get_fields", get_fields):
            with patch.object(fm, "_embed_attribution"):
                with patch.object(fm, "_embed_provenance"):
                    await fm.run_job(job)

    assert job.status == "done", job.error
    get_fields.assert_awaited()
    assert not any("geojsonseq_fold.py" in str(c[1]) for c in captured)
