"""Tests for FilterManager._osmconf — GDAL OSM driver INI builder.

Bugs to catch:
  - Missing [general] or layer sections breaks GDAL OSM driver silently.
  - Unsafe column names (shell metachars) reaching the INI without validation.
  - Empty key list producing malformed INI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from filter_manager import FilterManager


@pytest.fixture
def fm():
    return FilterManager(ws_manager=MagicMock())


_LAYERS = ["points", "lines", "multilinestrings", "multipolygons", "other_relations"]


def test_osmconf_contains_general_section(fm):
    out = fm._osmconf(["name", "highway"])
    assert "[general]" in out
    assert "attribute_name_laundering=yes" in out


def test_osmconf_contains_all_five_layer_sections(fm):
    out = fm._osmconf(["name"])
    for layer in _LAYERS:
        assert f"[{layer}]" in out


def test_osmconf_attributes_line_lists_keys_csv(fm):
    out = fm._osmconf(["name", "highway", "ref"])
    assert "attributes=name,highway,ref" in out


def test_osmconf_each_layer_has_osm_id_and_other_tags(fm):
    out = fm._osmconf(["name"])
    # osm_id and other_tags must be set on every layer (one per section)
    assert out.count("osm_id=yes") == len(_LAYERS)
    assert out.count("other_tags=yes") == len(_LAYERS)


def test_osmconf_empty_keys_yields_empty_attributes(fm):
    out = fm._osmconf([])
    # attributes= with empty value is acceptable for GDAL; make sure no crash
    assert "attributes=" in out
    # And no stray comma
    assert "attributes=," not in out


def test_osmconf_rejects_shell_metachars(fm):
    with pytest.raises(ValueError, match="Invalid column name"):
        fm._osmconf(["name", "evil; rm -rf /"])


def test_osmconf_rejects_whitespace_in_key(fm):
    with pytest.raises(ValueError, match="Invalid column name"):
        fm._osmconf(["bad key"])


def test_osmconf_rejects_quote_in_key(fm):
    with pytest.raises(ValueError, match="Invalid column name"):
        fm._osmconf(['name"injected'])


def test_osmconf_accepts_colon_and_dot_keys(fm):
    """OSM tag keys legitimately contain colon (addr:street) and dot (some keys)."""
    out = fm._osmconf(["addr:street", "name.en", "ref:iata"])
    assert "addr:street" in out
    assert "name.en" in out


def test_osmconf_accepts_hyphen_and_underscore(fm):
    out = fm._osmconf(["foo-bar", "foo_bar"])
    assert "foo-bar" in out
    assert "foo_bar" in out
