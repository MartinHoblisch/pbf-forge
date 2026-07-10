# Changelog

All notable changes to PBF Forge are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Exclusion filtering** — a second tag set ("OSM-Tags zum Ausschließen") runs as an inverted `osmium tags-filter --invert-match` pass after the include pass, producing the set difference. Example: include `railway=rail`, exclude `railway:traffic_mode=passenger` to extract a freight-usable rail network. Empty exclude field skips the second pass entirely (backwards compatible).
- Exclude tags embedded in GeoPackage and GeoJSON provenance metadata for reproducibility.

## [1.0.0] - 2026-05-02

### Added

- PBF download with MD5 checksum verification (fail-closed on mismatch).
- Tag filtering via `osmium tags-filter` (full `n/`, `w/`, `r/`, `nwr/` expression syntax).
- Named filter presets.
- Export to GeoPackage and GeoJSON.
  - GeoPackage: multi-layer split, CRS EPSG:4326, ODbL attribution + provenance in `gpkg_metadata`.
  - GeoJSON: RFC 7946; size guardrail warns at > 500 MB or > 1 M features.
- Live WebSocket progress for all long-running phases; each phase is cancellable.
- Size-based ETA hint for large extracts.
- Bilingual UI: English and German.
- Localhost-only bind (`127.0.0.1`); no telemetry, no CDN fetches.
- Docker Compose single-command startup.
