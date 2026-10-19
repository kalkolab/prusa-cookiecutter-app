# Production Dockerfile for Prusa Cookie Cutter & Stamp Studio
# Compatible with Google Cloud Run and Local Docker

# Debian 13 (trixie) ships PrusaSlicer 2.9, whose bundled Prusa profiles include the MK4S
FROM python:3.12-slim-trixie

# Avoid interactive prompts during Debian apt installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    JOBS_DIR=/var/tmp/cookiecutter_jobs \
    LIBRARY_DIR=/data/library \
    PRUSASLICER_DATADIR=/opt/prusaslicer-data \
    HOME=/var/tmp/app_home

# Install PrusaSlicer (CLI slicing is headless; no X server needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    prusa-slicer \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create working directory and temp directories
WORKDIR /app
RUN mkdir -p /var/tmp/cookiecutter_jobs /var/tmp/app_home /data/library && \
    chmod -R 777 /var/tmp/cookiecutter_jobs /var/tmp/app_home /data/library

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ ./app/

# Install Prusa's printer/filament profiles into a PrusaSlicer config folder once at build time
RUN python3 -c "from app.slicer import ensure_datadir; ensure_datadir()" && \
    chmod -R 777 /opt/prusaslicer-data

# Expose HTTP port (Cloud Run defaults to 8080)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + str(os.environ.get('PORT', 8080)) + '/api/health')" || exit 1

# Start Uvicorn server
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
