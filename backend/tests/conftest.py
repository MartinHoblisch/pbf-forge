from __future__ import annotations

from typing import Generator
from unittest.mock import MagicMock

import download_manager as dm_module
import filter_manager as fm_module
import httpx
import main as main_module
import presets as presets_module
import pytest
import routes.filter as routes_filter_module
import routes.settings as routes_settings_module
import state
from fastapi.testclient import TestClient
from main import app

import config

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def reset_state(tmp_data_dir, monkeypatch):
    urls_file = tmp_data_dir / ".osm_tool_urls.json"
    presets_file = tmp_data_dir / ".osm_tool_presets.json"

    monkeypatch.setattr(config, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(config, "URLS_FILE", urls_file)
    monkeypatch.setattr(config, "PRESETS_FILE", presets_file)

    # Patch module-level names that were already imported
    monkeypatch.setattr(dm_module, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(dm_module, "URLS_FILE", urls_file)
    monkeypatch.setattr(fm_module, "DATA_DIR", tmp_data_dir)
    monkeypatch.setattr(presets_module, "PRESETS_FILE", presets_file)
    monkeypatch.setattr(routes_filter_module, "DATA_DIR", tmp_data_dir)

    user_config_file = tmp_data_dir / "user-config.json"
    monkeypatch.setattr(config, "USER_CONFIG_FILE", user_config_file)
    monkeypatch.setattr(routes_settings_module, "USER_CONFIG_FILE", user_config_file)
    monkeypatch.setattr(main_module, "USER_CONFIG_FILE", user_config_file)

    state.ws_manager = None
    state.download_manager = None
    state.filter_manager = None

    yield

    state.ws_manager = None
    state.download_manager = None
    state.filter_manager = None


@pytest.fixture
def client(reset_state) -> Generator:
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def async_client(reset_state):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_mock_process(returncode: int, stdout: str = "") -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout.encode() if isinstance(stdout, str) else stdout
    mock.stderr = b""
    return mock
