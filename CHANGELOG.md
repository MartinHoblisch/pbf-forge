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
- **Quit button** — a power button in the header stops the server and, with it, the container, so a session can be ended from the browser instead of from a second terminal. It asks for confirmation, and names the cost explicitly when downloads or filter jobs are still running. The tab is not closed automatically: browsers only allow that for windows a script opened, and `start.bat` / `start.sh` open an ordinary one. `stop.bat` and `stop.sh` keep working unchanged.
- The output report now states the **data timestamp of the source extract** under `Source extract` — the moment the extract was cut from the OSM database, read from the PBF header's replication timestamp. This is not the download time, which says nothing about how current the data is. Extracts without that header field fall back to the file's own date, which for a downloaded extract is the publication time the server reported.
- The footer credits **osmium-tool and GDAL**, which do the filtering and the format conversion, and links to the project on GitHub.

### Changed

- The footer no longer scrolls away — it is sticky, like the header and the tab bar already were.
- **The Geofabrik credit is gone, from the footer and from every output file.** It claimed unconditionally what is only a default: the download URL is unrestricted, and any host publishing a PBF beside an `.md5` works — [planet.openstreetmap.org](https://planet.openstreetmap.org/pbf/) and [BBBike](https://download.bbbike.org/osm/) both do. An extract built from either of those still carried "Data sourced via Geofabrik" in its GeoPackage metadata and GeoJSON `attribution` key, which is a false provenance claim in a file users pass on. ODbL credits the contributors, not the distributor, so the embedded attribution now reads "© OpenStreetMap contributors (ODbL 1.0)." and nothing more.
- The output report names the **actual download URL** of each source under `Source URL`, resolved from the stored URL mapping. Files copied into the data directory by hand have no URL and omit the row. This replaces the blanket provider claim with a per-source fact.
- **The header and footer bars render their contents 30% larger, in less space.** Both framed the working area in type smaller than the content inside it, which made the data directory and the connection state harder to read than anything they sat next to. The bars themselves did not grow with their contents — the vertical padding absorbed the increase, so both bars ended up 8 px shorter than before: the header 44 px instead of 52, the footer 36 instead of 44.
- The footer opens with the GitHub link instead of repeating the app name the header already carries.
- The Quit button moved to the far right of the header, past the connection indicator, and the connection labels are lower-case ("connected", "verbunden") so they read as status rather than as headings.
- **The container is no longer restarted automatically after a clean exit.** `docker-compose.yml` now sets `restart: on-failure` instead of `restart: unless-stopped`, which is what lets the Quit button work — under the old policy Docker brought the container straight back up. Crashes are still restarted. The container no longer comes up by itself after a Docker Desktop or host restart; use `start.bat` / `start.sh`.
- Finished filter jobs now list their outputs by **host path** (`H:\pbf-forge\data\gpkg\berlin_barge.gpkg`) instead of the container path (`/data/gpkg/berlin_barge.gpkg`), so the path can be pasted straight into a file manager. Falls back to the container path while the data directory is unconfigured.

### Fixed

- **A resumed transfer no longer splices two builds together.** A Range request is answered from whatever the URL serves at that moment, and the slow retry loop waits for the network to return for as long as that takes — so a transfer interrupted before a nightly rebuild would resume days later against a different file, appending bytes from the new build onto bytes from the old one. The published size and timestamp are now re-read before every retry, fast or slow, and a partial that predates the build being served is discarded rather than extended. A partial that is still current is resumed as before. When the server cannot be reached for that refresh the figures from the start of the transfer are kept, since the retry is about to run into the same outage.
- **A stale published checksum no longer condemns an intact download.** Hosts serve `<region>-latest.osm.pbf` as a redirect to a dated build and publish an `.md5` next to both, but the sidecar beside the alias is not always in step with the redirect: Geofabrik's `germany-latest.osm.pbf.md5` currently describes a build from two months earlier, and `europe-latest.osm.pbf.md5` runs one rotation ahead of the redirect while the nightly update is in progress. Verifying against it reported a byte-perfect 4.8 GB download as corrupt and quarantined it as `.part.corrupt`. A sidecar names the build it describes; when that name does not match the file that was requested, the redirect is now followed and the checksum taken from the build that was actually served. If the names still disagree, the download is reported as unverifiable — naming both builds — and left in place instead of being quarantined as corrupt.
- **A filter that matches nothing now says so.** Narrowing a tag expression until it selects no feature left an empty intermediate export, which `ogr2ogr` cannot open — it answered with a list of every driver it knows and exit code 1, and the job ended as an error reading `Conversion exited with code 1`. The empty export is now detected before the conversion runs, and the job ends in a status of its own, **No matches** / **Keine Treffer**, with a plain explanation instead of an error. Sources are judged one by one, so a batch in which only some come up empty still publishes the rest.
- A filter job stopped by the absolute timeout now waits for the killed subprocess instead of only signalling it. Without that wait the child was never reaped, so asyncio kept its stdout pipe transport alive with a read still in flight — a leaked process handle per timed-out job, and an `unclosed transport` ResourceWarning once the garbage collector caught up.
- The language button and the folder-browser close button now have tooltips, in both languages. They were the only two controls whose label — `DE`/`EN` and an icon — did not explain what they do.
- **Updates are visible without a forced reload** — the frontend was served without a `Cache-Control` header, so browsers fell back to heuristic freshness (roughly 10% of the file's age) and could keep showing the previous build for hours after a `git pull` and rebuild, without ever contacting the server. It now sends `Cache-Control: no-cache`, which still allows caching but forces a revalidation on every load. Users who already have a stale copy cached need one hard reload (Ctrl+Shift+R); after that the header keeps it current on its own.
- **Icons render on every platform** — the UI drew its icons as emoji characters (bell, folder, gear, lightning, moon, and the download status glyphs), which resolve through the system font stack. On a Linux install without a colour-emoji font they came out as blank boxes or nothing at all, so the sound-notification toggle was invisible there. All of them are now inline SVG from a single sprite, needing no font and inheriting the surrounding text colour.
- `start.sh` now builds the image before starting the readiness poll. Previously the build ran in the background while the poll counted down 30 seconds, so a cold build (minutes of apt/pip work) always exhausted the budget and the browser never opened. The browser now also opens after a readiness timeout instead of being skipped silently, matching `start.bat`.
- GeoPackage metadata writes now close their SQLite connection. `sqlite3.connect()` used as a context manager commits the transaction but leaves the handle open for the garbage collector, so every GeoPackage output leaked two connections — one for the attribution write and one for the provenance write — until collection got around to them.
- Host paths shown for outputs stay consistent with the mount that is actually active. Repointing the data directory writes the new path to the config but requires a restart before the container's bind mount follows; the value is now read once at startup, so paths keep describing where files really are until that restart happens.
- Resource limits (Full Power / Background presets, thread and nice overrides) are now covered by tests at the point where they take effect. The limits are read inside the filter manager, whose module-level config path was never redirected in the test environment, so the whole feature was silently exercised against defaults only.

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
