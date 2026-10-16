# Production Dockerfile for Prusa Cookie Cutter & Stamp Studio
# Compatible with Google Cloud Run and Local Docker

FROM python:3.11-slim-bookworm

# Avoid interactive prompts during Debian apt installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    JOBS_DIR=/var/tmp/cookiecutter_jobs \
    HOME=/var/tmp/app_home

# Install PrusaSlicer and graphics/mesh rendering runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    prusa-slicer \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libglu1-mesa \
    xvfb \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create working directory and temp directories
WORKDIR /app
RUN mkdir -p /var/tmp/cookiecutter_jobs /var/tmp/app_home && \
    chmod -R 777 /var/tmp/cookiecutter_jobs /var/tmp/app_home

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY slicer_config.ini .
COPY app/ ./app/

# Expose HTTP port (Cloud Run defaults to 8080)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:' + str(os.environ.get('PORT', 8080)) + '/api/health')" || exit 1

# Start Uvicorn server
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
