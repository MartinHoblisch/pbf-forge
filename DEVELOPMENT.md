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

Releases are cut deliberately, not whenever `main` moves. Two questions come
before the mechanics: whether the change needs a release at all, and which
number it gets.

**A patch release fixes a published defect that cannot wait** — a blocked
install or update, data loss, or a security fix. `update.sh` shipping
non-executable in 1.2.0 qualified. Everything else waits for the next minor,
however small the change is and however ready it feels. A feature published
hours after the last tag spends a version number on itself and teaches nobody
to read the changelog.

**The number follows the commits since the last tag.** Any `feat` makes the
next release a minor, even when fixes travel with it; `fix`, `docs`, `ci` and
`chore` alone make it a patch; a breaking change makes it a major.
`git log v1.2.0..HEAD --oneline` answers the question, and the Conventional
Commits subjects CONTRIBUTING.md asks for are what make that possible.

**A defect that reached a release comes back with a test.** The fix and a
check that fails against the published artifact land together, so the same
class of bug cannot ship twice: `test_shell_scripts_are_executable` in
`backend/tests/test_docs_claims.py` reads the recorded file modes, and would
have held 1.2.0 back.

Rehearse anything uncertain with a pre-release tag, described below, rather
than by publishing and patching.

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

A pre-release tag (`v1.2.0-rc.1`) passes the same check, because it is compared
on its core version, and publishes only its own full version: neither `latest`
nor the major and minor tags move onto a pre-release, and no
`docker-compose.yml` names one. That makes it a rehearsal nothing fetches by
accident, which matters once installs are in other people's hands.

The package takes its visibility from the repository on the first push. Check
it once in the package settings after the first release: a private package
makes every launcher fall back to building locally, and nothing in the output
says why.

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
