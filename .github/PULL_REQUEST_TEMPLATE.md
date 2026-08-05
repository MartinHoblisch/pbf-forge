## What changes

## Why

## How it was verified

Commands run and what they printed. "Tests pass" without the output is not a
verification.

## Checklist

- [ ] `cd backend && pytest tests/` passes
- [ ] `pre-commit run --all-files` passes
- [ ] Commit messages follow Conventional Commits (`fix(filter): …`)
- [ ] Documentation updated if behaviour changed, and every new factual claim
      names the code that makes it true
- [ ] `CHANGELOG.md` updated under `[Unreleased]` if a user would notice
