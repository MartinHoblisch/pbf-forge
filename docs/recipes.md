# Recipes

Each of these is the whole form. Read [filtering.md](filtering.md) first if the
expression syntax is new to you: expressions carry no `n/`, `w/` or `r/`
prefix, because the tool adds it from the geometry checkboxes.

Sources are added by pasting a PBF URL into the Downloads tab. Any host works
that publishes a `.md5` beside the file, which is what
[limits.md](limits.md#the-host-has-to-publish-a-checksum) is about. The
examples below use Geofabrik, whose paths run
`https://download.geofabrik.de/<continent>/<country>-latest.osm.pbf`, one level
deeper for sub-regions.

## Footpaths for a small country

| Field | Value |
|---|---|
| Source | `https://download.geofabrik.de/europe/liechtenstein-latest.osm.pbf` |
| Include | `highway=footway,pedestrian,path,steps` |
| Geometry types | Ways |
| Format | GeoPackage |

One layer named after the output file, in EPSG:4326. The same layer also contains the
tagged nodes those ways use, as points.

## Charging stations for a whole country

| Field | Value |
|---|---|
| Source | `https://download.geofabrik.de/europe/germany-latest.osm.pbf` (about 4.5 GB) |
| Include | `amenity=charging_station` |
| Geometry types | Nodes |
| Format | GeoPackage |

Measured on the extract of 2026-07-27: 43,252 points, 14 MB, 4m 45s. Nodes-only
filters are the cheap case, because nothing has to be resolved.

GeoJSON of the same result is one large text file. GeoPackage opens faster and
indexes.

## Cycling network with its relations

| Field | Value |
|---|---|
| Source | `https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf` |
| Include | `route=bicycle`<br>`network=lcn,rcn,ncn,icn` |
| Geometry types | Nodes, Ways, Relations |
| Format | GeoPackage |

Two lines in the include field are OR: an object matching either is kept.

Checking Relations changes what the filter matches, not what the export writes.
`osmium export` produces points, linestrings and polygons, so a `route=bicycle`
relation has no geometry and is dropped: its `network`, `ref` and `name` tags do
not reach the GeoPackage. What you get is the member ways as linestrings, plus
any tagged nodes they use as points, in one layer. Relations tagged
`type=multipolygon` or `type=boundary` do export, as polygons. To work with the
relation objects themselves, choose the PBF output and read it with a tool that
understands relations.

## Rail network without passenger-only track

| Field | Value |
|---|---|
| Source | `https://download.geofabrik.de/europe-latest.osm.pbf` (about 33 GB) |
| Include | `railway=rail` |
| Exclude | `railway:traffic_mode=passenger` |
| Geometry types | Ways |
| Format | GeoPackage or GeoJSON |

`railway:traffic_mode=passenger` marks track used by passenger traffic. An
include expression on its own would return that track too, so the exclude field
runs a second, inverted pass over the result of the first. The tag is not mapped
everywhere, so this removes the track that carries it, not every passenger-only
line.

Europe at 33 GB is past the container's default 4 GB memory cap. Raise the cap
first; see [install.md](install.md).

Both expressions are listed in the report beside the output, so the run stays
traceable.

For scale: the same filter without the exclude pass, run against the 4.5 GB
German extract with Ways and Relations checked, took 17m 27s and peaked at
2 GB of memory. Europe is roughly seven times the input.

## Feeding another tool

Check PBF as the output format and you get the filtered extract itself rather
than a converted layer, with every original tag intact. That is the input you
want for `osmium export` with your own flags, for `osm2pgsql`, or for a second
pass through PBF Forge with a narrower filter.
