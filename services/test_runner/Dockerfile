FROM python:3.12-slim AS base

# Install git + openssh-client (needed for mirror operations via SSH deploy keys)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 runner && \
    useradd -u 1000 -g 1000 -m -s /bin/bash runner

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY test_runner/ /app/test_runner/
COPY migrations/ /app/migrations/
COPY orb_wrappers/ /app/orb_wrappers/

# Ensure directories exist
RUN mkdir -p /artifacts /repos /work && \
    chown -R 1000:1000 /artifacts /repos /work /app

USER 1000:1000

# Default: run API
CMD ["uvicorn", "test_runner.api:app", "--host", "0.0.0.0", "--port", "8000"]
