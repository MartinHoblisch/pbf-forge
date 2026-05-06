#!/usr/bin/env bash
set -e

echo "Starting PBF Forge..."

# Create user-config.json if missing (migrate from .env if present)
if [ ! -f "user-config.json" ]; then
    DATA_DIR_ENV=""
    if [ -f ".env" ]; then
        DATA_DIR_ENV=$(grep -oP '(?<=DATA_DIR=)[^\r\n]+' .env | head -1 | tr -d '[:space:]' || true)
    fi
    if [ -n "$DATA_DIR_ENV" ]; then
        CONFIGURED="true"
    else
        CONFIGURED="false"
    fi
    printf '{\n  "configured": %s,\n  "host_data_dir": "%s",\n  "pending_restart": false\n}\n' \
        "$CONFIGURED" "$DATA_DIR_ENV" > user-config.json
fi

# Tell git to ignore local changes to user config (written by the backend)
git update-index --skip-worktree user-config.json 2>/dev/null || true

# Read host_data_dir from config (fallback ./data)
DATA_DIR=$(python3 -c "import json; c=json.load(open('user-config.json')); print(c.get('host_data_dir',''))" 2>/dev/null || true)
if [ -z "$DATA_DIR" ]; then
    DATA_DIR="./data"
fi

mkdir -p "$DATA_DIR"

export DATA_DIR
docker compose down --remove-orphans 2>/dev/null || true
docker compose up --build &
COMPOSE_PID=$!

trap 'docker compose down; exit' INT TERM

echo ""
echo "Waiting for PBF Forge to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000 >/dev/null 2>&1; then
        echo "PBF Forge running at: http://127.0.0.1:8000"
        xdg-open http://127.0.0.1:8000 2>/dev/null || true
        break
    fi
    sleep 1
done

echo "Press Ctrl+C to stop."
wait $COMPOSE_PID
