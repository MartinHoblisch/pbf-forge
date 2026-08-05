<div align="center">
  <img src="docs/assets/logo.png" alt="PBF Forge" width="70%">
</div>

# PBF Forge

> Self-hosted web UI for downloading OSM PBF extracts, from Geofabrik or any other PBF host, and filtering them by tag. Docker-only. Exports GeoPackage, GeoJSON and filtered PBF.

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
  <img src="docs/assets/hero.gif" alt="Downloading a PBF extract, filtering it by tag, and exporting a GeoPackage" width="90%">
</p>

<details>
<summary><strong>Screenshots</strong></summary>

<p align="center">
  <img src="docs/assets/screenshots/presets.PNG" alt="Filter presets manager" width="48%">
  <img src="docs/assets/screenshots/filtermanager.PNG" alt="Tag filter expression editor" width="48%">
</p>
<p align="center">
  <img src="docs/assets/screenshots/downloadmanager.PNG" alt="Download list with per-file progress" width="48%">
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
- **MD5 checksum verification** — every download is verified against the `<file>.osm.pbf.md5` published next to it, before the file counts as complete. Mismatch fails closed, and so does a missing checksum file: a host that publishes no `.md5` cannot be used. A PBF copied into the data directory by hand is picked up as a source but has no checksum to verify against.
- **Tag filtering via `osmium tags-filter`** — tag expressions (`highway`, `railway=rail`, `highway=footway,pedestrian`), applied to the geometry types selected by checkbox. A second tag set removes matches in an inverted pass.
- **Named presets** — save canned filters for reuse.
- **Export formats**
  - **GeoPackage** (`.gpkg`) — recommended. One layer, named after the output file, CRS EPSG:4326, embedded ODbL attribution + provenance metadata in `gpkg_metadata`.
  - **GeoJSON** (`.geojson`) — WGS84. A guardrail warns when the source extract is larger than 200 MB and steers you to GeoPackage.
  - **PBF** (`.osm.pbf`) — the filtered extract itself, for further processing with osmium or another PBF tool.
- **Live progress** — WebSocket streaming for downloads and for every filter and export phase. A running download or filter job can be cancelled. Source extracts over 1 GB carry a warning that filtering may take hours depending on hardware.
- **Bilingual UI** — English and German.
- **Localhost-only by design** — no telemetry, no analytics, no update checks, no font/CDN fetches at runtime. Outbound traffic goes to the PBF and `.md5` URLs you supply, and nowhere else. At startup the tool checks each host already on the list for a newer build, without being asked.

---

## Use-case examples

PBF Forge passes your tag expressions to `osmium tags-filter`. Type each expression on its own line, without a `n/`, `w/` or `r/` prefix: the tool takes those from the **Geometry types** checkboxes and puts one in front of every expression, once per checked type. Pasting `w/highway=footway` with **Ways** checked produces `w/w/highway=footway`, which matches nothing.

The Downloads tab lists the eight continental extracts. Anything smaller is added by pasting its URL, which is also how a host other than Geofabrik is used.

### 1. Pedestrian-only routing graph for Liechtenstein

Goal: GeoPackage of footways and pedestrian areas only.

1. Source: paste `https://download.geofabrik.de/europe/liechtenstein-latest.osm.pbf`, then download it.
2. Filter expression:
   ```
   highway=footway,pedestrian,path,steps
   ```
3. Geometry types: **Ways** only.
4. Export: **GeoPackage**.

Output: one layer named after the output file, CRS EPSG:4326.

### 2. EV charging stations for an entire country

Goal: every `amenity=charging_station` node in Germany, as a point layer for analytics.

1. Source: paste `https://download.geofabrik.de/europe/germany-latest.osm.pbf` (about 4.5 GB).
2. Filter expression:
   ```
   amenity=charging_station
   ```
3. Geometry types: **Nodes** only.
4. Export: **GeoPackage**. GeoJSON of the same result is a single large text file.

Output on the extract of 2026-07-27: 43,252 points in one layer, 14.0 MB, with `socket:*`, `capacity` and `operator` tags carried through.

### 3. Cycling network with named relations for a federal state

Goal: bike routes plus their relations for Bavaria.

1. Source: paste `https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf`.
2. Filter expressions, one per line:
   ```
   route=bicycle
   network=lcn,rcn,ncn,icn
   ```
3. Geometry types: **Nodes**, **Ways** and **Relations**.
4. Export: **GeoPackage**.

Output: one layer named after the output file, holding the route segments and the relations together.

### 4. Rail freight routing network for Europe

Goal: GeoJSON of European rail lines usable for freight routing, with passenger-only tracks excluded.

Freight trains are not permitted on tracks tagged `railway:traffic_mode=passenger`. A single include expression would return those tracks too; the **OSM tags to exclude** field removes them in a second, inverted pass.

1. Source: **Europe**, one of the built-in extracts (about 33 GB, expect hours).
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

Output: one layer holding the mixed-use and freight-dedicated tracks. Load into a routing engine (e.g. pgRouting, Valhalla) to compute freight-feasible paths. The provenance metadata embedded in the GeoPackage and the GeoJSON records both the include and exclude expressions, so the filter is reproducible. A PBF output carries no such metadata.

Expect points in that layer even though only **Ways** is checked. `osmium tags-filter` keeps the nodes a matched way refers to, and the export writes out every node that carries tags of its own, so switches, signals and level crossings arrive alongside the tracks. Measured on the German extract of 2026-07-27 with **Ways** and **Relations** checked: 220,696 lines, 465,402 points and 8 polygons in one layer. Filter the layer by geometry type after loading it if you only want the lines.

---

## Roadmap

User-facing features under consideration are tracked in [docs/ROADMAP.md](docs/ROADMAP.md). Open an Issue if a missing feature is blocking you.

---

## Privacy & commercial use

**Privacy.** PBF Forge does not collect telemetry, analytics, crash reports, or usage statistics. It does not phone home for update checks. The HTML/CSS/JS are served locally from the container: no Google Fonts, no CDN scripts, no third-party trackers. Outbound network calls at runtime go to the PBF and `.md5` URLs on your list, and nowhere else. That list starts with the eight continental Geofabrik extracts and grows with every URL you add. At startup the tool contacts each host on it once to check for a newer build. Container logs (uvicorn access log, application stderr) stay inside the container; nothing is shipped off the host.

**Commercial use.** OpenStreetMap data is licensed under the [Open Database License (ODbL) 1.0](https://www.openstreetmap.org/copyright). PBF Forge embeds ODbL attribution in every GeoPackage and GeoJSON it produces — you must keep that attribution intact in any downstream product. **The terms of the server you download from apply on top of that, and they differ per host.** The built-in continental extracts come from Geofabrik, whose downloads are free for non-commercial use — commercial users should review <https://www.geofabrik.de/geofabrik/agb.html>. If you point PBF Forge at another host, check that host's terms instead.

---

## Security

CI runs the full test suite with coverage on Ubuntu. On Windows it runs the part that does not need osmium, ogr2ogr or POSIX-only behaviour. Every push is CVE-scanned with Trivy.

| What | How |
|---|---|
| Static analysis (SAST) | [CodeQL](https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml) runs on every push and pull request |
| Dependency vulnerabilities | [Dependabot](https://github.com/MartinHoblisch/pbf-forge/security/dependabot) watches pip packages, GitHub Actions, Docker base images and pre-commit hooks |
| Docker image CVEs | [Trivy](https://github.com/MartinHoblisch/pbf-forge/actions/workflows/security.yml) scans the image on every push; the build fails on CRITICAL or HIGH findings for which a fix is available |
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
