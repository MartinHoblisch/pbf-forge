"""Fold GeoJSONSeq (from `osmium export --output-format=geojsonseq`) into NDJSON
with a curated set of properties plus a compact `other_tags` column.

Why: pbf-forge's "Standard" column mode promises a fixed, predictable schema
(curated columns + other_tags) rather than one column per OSM tag key ever
seen — expanding every key would blow past SQLite's 2000-column cap on
Europe-scale extracts. This step reduces each feature's properties to that
curated schema before the file reaches ogr2ogr/SQLite.
"""

from __future__ import annotations

import json
import sys
from typing import IO

PROGRESS_EVERY = 100_000


def fold_feature(feature: dict, keep: list[str]) -> dict:
    """Reduce a GeoJSON Feature's properties to curated keys + other_tags."""
    props = feature.get("properties") or {}
    folded: dict = {}
    leftover: dict = {}

    for k, v in props.items():
        if k.startswith("@"):
            folded[k] = v

    for k in keep:
        # Emit null (not omit) for absent keys: GDAL only registers a column
        # for keys present in the scanned schema, so a curated key missing
        # from every feature would make ogr2ogr -sql fail with
        # "Unrecognized field name" the moment a query references it.
        folded[k] = props.get(k)

    keep_set = set(keep)
    for k, v in props.items():
        if k.startswith("@") or k in keep_set:
            continue
        leftover[k] = v

    folded["other_tags"] = json.dumps(
        leftover, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    result = dict(feature)
    result["properties"] = folded
    return result


def fold_stream(fin: IO[str], fout: IO[str], keep: list[str]) -> int:
    """Read GeoJSONSeq lines from fin, write folded NDJSON to fout. Returns count."""
    count = 0
    for lineno, raw in enumerate(fin, start=1):
        line = raw[1:] if raw.startswith("\x1e") else raw
        line = line.strip()
        if not line:
            continue

        try:
            feature = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: invalid JSON: {e}") from e

        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"line {lineno}: expected a GeoJSON Feature")

        folded = fold_feature(feature, keep)
        fout.write(json.dumps(folded, ensure_ascii=False) + "\n")
        count += 1

        if count % PROGRESS_EVERY == 0:
            print(f"folded {count} features", file=sys.stderr, flush=True)

    print(f"folded {count} features total", file=sys.stderr, flush=True)
    return count


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--keep":
        print("usage: geojsonseq_fold.py INPUT OUTPUT --keep CSV", file=sys.stderr)
        return 2

    input_path, output_path = argv[0], argv[1]
    keep = [k.strip() for k in argv[3].split(",") if k.strip()]

    if "other_tags" in keep:
        print("error: 'other_tags' cannot be a curated keep key (reserved)", file=sys.stderr)
        return 2

    with (
        open(input_path, encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8", newline="\n") as fout,
    ):
        try:
            fold_stream(fin, fout, keep)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
