# Ubuntu 24.04 LTS (Noble Numbat). Dependabot bumps the tag when a new LTS ships.
# For fully reproducible builds, replace the tag with a digest:
#   docker pull ubuntu:24.04 && docker inspect ubuntu:24.04 --format='{{index .RepoDigests 0}}'
# Refresh quarterly or when security advisories appear — see CONTRIBUTING.md.
FROM ubuntu:24.04@sha256:c4a8d5503dfb2a3eb8ab5f807da5bc69a85730fb49b5cfca2330194ebcc41c7b

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# Package versions locked by Ubuntu 24.04 LTS APT snapshot (Noble):
#   osmium-tool ~2.19  gdal-bin ~3.8  python3 ~3.12
# To verify installed versions: apt-cache policy <package>
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
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
