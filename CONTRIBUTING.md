# Contributing

## Dev setup

```bash
git clone https://github.com/martinhoblisch/pbf-forge.git
cd pbf-forge
docker compose up          # runs the app at http://127.0.0.1:8000
```

For backend development without Docker:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Running tests

```bash
cd backend
pytest
```

## Linting

```bash
pip install ruff
ruff check .
ruff format --check .
```

## Pull requests

- One logical change per PR.
- Include a test for new behaviour where practical.
- Run `ruff check .` and `pytest` before opening the PR.
- For larger changes, open an Issue first to discuss the approach.

Questions and feature requests → [Discussions](https://github.com/martinhoblisch/pbf-forge/discussions).

## Upgrade cadence

| Component | Cadence | How |
|-----------|---------|-----|
| Ubuntu base image | Quarterly | Update `FROM ubuntu:24.04` digest or new LTS tag |
| osmium-tool / gdal-bin | With base image | Verify versions via `apt-cache policy` in new image |
| Python deps | Weekly (Dependabot) | Review and merge Dependabot PRs |
| GitHub Actions | Weekly (Dependabot) | Review and merge Dependabot PRs |

To get the current base image digest for pinning:
```bash
docker pull ubuntu:24.04
docker inspect ubuntu:24.04 --format='{{index .RepoDigests 0}}'
```
