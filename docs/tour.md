# Interface tour

## Filter

<img src="assets/screenshots/filtermanager.PNG" alt="The filter form: include tags, exclude tags, geometry types, attribute mode and output formats" width="100%">

Include tags, exclude tags, the geometry-type checkboxes that decide the
expression prefixes, the attribute mode and the output formats.

## Presets

<img src="assets/screenshots/presets.PNG" alt="Saved filter presets with their tags and settings" width="100%">

A named preset stores the whole filter, include and exclude sets included, so
a recurring job is one click rather than retyped expressions.

## Downloads

<img src="assets/screenshots/downloadmanager.PNG" alt="Download list with per-file progress, local and server size and status" width="100%">

Every PBF in your data directory gets a row, plus any transfer that is running,
with local size, published size and status. Add one by pasting its URL; see
[limits.md](limits.md#the-host-has-to-publish-a-checksum) for what a host has
to provide. Transfers resume, and a check runs against every listed host at
startup.
