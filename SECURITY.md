# Security Policy

## Reporting a vulnerability

Open a [private security advisory](https://github.com/martinhoblisch/pbf-forge/security/advisories/new) with a description and reproduction steps. Please do not open a public GitHub Issue for security reports.

## Scope

PBF Forge is designed to run on `127.0.0.1` (localhost) only, by a single trusted user on a personal or workstation machine.

## Explicit non-goals

- **No authentication or access control.** Do not expose PBF Forge to a LAN, VPN, or public internet. There is no login, no API key, and no multi-user isolation.
- **Filter expressions are passed as argv to `osmium tags-filter`.** They are not sandboxed beyond argument-list validation. Do not run PBF Forge on a shared or multi-tenant host.
- **No hardened container.** The Docker image is a convenience wrapper, not a security boundary. Run it on a machine you control.

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
| [Trivy](https://github.com/aquasecurity/trivy) | Docker image CVEs (CRITICAL/HIGH = build fails) | [security.yml](.github/workflows/security.yml) |
| [OpenSSF Scorecard](https://securityscorecards.dev/) | Supply-chain / repo hygiene aggregate score | [security.yml](.github/workflows/security.yml) |
| [Dependabot](https://docs.github.com/en/code-security/dependabot) | Dependency version updates (pip + GitHub Actions) | [dependabot.yml](.github/dependabot.yml) |

Results are visible in the [Security tab](../../security) of this repository.
