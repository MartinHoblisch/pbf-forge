"""Integration test: two-pass osmium exclusion pipeline."""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock

import pytest

from filter_manager import FilterManager

_MINI_RAIL_OSM = """\
<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='test'>
  <node id='1' lat='52.000' lon='13.000' version='1'/>
  <node id='2' lat='52.010' lon='13.010' version='1'/>
  <node id='3' lat='52.020' lon='13.020' version='1'/>
  <node id='4' lat='52.030' lon='13.030' version='1'/>
  <node id='5' lat='52.040' lon='13.040' version='1'/>
  <node id='6' lat='52.050' lon='13.050' version='1'/>
  <node id='7' lat='52.060' lon='13.060' version='1'/>
  <node id='8' lat='52.070' lon='13.070' version='1'/>
  <node id='9' lat='52.080' lon='13.080' version='1'/>
  <way id='101' version='1'>
    <nd ref='1'/><nd ref='2'/><nd ref='3'/>
    <tag k='railway' v='rail'/>
  </way>
  <way id='102' version='1'>
    <nd ref='4'/><nd ref='5'/><nd ref='6'/>
    <tag k='railway' v='rail'/>
    <tag k='railway:traffic_mode' v='passenger'/>
  </way>
  <way id='103' version='1'>
    <nd ref='7'/><nd ref='8'/><nd ref='9'/>
    <tag k='highway' v='residential'/>
  </way>
</osm>
"""


def _count_ways(pbf_path) -> int:
    result = subprocess.run(
        ["osmium", "fileinfo", "-e", "-g", "data.count.ways", str(pbf_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"osmium fileinfo failed: {result.stderr}"
    return int(result.stdout.strip())


def _way_present(pbf_path, way_id: int) -> bool:
    """Return True if way_id appears in the PBF (via osmium tags-filter ID match)."""
    result = subprocess.run(
        ["osmium", "getid", str(pbf_path), f"w{way_id}", "--output-format=xml"],
        capture_output=True,
        text=True,
    )
    return f'id="{way_id}"' in result.stdout


@pytest.fixture
def mini_rail_pbf(tmp_data_dir):
    osm_file = tmp_data_dir / "mini_rail.osm"
    osm_file.write_text(_MINI_RAIL_OSM, encoding="utf-8")
    pbf_file = tmp_data_dir / "mini_rail.osm.pbf"
    result = subprocess.run(
        ["osmium", "cat", str(osm_file), "-o", str(pbf_file), "--overwrite"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"osmium cat failed: {result.stderr}"
    return pbf_file


@pytest.mark.integration
@pytest.mark.docker
async def test_exclude_pipeline_removes_passenger_rail(mini_rail_pbf, tmp_data_dir):
    fm = FilterManager(ws_manager=AsyncMock())
    job = fm.create_job(
        source_files=["mini_rail.osm.pbf"],
        tags=["railway=rail"],
        exclude_tags=["railway:traffic_mode=passenger"],
        geometry_types=["ways"],
        suffix="excl",
        output_formats=["pbf"],
        output_dir=str(tmp_data_dir),
        columns_mode="other_tags",
        manual_keys=[],
    )
    await fm.run_job(job)

    assert job.status == "done", f"Job failed: {job.error}"
    out_pbf = tmp_data_dir / "pbf" / "mini_rail_excl.osm.pbf"
    assert out_pbf.exists()

    assert _count_ways(out_pbf) == 1, "only freight rail way should remain"
    assert _way_present(out_pbf, 101), "freight rail way 101 must be in output"
    assert not _way_present(out_pbf, 102), "passenger rail way 102 must be excluded"


@pytest.mark.integration
@pytest.mark.docker
async def test_include_only_baseline(mini_rail_pbf, tmp_data_dir):
    """Without exclude_tags, both rail ways appear (baseline comparison)."""
    fm = FilterManager(ws_manager=AsyncMock())
    job = fm.create_job(
        source_files=["mini_rail.osm.pbf"],
        tags=["railway=rail"],
        exclude_tags=[],
        geometry_types=["ways"],
        suffix="incl",
        output_formats=["pbf"],
        output_dir=str(tmp_data_dir),
        columns_mode="other_tags",
        manual_keys=[],
    )
    await fm.run_job(job)

    assert job.status == "done", f"Job failed: {job.error}"
    out_pbf = tmp_data_dir / "pbf" / "mini_rail_incl.osm.pbf"
    assert out_pbf.exists()

    assert _count_ways(out_pbf) == 2, "include-only: both rail ways must be present"
    assert _way_present(out_pbf, 101)
    assert _way_present(out_pbf, 102)
