"""Tests for URL resolution helpers in download_manager.

Covers:
  - url_to_filename: Geofabrik URL → local filename ('-latest' stripped only when
    anchored to .osm.pbf$).
  - DownloadManager._resolve_url: filename → URL with date-stamped and -latest
    fallback variants. Anchoring matters — '-12345' (5 digits) must NOT match
    the 6-digit date pattern, and '-latest' must only match at end-of-stem.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from download_manager import DownloadManager, url_to_filename

# ── url_to_filename ──────────────────────────────────────────────────────────


def test_url_to_filename_strips_latest():
    assert (
        url_to_filename("https://download.geofabrik.de/europe-latest.osm.pbf") == "europe.osm.pbf"
    )


def test_url_to_filename_nested_path_uses_last_segment():
    assert (
        url_to_filename("https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf")
        == "berlin.osm.pbf"
    )


def test_url_to_filename_trailing_slash_handled():
    assert (
        url_to_filename("https://download.geofabrik.de/europe-latest.osm.pbf/") == "europe.osm.pbf"
    )


def test_url_to_filename_without_latest_returns_as_is():
    assert url_to_filename("https://download.geofabrik.de/europe.osm.pbf") == "europe.osm.pbf"


def test_url_to_filename_latest_only_matches_when_anchored():
    """'-latest' inside the stem (not before .osm.pbf$) must not be stripped."""
    assert (
        url_to_filename("https://example.com/foo-latest-test.osm.pbf") == "foo-latest-test.osm.pbf"
    )


# ── DownloadManager._resolve_url ─────────────────────────────────────────────


def _dm() -> DownloadManager:
    return DownloadManager(ws_manager=MagicMock())


def test_resolve_url_direct_match(tmp_data_dir):
    dm = _dm()
    # Pre-populated CONTINENTAL_URLS contains europe.osm.pbf
    assert (
        dm._resolve_url("europe.osm.pbf") == "https://download.geofabrik.de/europe-latest.osm.pbf"
    )


def test_resolve_url_date_stamped_strips_six_digits(tmp_data_dir):
    dm = _dm()
    dm._url_mapping["africa.osm.pbf"] = "https://example.com/africa.osm.pbf"
    # 6-digit YYMMDD-style date stripped → falls back to 'africa.osm.pbf'
    assert dm._resolve_url("africa-260427.osm.pbf") == "https://example.com/africa.osm.pbf"


def test_resolve_url_five_digits_does_not_match(tmp_data_dir):
    """Anchor regression: '-12345' (5 digits) must NOT trigger date strip."""
    dm = _dm()
    dm._url_mapping["africa.osm.pbf"] = "https://example.com/africa.osm.pbf"
    assert dm._resolve_url("africa-12345.osm.pbf") is None


def test_resolve_url_seven_digits_does_not_match(tmp_data_dir):
    """Anchor regression: '-1234567' (7 digits) must NOT trigger date strip."""
    dm = _dm()
    dm._url_mapping["africa.osm.pbf"] = "https://example.com/africa.osm.pbf"
    assert dm._resolve_url("africa-1234567.osm.pbf") is None


def test_resolve_url_latest_variant_strips_to_base(tmp_data_dir):
    dm = _dm()
    dm._url_mapping["germany.osm.pbf"] = "https://example.com/germany.osm.pbf"
    assert dm._resolve_url("germany-latest.osm.pbf") == "https://example.com/germany.osm.pbf"


def test_resolve_url_unknown_returns_none(tmp_data_dir):
    dm = _dm()
    assert dm._resolve_url("totally-unknown-region.osm.pbf") is None


def test_resolve_url_priority_direct_beats_stripped(tmp_data_dir):
    """Direct map hit wins even if stripped variant is also registered."""
    dm = _dm()
    dm._url_mapping["africa-260427.osm.pbf"] = "https://example.com/exact.osm.pbf"
    dm._url_mapping["africa.osm.pbf"] = "https://example.com/stripped.osm.pbf"
    assert dm._resolve_url("africa-260427.osm.pbf") == "https://example.com/exact.osm.pbf"


# ── Persistence of a chosen host ─────────────────────────────────────────────


def test_a_chosen_host_survives_a_restart_for_a_built_in_filename(tmp_data_dir):
    """A URL the user picked outranks the built-in default, permanently.

    The built-in mapping exists so a file that arrives without a recorded URL
    can still be checked. The eight names it covers are ordinary filenames, and
    nothing stops another host from serving one, so a source the user chose has
    to be stored rather than silently replaced on the next start.
    """
    chosen = "https://planet.openstreetmap.org/pbf/europe-latest.osm.pbf"

    dm = _dm()
    dm.register_url(chosen, "europe.osm.pbf")
    assert dm._resolve_url("europe.osm.pbf") == chosen

    restarted = _dm()  # reads the stored mapping back from disk
    assert restarted._resolve_url("europe.osm.pbf") == chosen
