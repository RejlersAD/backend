# ========================================================================
# Production-Grade Multi-Stage Dockerfile
# ========================================================================
# Purpose: Build once, deploy anywhere (local, staging, production)
# Security: No secrets baked in, non-root user, minimal attack surface
# Performance: Multi-stage build, optimized layers, minimal image size
# ========================================================================

# ========================================================================
# STAGE 1: Builder - Install dependencies
# ========================================================================
FROM python:3.11-slim as builder

# Metadata
LABEL maintainer="AIFlow DevOps Team"
LABEL version="1.0"
LABEL description="AIFlow Backend - Production Ready"

# Environment variables for build
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# ========================================================================
# STAGE 2: Runtime - Minimal production image
# ========================================================================
FROM python:3.11-slim

# Environment variables for runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user for security
RUN groupadd -r django && \
    useradd -r -g django -d /app -s /sbin/nologin django && \
    mkdir -p /app && \
    chown -R django:django /app

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=django:django . /app/

# Create necessary directories
RUN mkdir -p /app/staticfiles /app/media && \
    chown -R django:django /app/staticfiles /app/media

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/v1/health/ || exit 1

# Switch to non-root user
USER django

# Expose port (Railway will override with $PORT)
EXPOSE 8000

# Default command (can be overridden)
CMD ["sh", "-c", "python manage.py migrate --noinput && (python manage.py collectstatic --noinput || echo 'Skipping collectstatic due to error') && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3 --timeout 300 --access-logfile - --error-logfile - --log-level info"]
