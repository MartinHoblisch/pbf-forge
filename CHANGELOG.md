# Changelog

All notable changes to PBF Forge are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Exclusion filtering** — a second tag set ("OSM tags to exclude") runs as an inverted `osmium tags-filter --invert-match` pass after the include pass, producing the set difference. Example: include `railway=rail`, exclude `railway:traffic_mode=passenger` to extract a freight-usable rail network. Empty exclude field skips the second pass entirely (backwards compatible).
- Exclude tags embedded in GeoPackage and GeoJSON provenance metadata for reproducibility.
- **Output report** — every finished output file gets a plain-text sidecar next to it, named after the file itself (`berlin_barge.gpkg.txt`). It records the source extract and its size, every include and exclude tag, the geometry types and attribute mode, the completion timestamp, the job duration and the per-phase timings, plus the host folder and the job-log filename. A report of the same name is overwritten. Writing it can never fail a finished job.
- **Quit button** — a power button in the header stops the server and, with it, the container, so a session can be ended from the browser instead of from a second terminal. It asks for confirmation, and names the cost explicitly when downloads or filter jobs are still running. The tab is not closed automatically: browsers only allow that for windows a script opened, and `start.bat` / `start.sh` open an ordinary one. `stop.bat` and `stop.sh` keep working unchanged.
- The output report now states the **data timestamp of the source extract** under `Source extract` — the moment the extract was cut from the OSM database, read from the PBF header's replication timestamp. This is not the download time, which says nothing about how current the data is. Extracts without that header field fall back to the file's own date, which for a downloaded extract is the publication time the server reported.
- The footer credits **osmium-tool and GDAL**, which do the filtering and the format conversion, and links to the project on GitHub.

### Changed

- **GeoPackage output is one layer, not five.** Up to 1.0.0 the default
  attribute mode handed the filtered PBF to the GDAL OSM driver, which split
  it into `points`, `lines`, `multilinestrings`, `multipolygons` and
  `other_relations`. The export now goes through a GeoJSONSeq intermediate and
  writes a single table named after the output file (`berlin_rail.gpkg` gets a
  `berlin_rail` layer), which is what made a real `other_tags` column possible
  in that mode. Anything that addressed an output layer by the old fixed names
  has to be pointed at the new one.
- The footer no longer scrolls away — it is sticky, like the header and the tab bar already were.
- **The Geofabrik credit is gone, from the footer and from every output file.** It claimed unconditionally what is only a default: the download URL is unrestricted, and any host publishing a PBF beside an `.md5` works — [planet.openstreetmap.org](https://planet.openstreetmap.org/pbf/) and [BBBike](https://download.bbbike.org/osm/) both do. An extract built from either of those still carried "Data sourced via Geofabrik" in its GeoPackage metadata and GeoJSON `attribution` key, which is a false provenance claim in a file users pass on. ODbL credits the contributors, not the distributor, so the embedded attribution now reads "© OpenStreetMap contributors (ODbL 1.0)." and nothing more.
- The output report names the **actual download URL** of each source under `Source URL`, resolved from the stored URL mapping. Files copied into the data directory by hand have no URL and omit the row. This replaces the blanket provider claim with a per-source fact.
- **The header and footer bars render their contents 30% larger, in less space.** Both framed the working area in type smaller than the content inside it, which made the data directory and the connection state harder to read than anything they sat next to. The bars themselves did not grow with their contents — the vertical padding absorbed the increase, so both bars ended up 8 px shorter than before: the header 44 px instead of 52, the footer 36 instead of 44.
- The footer opens with the GitHub link instead of repeating the app name the header already carries.
- The Quit button moved to the far right of the header, past the connection indicator, and the connection labels are lower-case ("connected", "verbunden") so they read as status rather than as headings.
- **The container is no longer restarted automatically after a clean exit.** `docker-compose.yml` now sets `restart: on-failure` instead of `restart: unless-stopped`, which is what lets the Quit button work — under the old policy Docker brought the container straight back up. Crashes are still restarted. The container no longer comes up by itself after a Docker Desktop or host restart; use `start.bat` / `start.sh`.
- Finished filter jobs now list their outputs by **host path** (`D:\osm-data\gpkg\berlin_rail.gpkg`) instead of the container path (`/data/gpkg/berlin_rail.gpkg`), so the path can be pasted straight into a file manager. Falls back to the container path while the data directory is unconfigured.

### Fixed

- **Reading a source extract's data timestamp no longer streams the whole file.** The report states how current the source data is, read from the PBF header. The reader was opened for the default entity types, so it began decoding the data blocks in the background as soon as it was constructed — asking a 4.8 GB extract for its header alone streamed all of it. The first read in a process still returned in milliseconds, every later one paid for the traffic the earlier ones had started: measured against `germany.osm.pbf`, 0.03 s, then 107 s, then 139 s. Since this runs once per published output and the server is a long-lived process, a job writing two formats spent minutes there. With no entity types selected, only the header block is read: 0.05 s, then 0.002 s, unchanged however often it is called.
- **The work after the last phase is a phase of its own.** Writing the reports ran outside the phase list, so the step counter went one past the end and the interface showed "Step 3 of 2" with no name against it, and the time it took appeared in no report. It is now a phase named *write reports*, counted and timed like the others, and listed in every report. Embedding the metadata and moving a finished file into place have moved into the export phase that produces that file, where their cost belongs. The step counter can no longer run past the number of phases.
- **Cancelling no longer rings the completion sound.** The bell fires when the last active transfer or filter job leaves its running state, and a cancel does exactly that — so stopping a download played the *finished* chime, as if it had succeeded. Cancelling a filter job played the failure chime, since a cancelled job is recorded in the same error state as one that failed on its own. Stopping something is not finishing it: cancelled members no longer count towards the batch that rings, and a batch in which everything was cancelled stays silent. If something else in the same batch did run to an end, that still rings. Jobs now carry whether they were cancelled, which is what lets the two kinds of error be told apart.
- **Reloading the page no longer forgets what a check found.** Every directory scan — and one runs on each page load — reset the status column of every row to *Unknown*, while the local and server columns beside it went on showing the result of the check that had supposedly never happened. The verdict of a check is a function of four figures the row already carries, so a scan now re-derives it instead of discarding it. It is re-derived rather than remembered: a file that changed on disk since the last check is judged on what is there now. Only a row that has never been checked has nothing to derive from and stays *Unknown*.
- **A paused download stays paused across a check.** Cancelling an update leaves the previous complete file next to the `.part` of the new one, and a check judged that row by the complete file alone: the status flipped to *Update available* and the progress bar disappeared, although the partial was the progress towards exactly that update. Listing the files did the same, dropping the row to *Unknown*. A partial now decides the status wherever resuming it would still achieve something — and only there. A partial beside a file that is already current remains a remnant, and one the server has built past gives way to the verdict on the complete file: a newer build on the server is what ends a resume, and nothing else.
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

> **Correction, 2026-08-05.** Five statements in this entry did not describe
> what 1.0.0 shipped. They are corrected in place and marked, rather than
> silently rewritten, so the record stays readable against the released tag.
> The tag `v1.0.0` itself is unchanged.

### Added

- PBF download with MD5 checksum verification (fail-closed on mismatch).
- Tag filtering via `osmium tags-filter`. **Corrected:** the original entry
  claimed "full `n/`, `w/`, `r/`, `nwr/` expression syntax". The tool builds
  the geometry prefix itself from the geometry-type checkboxes and puts it in
  front of every expression, so an expression typed with its own prefix is
  prefixed twice and matches nothing.
- Named filter presets.
- Export to GeoPackage, GeoJSON and PBF. **Corrected:** the original entry
  omitted PBF, which 1.0.0 already offered.
  - GeoPackage: one table per geometry type as produced by the GDAL OSM
    driver, CRS EPSG:4326, ODbL attribution + provenance in `gpkg_metadata`.
  - GeoJSON: WGS84. **Corrected:** the original entry claimed RFC 7946
    conformance, which was never requested from `ogr2ogr`. The guardrail
    warns when the selected source extract is larger than 200 MB; the
    original entry named thresholds of 500 MB and 1 M features, neither of
    which appears in the code.
- Live WebSocket progress for all long-running phases; a running job is
  cancellable.
- A warning for source extracts over 1 GB that filtering may take hours
  depending on hardware. **Corrected:** the original entry called this a
  "size-based ETA hint". It estimates nothing.
- Bilingual UI: English and German.
- Localhost-only bind (`127.0.0.1`); no telemetry, no CDN fetches.
- Docker Compose single-command startup.
