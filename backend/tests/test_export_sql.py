"""Tests for the SELECT statement FilterManager hands to ogr2ogr."""

from __future__ import annotations

from unittest.mock import MagicMock

from filter_manager import FilterJob, FilterManager


def _mgr() -> FilterManager:
    return FilterManager(MagicMock())


def _job(**kwargs) -> FilterJob:
    defaults = dict(
        id="x",
        source_files=["berlin.osm.pbf"],
        tags=["waterway"],
        exclude_tags=[],
        geometry_types=["ways"],
        suffix="test",
        output_formats=["gpkg"],
        output_dir="/tmp",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(kwargs)
    return FilterJob(**defaults)


LAYER = "export_layer"
FIELDS = ["name", "waterway", "width", "operator", "ref"]


def test_manual_mode_includes_only_present_keys():
    job = _job(columns_mode="manual", manual_keys=["name", "maxdepth", "waterway"])
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    assert '"name"' in sql
    assert '"waterway"' in sql
    assert '"maxdepth"' not in sql


def test_manual_mode_all_missing_keys_yields_osm_id_only():
    job = _job(columns_mode="manual", manual_keys=["maxdepth", "depth"])
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    assert sql == f'SELECT "@id" AS osm_id FROM "{LAYER}"'


def test_manual_mode_preserves_user_key_order():
    job = _job(columns_mode="manual", manual_keys=["ref", "name", "operator"])
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    assert sql.index('"ref"') < sql.index('"name"') < sql.index('"operator"')


def test_other_tags_mode_uses_all_detected_fields():
    job = _job(columns_mode="other_tags")
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    for f in FIELDS:
        assert f'"{f}"' in sql


def test_empty_manual_keys_falls_back_to_all_fields():
    job = _job(columns_mode="manual", manual_keys=[])
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    for f in FIELDS:
        assert f'"{f}"' in sql
