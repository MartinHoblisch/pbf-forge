# GitHub Native Security Baseline

PBF Forge relies on GitHub's built-in security features. This document outlines the configuration and scope.

## Enabled Features

### Dependabot

**Status:** Enabled  
**Scope:** `backend/` (pip), root (GitHub Actions)  
**Cadence:** Weekly updates  
**Config:** `.github/dependabot.yml`

Automated dependency updates for:
- Python packages via pip
- GitHub Actions versions

Pull requests are created automatically; maintainers review and merge.

### Branch Protection

**Status:** Configured for `main`  
**Requirements:**
- Pull request review before merge (1+ approval)
- Status checks pass (CI workflow)
- No force push or deletion

**Rationale:** Prevents accidental direct commits and requires peer review before release.

### Secret Scanning

**Status:** Enabled (GitHub Free)  
**Scope:** All pushes to `main`

GitHub scans for secrets in:
- API keys, tokens, credentials
- Private SSH/PGP keys
- Database credentials

**Response:** Alerts maintainers; commits are not blocked (advisory only).

### Code Scanning

**Status:** Not configured  
**Reason:** Defer to external tools (bandit, ruff, mypy) in CI for now.

## Pre-Commit Security

Local enforcement via `.pre-commit-config.yaml`:
- **ruff** lints for security issues (E712, F401, etc.)
- **ruff-format** ensures consistent code style (reduces hidden bugs)
- **trailing-whitespace, end-of-file-fixer** prevent whitespace-related issues

Run locally: `pre-commit run --all-files`

## CI Security (GitHub Actions)

**.github/workflows/ci.yml** enforces:
- **ruff lint & format** pass on all Python code
- **pytest** with coverage (116 tests, 0 failures)
- **docker build** smoke test (verifies Dockerfile integrity)

Workflow runs on:
- Push to `main`
- All pull requests

## Limitations & Gaps

- **No SAST CodeQL:** Consider enabling for deeper static analysis
- **No DAST:** Runtime security testing not automated
- **No SBOM:** Software Bill of Materials not generated
- **Manual security review:** All releases require manual audit

## Incident Response

If a secret is exposed:
1. GitHub alerts maintainers via email + dashboard
2. Secret is immediately revoked in the service
3. Commit is flagged but remains in history (transparency)
4. Future commits are scanned to prevent re-exposure

## Maintenance Cadence

| Task | Frequency |
|------|-----------|
| Review Dependabot PRs | Weekly |
| Review GitHub secret alerts | Ongoing (as they arrive) |
| Audit branch protection rules | Quarterly |
| Update `.pre-commit-config.yaml` | As dependencies update |

---

**Last updated:** 2026-04-29  
**Status:** Initial baseline, Phase 5.6
