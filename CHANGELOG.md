# Changelog

All notable changes to PBF Forge are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **An `osm_type` column beside `osm_id`.** OSM ids are unique only per object
  type, and the export puts every type in one layer, so a node and a way could
  both arrive as `osm_id` 1. Using it as a primary key raised a constraint
  violation; joining on it returned wrong rows and said nothing. `osm_type`
  holds `n`, `w` or `r`, so `(osm_type, osm_id)` identifies a feature. `osm_id`
  keeps its integer type and its meaning, so queries that never needed
  uniqueness are unaffected.
- **A count of features dropped for geometry errors.** A way whose nodes are
  cut off at the edge of the extract has no coordinates to build a line from,
  so `osmium export` skips it. It always has; nothing said so, and a run that
  quietly wrote fewer rows than expected looked exactly like a run that did
  not. osmium reports the number under `--verbose`, which the pipeline already
  passed, so the figure existed and reached nobody. The report beside every
  output now carries a "Dropped features" line whenever the count is not zero.
  `-e` is deliberately not passed: on a country-sized extract it can emit tens
  of thousands of lines.

### Changed

- **The container base moved to Ubuntu 26.04 LTS.** It carried Ubuntu 24.04,
  whose archive caps GDAL at 3.8.4 (February 2024) — an old format-parser
  surface that apt cannot advance past what the archive holds. 26.04 raises
  osmium-tool to 1.19, GDAL to 3.12 and python3 to 3.14. The
  integration-marked tests now run inside the built image, so the toolchain
  under test is the one that ships.
- **A failed check now names the unreachable host.** A connection failure or
  timeout while checking a file used to surface only as a generic "Error"
  badge, with the actual reason buried in a hover tooltip as a raw exception
  string. It read the same whether the mirror was down or pbf-forge itself
  had a bug. The status column now shows "`<host>` unreachable" beside the
  badge, so an outage at the source reads as one at a glance.

### Documentation

- Named the facts a reader evaluating the tool looks for and could not find:
  the REST API under `/api` exists and is internal, with no stability promise;
  the image carries osmium-tool 1.19 and GDAL 3.12; `MAX_DOWNLOAD_SIZE` caps a
  download at 100 GB. Guards keep the last two matching the Dockerfile and
  `config.py`.
- Gave the peak disk figure a measurement instead of a shrug. Iceland (62 MB),
  a broad `highway` filter to GeoPackage: 209 MB peak, about 3.4 times the
  source, because the source, the filtered PBF, the streaming export and the
  output coexist. A narrow filter needs far less.

### Removed

- **Nothing is written into output files any more.** GeoPackage and GeoJSON
  outputs used to carry an ODbL notice and a JSON block describing the run.
  Neither is written now.

  The ODbL notice was never required of this tool. The licence binds whoever
  publicly uses the data, which is you when you publish a result, not a
  converter running on your own machine. osmium and GDAL embed nothing either.
  Stamping a notice into every file was a decision made on the user's behalf.

  The run description went into the same GeoPackage table, which was never
  registered through the metadata extension, so no ordinary client displayed
  it. The report beside every output holds the same facts, in a form people can
  read, and it is unchanged.

  `README.md` now states the ODbL obligation as the user's own, and
  `docs/filtering.md` says plainly that no format carries metadata about the
  run.

---

## [1.1.0] - 2026-08-22

Highlights: exclusion filtering as a second, inverted pass; a job queue that
runs several filters at once and adapts to the machine; jobs and their logs
survive a crash and report why they died; a plain-text report beside every
output that makes a run reproducible; a Quit button that ends the session from
the browser; a documentation pass that removed every claim the code did not
support; and four fixes found by checking each of those claims against the
code, including an exclude pass that silently did nothing and a memory limit
read from the host rather than from the container.

### Added

- **Job queue with adaptive parallelism.** Several filter jobs can be started
  at once. How many run in parallel is derived from the machine rather than
  fixed, and the rest wait rather than competing for memory.
- **Resource limits.** Two presets: full power uses every core at normal
  priority, background halves the thread count and runs at nice 10 so a long
  filter leaves the machine usable. Thread count and nice value can also be set
  explicitly, and the explicit value wins.
- **Jobs and their logs survive a crash.** State is written to
  `config/jobs/manifest.json` as it changes, so a container that is killed or
  restarted comes back with its job list intact. A job killed by the
  out-of-memory killer is now reported as that, instead of as an unexplained
  failure.
- **Gate 0, a risk check before any work starts.** A job whose sources look too
  large for the machine's memory raises a confirmation dialog rather than
  running for twenty minutes and then dying.
- **Disk-space checks** before and during a job, so a full disk is reported as
  a full disk.
- **A real `other_tags` column in Standard mode.** Tags outside the curated set
  are folded into one JSON column through a streaming pass over the export,
  rather than being dropped or expanded into thousands of columns.
- **PBF tag reduction in Manual mode.** Choosing specific keys with a PBF
  output now strips the rest from the PBF as well, instead of quietly keeping
  everything.
- **Completion sound** when the last download or filter job in a batch
  finishes, with a separate tone for failure. Off by default, toggled in the
  header.
- **Two-tier download retry.** Short interruptions are retried immediately;
  longer outages fall back to a slow loop with a visible countdown, so a
  transfer survives a network that comes back in an hour.
- **`CONFIG_DIR`, separate from `DATA_DIR`.** Presets, custom URLs and filter
  history are user-global state and no longer live in the data directory,
  which is a place users repoint. Existing files are migrated on first load, so
  nothing has to be moved by hand.
- **The log file path is shown in the interface**, and named in the error when
  a job cannot be recovered.
- **Richer job logs**: timestamps, phase markers, and an inventory of what was
  written.
- **Throughput while a job runs**, in place of the countdown that used to be
  shown.
- **The browser opens by itself on Linux** once the server answers,
  which `start.bat` already did on Windows.
- **Exclusion filtering.** A second tag set ("OSM tags to exclude") runs as an inverted `osmium tags-filter --invert-match` pass after the include pass, producing the set difference. Example: include `railway=rail`, exclude `railway:traffic_mode=passenger` to extract a freight-usable rail network. Empty exclude field skips the second pass entirely (backwards compatible).
- Exclude tags embedded in GeoPackage and GeoJSON provenance metadata for reproducibility.
- **Output report.** Every finished output file gets a plain-text sidecar next to it, named after the file itself (`berlin_barge.gpkg.txt`). It records the source extract and its size, every include and exclude tag, the geometry types and attribute mode, the completion timestamp, the job duration and the per-phase timings, plus the host folder and the job-log filename. A report of the same name is overwritten. Writing it can never fail a finished job.
- **Quit button.** A power button in the header stops the server and, with it, the container, so a session can be ended from the browser instead of from a second terminal. It asks for confirmation, and names the cost explicitly when downloads or filter jobs are still running. The tab is not closed automatically: browsers only allow that for windows a script opened, and `start.bat` / `start.sh` open an ordinary one. `stop.bat` and `stop.sh` keep working unchanged.
- The output report now states the **data timestamp of the source extract** under `Source extract`: the moment the extract was cut from the OSM database, read from the PBF header's replication timestamp. This is not the download time, which says nothing about how current the data is. Extracts without that header field fall back to the file's own date, which for a downloaded extract is the publication time the server reported.
- The footer credits **osmium-tool and GDAL**, which do the filtering and the format conversion, and links to the project on GitHub.

### Changed

- **The filter form starts with nothing preselected.** Ways, Relations and
  GeoPackage were checked when the form opened. The Relations default was the
  costly one: checking it changes what the filter matches but not what the
  export writes, because `osmium export` produces points, linestrings and
  polygons and drops relations that carry no geometry. A user who never touched
  the box paid for matching relations and saw nothing for it. Every box now
  starts empty, so a run states which geometry types and which formats were
  actually asked for.
- **The size countdown is gone.** It was derived from file size alone, which
  does not predict a filter: the same extract takes minutes or hours depending
  on whether ways and relations have to be resolved. Measured throughput is
  shown instead, and extracts over 1 GB carry a plain warning that this may
  take hours.
- **Preset suffixes are English.** Presets saved with German suffixes by an
  earlier version are migrated on load, including ones a partial earlier
  migration had already touched.
- **GeoPackage output is one layer, not five.** Up to 1.0.0 the default
  attribute mode handed the filtered PBF to the GDAL OSM driver, which split
  it into `points`, `lines`, `multilinestrings`, `multipolygons` and
  `other_relations`. The export now goes through a GeoJSONSeq intermediate and
  writes a single table named after the output file (`berlin_rail.gpkg` gets a
  `berlin_rail` layer), which is what made a real `other_tags` column possible
  in that mode. Anything that addressed an output layer by the old fixed names
  has to be pointed at the new one.
- The footer no longer scrolls away; it is sticky, like the header and the tab bar already were.
- **The Geofabrik credit is gone, from the footer and from every output file.** It claimed unconditionally what is only a default: the download URL is unrestricted, and any host publishing a PBF beside an `.md5` works: [planet.openstreetmap.org](https://planet.openstreetmap.org/pbf/) and [BBBike](https://download.bbbike.org/osm/) both do. An extract built from either of those still carried "Data sourced via Geofabrik" in its GeoPackage metadata and GeoJSON `attribution` key, which is a false provenance claim in a file users pass on. ODbL credits the contributors, not the distributor, so the embedded attribution now reads "© OpenStreetMap contributors (ODbL 1.0)." and nothing more.
- The output report names the **actual download URL** of each source under `Source URL`, resolved from the stored URL mapping. Files copied into the data directory by hand have no URL and omit the row. This replaces the blanket provider claim with a per-source fact.
- **The header and footer bars render their contents 30% larger, in less space.** Both framed the working area in type smaller than the content inside it, which made the data directory and the connection state harder to read than anything they sat next to. The bars themselves did not grow with their contents; the vertical padding absorbed the increase, so both bars ended up 8 px shorter than before: the header 44 px instead of 52, the footer 36 instead of 44.
- The footer opens with the GitHub link instead of repeating the app name the header already carries.
- The Quit button moved to the far right of the header, past the connection indicator, and the connection labels are lower-case ("connected", "verbunden") so they read as status rather than as headings.
- **The container is no longer restarted automatically after a clean exit.** `docker-compose.yml` now sets `restart: on-failure` instead of `restart: unless-stopped`, which is what lets the Quit button work: under the old policy Docker brought the container straight back up. Crashes are still restarted. The container no longer comes up by itself after a Docker Desktop or host restart; use `start.bat` / `start.sh`.
- Finished filter jobs now list their outputs by **host path** (`D:\osm-data\gpkg\berlin_rail.gpkg`) instead of the container path (`/data/gpkg/berlin_rail.gpkg`), so the path can be pasted straight into a file manager. Falls back to the container path while the data directory is unconfigured.
- **The URL field no longer names one host.** It read "Enter Geofabrik download URL" above an input that takes any address: the only rejected ones are loopback and private ranges, which would turn the download endpoint into a way to reach services on your machine. The label now names the format, and Geofabrik stays in the example beside it. The one real constraint on a host, an `.md5` published next to the file, is stated in [docs/limits.md](docs/limits.md) with the sidecar requested from four hosts rather than assumed. The documentation is corrected alongside it: there are no ready-made extracts waiting in the Downloads tab, the list is built from the data directory and starts empty.
- **The interface reads in one voice with the documentation.** Sentence case throughout ("Resource limits", "Full power", "OSM attributes"), the three attribute-mode tooltips rewritten, em and en dashes out of interface strings, and the German strings impersonal where two of ninety had addressed the user informally.
- The document language is now declared as English. `<html lang>` said `de` while the markup and the default interface language were English, and that attribute is what screen readers and browser translation prompts read first.

### Fixed

- **A small memory limit did not cap the job queue.** `_compute_max_parallel`
  documents its result as `max(1, min(cpu//4, ram_gb//8))` but skipped the
  memory term entirely below 8 GB, and the shipped container is capped at 4 GB.
  On a machine with many cores the queue sized itself from cores alone and
  would start several filters, each able to peak at 2 GB, inside that 4 GB. A
  container under 8 GB now runs one job at a time.
- **The preset form accepted a preset that could not match anything.** It sent
  an empty geometry list to the API and silently substituted GeoPackage for a
  missing output format. It now refuses both, with the same messages the filter
  form already used.

- **Queue sizing and the memory warning read the host's RAM, not the
  container's limit.** Both took the total from `/proc/meminfo`, which inside a
  container reports the host. A 4 GB container on a 32 GB machine therefore
  believed it had 32 GB: the pre-flight warning almost never fired, and the
  queue could start several jobs whose combined peak exceeded the cap. Measured
  on a container capped at 4 GB, the old figure was 7.7 GB. Both now use the
  cgroup limit where one applies and fall back to `MemTotal` otherwise.
- **The exclude field did nothing for ways a surviving relation referenced.**
  The exclude pass runs `osmium tags-filter --invert-match`, which keeps every
  object that does not match and then completes the references of the
  survivors. A relation still in the file names its member ways, so reference
  completion put back the ways the pass had just removed. This applied whenever
  Relations was checked and the include expression matched relations too, which
  is the configuration the rail-network recipe recommends. Both exclude passes
  now run with `-R`. Nodes are unaffected: an untagged node never matches a tag
  expression, so inversion keeps it without reference completion.
- **Intact downloads from mirrored hosts were reported as checksum failures.**
  Large extracts are redirected to a mirror that serves the same
  `<region>-latest.osm.pbf` name, so the resolved URL never carries a dated
  filename while the sidecar beside it names the build it describes. Comparing
  those names rejected every such download, even when the file was byte-perfect
  — Geofabrik's Germany extract failed on every attempt. Verification now lets
  the digest decide: the file is hashed once, checked against the sidecar beside
  the alias, and against the one beside the resolved build when the first
  describes another build. A mismatch against a sidecar that names the served
  file is still a corrupt download and is still quarantined.
- **A filter asking for more tag keys than GeoPackage can hold now fails before
  it starts.** SQLite caps a table at 2000 columns. Manual mode counts the
  columns it would need and reports the number, instead of failing partway
  through an export that has already run for minutes.
- **Cleared filter jobs no longer reappear** when a preset is applied.
- **Manual columns that do not exist in the data are skipped** rather than
  breaking the `ogr2ogr` query.
- **A download is verified before its timestamp is set, and a corrupt file is
  quarantined** instead of being left where a filter could pick it up.
- **Interrupted downloads write to a `.part` file** and are renamed into place
  only after verification, so a partial transfer can never be mistaken for a
  finished one.
- **The update check bypasses CDN caches**, and a missing `Last-Modified`
  header no longer falls back to the current time, which made every file look
  current.
- **The folder browser handles Docker Desktop's nested drive mount** on
  Windows.
- **Reading a source extract's data timestamp no longer streams the whole file.** The report states how current the source data is, read from the PBF header. The reader was opened for the default entity types, so it began decoding the data blocks in the background as soon as it was constructed; asking a 4.8 GB extract for its header alone streamed all of it. The first read in a process still returned in milliseconds, every later one paid for the traffic the earlier ones had started: measured against `germany.osm.pbf`, 0.03 s, then 107 s, then 139 s. Since this runs once per published output and the server is a long-lived process, a job writing two formats spent minutes there. With no entity types selected, only the header block is read: 0.05 s, then 0.002 s, unchanged however often it is called.
- **The work after the last phase is a phase of its own.** Writing the reports ran outside the phase list, so the step counter went one past the end and the interface showed "Step 3 of 2" with no name against it, and the time it took appeared in no report. It is now a phase named *write reports*, counted and timed like the others, and listed in every report. Embedding the metadata and moving a finished file into place have moved into the export phase that produces that file, where their cost belongs. The step counter can no longer run past the number of phases.
- **Cancelling no longer rings the completion sound.** The bell fires when the last active transfer or filter job leaves its running state, and a cancel does exactly that, so stopping a download played the *finished* chime, as if it had succeeded. Cancelling a filter job played the failure chime, since a cancelled job is recorded in the same error state as one that failed on its own. Stopping something is not finishing it: cancelled members no longer count towards the batch that rings, and a batch in which everything was cancelled stays silent. If something else in the same batch did run to an end, that still rings. Jobs now carry whether they were cancelled, which is what lets the two kinds of error be told apart.
- **Reloading the page no longer forgets what a check found.** Every directory scan (and one runs on each page load) reset the status column of every row to *Unknown*, while the local and server columns beside it went on showing the result of the check that had supposedly never happened. The verdict of a check is a function of four figures the row already carries, so a scan now re-derives it instead of discarding it. It is re-derived rather than remembered: a file that changed on disk since the last check is judged on what is there now. Only a row that has never been checked has nothing to derive from and stays *Unknown*.
- **A paused download stays paused across a check.** Cancelling an update leaves the previous complete file next to the `.part` of the new one, and a check judged that row by the complete file alone: the status flipped to *Update available* and the progress bar disappeared, although the partial was the progress towards exactly that update. Listing the files did the same, dropping the row to *Unknown*. A partial now decides the status wherever resuming it would still achieve something, and only there. A partial beside a file that is already current remains a remnant, and one the server has built past gives way to the verdict on the complete file: a newer build on the server is what ends a resume, and nothing else.
- **A resumed transfer no longer splices two builds together.** A Range request is answered from whatever the URL serves at that moment, and the slow retry loop waits for the network to return for as long as that takes, so a transfer interrupted before a nightly rebuild would resume days later against a different file, appending bytes from the new build onto bytes from the old one. The published size and timestamp are now re-read before every retry, fast or slow, and a partial that predates the build being served is discarded rather than extended. A partial that is still current is resumed as before. When the server cannot be reached for that refresh the figures from the start of the transfer are kept, since the retry is about to run into the same outage.
- **A stale published checksum no longer condemns an intact download.** Hosts serve `<region>-latest.osm.pbf` as a redirect to a dated build and publish an `.md5` next to both, but the sidecar beside the alias is not always in step with the redirect: Geofabrik's `germany-latest.osm.pbf.md5` currently describes a build from two months earlier, and `europe-latest.osm.pbf.md5` runs one rotation ahead of the redirect while the nightly update is in progress. Verifying against it reported a byte-perfect 4.8 GB download as corrupt and quarantined it as `.part.corrupt`. A sidecar names the build it describes; when that name does not match the file that was requested, the redirect is now followed and the checksum taken from the build that was actually served. If the names still disagree, the download is reported as unverifiable, naming both builds, and left in place instead of being quarantined as corrupt.
- **A filter that matches nothing now says so.** Narrowing a tag expression until it selects no feature left an empty intermediate export, which `ogr2ogr` cannot open: it answered with a list of every driver it knows and exit code 1, and the job ended as an error reading `Conversion exited with code 1`. The empty export is now detected before the conversion runs, and the job ends in a status of its own, **No matches** / **Keine Treffer**, with a plain explanation instead of an error. Sources are judged one by one, so a batch in which only some come up empty still publishes the rest.
- A filter job stopped by the absolute timeout now waits for the killed subprocess instead of only signalling it. Without that wait the child was never reaped, so asyncio kept its stdout pipe transport alive with a read still in flight: a leaked process handle per timed-out job, and an `unclosed transport` ResourceWarning once the garbage collector caught up.
- The language button and the folder-browser close button now have tooltips, in both languages. They were the only two controls whose label (`DE`/`EN` and an icon) did not explain what they do.
- **Updates are visible without a forced reload.** The frontend was served without a `Cache-Control` header, so browsers fell back to heuristic freshness (roughly 10% of the file's age) and could keep showing the previous build for hours after a `git pull` and rebuild, without ever contacting the server. It now sends `Cache-Control: no-cache`, which still allows caching but forces a revalidation on every load. Users who already have a stale copy cached need one hard reload (Ctrl+Shift+R); after that the header keeps it current on its own.
- **Icons render on every platform.** The UI drew its icons as emoji characters (bell, folder, gear, lightning, moon, and the download status glyphs), which resolve through the system font stack. On a Linux install without a colour-emoji font they came out as blank boxes or nothing at all, so the sound-notification toggle was invisible there. All of them are now inline SVG from a single sprite, needing no font and inheriting the surrounding text colour.
- `start.sh` now builds the image before starting the readiness poll. Previously the build ran in the background while the poll counted down 30 seconds, so a cold build (minutes of apt/pip work) always exhausted the budget and the browser never opened. The browser now also opens after a readiness timeout instead of being skipped silently, matching `start.bat`.
- GeoPackage metadata writes now close their SQLite connection. `sqlite3.connect()` used as a context manager commits the transaction but leaves the handle open for the garbage collector, so every GeoPackage output leaked two connections (one for the attribution write and one for the provenance write) until collection got around to them.
- Host paths shown for outputs stay consistent with the mount that is actually active. Repointing the data directory writes the new path to the config but requires a restart before the container's bind mount follows; the value is now read once at startup, so paths keep describing where files really are until that restart happens.
- Resource limits (Full Power / Background presets, thread and nice overrides) are now covered by tests at the point where they take effect. The limits are read inside the filter manager, whose module-level config path was never redirected in the test environment, so the whole feature was silently exercised against defaults only.
- **The download host you chose is kept.** Eight filenames carry a fallback URL, so a file that turns up without a recorded one can still be checked for updates. The store dropped every entry matching one of those names before writing, on the assumption they are re-seeded at startup. They are, which is the problem: `europe.osm.pbf` is an ordinary filename and more than one host serves it, so a URL you set for it was discarded and replaced by the fallback at the next start. Every check and every update went to the fallback host from then on, without saying so. An entry is now stored unless it still equals the default it came from.
- **Sorting the download list by status orders outdated files by how far behind they are.** Every row was keyed by its status alone, so all *Update available* rows shared one key and kept whatever order the list arrived in, in both directions. They are now compared on the published and local timestamps rather than on the label, which cannot carry the order: five days behind reads "5 days" and thirty-five days behind reads "1 month".
- **An age one unit long reads in the singular.** The unit labels were fixed in the plural, so a file a month out of date read "1 months outdated" and one day out of date "1 days". Both languages.
- **The tag field labels are translated.** The filter form and the preset editor both labelled their include-tags field "OSM-Tags", written into the markup and therefore German in the English interface, while the exclude field beside them read from the translation table. Three further labels bypassed the table without showing it, their German and English wording being identical.
- **The data directory field no longer calls its value a Windows path.** It was labelled "Directory (Windows path)" in both languages, while the backend takes POSIX paths just as well, so Linux users were reading a label that did not apply to them.
- **The GeoJSON size warning names no invented threshold.** It promised output "can exceed 500 MB", a figure that appears nowhere in the code. It now names the property that holds regardless of size: GeoJSON from a large source gets unwieldy, and GeoPackage stays smaller and opens faster.

### Documentation

- Corrected claims that did not match the code: job history survives a restart
  but running jobs are marked failed rather than resumed; `mem_limit` has to
  be raised together with `memswap_limit`; there is no built-in Europe source;
  Standard mode's columns are `name` plus the keys of the include expressions,
  not a project-curated list; and ODbL attribution is embedded on a best-effort
  basis, into a `gpkg_metadata` row that most clients do not surface.
- Documented that relations without geometry never reach GeoPackage or GeoJSON,
  that `osm_id` is unique only per object type while the output uses one layer,
  and that `other_tags` holds JSON rather than the HSTORE of GDAL's OSM driver.
- Replaced idiomatic and inverted phrasing throughout the README and `docs/`
  with plain wording, for readers whose English is not native.
- Reworked the README: a demo recording in place of the static screenshot, the
  logo above the title, Quickstart directly after the problem statement, and
  the audience, benchmark and alternatives sections removed or moved into
  `docs/`. The interface tour follows the order the tabs are used in.
- Inline code is now reserved for strings that have to be reproduced exactly.
  Tool names, format names and quoted text are set as ordinary prose.

---

## [1.0.0] - 2026-05-02

> **Correction, 2026-08-20.** Nine statements in this entry did not describe
> what 1.0.0 shipped. They are corrected in place and marked, rather than
> silently rewritten, so the record stays readable against the released tag.
> The tag `v1.0.0` itself is unchanged.

### Added

- Source selection by PBF URL. **Corrected:** the original entry listed a
  "Geofabrik region browser with continent → country → sub-region tree and
  file sizes". No browser was built. 1.0.0 took a download URL typed into a
  single field, with Geofabrik as the suggested source.
- PBF download with MD5 checksum verification (fail-closed on mismatch).
- Tag filtering via `osmium tags-filter`. **Corrected:** the original entry
  claimed "full `n/`, `w/`, `r/`, `nwr/` expression syntax". The tool builds
  the geometry prefix itself from the geometry-type checkboxes and puts it in
  front of every expression, so an expression typed with its own prefix is
  prefixed twice and matches nothing.
- Filter history and named presets. **Corrected:** the original entry said the
  history kept the last 50 expressions. The store keeps 200.
- Export to GeoPackage, GeoJSON and PBF. **Corrected:** the original entry
  omitted PBF, which 1.0.0 already offered, and listed GeoParquet, which it did
  not. GeoParquet appeared only in a hint suggesting it as the more efficient
  format.
  - GeoPackage: one table per geometry type as produced by the GDAL OSM
    driver, CRS EPSG:4326, ODbL attribution + provenance in `gpkg_metadata`.
  - GeoJSON: WGS84. **Corrected:** the original entry claimed RFC 7946
    conformance, which was never requested from `ogr2ogr`. The guardrail
    warns when the selected source extract is larger than 200 MB; the
    original entry named thresholds of 500 MB and 1 M features, neither of
    which appears in the code.
- Live WebSocket progress for all long-running phases; a running job is
  cancellable. **Corrected:** the original entry said each phase was
  cancellable. Cancellation applies to the job.
- A warning for source extracts over 1 GB that filtering may take hours
  depending on hardware. **Corrected:** the original entry called this a
  "size-based ETA hint". It estimates nothing.
- Bilingual UI: English and German.
- Localhost-only bind (`127.0.0.1`); no telemetry, no CDN fetches.
- Docker Compose single-command startup.

[1.1.0]: https://github.com/MartinHoblisch/pbf-forge/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/MartinHoblisch/pbf-forge/releases/tag/v1.0.0
