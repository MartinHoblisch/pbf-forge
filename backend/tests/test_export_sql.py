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


def test_manual_mode_all_missing_keys_yields_the_identity_columns_only():
    job = _job(columns_mode="manual", manual_keys=["maxdepth", "depth"])
    sql = _mgr()._build_export_sql(LAYER, job, FIELDS)
    assert sql == (f'SELECT "@id" AS osm_id, substr("id",1,1) AS osm_type FROM "{LAYER}"')


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


def test_export_sql_selects_osm_type_next_to_osm_id():
    """osm_id is unique only per object type, and every type shares one layer.

    osmium export -u type_id writes n1 / w1 / r1 into the GeoJSON feature id,
    which OGR exposes as a String field named id. Its first character is the
    object type, so (osm_type, osm_id) identifies a feature.
    """
    from unittest.mock import MagicMock

    from filter_manager import FilterManager

    fm = FilterManager(ws_manager=MagicMock())
    job = MagicMock()
    job.columns_mode = "other_tags"
    job.manual_keys = []

    sql = fm._build_export_sql("layer", job, ["name", "highway"])

    assert '"@id" AS osm_id' in sql
    assert 'substr("id",1,1) AS osm_type' in sql
    assert sql.index("osm_id") < sql.index("osm_type")


def test_discovered_fields_exclude_the_type_prefixed_id():
    """id is selected explicitly as the osm_type source.

    Leaving it in the discovered field list would select it twice and put a
    redundant n1 / w1 column in every output.
    """
    from filter_manager import FilterManager

    assert FilterManager._EXCLUDED_EXPORT_FIELDS == {"@id", "id"}
