# Roadmap

Features under consideration for future releases. Open an [Issue](https://github.com/martinhoblisch/pbf-forge/issues) or start a [Discussion](https://github.com/martinhoblisch/pbf-forge/discussions) if something here is blocking you — real user signal determines priority.

---

## Planed

- **GeoParquet polish** — geometry type per layer, Parquet metadata conformance with GeoParquet 1.1 spec.
- **Region search** — type a place name to jump to the matching Geofabrik region, backed by a local Nominatim lookup or a static region index.

## Under consideration

- **FlatGeobuf export** — streamable binary vector format; good alternative to GeoJSON for web viewers.
- **Tag histogram** — count tag values across the current extract before filtering, so you can explore the data without running a full filter + export cycle. (This is a local histogram against the downloaded PBF, not a wrapper around taginfo.)
- **Exclusion filtering** — two-pass osmium pipeline to exclude objects matching a second tag expression (e.g. select `railway=rail` but exclude `railway:traffic_mode=passenger`). Requires running `osmium tags-filter` twice: once to select, once with `--invert` to remove matching objects from the result.
- **Merge tool** — combine two filtered PBFs before export (e.g. merge a roads layer with a POI layer).
- **Incremental PBF updates** — keep a downloaded PBF current by applying daily Geofabrik `.osc.gz` osmChange diffs instead of re-downloading the full file.

## Explicit non-goals

- **Shapefile export** — Shapefile truncates field names to 10 characters and cannot encode Unicode reliably. OSM data does not fit the format. Use GeoPackage instead.
- **Public-internet deployment** — PBF Forge has no authentication. It is a localhost tool by design.
- **Multi-user / SaaS mode** — out of scope.
