# Alternatives

PBF Forge is a form in front of `osmium tags-filter` and `ogr2ogr`. Every tool
below does something it does not.

## Overpass API and overpass-turbo

A query language against a live copy of the OSM database, with real spatial
predicates: bounding boxes, "around", "in area". For a city, a neighbourhood
or an ad-hoc question, it is the better tool and the answer is current.

It runs on somebody else's server, under a time and memory budget per query,
shared with everyone else using that instance. Country-scale extraction is
what those budgets exist to prevent. If Overpass timing out is what brought
you here, that is the boundary you hit.

## osmium-tool

The engine PBF Forge calls. If you are comfortable in a terminal, it does
everything described here and considerably more: `extract` for area cuts,
`merge`, `sort`, `apply-changes` for diff updates, `fileinfo`.

Nothing here replaces it. What the container adds is that the flags are
already right, the checksum is verified, the intermediates are cleaned up, and
a report describes what ran.

## QuickOSM for QGIS

A QGIS plugin that builds Overpass queries from a form and loads the result
straight into the project. The right answer when you are already in QGIS and
the area is small. It inherits Overpass's ceiling, because it is Overpass.

## osm2pgsql and imposm

For loading OSM data into PostGIS. If the destination is a database you intend
to keep current with diffs, rather than a file you produce once, start there.
They handle schema and updates, which PBF Forge does not attempt.

The filtered PBF output is a reasonable input for either: filter first, load
less.

## Geofabrik custom extracts

Geofabrik will cut an extract to your own polygon as a paid service. Worth it
when the area is what matters and it is not a shape any standard extract has.

## Osmosis

The older Java tool for the same work. Still functional and still documented
in many tutorials, but osmium is faster on the same task, which is why it is
what runs here.

## What none of them are

A hosted service you point a colleague at. PBF Forge is not that either: it
binds to localhost and has no authentication, by design.
