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

| | |
|---|---|
| Source | `https://download.geofabrik.de/europe/liechtenstein-latest.osm.pbf` |
| Include | `highway=footway,pedestrian,path,steps` |
| Geometry types | Ways |
| Format | GeoPackage |

One layer named after the output file, in EPSG:4326. Expect tagged nodes that
those ways refer to as points in the same layer.

## Charging stations for a whole country

| | |
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

| | |
|---|---|
| Source | `https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf` |
| Include | `route=bicycle`<br>`network=lcn,rcn,ncn,icn` |
| Geometry types | Nodes, Ways, Relations |
| Format | GeoPackage |

Two lines in the include field are OR: an object matching either is kept. The
route segments and the relations land in the same layer, alongside any tagged
nodes they use.

## Rail network without passenger-only track

| | |
|---|---|
| Source | Europe, built in (about 33 GB, expect hours) |
| Include | `railway=rail` |
| Exclude | `railway:traffic_mode=passenger` |
| Geometry types | Ways |
| Format | GeoPackage or GeoJSON |

Freight trains are not permitted on `railway:traffic_mode=passenger`, and a
single include expression would return those tracks too. The exclude field
runs a second, inverted pass over the result of the first.

Both expressions land in the output's provenance metadata, so the file says
what produced it.

For scale: the same filter without the exclude pass, run against the 4.5 GB
German extract with Ways and Relations checked, took 17m 27s and peaked at
2 GB of memory. Europe is roughly seven times the input.

## Feeding another tool

Check PBF as the output format and you get the filtered extract itself rather
than a converted layer, with every original tag intact. That is the input you
want for `osmium export` with your own flags, for `osm2pgsql`, or for a second
pass through PBF Forge with a narrower filter.
