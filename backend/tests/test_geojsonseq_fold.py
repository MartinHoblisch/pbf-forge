"""Tests for geojsonseq_fold: GeoJSONSeq -> curated-columns NDJSON.

Bug class to prevent: a curated key silently omitted when absent from a
feature would crash ogr2ogr -sql downstream with "Unrecognized field name"
(GDAL only registers columns present in the scanned schema) — every curated
key must always be emitted, with null as the value when absent.
"""

from __future__ import annotations

import io
import json

import pytest
from geojsonseq_fold import fold_feature, fold_stream, main


def test_at_id_passes_through_as_top_level_property():
    feature = {"type": "Feature", "properties": {"@id": "n123"}, "geometry": None}
    folded = fold_feature(feature, keep=[])
    assert folded["properties"]["@id"] == "n123"


def test_curated_key_present_is_kept_with_value():
    feature = {"type": "Feature", "properties": {"name": "Berlin"}, "geometry": None}
    folded = fold_feature(feature, keep=["name"])
    assert folded["properties"]["name"] == "Berlin"


def test_curated_key_absent_is_present_as_null():
    feature = {"type": "Feature", "properties": {}, "geometry": None}
    folded = fold_feature(feature, keep=["name"])
    assert "name" in folded["properties"]
    assert folded["properties"]["name"] is None

    line = json.dumps(folded["properties"])
    assert json.loads(line)["name"] is None


def test_other_tags_always_present_and_empty_when_nothing_leftover():
    feature = {"type": "Feature", "properties": {"name": "Berlin"}, "geometry": None}
    folded = fold_feature(feature, keep=["name"])
    assert folded["properties"]["other_tags"] == "{}"


def test_leftover_tags_land_in_other_tags_compact_sorted_non_ascii():
    feature = {
        "type": "Feature",
        "properties": {"name": "Berlin", "highway": "primary", "note": "Тест"},
        "geometry": None,
    }
    folded = fold_feature(feature, keep=["name"])
    other_tags = folded["properties"]["other_tags"]

    assert other_tags == json.dumps(
        {"highway": "primary", "note": "Тест"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "Тест" in other_tags
    assert json.loads(other_tags) == {"highway": "primary", "note": "Тест"}


def test_fold_stream_strips_rs_and_skips_blank_lines():
    feature1 = json.dumps({"type": "Feature", "properties": {"name": "A"}, "geometry": None})
    feature2 = json.dumps({"type": "Feature", "properties": {"name": "B"}, "geometry": None})
    fin = io.StringIO(f"\x1e{feature1}\n\n\x1e{feature2}\n")
    fout = io.StringIO()

    count = fold_stream(fin, fout, keep=["name"])

    assert count == 2
    output = fout.getvalue()
    assert "\x1e" not in output
    lines = [line for line in output.splitlines() if line]
    assert len(lines) == 2
    assert json.loads(lines[0])["properties"]["name"] == "A"
    assert json.loads(lines[1])["properties"]["name"] == "B"


def test_fold_stream_malformed_json_raises_value_error_with_line_number():
    fin = io.StringIO("not json\n")
    fout = io.StringIO()

    with pytest.raises(ValueError, match="line 1"):
        fold_stream(fin, fout, keep=[])


def test_fold_stream_non_feature_raises_value_error():
    fin = io.StringIO(json.dumps({"type": "FeatureCollection", "features": []}) + "\n")
    fout = io.StringIO()

    with pytest.raises(ValueError, match="line 1"):
        fold_stream(fin, fout, keep=[])


def test_main_rejects_other_tags_in_keep(tmp_path):
    input_path = tmp_path / "in.geojsonseq"
    output_path = tmp_path / "out.ndjson"
    input_path.write_text("", encoding="utf-8")

    exit_code = main([str(input_path), str(output_path), "--keep", "name,other_tags"])

    assert exit_code == 2


def test_main_end_to_end_with_rs_prefixes(tmp_path):
    input_path = tmp_path / "in.geojsonseq"
    output_path = tmp_path / "out.ndjson"

    feature1 = json.dumps(
        {"type": "Feature", "properties": {"@id": "n1", "name": "Berlin", "highway": "primary"}}
    )
    feature2 = json.dumps({"type": "Feature", "properties": {"@id": "n2"}})
    input_path.write_text(f"\x1e{feature1}\n\x1e{feature2}\n", encoding="utf-8")

    exit_code = main([str(input_path), str(output_path), "--keep", "name"])

    assert exit_code == 0
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["properties"]["@id"] == "n1"
    assert first["properties"]["name"] == "Berlin"
    assert json.loads(first["properties"]["other_tags"]) == {"highway": "primary"}

    second = json.loads(lines[1])
    assert second["properties"]["@id"] == "n2"
    assert second["properties"]["name"] is None
    assert second["properties"]["other_tags"] == "{}"


def test_progress_lines_printed_at_threshold(monkeypatch, capsys):
    import geojsonseq_fold

    monkeypatch.setattr(geojsonseq_fold, "PROGRESS_EVERY", 2)

    features = [
        json.dumps({"type": "Feature", "properties": {}}),
        json.dumps({"type": "Feature", "properties": {}}),
        json.dumps({"type": "Feature", "properties": {}}),
    ]
    fin = io.StringIO("\n".join(features) + "\n")
    fout = io.StringIO()

    count = geojsonseq_fold.fold_stream(fin, fout, keep=[])

    assert count == 3
    err = capsys.readouterr().err
    assert "folded 2 features" in err
    assert "folded 3 features total" in err
