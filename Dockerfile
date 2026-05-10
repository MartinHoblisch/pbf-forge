# THROW-AWAY VERIFICATION — DO NOT MERGE. Tests Trivy fail-on-CRITICAL gate.
# Original on main: ubuntu:24.04@sha256:c4a8d5503dfb...
# Bare image so build succeeds; ubuntu:18.04 has many fixed CRITICAL CVEs.
FROM ubuntu:18.04
RUN echo "verify trivy fail-on-critical"
