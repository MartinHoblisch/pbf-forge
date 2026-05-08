"""Tag-filter logic tests for pbf_tag_reducer._TagReducer.

The pyosmium library is a C extension that's only present in the Docker
runtime image, not in the dev/test environment. We stub osmium in sys.modules
before import so the filter-comprehension logic can still be exercised in
unit tests. The apply_file call (which actually parses a PBF stream) is
NOT covered here — that needs real osmium and a real PBF, and is exercised
end-to-end by the docker-build smoke test.

Bug class to prevent: a regression in the tag-filter comprehension that
either keeps tags it shouldn't (privacy leak) or strips tags it should keep
(silent data loss in the manual-columns export).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


class _FakeSimpleHandler:
    """Stand-in for osmium.SimpleHandler so _TagReducer can subclass it."""
    def __init__(self, *args, **kwargs):
        pass

    def apply_file(self, src):
        return None


# Pre-import stub so `import pbf_tag_reducer` succeeds without real pyosmium
_osmium_stub = MagicMock()
_osmium_stub.SimpleHandler = _FakeSimpleHandler
_osmium_stub.SimpleWriter = MagicMock  # used as a class in reduce_tags
_osmium_io_stub = MagicMock()

sys.modules.setdefault("osmium", _osmium_stub)
sys.modules.setdefault("osmium.io", _osmium_io_stub)

# Now safe to import the module under test
from pbf_tag_reducer import _TagReducer, reduce_tags  # noqa: E402


class _FakeOsmObject:
    """Minimal stand-in for osmium.osm.Node/Way/Relation supporting
    `.tags` iteration and `.replace(tags=...)`."""
    def __init__(self, tags: list[tuple[str, str]]):
        self.tags = tags

    def replace(self, *, tags: dict) -> "_FakeOsmObject":
        return _FakeOsmObject(list(tags.items()))


def _writer() -> MagicMock:
    w = MagicMock()
    w.add_node = MagicMock()
    w.add_way = MagicMock()
    w.add_relation = MagicMock()
    w.close = MagicMock()
    return w


# ── filter logic on each entity type ─────────────────────────────────────────


@pytest.mark.parametrize("entity_method,writer_method", [
    ("node", "add_node"),
    ("way", "add_way"),
    ("relation", "add_relation"),
])
def test_tag_reducer_filters_tags_via_keep_set(entity_method, writer_method):
    w = _writer()
    reducer = _TagReducer(writer=w, keep={"a", "c"})
    obj = _FakeOsmObject([("a", "1"), ("b", "2"), ("c", "3")])

    getattr(reducer, entity_method)(obj)

    write_call = getattr(w, writer_method)
    write_call.assert_called_once()
    replaced = write_call.call_args.args[0]
    # Replacement should contain only the kept keys, in the same order
    assert replaced.tags == [("a", "1"), ("c", "3")]


def test_tag_reducer_empty_keep_strips_all_tags():
    w = _writer()
    reducer = _TagReducer(writer=w, keep=set())
    obj = _FakeOsmObject([("a", "1"), ("b", "2")])

    reducer.node(obj)

    replaced = w.add_node.call_args.args[0]
    assert replaced.tags == []


def test_tag_reducer_no_matching_keys_yields_empty_tags():
    w = _writer()
    reducer = _TagReducer(writer=w, keep={"x", "y"})
    obj = _FakeOsmObject([("a", "1"), ("b", "2")])

    reducer.node(obj)

    replaced = w.add_node.call_args.args[0]
    assert replaced.tags == []


def test_tag_reducer_preserves_value_unchanged():
    """The filter should never mutate the value side, only the key side."""
    w = _writer()
    reducer = _TagReducer(writer=w, keep={"name"})
    obj = _FakeOsmObject([("name", "東京都"), ("amenity", "cafe")])

    reducer.node(obj)

    replaced = w.add_node.call_args.args[0]
    assert replaced.tags == [("name", "東京都")]


# ── reduce_tags entry point + cleanup ────────────────────────────────────────


def test_reduce_tags_closes_writer_after_apply(monkeypatch):
    """The writer's .close() must run via the try/finally so the destination
    PBF is finalized even if apply_file raised."""
    instances = []

    def fake_writer_factory(dst):
        w = _writer()
        instances.append(w)
        return w

    import pbf_tag_reducer as ptr
    monkeypatch.setattr(ptr.osmium, "SimpleWriter", fake_writer_factory)

    reduce_tags(src="src.osm.pbf", dst="dst.osm.pbf", keep={"name"})

    assert len(instances) == 1
    instances[0].close.assert_called_once()


def test_reduce_tags_closes_writer_on_apply_failure(monkeypatch):
    """If apply_file raises, the writer must still be closed."""
    import pbf_tag_reducer as ptr

    instances = []

    def fake_writer_factory(dst):
        w = _writer()
        instances.append(w)
        return w

    class _RaisingHandler(_FakeSimpleHandler):
        def apply_file(self, src):
            raise RuntimeError("simulated osmium parse error")

    monkeypatch.setattr(ptr.osmium, "SimpleWriter", fake_writer_factory)
    monkeypatch.setattr(ptr, "_TagReducer", _RaisingHandler)

    with pytest.raises(RuntimeError, match="simulated osmium parse error"):
        reduce_tags(src="src.osm.pbf", dst="dst.osm.pbf", keep={"name"})

    instances[0].close.assert_called_once()
