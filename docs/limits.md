# Limits

## Filtering is by tag, never by geometry

There is no bounding box, no polygon clip, no "within 500 m of". If you need
an area cut, do it first with `osmium extract`, then filter the result here.

## Your output contains points you did not ask for

`osmium tags-filter` keeps the nodes that a matched way refers to, because a
way without its nodes has no geometry. Those nodes come through with whatever
tags they carry, and `osmium export` writes out every feature that has tags.

The effect is large. Filtering the 4.5 GB German extract for
`railway=rail` with Nodes unchecked, Ways and Relations checked:

| Geometry | Features |
|---|---|
| Point | 465,402 |
| LineString | 220,696 |
| MultiPolygon | 8 |

Two thirds of the output are points: switches, signals, level crossings,
milestones. They are legitimate data, and they are not what most people expect
from a filter they set to Ways. Filter by geometry type after loading, or in
the SQL your consumer uses.

## The exclude pass leaves nodes behind

`--invert-match` removes the objects that match. It does not remove the nodes
those objects were built from: a second pass would have to know which of them
are still referenced by something that survived.

Measured on a Berlin extract, including `highway` and then excluding
`highway=footway`:

| | Nodes | Ways |
|---|---|---|
| After the include pass | 1,348,436 | 469,384 |
| After the exclude pass | 1,348,436 | 262,760 |

44% of the ways are gone and the node count is unchanged. What that means
depends on the format:

- **PBF output** keeps them. The file contains nodes that belong to nothing.
  Harmless for most consumers, but the file is larger than its contents
  suggest, and a strict validator will complain.
- **GeoPackage and GeoJSON** drop the untagged ones, because the export is not
  asked to keep untagged features. Tagged ones stay: in the run above, the
  exclude pass removed 44% of the lines and not a single point.

If a clean point set matters, filter the points separately with an expression
that names them.

## One layer per output file

The layer is named after the file, and it holds whatever geometry types the
filter produced. Up to version 1.0.0 the default mode split the output into
`points`, `lines`, `multilinestrings`, `multipolygons` and `other_relations`;
that no longer happens. Scripts and project files that use those layer names
have to be changed to the single new layer name.

Because every type shares one layer, `osm_id` is not unique in it: OSM ids are
only unique per object type, so a node and a way can both appear as `osm_id` 1.
Use it together with the geometry type, not as a primary key.

## GeoJSON has no hard size limit, but large files are hard to use

The export streams, so the only hard ceiling is free disk space. There is a
warning when the selected source extract is larger than 200 MB and GeoJSON is
checked. That threshold is on the input, not the output, because the output
size is not known before the filter runs. GeoPackage opens faster and supports
spatial indexes, which is why it is the default.

## No negation inside an expression

An expression beginning with `-` would reach osmium as a command-line flag, so
it is rejected. Use the exclude field, which runs a second pass.

## The host has to publish a checksum

Any PBF URL works. There is no allowlist, and nothing in the tool prefers one
host. Four internal hostnames and any literal loopback, private, link-local or
reserved IP address are rejected, so the download endpoint is not an obvious
way to reach services on your machine. The check is a guard rail, not a
boundary: it does not resolve hostnames and does not re-check redirects. See
[../SECURITY.md](../SECURITY.md).

The one requirement is a `.md5` sidecar next to the file, at the download URL
with `.md5` appended. If the checksum cannot be verified, the download is
rejected, so every download from a host that publishes no sidecar fails. Checked by requesting the
sidecar, not by assumption:

| Host | `<url>.md5` |
|---|---|
| `download.geofabrik.de` | yes |
| `planet.openstreetmap.org` | yes |
| `download.bbbike.org` | yes |
| `download.openstreetmap.fr` | no, downloads from it fail |

A PBF you copy into the data directory yourself needs no checksum. It has
nothing to verify against and is used as it is.

## Extracts are snapshots

The data is as current as the extract, which is hours to days old depending on
the host and the region. The report next to every output states the extract's
OSM timestamp, which is the number that matters, not the download time.

## Cost is driven by the filter, not the file

A nodes-only filter scans. A filter touching ways or relations has to resolve
members, which costs time and memory. Both runs below are the same 4.5 GB
`germany.osm.pbf` on the same machine, four cores with the container capped at
4 GB:

| | `amenity=charging_station`, Nodes | `railway=rail`, Ways and Relations |
|---|---|---|
| Runtime | 4m 45s | 17m 27s |
| Peak memory | 73 MiB | 2.01 GiB |
| Output | 14 MB, 43,252 features | 253 MB, 686,106 features |

The filter over ways and relations took roughly four times as long and thirty
times the memory. The absolute times say more about the hardware than about the
tool; the ratio between the two runs is what carries over to your own machine.
Plan for the member-resolving case,
and see [install.md](install.md) if a job is killed for memory.

## No authentication

There is no login and no user separation, and on Windows the folder picker can
read every drive Docker Desktop can. [../SECURITY.md](../SECURITY.md) states
the full posture and the known gaps.
