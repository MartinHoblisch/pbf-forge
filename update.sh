#!/usr/bin/env bash
set -e

# Fetches the newest release and hands over to start.sh, which stops the old
# container, rebuilds the image and starts the new one. Nothing here talks to
# Docker directly, so the update path cannot drift away from the start path.

cd "$(dirname "$0")"

echo "Updating PBF Forge..."

if ! command -v git >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Git is not installed, so this folder cannot update itself."
    echo "Get the newest release from:"
    echo "  https://github.com/MartinHoblisch/pbf-forge/releases/latest"
    exit 1
fi

if [ ! -d .git ]; then
    echo ""
    echo "ERROR: This folder is not a Git checkout, so there is nothing to pull."
    echo "Get the newest release from:"
    echo "  https://github.com/MartinHoblisch/pbf-forge/releases/latest"
    exit 1
fi

# Your own edits are never overwritten silently. Config and data live outside
# the tracked files, so this only ever trips on changed source.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo ""
    echo "ERROR: This folder has local changes to tracked files."
    echo "Commit or discard them, then run this script again:"
    echo "  git stash"
    exit 1
fi

# --ff-only: an update may fast-forward, never turn into a merge nobody asked
# for. A diverged branch stops here instead of producing conflicts.
if ! git pull --ff-only; then
    echo ""
    echo "ERROR: Could not fast-forward to the newest release."
    echo "See the message above, or reinstall from:"
    echo "  https://github.com/MartinHoblisch/pbf-forge/releases/latest"
    exit 1
fi

echo ""
echo "Update fetched. Starting PBF Forge..."
exec ./start.sh
