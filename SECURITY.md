# Security Policy

## Reporting a vulnerability

Open a [private security advisory](https://github.com/MartinHoblisch/pbf-forge/security/advisories/new) with a description and reproduction steps. Please do not open a public GitHub Issue for security reports.

## Scope

PBF Forge is designed to run on `127.0.0.1` (localhost) only, by a single trusted user on a personal or workstation machine.

## Explicit non-goals

- **No authentication or access control.** Do not expose PBF Forge to a LAN, VPN, or public internet. There is no login, no API key, and no multi-user isolation. Every endpoint is reachable by anything that can reach the port, including `GET /api/fs/browse`, which lists directories on the host so the folder picker can work.
- **Filter expressions are passed as argv to `osmium tags-filter`.** They are not sandboxed beyond argument-list validation. Do not run PBF Forge on a shared or multi-tenant host.
- **No hardened container.** The Docker image is a convenience wrapper, not a security boundary. Run it on a machine you control.

## What the container can read

On Linux the container sees the data directory and `./config`, and nothing else.

On Windows, `docker-compose.windows.yml` adds `/mnt:/host_drives:ro`. Docker Desktop exposes the host's drive letters under `/mnt`, so on Windows the container can read every drive the Docker Desktop user can, read-only. This is what makes the folder picker able to offer `C:`, `D:` and so on. If that is more than you want to hand to a container, start it with `docker compose up` without the Windows override file; the folder picker then has nothing to list, and the data directory still works.

## Known gaps

Stated rather than implied, because a security policy that only lists what is covered is misleading about what is not.

- **No required review on `main`.** Branch protection requires the `lint`, `test`, `test-windows` and `CodeQL` checks to pass and forbids branch deletion, but it does not require an approving review. A single maintainer can merge unreviewed.
- **Force push to `main` is permitted**, and administrators are not subject to the protection rules. History on `main` can be rewritten.
- **System packages are unmanaged.** `osmium-tool` and `gdal-bin` are installed with apt, and no Dependabot ecosystem covers apt. Their versions are capped by the Ubuntu 24.04 archive and refreshed by `apt-get upgrade` on each image build. Dependabot tracks the pinned base image digest, which pulls in patched layers, but cannot advance a package past what the archive holds.
- **Universe packages carry no vendor security guarantee.** `osmium-tool` and `gdal-bin` live in Ubuntu universe, which has no guaranteed Canonical security maintenance in an LTS. Trivy reads Ubuntu OVAL and USN data, so a universe vulnerability without a USN can go unreported: a green scan is weaker evidence here than it would be for a package from `main`. Base image migration is tracked in issue [#44](https://github.com/MartinHoblisch/pbf-forge/issues/44).
- **No runtime security testing (DAST)** and **no SBOM artifact** published with releases. GitHub's dependency graph is populated; nothing is attached to a release.
- **Release audit is manual.** This is a solo-maintained project.

## Response SLA

| Stage | Target |
|---|---|
| Acknowledgment | 48 hours |
| Triage and initial assessment | 7 days |
| Fix or mitigation | Best-effort; severity-dependent |

This is a solo-maintained project. Critical vulnerabilities are prioritized over feature work.

## Automated scanning

The following scans run automatically on every push and weekly:

| Tool | What it checks | Workflow |
|---|---|---|
| [CodeQL](https://codeql.github.com/) | Static analysis (SAST) for Python source | [security.yml](.github/workflows/security.yml) |
| [Trivy](https://github.com/aquasecurity/trivy) | Docker image CVEs. The build fails on CRITICAL or HIGH findings for which a fix is available; `ignore-unfixed` is on, so a CVE with no fix does not block | [security.yml](.github/workflows/security.yml) |
| [OpenSSF Scorecard](https://securityscorecards.dev/) | Supply-chain / repo hygiene aggregate score | [security.yml](.github/workflows/security.yml) |
| [Dependabot](https://docs.github.com/en/code-security/dependabot) | Dependency updates for pip, GitHub Actions, Docker base image digests and pre-commit hooks | [dependabot.yml](.github/dependabot.yml) |
| Secret scanning | Push protection is active: a push containing a recognized secret is blocked | Repository setting |

Results are visible in the [Security tab](https://github.com/MartinHoblisch/pbf-forge/security) of this repository.
