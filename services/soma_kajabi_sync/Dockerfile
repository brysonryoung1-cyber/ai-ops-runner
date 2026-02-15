# Dockerfile for Soma Kajabi Sync service
# Designed for idempotent runs in a read-only container.

FROM python:3.12-slim AS base

# Install minimal system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 runner && \
    useradd -u 1000 -g 1000 -m -s /bin/bash runner

WORKDIR /app

# Install Python deps (currently stdlib-only)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

# Copy application code
COPY . /app/soma_kajabi_sync/

# Ensure artifact directory exists
RUN mkdir -p /artifacts/soma && \
    chown -R 1000:1000 /artifacts /app

USER 1000:1000

ENV SOMA_ARTIFACTS_ROOT=/artifacts/soma

# Default: run smoke test
CMD ["python", "-m", "soma_kajabi_sync.snapshot", "--smoke"]
