"""Integration test: two-pass osmium exclusion pipeline."""

from __future__ import annotations

import shutil
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


_ROUTE_RELATION_OSM = """\
<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='test'>
  <node id='1' lat='52.000' lon='13.000' version='1'/>
  <node id='2' lat='52.010' lon='13.010' version='1'/>
  <node id='3' lat='52.020' lon='13.020' version='1'/>
  <node id='4' lat='52.030' lon='13.030' version='1'/>
  <way id='101' version='1'>
    <nd ref='1'/><nd ref='2'/>
    <tag k='railway' v='rail'/>
  </way>
  <way id='102' version='1'>
    <nd ref='3'/><nd ref='4'/>
    <tag k='railway' v='rail'/>
    <tag k='railway:traffic_mode' v='passenger'/>
  </way>
  <relation id='201' version='1'>
    <member type='way' ref='101' role=''/>
    <member type='way' ref='102' role=''/>
    <tag k='railway' v='rail'/>
  </relation>
</osm>
"""


@pytest.fixture
def route_relation_pbf(tmp_data_dir):
    osm_file = tmp_data_dir / "route_rel.osm"
    osm_file.write_text(_ROUTE_RELATION_OSM, encoding="utf-8")
    pbf_file = tmp_data_dir / "route_rel.osm.pbf"
    result = subprocess.run(
        ["osmium", "cat", str(osm_file), "-o", str(pbf_file), "--overwrite"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"osmium cat failed: {result.stderr}"
    return pbf_file


@pytest.mark.integration
@pytest.mark.docker
async def test_exclude_survives_a_relation_that_references_the_excluded_way(
    route_relation_pbf, tmp_data_dir
):
    """An excluded way must stay excluded even when a relation still names it.

    Relation 201 matches the include expression, so it is in the intermediate
    when the exclude pass runs. Without --omit-referenced the exclude pass
    completes that relation's references and puts way 102 back, which makes the
    exclude field silently do nothing whenever Relations is checked.
    """
    fm = FilterManager(ws_manager=AsyncMock())
    job = fm.create_job(
        source_files=["route_rel.osm.pbf"],
        tags=["railway=rail"],
        exclude_tags=["railway:traffic_mode=passenger"],
        geometry_types=["ways", "relations"],
        suffix="excl",
        output_formats=["pbf"],
        output_dir=str(tmp_data_dir),
        columns_mode="other_tags",
        manual_keys=[],
    )
    await fm.run_job(job)

    assert job.status == "done", f"Job failed: {job.error}"
    out_pbf = tmp_data_dir / "pbf" / "route_rel_excl.osm.pbf"
    assert out_pbf.exists()

    assert _way_present(out_pbf, 101), "freight rail way 101 must survive"
    assert not _way_present(out_pbf, 102), (
        "passenger way 102 must not come back through relation 201"
    )


_BOUNDARY_CUT_OSM = """\
<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='test'>
  <node id='1' lat='52.000' lon='13.000' version='1'/>
  <node id='2' lat='52.010' lon='13.010' version='1'/>
  <way id='10' version='1'>
    <nd ref='1'/><nd ref='2'/>
    <tag k='highway' v='primary'/>
  </way>
  <way id='20' version='1'>
    <nd ref='1'/><nd ref='999'/>
    <tag k='highway' v='primary'/>
  </way>
</osm>
"""


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.skipif(
    shutil.which("ogr2ogr") is None,
    reason="GeoPackage output needs ogr2ogr; the count itself comes from osmium export",
)
async def test_a_boundary_cut_way_is_counted_not_swallowed(tmp_data_dir):
    """Way 20 references a node outside the file, so it has no geometry.

    osmium export skips it and writes nothing about it anywhere the user
    looks. Without the count there is no way to notice a row is missing.
    """
    osm_file = tmp_data_dir / "cut.osm"
    osm_file.write_text(_BOUNDARY_CUT_OSM, encoding="utf-8")
    pbf_file = tmp_data_dir / "cut.osm.pbf"
    subprocess.run(
        ["osmium", "cat", str(osm_file), "-o", str(pbf_file), "--overwrite"],
        capture_output=True,
        check=True,
    )

    fm = FilterManager(ws_manager=AsyncMock())
    job = fm.create_job(
        source_files=["cut.osm.pbf"],
        tags=["highway=primary"],
        exclude_tags=[],
        geometry_types=["ways"],
        suffix="cut",
        output_formats=["gpkg"],
        output_dir=str(tmp_data_dir),
        columns_mode="other_tags",
        manual_keys=[],
    )
    await fm.run_job(job)

    assert job.status == "done", f"Job failed: {job.error}"
    assert job.geometry_errors == 1

    reports = list((tmp_data_dir / "gpkg").glob("*.txt"))
    assert reports, "expected a report beside the output"
    assert "Dropped features" in reports[0].read_text(encoding="utf-8")
