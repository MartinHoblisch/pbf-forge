# Contributing

Issues and pull requests are welcome. Discussions are turned off; everything
goes through [Issues](https://github.com/MartinHoblisch/pbf-forge/issues).

Security problems are the exception: open a
[private advisory](https://github.com/MartinHoblisch/pbf-forge/security/advisories/new)
instead of a public issue. See [SECURITY.md](SECURITY.md).

## Before you start

For anything larger than a fix, open an issue first. This is a single-
maintainer project, and [docs/ROADMAP.md](docs/ROADMAP.md) lists what is out
of scope on purpose. That conversation is cheaper than a rejected branch.

## Setup

[DEVELOPMENT.md](DEVELOPMENT.md) covers running the app and the backend
outside Docker. The short version:

```bash
cd backend
pip install -r requirements.txt
pytest tests/
pre-commit install
```

`pytest` is run from `backend/`, which is where its configuration lives. There
is no configuration at the repository root, so a bare `pytest` there will not
work.

## What CI checks

`ruff` lint and format, the test suite with coverage on Ubuntu, the part of it
that does not need osmium, ogr2ogr or POSIX behaviour on Windows, `shellcheck`
on the shell launchers, a structure check on `start.bat`, and a Docker build.
CodeQL and Trivy run alongside. All of it runs on every pull request.

Run `pre-commit run --all-files` before pushing and most of the lint round
trips disappear.

## Commits

Conventional Commits, which is what the history already uses:

```
fix(filter): account for the work that runs after the last phase
feat(downloads): two-tier retry with visual countdown
docs: rewrite the README and split the detail into docs/
```

Explain why in the body when the diff does not. Do not add co-author trailers.

## Documentation

Every factual statement in the README or in `docs/` has to be true of the code
as merged. If your change makes a documented claim wrong, the documentation
moves in the same pull request. Numbers are measured or they are removed;
there are no estimates in user-facing text.

## Code of conduct

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
