"""Tests for main._is_allowed_origin — DNS-rebinding defense for the WS endpoint.

Bug class to prevent:
  A browser on a third-party origin opens ws://localhost:5000/ws and exfiltrates
  download/filter state. The guard must reject any non-loopback Origin header
  while still allowing non-browser clients (curl, no Origin header).
"""

from __future__ import annotations

import pytest

from main import _is_allowed_origin

# ── Allow paths ───────────────────────────────────────────────────────────────


def test_allow_missing_origin():
    """Non-browser clients (curl, websocat) send no Origin → allowed."""
    assert _is_allowed_origin(None) is True


def test_allow_empty_origin_string():
    """Falsy Origin (empty string) → treated as missing."""
    assert _is_allowed_origin("") is True


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:5000",
        "https://localhost:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:5000",
        "ws://127.0.0.1:5000",
        "http://[::1]",
        "http://[::1]:5000",
    ],
)
def test_allow_loopback_origins(origin: str):
    assert _is_allowed_origin(origin) is True


# ── Deny paths ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.com",
        "https://attacker.example",
        "http://10.0.0.1",
        "http://192.168.1.5:5000",
        # Hostname that *contains* localhost as substring but isn't loopback:
        "http://notlocalhost.com",
        "http://localhost.evil.com",  # subdomain attack
    ],
)
def test_deny_non_loopback_origins(origin: str):
    assert _is_allowed_origin(origin) is False


def test_deny_malformed_origin_raising_in_urlparse():
    """urlparse raises on bracketed-IPv6 with bad form (e.g. '[::1::]').

    The guard must catch the exception and return False (deny by default),
    not propagate the error and 500 the WS handshake.
    """
    assert _is_allowed_origin("http://[::1::]/x") is False
