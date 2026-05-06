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

# Read host_data_dir from config (fallback ./data)
DATA_DIR=$(python3 -c "import json; c=json.load(open('user-config.json')); print(c.get('host_data_dir',''))" 2>/dev/null || true)
if [ -z "$DATA_DIR" ]; then
    DATA_DIR="./data"
fi

mkdir -p "$DATA_DIR"

export DATA_DIR
docker compose down --remove-orphans 2>/dev/null || true
docker compose up --build

echo ""
echo "PBF Forge running at: http://localhost:8000"
echo "To stop: docker compose down"
