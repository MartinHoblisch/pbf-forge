# Roadmap

Features under consideration for future releases. Open an [Issue](https://github.com/MartinHoblisch/pbf-forge/issues) if something here is blocking you: real user signal determines priority.

---

## Planned

- **GeoParquet export.** Columnar output for analytics workflows (DuckDB, pandas).
- **Geofabrik region browser.** Pick extracts from a continent → country → sub-region tree instead of pasting URLs.
- **Region search.** Type a place name to jump to the matching Geofabrik region, backed by a local Nominatim lookup or a static region index.

## Under consideration

- **FlatGeobuf export.** A streamable binary vector format, and a better fit than GeoJSON for web viewers.
- **Tag histogram.** Count tag values across the current extract before filtering, so you can explore the data without running a full filter and export cycle. This would be a local histogram over the downloaded PBF, not a wrapper around taginfo.
- **Merge tool.** Combine two filtered PBFs before export, for example a roads layer with a POI layer.
- **Incremental PBF updates.** Keep a downloaded PBF current by applying `.osc.gz` osmChange diffs instead of re-downloading the whole file, wherever the host publishes them.

## Explicit non-goals

- **Shapefile export.** Shapefile truncates field names to 10 characters and cannot encode Unicode reliably. OSM data does not fit the format. Use GeoPackage instead.
- **Public-internet deployment.** PBF Forge has no authentication. It is a localhost tool by design.
- **Multi-user or SaaS mode.** Out of scope.
