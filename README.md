<div align="center">
  <img src="docs/assets/logo.png" alt="PBF Forge" width="70%">
</div>

# PBF Forge

> Self-hosted web UI for downloading OSM PBF extracts — from Geofabrik or any other PBF host — and filtering them by tag. Docker-only. Exports GeoPackage and GeoJSON.

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/MartinHoblisch/pbf-forge/releases/latest"><img src="https://img.shields.io/github/v/release/MartinHoblisch/pbf-forge" alt="Release"></a>
  <a href="https://github.com/MartinHoblisch/pbf-forge/actions/workflows/ci.yml"><img src="https://github.com/MartinHoblisch/pbf-forge/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml"><img src="https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml/badge.svg" alt="Security (CodeQL · Trivy)"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/MartinHoblisch/pbf-forge"><img src="https://api.scorecard.dev/projects/github.com/MartinHoblisch/pbf-forge/badge" alt="OpenSSF Scorecard"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue" alt="Platform: Windows | Linux">
  <a href="https://codecov.io/gh/MartinHoblisch/pbf-forge"><img src="https://codecov.io/gh/MartinHoblisch/pbf-forge/branch/main/graph/badge.svg" alt="Coverage"></a>
</p>

<p align="center">
  <img src="docs/assets/hero.gif" alt="PBF Forge — download a Geofabrik extract, filter by tag, export GeoPackage in under a minute" width="90%">
</p>

<details>
<summary><strong>Screenshots</strong></summary>

<p align="center">
  <img src="docs/assets/screenshots/presets.PNG" alt="Filter presets manager" width="48%">
  <img src="docs/assets/screenshots/filtermanager.PNG" alt="Tag filter expression editor" width="48%">
</p>
<p align="center">
  <img src="docs/assets/screenshots/downloadmanager.PNG" alt="Download progress and regional PBF browser" width="48%">
</p>

</details>

---

## Why I built this

I needed reproducible OSM tag-filtered extracts for small GIS jobs and grew tired of stitching `wget` + `osmium tags-filter` + `ogr2ogr` shell pipelines for each request. PBF Forge wraps that pipeline in a browser UI so a non-CLI user can run the same workflow without memorising flags. It does not replace `osmium-tool` or QGIS — it removes friction for the narrow "download → filter → export" loop.

---

## Quickstart

**Requires Docker Desktop or Docker Engine ≥ 24.**

**Linux / macOS**

```bash
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
./start.sh
```

Logs stream in the terminal. Browser opens automatically once the container is ready. Stop with the Quit button in the header, with Ctrl+C (shuts down the container), or with `./stop.sh`.

> **Linux:** if you get `permission denied while trying to connect to the Docker daemon socket`, your user is not in the `docker` group yet. Run `sudo usermod -aG docker $USER`, then log out and back in (or run `newgrp docker` in the current shell). Then retry `./start.sh`.

**Windows** — run in Command Prompt or PowerShell, or double-click `start.bat`:

```
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
start.bat
```

Docker Desktop starts automatically if not running. The browser opens at <http://localhost:8000> once the container is ready. Stop with the Quit button in the header, or with `stop.bat`.

`start.sh` / `start.bat` handle first-run setup (config file creation, data directory). Running `docker compose up` directly is not recommended on a fresh clone — it skips this setup step.

The container binds to `127.0.0.1` only and ships without authentication. **Do not expose it to a LAN or public internet.** See [SECURITY.md](SECURITY.md).

---

## Features

- **Direct downloads** — the eight continental extracts are built in; paste any other PBF URL to add it. File size and update check shown before download. Geofabrik is the default host, not a requirement: [planet.openstreetmap.org](https://planet.openstreetmap.org/pbf/) and [BBBike](https://download.bbbike.org/osm/) work the same way.
- **MD5 checksum verification** — every PBF is verified against the `<file>.osm.pbf.md5` published next to it before any filter step. Mismatch fails closed, and so does a missing checksum file — a host that publishes no `.md5` cannot be used.
- **Tag filtering via `osmium tags-filter`** — full expression syntax (`n/`, `w/`, `r/`, `nwr/`, multi-tag, negation).
- **Named presets** — save canned filters for reuse.
- **Export formats**
  - **GeoPackage** (`.gpkg`) — recommended. Multi-layer (`points`, `lines`, `multilinestrings`, `multipolygons`, `other_relations`), CRS EPSG:4326, embedded ODbL attribution + provenance metadata in `gpkg_metadata`.
  - **GeoJSON** (`.geojson`) — RFC 7946 (WGS84). A size guardrail warns when output would exceed 500 MB or 1 M features and steers you to GeoPackage.
- **Live progress** — WebSocket streaming for download, checksum, filter, and export phases. Each long-running step is cancellable, with a size-based ETA hint (filtering a 30 GB Europe extract takes hours — the UI is honest about it).
- **Bilingual UI** — English and German.
- **Localhost-only by design** — no telemetry, no analytics, no update checks, no font/CDN fetches at runtime. The only outbound network traffic is to `download.geofabrik.de` for PBF and checksum files.

---

## Use-case examples

PBF Forge runs the same `osmium tags-filter` syntax you would type by hand. The expression goes into the **Filter** field; everything else is point-and-click. Three concrete scenarios:

### 1. Pedestrian-only routing graph for Liechtenstein

Goal: GeoPackage of footways and pedestrian areas only.

1. Region: **Europe → Liechtenstein** (`liechtenstein-latest.osm.pbf`, ~2 MB).
2. Filter expression:
   ```
   w/highway=footway,pedestrian,path,steps
   ```
3. Export: **GeoPackage**.

Output: ~5 k features, opens in QGIS as the `lines` layer with CRS EPSG:4326.

### 2. EV charging stations for an entire country

Goal: every `amenity=charging_station` node in Germany, as a single point layer for analytics.

1. Region: **Europe → Germany** (`germany-latest.osm.pbf`, ~4 GB).
2. Filter expression:
   ```
   n/amenity=charging_station
   ```
3. Export: **GeoPackage** (not GeoJSON — that would produce a multi-hundred-MB JSON file).

Output: a few tens of thousands of points with all `socket:*`, `capacity`, and `operator` tags preserved.

### 3. Cycling network with named relations for a federal state

Goal: bike routes plus their hierarchical relations for Bavaria.

1. Region: **Europe → Germany → Bavaria**.
2. Filter expression:
   ```
   nwr/route=bicycle nwr/network=lcn,rcn,ncn,icn
   ```
3. Export: **GeoPackage**.

Output: multi-layer GeoPackage where the `lines` and `other_relations` layers carry the route-segment geometries and the relation membership respectively.

### 4. Rail freight routing network for Europe

Goal: GeoJSON of European rail lines usable for freight routing — passenger-only tracks excluded.

Freight trains are not permitted on tracks tagged `railway:traffic_mode=passenger`. A single include expression would return those tracks too; the **OSM-Tags zum Ausschließen** field removes them in a second pass.

1. Region: **Europe** (`europe-latest.osm.pbf`, ~30 GB — use the progress bar, this takes time).
2. Include expression:
   ```
   railway=rail
   ```
3. Exclude expression:
   ```
   railway:traffic_mode=passenger
   ```
4. Geometry types: **Ways** only.
5. Export: **GeoJSON** or **GeoPackage**.

Output: a line layer containing only mixed-use and freight-dedicated tracks. Load into a routing engine (e.g. pgRouting, Valhalla) to compute freight-feasible paths. The provenance metadata embedded in the output records both the include and exclude expressions so the filter is reproducible.

---

## Roadmap

User-facing features under consideration are tracked in [docs/ROADMAP.md](docs/ROADMAP.md). Open an Issue or start a Discussion if a missing feature is blocking you.

---

## Privacy & commercial use

**Privacy.** PBF Forge does not collect telemetry, analytics, crash reports, or usage statistics. It does not phone home for update checks. The HTML/CSS/JS are served locally from the container — no Google Fonts, no CDN scripts, no third-party trackers. The only outbound network calls at runtime are to `download.geofabrik.de` for the PBF you requested and its `.md5` checksum file. Container logs (uvicorn access log, application stderr) stay inside the container; nothing is shipped off the host.

**Commercial use.** OpenStreetMap data is licensed under the [Open Database License (ODbL) 1.0](https://www.openstreetmap.org/copyright). PBF Forge embeds ODbL attribution in every GeoPackage and GeoJSON it produces — you must keep that attribution intact in any downstream product. **The terms of the server you download from apply on top of that, and they differ per host.** The built-in continental extracts come from Geofabrik, whose downloads are free for non-commercial use — commercial users should review <https://www.geofabrik.de/geofabrik/agb.html>. If you point PBF Forge at another host, check that host's terms instead.

---

## Security

CI verifies builds on Ubuntu and runs the full test suite on Windows; all images are CVE-scanned via Trivy on every push.

| What | How |
|---|---|
| Static analysis (SAST) | [CodeQL](https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml) runs on every push and pull request |
| Dependency vulnerabilities | [Dependabot](https://github.com/MartinHoblisch/pbf-forge/security/dependabot) watches pip packages and GitHub Actions |
| Docker image CVEs | [Trivy](https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml) scans the image on every push — build fails on CRITICAL or HIGH findings |
| Supply-chain posture | [OpenSSF Scorecard](https://scorecard.dev/viewer/?uri=github.com/MartinHoblisch/pbf-forge) runs weekly |
| Secret leak prevention | GitHub Push Protection is active on this repository |

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure process, scope, and response SLA.

---

## Acknowledgements

- **[Geofabrik GmbH](https://www.geofabrik.de/)** — free regional OSM PBF extracts and MD5 checksums.
- **[OpenStreetMap contributors](https://www.openstreetmap.org/copyright)** — the underlying map data, licensed under ODbL 1.0.
- **[osmium-tool](https://osmcode.org/osmium-tool/)** — the C++ engine that does the actual tag filtering.
- **[GDAL / OGR](https://gdal.org/)** — converts filtered PBF into GeoPackage and GeoJSON.
- **[FastAPI](https://fastapi.tiangolo.com/)** — backend framework.

---

## License

[MIT](LICENSE) © 2026 Martin Hoblisch.

OSM data is **not** MIT-licensed — it remains under [ODbL 1.0](https://www.openstreetmap.org/copyright). The MIT license covers PBF Forge's source code only.
