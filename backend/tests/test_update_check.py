"""The release check must stay cheap, quiet and optional.

The behaviour worth guarding is not "does it find a version" but what happens
around it: how often GitHub is asked, what a failed request leaves behind, and
that switching the check off really stops the request.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import config
import update_check


def _response(status_code: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    resp.raise_for_status.side_effect = (
        None if status_code < 400 else RuntimeError(f"HTTP {status_code}")
    )
    return resp


@pytest.fixture
def github(monkeypatch):
    """Stands in for the releases API and counts how often it is asked."""
    get = MagicMock(return_value=_response(200, {"tag_name": "v2.0.0"}))
    monkeypatch.setattr(update_check.requests, "get", get)
    return get


# ── Version comparison ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("  v1.2.3  ", (1, 2, 3)),
        ("1.2", None),
        ("1.2.3-rc1", None),
        ("latest", None),
        ("", None),
    ],
)
def test_parse_version(tag, expected):
    assert update_check.parse_version(tag) == expected


@pytest.mark.parametrize(
    "latest, current, expected",
    [
        ("1.2.0", "1.1.0", True),
        ("v1.1.1", "1.1.0", True),
        ("2.0.0", "1.99.99", True),
        ("1.1.0", "1.1.0", False),
        ("1.0.0", "1.1.0", False),
        ("1.2.0-rc1", "1.1.0", False),
        ("garbage", "1.1.0", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert update_check.is_newer(latest, current) is expected


# ── Caching ───────────────────────────────────────────────────────────────────


def test_first_check_asks_github_and_caches_the_answer(github):
    cache = update_check.check(now=1000.0)

    assert cache["latest"] == "v2.0.0"
    assert cache["checked_at"] == 1000.0
    assert github.call_count == 1

    on_disk = json.loads((config.CONFIG_DIR / "update-check.json").read_text(encoding="utf-8"))
    assert on_disk["latest"] == "v2.0.0"


def test_second_check_within_a_day_does_not_ask_again(github):
    update_check.check(now=1000.0)
    cache = update_check.check(now=1000.0 + update_check.CHECK_INTERVAL - 1)

    assert github.call_count == 1
    assert cache["latest"] == "v2.0.0"


def test_check_asks_again_after_a_day(github):
    update_check.check(now=1000.0)
    update_check.check(now=1000.0 + update_check.CHECK_INTERVAL)

    assert github.call_count == 2


def test_missing_release_is_a_cached_answer_not_a_failure(monkeypatch):
    get = MagicMock(return_value=_response(404))
    monkeypatch.setattr(update_check.requests, "get", get)

    cache = update_check.check(now=1000.0)

    assert cache["latest"] == ""
    assert "failed_at" not in cache
    update_check.check(now=1000.0 + 60)
    assert get.call_count == 1


# ── Failure ───────────────────────────────────────────────────────────────────


def test_a_failed_check_keeps_the_previous_answer(github, monkeypatch):
    update_check.check(now=1000.0)

    monkeypatch.setattr(
        update_check.requests, "get", MagicMock(side_effect=OSError("no route to host"))
    )
    cache = update_check.check(now=1000.0 + update_check.CHECK_INTERVAL)

    assert cache["latest"] == "v2.0.0"
    assert cache["failed_at"] == 1000.0 + update_check.CHECK_INTERVAL


def test_a_failed_check_backs_off_but_retries_within_the_day(monkeypatch):
    failing = MagicMock(side_effect=OSError("no route to host"))
    monkeypatch.setattr(update_check.requests, "get", failing)

    update_check.check(now=1000.0)
    update_check.check(now=1000.0 + update_check.RETRY_INTERVAL - 1)
    assert failing.call_count == 1

    update_check.check(now=1000.0 + update_check.RETRY_INTERVAL)
    assert failing.call_count == 2


def test_an_unreadable_cache_does_not_stop_the_check(github):
    (config.CONFIG_DIR / "update-check.json").write_text("{ not json", encoding="utf-8")

    assert update_check.check(now=1000.0)["latest"] == "v2.0.0"


# ── Status ────────────────────────────────────────────────────────────────────


def test_status_reports_an_available_update(github, monkeypatch):
    monkeypatch.setattr(config, "VERSION", "1.1.0")

    status = update_check.status(enabled=True)

    assert status["update_available"] is True
    assert status["latest"] == "v2.0.0"
    assert status["current"] == "1.1.0"


def test_status_on_the_newest_version_offers_nothing(github, monkeypatch):
    monkeypatch.setattr(config, "VERSION", "2.0.0")

    assert update_check.status(enabled=True)["update_available"] is False


def test_disabled_status_never_reaches_the_network(github):
    status = update_check.status(enabled=False)

    assert github.call_count == 0
    assert status == {
        "enabled": False,
        "current": config.VERSION,
        "latest": None,
        "update_available": False,
        "release_url": update_check.RELEASES_PAGE,
        "checked_at": None,
    }
