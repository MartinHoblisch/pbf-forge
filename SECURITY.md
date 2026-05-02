# Security Policy

## Reporting a vulnerability

Email **m.hoblisch@gmail.com** with a description and reproduction steps. Please do not open a public GitHub Issue for security reports.

## Scope

PBF Forge is designed to run on `127.0.0.1` (localhost) only, by a single trusted user on a personal or workstation machine.

## Explicit non-goals

- **No authentication or access control.** Do not expose PBF Forge to a LAN, VPN, or public internet. There is no login, no API key, and no multi-user isolation.
- **Filter expressions are passed as argv to `osmium tags-filter`.** They are not sandboxed beyond argument-list validation. Do not run PBF Forge on a shared or multi-tenant host.
- **No hardened container.** The Docker image is a convenience wrapper, not a security boundary. Run it on a machine you control.
