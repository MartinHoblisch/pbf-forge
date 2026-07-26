"""Container -> host path translation, and its exposure through FilterJob.to_dict()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from filter_manager import FilterJob
from host_paths import host_data_dir, to_host_path


@pytest.fixture
def data_root(monkeypatch):
    """Pin DATA_DIR to the container default so path cases read literally."""
    monkeypatch.setattr(config, "DATA_DIR", Path("/data"))


@pytest.mark.parametrize(
    ("host_root", "container_path", "expected"),
    [
        # Windows hosts
        ("H:\\pbf-forge\\data", "/data/gpkg/b.gpkg", "H:\\pbf-forge\\data\\gpkg\\b.gpkg"),
        ("H:\\pbf-forge\\data\\", "/data/gpkg/b.gpkg", "H:\\pbf-forge\\data\\gpkg\\b.gpkg"),
        ("H:/pbf-forge/data", "/data/gpkg/b.gpkg", "H:\\pbf-forge\\data\\gpkg\\b.gpkg"),
        ("H:", "/data/gpkg/b.gpkg", "H:\\gpkg\\b.gpkg"),
        (".\\data", "/data/gpkg/b.gpkg", ".\\data\\gpkg\\b.gpkg"),
        # POSIX hosts
        ("/home/u/osm-data", "/data/gpkg/b.gpkg", "/home/u/osm-data/gpkg/b.gpkg"),
        ("/home/u/osm-data/", "/data/gpkg/b.gpkg", "/home/u/osm-data/gpkg/b.gpkg"),
        ("/", "/data/gpkg/b.gpkg", "/gpkg/b.gpkg"),
        ("./data", "/data/gpkg/b.gpkg", "./data/gpkg/b.gpkg"),
        # Nested output_dir below DATA_DIR
        ("H:\\data", "/data/sub/gpkg/b.gpkg", "H:\\data\\sub\\gpkg\\b.gpkg"),
        # The data directory itself
        ("H:\\data", "/data", "H:\\data"),
        ("/home/u/osm-data", "/data", "/home/u/osm-data"),
        # Unmappable: pass the container path through unchanged
        ("", "/data/gpkg/b.gpkg", "/data/gpkg/b.gpkg"),
        ("H:\\data", "/var/tmp/x.gpkg", "/var/tmp/x.gpkg"),
    ],
)
def test_to_host_path_cases(data_root, host_root, container_path, expected):
    assert to_host_path(container_path, host_root) == expected


def test_host_data_dir_reads_config():
    config.USER_CONFIG_FILE.write_text(json.dumps({"host_data_dir": "H:\\mydata"}))
    assert host_data_dir() == "H:\\mydata"


@pytest.mark.parametrize(
    "payload",
    [
        None,  # file absent
        "{not json",
        json.dumps({}),
        json.dumps({"host_data_dir": None}),
        json.dumps({"host_data_dir": 42}),
    ],
)
def test_host_data_dir_returns_empty_when_unusable(payload):
    if payload is not None:
        config.USER_CONFIG_FILE.write_text(payload)
    assert host_data_dir() == ""


def _job(output_files: list[str]) -> FilterJob:
    return FilterJob(
        id="j1",
        source_files=["a.osm.pbf"],
        tags=["railway=rail"],
        exclude_tags=[],
        geometry_types=["ways"],
        suffix="t",
        output_formats=["gpkg"],
        output_dir=str(config.DATA_DIR),
        columns_mode="other_tags",
        manual_keys=[],
        output_files=output_files,
    )


def test_to_dict_exposes_host_output_files(data_root):
    config.USER_CONFIG_FILE.write_text(json.dumps({"host_data_dir": "H:\\hostdata"}))
    d = _job(["/data/gpkg/b.gpkg"]).to_dict()
    assert d["output_files_host"] == ["H:\\hostdata\\gpkg\\b.gpkg"]
    assert d["output_files"] == ["/data/gpkg/b.gpkg"]


def test_to_dict_host_paths_fall_back_without_config(data_root):
    d = _job(["/data/gpkg/b.gpkg"]).to_dict()
    assert d["output_files_host"] == d["output_files"]


def test_manifest_omits_host_output_files(data_root):
    config.USER_CONFIG_FILE.write_text(json.dumps({"host_data_dir": "H:\\hostdata"}))
    d = _job(["/data/gpkg/b.gpkg"]).to_manifest_dict()
    assert "output_files_host" not in d
    assert d["output_files"] == ["/data/gpkg/b.gpkg"]
