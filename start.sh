#!/usr/bin/env bash
set -e

# --build ignores the published image and builds from the source in this
# folder. Only useful when that source differs from the last release.
BUILD_FROM_SOURCE=""
if [ "${1:-}" = "--build" ]; then
    BUILD_FROM_SOURCE="yes"
fi

echo "Starting PBF Forge..."

# Create user-config.json if missing (migrate from .env if present)
if [ ! -f "config/user-config.json" ]; then
    DATA_DIR_ENV=""
    if [ -f ".env" ]; then
        DATA_DIR_ENV=$(grep -oP '(?<=DATA_DIR=)[^\r\n]+' .env | head -1 | tr -d '[:space:]' || true)
    fi
    if [ -n "$DATA_DIR_ENV" ]; then
        CONFIGURED="true"
    else
        CONFIGURED="false"
    fi
    mkdir -p config
    printf '{\n  "configured": %s,\n  "host_data_dir": "%s",\n  "pending_restart": false\n}\n' \
        "$CONFIGURED" "$DATA_DIR_ENV" > config/user-config.json
fi

# Read host_data_dir from config (fallback ./data)
DATA_DIR=$(python3 -c "import json; c=json.load(open('config/user-config.json')); print(c.get('host_data_dir',''))" 2>/dev/null || true)
if [ -z "$DATA_DIR" ]; then
    DATA_DIR="./data"
fi

mkdir -p "$DATA_DIR"

URL="http://127.0.0.1:8000"

export DATA_DIR
trap 'docker compose down; exit' INT TERM
docker compose down --remove-orphans 2>/dev/null || true

# Fetching the published image takes seconds where building osmium, GDAL and
# the Python dependencies takes minutes, so a build only happens when there is
# nothing to fetch: a working copy ahead of the last release, a platform with
# no image published for it, or --build to test local changes.
IMAGE=$(docker compose config --images 2>/dev/null | head -1 || true)
NEED_BUILD=""
if [ -n "$BUILD_FROM_SOURCE" ]; then
    NEED_BUILD="yes"
elif docker compose pull --quiet 2>/dev/null; then
    :
elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
    :  # nothing to fetch, but the image is already here
else
    echo ""
    echo "No published image for this version. Building it from source instead."
    NEED_BUILD="yes"
fi

# Build in the foreground, before starting the readiness poll. A cold build
# installs osmium/GDAL and pip dependencies and takes minutes; if the build ran
# in the background the poll below would spend its whole budget waiting for apt
# and give up before the container ever started.
if [ -n "$NEED_BUILD" ] && ! docker compose build; then
    echo ""
    echo "ERROR: Docker image build failed. See the output above."
    exit 1
fi

docker compose up &
COMPOSE_PID=$!

echo ""
echo "Waiting for PBF Forge to be ready..."
READY=""
for _ in $(seq 1 60); do
    if curl -sf "$URL" >/dev/null 2>&1; then
        READY="yes"
        break
    fi
    sleep 1
done

if [ -z "$READY" ]; then
    echo "WARNING: Server did not respond within 60 seconds. Opening browser anyway."
    echo "If the page fails to load, wait a moment and refresh."
fi

echo "PBF Forge running at: $URL"
xdg-open "$URL" >/dev/null 2>&1 \
    || python3 -m webbrowser "$URL" >/dev/null 2>&1 \
    || echo "Could not open a browser automatically. Please open $URL manually."

echo "Press Ctrl+C to stop."
wait $COMPOSE_PID
