# Design: User-Global Config Files

**Date:** 2026-05-07

---

## Context

`.osm_tool_presets.json`, `.osm_tool_urls.json`, and `.filter_history.json` currently live in `DATA_DIR` (the user's chosen project folder). When a user switches `DATA_DIR` for a different geo project, all three files are lost — presets, custom URLs, and ETA history reset. These files are user-global (not project-specific) and must persist across project switches.

Fix: move all three to `config/` alongside `user-config.json`.

---

## Design

### 1. `config.py`

```python
CONFIG_DIR = Path("/app/config")

USER_CONFIG_FILE = CONFIG_DIR / "user-config.json"       # was: Path("/app/user-config.json")
URLS_FILE        = CONFIG_DIR / ".osm_tool_urls.json"    # was: DATA_DIR / ".osm_tool_urls.json"
PRESETS_FILE     = CONFIG_DIR / ".osm_tool_presets.json" # was: DATA_DIR / ".osm_tool_presets.json"
```

`DATA_DIR` unchanged.

### 2. `filter_manager.py:118`

```python
from config import CONFIG_DIR, ...
self._history = FilterHistory(CONFIG_DIR / ".filter_history.json")
```

### 3. `docker-compose.yml`

```yaml
# was:
- ./config/user-config.json:/app/user-config.json
# becomes:
- ./config:/app/config
```

### 4. Migration Logic (one-time, silent)

Each loader checks on first access — copy from old `DATA_DIR` location if new path doesn't exist yet.

`presets.py._load()`:
```python
_old = DATA_DIR / ".osm_tool_presets.json"
if not PRESETS_FILE.exists() and _old.exists():
    import shutil; shutil.copy(_old, PRESETS_FILE)
```

`download_manager.py` URL loader:
```python
_old = DATA_DIR / ".osm_tool_urls.json"
if not URLS_FILE.exists() and _old.exists():
    import shutil; shutil.copy(_old, URLS_FILE)
```

`filter_history.py._load()`:
```python
_old = DATA_DIR / ".filter_history.json"
if not self._path.exists() and _old.exists():
    import shutil; shutil.copy(_old, self._path)
```

No delete of old files.

### 5. `.gitignore`

```
config/.osm_tool_presets.json
config/.osm_tool_urls.json
config/.filter_history.json
```

### 6. File Map

| File | Change |
|------|--------|
| `backend/config.py` | Add `CONFIG_DIR`, update `USER_CONFIG_FILE`, `URLS_FILE`, `PRESETS_FILE` |
| `backend/filter_manager.py` | Import `CONFIG_DIR`, update `FilterHistory` path |
| `backend/presets.py` | Migration check in `_load()` |
| `backend/download_manager.py` | Migration check in URL loader |
| `backend/filter_history.py` | Migration check in `_load()` |
| `docker-compose.yml` | Dir mount replaces file mount |
| `.gitignore` | 3 new entries |

`start.bat`, `start.sh` — no changes.

### 7. Verification

1. Existing `DATA_DIR` files auto-migrated to `config/` on first start
2. Switch `DATA_DIR` → presets/URLs/history still present
3. `git status` clean after run
4. `cd backend && pytest` — all pass
