# Ubuntu 24.04 LTS (Noble Numbat), pinned by digest so builds are reproducible.
# Dependabot refreshes the digest weekly; the tag stays put, because moving to
# the next LTS also moves osmium-tool, GDAL and python3 (tracked in issue #44).
FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# Package versions locked by Ubuntu 24.04 LTS APT snapshot (Noble):
#   osmium-tool ~1.16  gdal-bin ~3.8  python3 ~3.12
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
