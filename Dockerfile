# THROW-AWAY VERIFICATION — DO NOT MERGE. Tests Trivy fail-on-CRITICAL gate.
# Original on main: ubuntu:24.04@sha256:c4a8d5503dfb...
# nginx:1.18.0 (2020 release) has many fixed CRITICAL/HIGH OS-package CVEs that
# survive `ignore-unfixed: true` filter.
FROM nginx:1.18.0
RUN echo "verify trivy fail-on-critical"
