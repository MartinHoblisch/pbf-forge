# Changelog

All notable changes to PBF Forge are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Exclusion filtering** — a second tag set ("OSM-Tags zum Ausschließen") runs as an inverted `osmium tags-filter --invert-match` pass after the include pass, producing the set difference. Example: include `railway=rail`, exclude `railway:traffic_mode=passenger` to extract a freight-usable rail network. Empty exclude field skips the second pass entirely (backwards compatible).
- Exclude tags embedded in GeoPackage and GeoJSON provenance metadata for reproducibility.
- **Output report** — every finished output file gets a plain-text sidecar next to it, named after the file itself (`berlin_barge.gpkg.txt`). It records the source extract and its size, every include and exclude tag, the geometry types and attribute mode, the completion timestamp, the job duration and the per-phase timings, plus the host folder and the job-log filename. A report of the same name is overwritten. Writing it can never fail a finished job.

### Changed

- Finished filter jobs now list their outputs by **host path** (`H:\pbf-forge\data\gpkg\berlin_barge.gpkg`) instead of the container path (`/data/gpkg/berlin_barge.gpkg`), so the path can be pasted straight into a file manager. Falls back to the container path while the data directory is unconfigured.

### Fixed

- `start.sh` now builds the image before starting the readiness poll. Previously the build ran in the background while the poll counted down 30 seconds, so a cold build (minutes of apt/pip work) always exhausted the budget and the browser never opened. The browser now also opens after a readiness timeout instead of being skipped silently, matching `start.bat`.

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
