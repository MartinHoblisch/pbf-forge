# Interface tour

The three tabs in the order you use them: download an extract, filter it, and
save the filter if you expect to run it again.

## Downloads

<img src="assets/screenshots/downloadmanager.PNG" alt="Download list with per-file progress, local and server size and status" width="100%">

Every PBF in your data directory gets a row, and so does every running
transfer. Each row shows local size, published size and status. Add a file by
pasting its URL; see [limits.md](limits.md#the-host-has-to-publish-a-checksum)
for what a host has to provide. Interrupted transfers resume, and at startup
the tool checks every host in the list.

## Filter

<img src="assets/screenshots/filtermanager.PNG" alt="The filter form: include tags, exclude tags, geometry types, attribute mode and output formats" width="100%">

Include tags, exclude tags, the geometry-type checkboxes that decide the
expression prefixes, the attribute mode and the output formats. Nothing is
preselected, so every run states which geometry types and which formats you
asked for.

## Presets

<img src="assets/screenshots/presets.PNG" alt="Saved filter presets with their tags and settings" width="100%">

A named preset stores the whole filter, including the include and exclude
sets. A recurring job is then one click instead of retyped expressions.
