"""Test for main.generic_exception_handler — sensitive data leak prevention.

Bug class: An unhandled exception in any route returns the exception's repr to
the client, leaking stack traces, internal paths, secret material in error
messages, etc. The handler must respond with 500 + a generic body that does
NOT include the original exception class name or message.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app

_SECRET = "DBPassword=hunter2"


@pytest.fixture
def throwing_client(reset_state):
    """TestClient with a temporary throwing route + raise_server_exceptions off
    so the registered exception_handler runs (default behavior of TestClient
    is to re-raise app exceptions in the test process)."""

    @app.get("/_test_throw")
    def _throw():
        raise RuntimeError(_SECRET)

    # Move route ahead of StaticFiles mount (registered last in main.py)
    new_route = app.router.routes[-1]
    app.router.routes.remove(new_route)
    app.router.routes.insert(0, new_route)

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) != "/_test_throw"
    ]


def test_unhandled_exception_returns_500(throwing_client):
    resp = throwing_client.get("/_test_throw")
    assert resp.status_code == 500


def test_unhandled_exception_body_is_generic(throwing_client):
    resp = throwing_client.get("/_test_throw")
    body = resp.json()
    assert "detail" in body
    assert body["detail"] == "An internal error occurred. Check server logs for details."


def test_unhandled_exception_does_not_leak_message(throwing_client):
    resp = throwing_client.get("/_test_throw")
    text = resp.text
    assert _SECRET not in text
    assert "RuntimeError" not in text
    assert "hunter2" not in text


def test_unhandled_exception_does_not_leak_traceback(throwing_client):
    resp = throwing_client.get("/_test_throw")
    text = resp.text
    assert "Traceback" not in text
    assert "main.py" not in text
    assert ".py\", line" not in text
