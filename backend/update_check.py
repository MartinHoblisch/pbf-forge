"""Ask GitHub once a day whether a newer release exists.

This is the only outbound request the tool makes that the user did not ask for
by starting a download, so it is deliberately small: one cached call per day,
no identifiers beyond the user agent every request already carries, silent on
failure, and switchable off in the interface. Nothing is downloaded or
installed here — the check only decides whether the interface offers a hint.
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

import config
from config import USER_AGENT

_log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/MartinHoblisch/pbf-forge/releases/latest"
RELEASES_PAGE = "https://github.com/MartinHoblisch/pbf-forge/releases/latest"

# A release every few weeks does not need a tighter interval, and one call a
# day stays far inside GitHub's 60 unauthenticated requests per hour.
CHECK_INTERVAL = 24 * 3600

# A failed check must not lock the next one out for a whole day: a machine that
# happened to be offline would otherwise never learn about an update.
RETRY_INTERVAL = 3600

REQUEST_TIMEOUT = 5

# Release tags are plain semver, optionally with a leading v. A pre-release tag
# fails to match on purpose: it is not a version users should be pushed to.
_TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) for a release tag, None for anything else."""
    match = _TAG.fullmatch(tag.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def is_newer(latest: str, current: str) -> bool:
    """True only when both tags parse and latest is ahead of current."""
    new, old = parse_version(latest), parse_version(current)
    return bool(new and old and new > old)


def _cache_file():
    # Resolved per call so the tests' temporary config directory applies.
    return config.CONFIG_DIR / "update-check.json"


def _read_cache() -> dict:
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _log.warning("Could not read the update cache: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(cache: dict) -> None:
    try:
        _cache_file().write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as exc:
        _log.warning("Could not write the update cache: %s", exc)


def _timestamp(cache: dict, key: str) -> float:
    try:
        return float(cache.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_latest_tag() -> str:
    """The newest release tag, or an empty string when there is no release."""
    resp = requests.get(
        RELEASES_API,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    # A repository without a published release answers 404. That is an answer,
    # not a failure, and caching it keeps the next start from asking again.
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    tag = resp.json().get("tag_name")
    return tag if isinstance(tag, str) else ""


def check(now: float | None = None) -> dict:
    """The newest known release tag, refreshed from GitHub at most once a day.

    Returns the cache: `latest` (tag or empty string), `checked_at` for the
    last answer and `failed_at` for the last unanswered attempt.
    """
    now = time.time() if now is None else now
    cache = _read_cache()

    # A missing timestamp means never, and a clock that moved backwards leaves a
    # negative age; both have to fall through to a fresh request.
    answered_age = now - _timestamp(cache, "checked_at") if "checked_at" in cache else None
    failed_age = now - _timestamp(cache, "failed_at") if "failed_at" in cache else None
    if answered_age is not None and 0 <= answered_age < CHECK_INTERVAL:
        return cache
    if failed_age is not None and 0 <= failed_age < RETRY_INTERVAL:
        return cache

    try:
        tag = _fetch_latest_tag()
    except Exception as exc:
        # Offline, rate limited, GitHub down: the interface stays as it was.
        _log.info("Update check failed: %s", exc)
        cache["failed_at"] = now
        _write_cache(cache)
        return cache

    cache["latest"] = tag
    cache["checked_at"] = now
    cache.pop("failed_at", None)
    _write_cache(cache)
    return cache


def status(enabled: bool) -> dict:
    """What the interface needs to decide whether to offer an update hint."""
    if not enabled:
        return {
            "enabled": False,
            "current": config.VERSION,
            "latest": None,
            "update_available": False,
            "release_url": RELEASES_PAGE,
            "checked_at": None,
        }

    cache = check()
    latest = cache.get("latest") or None
    return {
        "enabled": True,
        "current": config.VERSION,
        "latest": latest,
        "update_available": bool(latest and is_newer(latest, config.VERSION)),
        "release_url": RELEASES_PAGE,
        "checked_at": cache.get("checked_at"),
    }
