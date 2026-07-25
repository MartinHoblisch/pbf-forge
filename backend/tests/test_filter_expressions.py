"""Tests for the osmium tags-filter expressions built from tags and geometry types."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from filter_manager import FilterJob, FilterManager


def _make_job(**kwargs) -> FilterJob:
    defaults = dict(
        id="test-id",
        source_files=["berlin.osm.pbf"],
        tags=["amenity"],
        exclude_tags=[],
        geometry_types=["nodes"],
        suffix="test",
        output_formats=["gpkg"],
        output_dir="/tmp/out",
        columns_mode="other_tags",
        manual_keys=[],
    )
    defaults.update(kwargs)
    return FilterJob(**defaults)


@pytest.fixture
def fm():
    return FilterManager(ws_manager=MagicMock())


@pytest.mark.parametrize(
    "geom,prefix",
    [
        ("nodes", "n"),
        ("ways", "w"),
        ("relations", "r"),
    ],
)
def test_single_tag_single_geometry(fm, geom, prefix):
    job = _make_job(tags=["amenity=cafe"], geometry_types=[geom])
    exprs = fm._build_expressions(job)
    assert exprs == [f"{prefix}/amenity=cafe"]


def test_single_tag_all_geometries(fm):
    job = _make_job(tags=["highway"], geometry_types=["nodes", "ways", "relations"])
    exprs = fm._build_expressions(job)
    assert exprs == ["n/highway", "w/highway", "r/highway"]


def test_two_tags_two_geometries_cartesian(fm):
    job = _make_job(tags=["amenity", "shop"], geometry_types=["nodes", "ways"])
    exprs = fm._build_expressions(job)
    assert len(exprs) == 4
    assert exprs == ["n/amenity", "w/amenity", "n/shop", "w/shop"]


def test_empty_tags_returns_empty(fm):
    job = _make_job(tags=[], geometry_types=["nodes"])
    assert fm._build_expressions(job) == []


def test_empty_geometry_types_returns_empty(fm):
    job = _make_job(tags=["amenity"], geometry_types=[])
    assert fm._build_expressions(job) == []


def test_whitespace_only_tag_skipped(fm):
    job = _make_job(tags=["  ", "shop"], geometry_types=["ways"])
    exprs = fm._build_expressions(job)
    assert exprs == ["w/shop"]


def test_unknown_geometry_type_raises(fm):
    job = _make_job(tags=["amenity"], geometry_types=["polygons"])
    with pytest.raises(ValueError, match="Unknown geometry type"):
        fm._build_expressions(job)


def test_exclude_single_tag_single_geometry(fm):
    job = _make_job(exclude_tags=["railway:traffic_mode=passenger"], geometry_types=["ways"])
    exprs = fm._build_expressions(job, kind="exclude")
    assert exprs == ["w/railway:traffic_mode=passenger"]


def test_exclude_multiple_tags_cartesian(fm):
    job = _make_job(exclude_tags=["access=private", "barrier"], geometry_types=["ways", "nodes"])
    exprs = fm._build_expressions(job, kind="exclude")
    assert len(exprs) == 4
    assert exprs == ["w/access=private", "n/access=private", "w/barrier", "n/barrier"]


def test_exclude_empty_returns_empty(fm):
    job = _make_job(exclude_tags=[], geometry_types=["ways"])
    assert fm._build_expressions(job, kind="exclude") == []


def test_exclude_invalid_tag_raises(fm):
    job = _make_job(exclude_tags=["-invalid"], geometry_types=["ways"])
    with pytest.raises(ValueError, match="Invalid tag expression"):
        fm._build_expressions(job, kind="exclude")
