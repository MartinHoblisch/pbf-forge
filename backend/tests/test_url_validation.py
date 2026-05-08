"""SSRF guard tests for routes/downloads._validate_url and the API endpoints
that depend on it (/api/url-info, /api/add-url).

Bug class to prevent: an attacker (or careless user) submits a URL pointing to
the host's internal network — Geofabrik dispatch becomes a forwarder that
fetches http://192.168.1.1/admin or http://10.0.0.5/private. The download
manager would then either save the response, or — worse — leak existence
metadata (size, mtime) via /api/url-info responses.

The guard must reject:
  - non-http(s) schemes
  - hostnames with no/empty host
  - blocked literal names (localhost / 127.0.0.1 / ::1 / 0.0.0.0)
  - private IPv4 (10/8, 172.16/12, 192.168/16), loopback, link-local, reserved
  - IPv6 loopback (::1)

The guard must allow:
  - real public hostnames (download.geofabrik.de)
  - public IP literals
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from routes.downloads import _validate_url


# ── _validate_url — direct unit tests ────────────────────────────────────────


def test_accepts_https_geofabrik():
    _validate_url("https://download.geofabrik.de/europe-latest.osm.pbf")  # no raise


def test_accepts_http_public_hostname():
    _validate_url("http://example.com/foo.osm.pbf")  # no raise


def test_accepts_public_ip_literal():
    """8.8.8.8 is public — guard must not block it."""
    _validate_url("http://8.8.8.8/foo.osm.pbf")  # no raise


@pytest.mark.parametrize("url", [
    "ftp://example.com/foo.osm.pbf",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "gopher://example.com/foo",
    "data:text/plain,hello",
])
def test_rejects_non_http_schemes(url: str):
    with pytest.raises(ValueError, match="Only http/https"):
        _validate_url(url)


@pytest.mark.parametrize("url", [
    "http://",
    "https://",
])
def test_rejects_empty_hostname(url: str):
    with pytest.raises(ValueError, match="no hostname"):
        _validate_url(url)


@pytest.mark.parametrize("hostname", [
    "localhost",
    "LOCALHOST",
    "Localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
])
def test_rejects_blocked_hostnames(hostname: str):
    """The literal blocklist must be case-insensitive (matches lower())."""
    url = f"http://{hostname}/foo.osm.pbf" if ":" not in hostname else f"http://[{hostname}]/foo.osm.pbf"
    with pytest.raises(ValueError, match="internal host"):
        _validate_url(url)


@pytest.mark.parametrize("ip", [
    "10.0.0.1",       # 10/8 private
    "10.255.255.254",
    "172.16.0.1",     # 172.16/12 private
    "172.31.255.254",
    "192.168.0.1",    # 192.168/16 private
    "192.168.255.254",
    "127.0.0.5",      # loopback (additional to literal blocklist)
    "169.254.1.1",    # link-local
    "240.0.0.1",      # reserved (240/4)
])
def test_rejects_private_ipv4_literals(ip: str):
    with pytest.raises(ValueError, match="internal host"):
        _validate_url(f"http://{ip}/foo.osm.pbf")


def test_rejects_ipv6_loopback_literal():
    """[::1] in URL form (handled via blocklist on '::1')."""
    with pytest.raises(ValueError, match="internal host"):
        _validate_url("http://[::1]/foo.osm.pbf")


def test_rejects_ipv6_private_literal():
    """fc00::/7 is unique-local IPv6 (private)."""
    with pytest.raises(ValueError, match="internal host"):
        _validate_url("http://[fc00::1]/foo.osm.pbf")


def test_rejects_ipv4_compatible_hostname_172_outside_private():
    """172.32+ is public; 172.16-31 is private. Guard must distinguish."""
    _validate_url("http://172.32.0.1/foo.osm.pbf")  # public; no raise
    with pytest.raises(ValueError, match="internal host"):
        _validate_url("http://172.16.0.1/foo.osm.pbf")


# ── /api/url-info endpoint — SSRF coverage ───────────────────────────────────


def test_url_info_rejects_internal_hostname(client):
    resp = client.post("/api/url-info", json={"url": "http://localhost/x.osm.pbf"})
    assert resp.status_code == 400


def test_url_info_rejects_private_ip(client):
    resp = client.post("/api/url-info", json={"url": "http://10.0.0.1/x.osm.pbf"})
    assert resp.status_code == 400


def test_url_info_rejects_loopback_ipv6(client):
    resp = client.post("/api/url-info", json={"url": "http://[::1]/x.osm.pbf"})
    assert resp.status_code == 400


# ── /api/add-url endpoint — SSRF coverage ────────────────────────────────────


def test_add_url_rejects_internal_hostname(client):
    resp = client.post(
        "/api/add-url",
        json={"url": "http://localhost/x.osm.pbf", "filename": "x.osm.pbf"},
    )
    assert resp.status_code == 400


def test_add_url_rejects_private_ip(client):
    resp = client.post(
        "/api/add-url",
        json={"url": "http://192.168.1.1/x.osm.pbf"},
    )
    assert resp.status_code == 400


def test_add_url_accepts_public_url_and_derives_filename(client):
    """Without explicit filename, derive from URL using url_to_filename rules."""
    import state

    with patch.object(state.download_manager._executor, "submit"):
        resp = client.post(
            "/api/add-url",
            json={"url": "https://example.com/myregion-latest.osm.pbf"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "myregion.osm.pbf"
    assert body["status"] == "queued"


def test_add_url_accepts_public_url_with_explicit_filename(client):
    import state

    with patch.object(state.download_manager._executor, "submit"):
        resp = client.post(
            "/api/add-url",
            json={
                "url": "https://example.com/myregion-latest.osm.pbf",
                "filename": "custom-name.osm.pbf",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["filename"] == "custom-name.osm.pbf"
