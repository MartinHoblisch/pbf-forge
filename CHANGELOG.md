# Changelog

All notable changes to PBF Forge are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.0.0] - 2026-05-02

### Added

- Geofabrik region browser with continent → country → sub-region tree and file sizes.
- PBF download with MD5 checksum verification (fail-closed on mismatch).
- Tag filtering via `osmium tags-filter` (full `n/`, `w/`, `r/`, `nwr/` expression syntax).
- Filter history (last 50 expressions) and named presets.
- Export to GeoPackage, GeoParquet, and GeoJSON.
  - GeoPackage: multi-layer split, CRS EPSG:4326, ODbL attribution + provenance in `gpkg_metadata`.
  - GeoParquet: CRS + attribution in `geo` JSON metadata block.
  - GeoJSON: RFC 7946; size guardrail warns at > 500 MB or > 1 M features.
- Live WebSocket progress for all long-running phases; each phase is cancellable.
- Size-based ETA hint for large extracts.
- Bilingual UI: English and German.
- Localhost-only bind (`127.0.0.1`); no telemetry, no CDN fetches.
- Docker Compose single-command startup.
