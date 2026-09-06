# Ubuntu 26.04 LTS (Resolute Raccoon), pinned by digest so builds are
# reproducible. Dependabot refreshes the digest weekly; the tag stays put,
# because moving to the next LTS also moves osmium-tool, GDAL and python3.
FROM ubuntu:26.04@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# Package versions locked by Ubuntu 26.04 LTS APT snapshot (Resolute):
#   osmium-tool ~1.19  gdal-bin ~3.12  python3 ~3.14
# To verify installed versions: apt-cache policy <package>
# The 26.04 base rootfs carries /usr/bin/pebble, Canonical's container service
# manager. It arrives outside dpkg, so apt cannot remove it and no Ubuntu
# security update can reach it. Nothing in this image runs it — the CMD is
# uvicorn — while its vendored Go standard library reports as vulnerable
# whenever upstream Go is ahead of the rebuild, so the file is deleted.
RUN apt-get update && apt-get upgrade -y \
    && rm -f /usr/bin/pebble \
    && apt-get install -y --no-install-recommends \
    osmium-tool \
    gdal-bin \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY backend/requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r /app/requirements.txt

# Mirror repo layout so Path(__file__).parent.parent / "frontend" resolves correctly:
# /app/backend/main.py  →  parent.parent = /app  →  /app/frontend  ✓
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

# DATA_DIR is mounted at runtime via docker-compose volume
ENV DATA_DIR=/data

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
