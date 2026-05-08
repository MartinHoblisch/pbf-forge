"""Tests for main._clear_pending_restart.

The user-config has a `pending_restart` flag set when settings change. The
backend infers — via comparison with the env var HOST_DATA_DIR baked in by
docker-compose at container creation — whether the new bind mount is now in
effect. Only then is the flag cleared.

Bug class to prevent:
  - Flag never clears → user permanently sees "Restart required" warning.
  - Flag clears prematurely on a plain `docker restart` → user thinks new
    bind mount is active when it isn't, triggers data-loss confusion.
  - Corrupt user-config crashes lifespan startup.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import main as main_module


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_config_file_is_noop(tmp_config_dir):
    # USER_CONFIG_FILE patched by reset_state fixture, file does not exist
    main_module._clear_pending_restart()  # must not raise
    assert not main_module.USER_CONFIG_FILE.exists()


def test_pending_restart_false_is_noop(tmp_config_dir):
    main_module.USER_CONFIG_FILE.write_text(
        json.dumps({"configured": True, "host_data_dir": "H:\\data", "pending_restart": False}),
        encoding="utf-8",
    )
    main_module._clear_pending_restart()
    cfg = _read(main_module.USER_CONFIG_FILE)
    assert cfg["pending_restart"] is False  # unchanged


def test_pending_cleared_when_env_matches_saved_dir(tmp_config_dir):
    """Compose recreated the container with new HOST_DATA_DIR matching the
    saved value → pending_restart flips to false and is persisted."""
    main_module.USER_CONFIG_FILE.write_text(
        json.dumps(
            {
                "configured": True,
                "host_data_dir": "H:\\new_data",
                "pending_restart": True,
            }
        ),
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"HOST_DATA_DIR": "H:\\new_data"}, clear=False):
        main_module._clear_pending_restart()
    cfg = _read(main_module.USER_CONFIG_FILE)
    assert cfg["pending_restart"] is False


def test_pending_persists_when_env_differs(tmp_config_dir):
    """Plain `docker restart` (env still old, saved value new) → flag stays True."""
    main_module.USER_CONFIG_FILE.write_text(
        json.dumps(
            {
                "configured": True,
                "host_data_dir": "H:\\new_data",
                "pending_restart": True,
            }
        ),
        encoding="utf-8",
    )
    with patch.dict("os.environ", {"HOST_DATA_DIR": "H:\\old_data"}, clear=False):
        main_module._clear_pending_restart()
    cfg = _read(main_module.USER_CONFIG_FILE)
    assert cfg["pending_restart"] is True  # not cleared


def test_pending_persists_when_env_unset(tmp_config_dir):
    """No HOST_DATA_DIR env var → can't confirm compose-up happened → keep flag."""
    main_module.USER_CONFIG_FILE.write_text(
        json.dumps(
            {
                "configured": True,
                "host_data_dir": "H:\\new_data",
                "pending_restart": True,
            }
        ),
        encoding="utf-8",
    )
    # Remove env var if present
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("HOST_DATA_DIR", None)
        main_module._clear_pending_restart()
    cfg = _read(main_module.USER_CONFIG_FILE)
    assert cfg["pending_restart"] is True


def test_corrupt_config_does_not_raise(tmp_config_dir):
    """A user with a hand-edited bad JSON must not crash lifespan startup."""
    main_module.USER_CONFIG_FILE.write_text("{bad json", encoding="utf-8")
    main_module._clear_pending_restart()  # warning logged, no exception
    # Original (corrupt) content untouched
    assert main_module.USER_CONFIG_FILE.read_text(encoding="utf-8") == "{bad json"


def test_empty_saved_host_dir_clears_flag_unconditionally(tmp_config_dir):
    """Defensive path: if saved host_data_dir is empty (unreachable via the
    /api/settings endpoint, but possible from a hand-edited config), the
    truthiness guard `if host_data_dir and ...` short-circuits and the flag
    gets cleared — documented behavior, not a bug."""
    main_module.USER_CONFIG_FILE.write_text(
        json.dumps(
            {
                "configured": False,
                "host_data_dir": "",
                "pending_restart": True,
            }
        ),
        encoding="utf-8",
    )
    main_module._clear_pending_restart()
    cfg = _read(main_module.USER_CONFIG_FILE)
    assert cfg["pending_restart"] is False
