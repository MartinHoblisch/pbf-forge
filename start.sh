#!/usr/bin/env bash
set -euo pipefail

echo "Starting PBF Forge..."

if [ ! -f ".env" ]; then
  echo "ERROR: .env file not found."
  echo "Copy .env.example to .env and set DATA_DIR."
  echo "  cp .env.example .env"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not running."
  exit 1
fi

docker compose --env-file .env up --build -d

echo ""
echo "PBF Forge running at: http://localhost:8000"
echo "To stop: docker compose down"
echo ""

# Open browser
sleep 2
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open http://localhost:8000
elif command -v open >/dev/null 2>&1; then
  open http://localhost:8000
fi
