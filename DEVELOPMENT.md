# Development

## Dev setup

```bash
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
./start.sh                 # start.bat on Windows; runs the app at http://127.0.0.1:8000
```

The launchers do the first-run setup (config file, data directory) that a bare
`docker compose up` skips.

They also prefer the published image over a local build, so a checkout with
changed source runs the last release until you ask for a build:

```bash
./start.sh --build          # start.bat --build on Windows
```

For backend development without Docker:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Releasing

A tag publishes the container image, so three places have to name the same
version before the tag is pushed:

1. `VERSION` in `backend/config.py`
2. The image tag in `docker-compose.yml`
3. The Git tag itself, as `v<version>`

`test_compose_pulls_the_version_the_app_reports` catches the first two, and
`.github/workflows/release.yml` refuses to publish when any of the three
disagree. Move the CHANGELOG entries from *Unreleased* into the new version,
commit, then tag:

```bash
git tag v1.2.0 && git push origin v1.2.0
```

The workflow builds the image, starts it and checks that it serves the
interface and reports the version being released before anything is pushed, so
a failed release publishes nothing.

To rehearse the whole pipeline without publishing anything a launcher would
pull, tag a pre-release of the same version:

```bash
git tag v1.2.0-rc.1 && git push origin v1.2.0-rc.1
```

The version check compares the core version, so this passes on the same source.
`docker/metadata-action` never moves `latest`, `{{major}}` or
`{{major}}.{{minor}}` onto a pre-release, and no `docker-compose.yml` names
one, so the only thing published is `1.2.0-rc.1`, which nothing fetches by
accident. Delete the package version and the tag afterwards.

The first push of a package to GHCR creates it as private. Set it to public
once in the package settings, or the launchers fall back to building locally.

## Running tests

```bash
cd backend
pytest
```

625 tests as of this writing. The `cd` is not optional: the pytest
configuration lives in `backend/pyproject.toml`, and there is none at the
repository root.

Some tests need real binaries or POSIX behaviour and are marked accordingly.
To run what a Windows machine can run:

```bash
pytest tests/ -m "not docker and not posix and not integration"
```

Coverage, as CI measures it on Ubuntu:

```bash
pytest tests/ --cov=. --cov-report=term-missing
```

## Linting

```bash
pip install ruff
ruff check .
ruff format --check .
```
