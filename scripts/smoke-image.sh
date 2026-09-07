#!/usr/bin/env bash
set -euo pipefail

# Proves that a built image runs: it serves the interface, answers on the API,
# and reports the version it was built for.
#
# CI runs this on every change and the release workflow runs it as the last
# gate before an image is pushed, from one definition, so a release cannot
# publish an image that nothing ever started. The checks used to live inline in
# the release workflow, where they first ran at the tag — and two defects in
# them failed a release that had nothing wrong with the image.
#
# Usage: scripts/smoke-image.sh <image> <expected version>

IMAGE="${1:?usage: smoke-image.sh <image> <expected version>}"
EXPECTED="${2:?usage: smoke-image.sh <image> <expected version>}"
PORT="${PORT:-8000}"

NAME="pbf-forge-smoke-$$"
WORK=$(mktemp -d)

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/data" "$WORK/config"

# The release check is switched off for the run. It would put one request to
# the GitHub API into every CI run for an answer this test does not read, and
# the version still reaches /api/update-check without it.
printf '{"update_check": false}\n' > "$WORK/config/user-config.json"

docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8000" \
    -v "$WORK/data:/data" -v "$WORK/config:/app/config" "$IMAGE" >/dev/null

url="http://127.0.0.1:$PORT"

# `curl && break` would end the script under set -e on the first attempt,
# before the server has finished starting. A container that exits instead of
# starting is reported as that, rather than as a connection refused a minute
# later with nothing to explain it.
ready=""
for _ in $(seq 1 60); do
    state=$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || echo gone)
    if [ "$state" != "true" ]; then
        echo "the container stopped before it answered" >&2
        docker logs "$NAME" >&2
        exit 1
    fi
    if curl -sf "$url/" >/dev/null; then ready="yes"; break; fi
    sleep 1
done

if [ -z "$ready" ]; then
    echo "the container did not answer on $url within 60 seconds" >&2
    docker logs "$NAME" >&2
    exit 1
fi

# Every response is written to a file before it is searched. Piping curl into
# grep -q closes the pipe at the first match, curl then dies of EPIPE with
# status 23, and pipefail fails the run on a check that actually passed.
echo "-- the interface is served"
curl -sf "$url/" -o "$WORK/index.html"
grep -q "PBF Forge" "$WORK/index.html"

echo "-- the API answers"
curl -sf "$url/api/settings" -o "$WORK/settings.json"
grep -q '"configured"' "$WORK/settings.json"

echo "-- the image reports version $EXPECTED"
curl -sf "$url/api/update-check" -o "$WORK/update.json"
reported=$(grep -o '"current":"[^"]*"' "$WORK/update.json" | cut -d'"' -f4)
if [ "$reported" != "$EXPECTED" ]; then
    echo "the image reports $reported, expected $EXPECTED" >&2
    docker logs "$NAME" >&2
    exit 1
fi

docker logs "$NAME"
echo "-- $IMAGE passed"
