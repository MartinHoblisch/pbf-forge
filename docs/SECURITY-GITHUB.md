# GitHub Native Security Baseline

PBF Forge relies on GitHub's built-in security features. This document outlines the configuration and scope.

## Enabled Features

### Dependabot

**Status:** Enabled  
**Scope:** `backend/` (pip), root (GitHub Actions, pre-commit, Docker)  
**Cadence:** Weekly updates  
**Config:** `.github/dependabot.yml`

Automated dependency updates for:
- Python packages via pip
- GitHub Actions versions
- pre-commit hook revisions
- The pinned base image digest in `Dockerfile`

GitHub Actions updates are grouped into one pull request, so sub-actions pinned to a shared SHA (`github/codeql-action/*`) always move together.

The Docker ecosystem is restricted to digest refreshes: major and minor tag bumps are ignored, because the Ubuntu tag also determines the `osmium-tool`, `gdal-bin` and `python3` versions. Changing it is a deliberate migration, not a dependency bump.

Dependabot vulnerability alerts and security updates are enabled on the repository.

Pull requests are created automatically; maintainers review and merge.

### Branch Protection

**Status:** Configured for `main`  
**Enforced:**
- Required status checks: `lint`, `test`, `test-windows`, `CodeQL`
- Branch must be up to date with `main` before merging
- Branch deletion blocked

**Not enforced:**
- Pull request approval — merging without review is possible
- Force push — `allow_force_pushes` is on, so history on `main` can be rewritten
- Admin enforcement — `enforce_admins` is off

**Rationale:** Required status checks keep broken code off `main`. Approval and force-push restrictions are currently not active; see Limitations & Gaps.

### Secret Scanning

**Status:** Enabled  
**Push protection:** Enabled  
**Scope:** Entire repository (public)

GitHub scans for secrets in:
- API keys, tokens, credentials
- Private SSH/PGP keys
- Database credentials

**Response:** Pushes containing a recognized secret are blocked outright. Secrets already present in history raise alerts to maintainers.

### Code Scanning

**Status:** Enabled  
**Engine:** CodeQL (`python`), configured in `.github/workflows/security.yml`  
**Triggers:** Push to `main`, all pull requests, weekly cron (Sunday 06:00 UTC)

CodeQL is a required status check, so a failing analysis blocks the merge.

### Image & Supply Chain Scanning

**Trivy:** Builds the image and scans it, failing on CRITICAL/HIGH findings that have a fix available. Results are uploaded as SARIF.  
**OpenSSF Scorecard:** Runs on push and weekly cron, publishing results.

## Pre-Commit Security

Local enforcement via `.pre-commit-config.yaml`:
- **ruff** lints for security issues (E712, F401, etc.)
- **ruff-format** ensures consistent code style (reduces hidden bugs)
- **trailing-whitespace, end-of-file-fixer** prevent whitespace-related issues

The ruff revision in that file is the single source of truth: the CI lint job reads it from there instead of carrying its own pin, so the two cannot drift apart.

Run locally: `pre-commit run --all-files`

## CI Security (GitHub Actions)

**.github/workflows/ci.yml** enforces:
- **ruff lint & format** pass on all Python code
- **pytest** with coverage on Linux (475 tests) plus a platform-safe subset on Windows
- **shellcheck** on `start.sh` and `stop.sh`
- **start.bat** structure smoke test
- **docker build** smoke test (verifies Dockerfile integrity)

**.github/workflows/security.yml** adds CodeQL, Trivy and Scorecard, as described above.

Workflows run on:
- Push to `main`
- All pull requests
- Weekly cron (`security.yml` only)

## Limitations & Gaps

- **No required review:** Branch protection does not require approvals, so a single maintainer can merge unreviewed
- **Force push permitted:** History on `main` can be rewritten
- **System packages unmanaged:** `osmium-tool` and `gdal-bin` are installed via apt, and no Dependabot ecosystem covers apt. Their versions are capped by the Ubuntu 24.04 archive and refreshed by `apt-get upgrade` on each image build. Dependabot tracks the base image digest, which pulls in patched layers, but cannot advance a package past what the archive holds
- **Universe packages carry no vendor security guarantee:** `osmium-tool` and `gdal-bin` live in Ubuntu universe, which has no guaranteed Canonical security maintenance in an LTS (that would require Ubuntu Pro `esm-apps`, not attached here). Trivy reads Ubuntu OVAL/USN data, so a universe vulnerability without a USN can stay unreported — a green scan is weaker evidence here than for `main` packages. Base image migration tracked in issue #44
- **No DAST:** Runtime security testing not automated
- **No SBOM:** GitHub's dependency graph is populated, but no SBOM artifact is published with releases
- **Manual security review:** All releases require manual audit

## Incident Response

If a secret is exposed:
1. GitHub alerts maintainers via email + dashboard
2. Secret is immediately revoked in the service
3. Commit is flagged but remains in history (transparency)
4. Further pushes carrying the secret are blocked by push protection

## Maintenance Cadence

| Task | Frequency |
|------|-----------|
| Review Dependabot PRs | Weekly |
| Review GitHub secret alerts | Ongoing (as they arrive) |
| Audit branch protection rules | Quarterly |
| Update `.pre-commit-config.yaml` | Automated via Dependabot |

---

**Last updated:** 2026-07-25  
**Status:** Verified against repository settings and workflow definitions
