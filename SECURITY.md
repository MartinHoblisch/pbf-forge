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

## What leaves the machine

Downloads go to whichever host the pasted URL names, and to nothing else. Beyond that the tool makes one request the user did not ask for: once a day it reads `https://api.github.com/repos/MartinHoblisch/pbf-forge/releases/latest` to learn whether a newer release exists. It sends what any HTTP request sends — an IP address and the `pbf-forge/<version>` user agent — and no identifier of its own. The answer is cached in `config/update-check.json`, nothing is downloaded or installed as a result, and the check can be switched off in the update dialog, which stops the request entirely.

## Published images

Release images are built from the tagged commit by
[.github/workflows/release.yml](.github/workflows/release.yml) and pushed to
`ghcr.io/martinhoblisch/pbf-forge`. The workflow refuses to publish when the
tag, `VERSION` in `backend/config.py` and the image tag in `docker-compose.yml`
disagree, and it starts the built image and checks that it serves the interface
and reports the version being released before pushing anything, so a published
image always matches the source that carries the same version and is known to
run. Each image is built with a BuildKit SBOM and signed build provenance:

```bash
gh attestation verify oci://ghcr.io/martinhoblisch/pbf-forge:1.1.0 --repo MartinHoblisch/pbf-forge
```

Only `linux/amd64` is published. On any other platform the launchers build the
image locally from the same source.

## Known gaps

Stated rather than implied, because a security policy that only lists what is covered is misleading about what is not.

- **No required review on `main`.** Branch protection requires the `lint`, `test`, `test-windows` and `CodeQL` checks to pass and forbids branch deletion, but it does not require an approving review. A single maintainer can merge unreviewed.
- **Force push to `main` is permitted**, and administrators are not subject to the protection rules. History on `main` can be rewritten.
- **System packages are unmanaged.** `osmium-tool` and `gdal-bin` are installed with apt, and no Dependabot ecosystem covers apt. Their versions are capped by the Ubuntu 26.04 archive and refreshed by `apt-get upgrade` on each image build. Dependabot tracks the pinned base image digest, which pulls in patched layers, but cannot advance a package past what the archive holds.
- **Universe packages carry no vendor security guarantee.** `osmium-tool` and `gdal-bin` live in Ubuntu universe, which has no guaranteed Canonical security maintenance in an LTS. Trivy reads Ubuntu OVAL and USN data, so a universe vulnerability without a USN can go unreported: a green scan is weaker evidence here than it would be for a package from `main`. Base image migration is tracked in issue [#44](https://github.com/MartinHoblisch/pbf-forge/issues/44).
- **No runtime security testing (DAST).** Release images carry an SBOM and build provenance as attestations, and GitHub's dependency graph is populated, but no artifact is attached to the release itself.
- **The download URL check does not resolve names or follow-up redirects.** `POST /api/downloads` rejects four internal hostnames and any literal loopback, private, link-local or reserved IP address. A hostname that resolves to such an address is accepted, and redirects are followed without re-checking the target, so the endpoint can be pointed at a service on the host. This is bounded by the non-goals above: the tool is auth-less and bound to loopback, so anyone who can call the endpoint can already reach those services directly.
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
| [OpenSSF Scorecard](https://securityscorecards.dev/) | Supply-chain / repo hygiene aggregate score, published to [scorecard.dev](https://scorecard.dev/viewer/?uri=github.com/MartinHoblisch/pbf-forge) | [security.yml](.github/workflows/security.yml) |
| [Dependabot](https://docs.github.com/en/code-security/dependabot) | Dependency updates for pip, GitHub Actions, Docker base image digests and pre-commit hooks | [dependabot.yml](.github/dependabot.yml) |
| Secret scanning | Push protection is active: a push containing a recognized secret is blocked | Repository setting |

CodeQL, Trivy and Dependabot results are visible in the [Security tab](https://github.com/MartinHoblisch/pbf-forge/security) of this repository. The Scorecard result is published as a score rather than as individual alerts; the badge at the top of the README links to the full report.
