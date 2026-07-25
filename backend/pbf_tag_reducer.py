"""Strip unwanted tags from a PBF file, keeping the objects themselves intact.

Used by the "manual columns" mode, where the output should carry only the tag
keys the user picked. osmium-tool has no equivalent operation, so this walks the
file with pyosmium instead.
"""

from __future__ import annotations

import osmium
import osmium.io


def reduce_tags(src: str, dst: str, keep: set[str]) -> None:
    """Write *src* PBF to *dst* keeping only tags whose key is in *keep*."""
    writer = osmium.SimpleWriter(dst)
    try:
        _TagReducer(writer, keep).apply_file(src)
    finally:
        writer.close()


class _TagReducer(osmium.SimpleHandler):
    def __init__(self, writer: osmium.SimpleWriter, keep: set[str]) -> None:
        super().__init__()
        self._writer = writer
        self._keep = keep

    def node(self, n: osmium.osm.Node) -> None:
        self._writer.add_node(n.replace(tags={k: v for k, v in n.tags if k in self._keep}))

    def way(self, w: osmium.osm.Way) -> None:
        self._writer.add_way(w.replace(tags={k: v for k, v in w.tags if k in self._keep}))

    def relation(self, r: osmium.osm.Relation) -> None:
        self._writer.add_relation(r.replace(tags={k: v for k, v in r.tags if k in self._keep}))
