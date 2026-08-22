# Filtering

## Expressions

Type each expression on its own line, without a geometry prefix:

```
highway=footway,pedestrian,path,steps
railway=rail
building
```

A bare key matches any object carrying it. `key=value` matches that value.
`key=a,b,c` matches any of them. Several lines are OR: an object matching any
line is kept.

Do not type the `n/`, `w/` or `r/` prefixes yourself. The tool takes them
from the Geometry types checkboxes and puts one in front of every expression,
once per checked type, so `railway=rail` with Ways and Relations checked
becomes `w/railway=rail r/railway=rail`. Typing the prefix yourself produces
`w/w/railway=rail`, which osmium accepts as a filter on a key literally named
`w/railway`. The result is empty, and no error message explains why.

There is no negation inside an expression. An expression starting with `-`
would be read by osmium as a command-line flag, so it is rejected before the
command is built. Excluding is a separate field, described below.

## The two passes

With an exclude set filled in, two commands run in sequence:

```
osmium tags-filter <source> w/railway=rail r/railway=rail -o pass1.osm.pbf
osmium tags-filter pass1.osm.pbf w/railway:traffic_mode=passenger \
    r/railway:traffic_mode=passenger --invert-match -o pass2.osm.pbf
```

The second pass keeps everything that does *not* match, which gives you the
set difference. Both passes take their prefixes from the same checkboxes.

The exclude pass removes objects, not the nodes those objects used. A way that
disappears leaves its nodes behind, orphaned. See [limits.md](limits.md) for
what that means per output format.

## Attribute modes

| Mode | GeoPackage and GeoJSON | PBF |
|---|---|---|
| Standard | `name` and the key of every include expression you typed become columns; every other tag goes into one `other_tags` column | All original tags kept |
| All keys | One column per distinct tag key | All original tags kept |
| Manual | Only the keys you name become columns | A reduction pass strips every other tag |

Standard is the default. Use it unless you have a reason to choose another
mode. Expanding every key is not only slow: SQLite caps a table at 2000
columns, and a continent-sized extract carries more distinct tag keys than
that. The column count is checked before the conversion step, so a run that
would exceed the cap stops with the number instead of failing partway through
the export.

`other_tags` holds a JSON object, not the HSTORE that GDAL's OSM driver writes
into a column of the same name. Query it with SQLite's JSON functions, for
example `json_extract(other_tags, '$.surface')`.

Every feature carries an `osm_id` column holding the raw OSM object id. Ids are
only unique per object type, and the output puts all types in one layer, so a
node and a way can both arrive as `osm_id` 1. Do not use it as a primary key or
as a join key on its own.

## Output formats

| | |
|---|---|
| GeoPackage | The default. One layer named after the output file, EPSG:4326, ODbL attribution and the full filter provenance embedded in `gpkg_metadata`. |
| GeoJSON | Same content as text. Written through a streaming intermediate, so the only size limit is your free disk space, but large files quickly become hard to handle. Above a 200 MB source extract the interface says so. |
| PBF | The filtered extract itself, for feeding into osmium or anything else that reads PBF. Carries no provenance metadata: the format has nowhere to put it. |

Several formats can be selected at once. They share one filter pass, so
picking two costs one export, not two filters.

## The report

Every output file gets a `.txt` beside it, named after the file. It records:

| Field | |
|---|---|
| `Source extract` | The file and its size |
| `Data timestamp` | When the extract was cut from the OSM database, read from the PBF header. Not the download time. |
| `Source URL` | Where it came from, if it was downloaded here. Hand-copied files have no URL and the row is omitted. |
| `Include tags` / `Exclude tags` | The expressions as typed |
| `Geometry types`, `Attributes`, `Filename suffix` | The rest of the form |
| `Job duration` and `PHASES` | Total and per phase, including report writing |
| `Job log` | The log file under `config/jobs/` |

That file is what makes a result reproducible six months later. Keep it with
the output.
