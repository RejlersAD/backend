FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    libpq-dev \
    gcc \
    python3-dev \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    libsm6 \
    libxext6 \
    libxrender-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Layer A: Heavy ML/OCR packages (easyocr → PyTorch ~2GB, paddlepaddle ~700MB)
# This layer is pinned by version — its Docker content-hash stays identical
# between deployments as long as these versions don't change.
# OCI registries skip re-uploading identical layers → 2.7 GB NOT re-pushed
# on every deploy once this layer is in Railway's registry.
COPY requirements-prod-base.txt .
RUN pip install --no-cache-dir -r requirements-prod-base.txt

# ── Layer B: Application packages (lighter, may change with features)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Layer C: Application code (changes every deploy — tiny)
COPY . .

# Create necessary directories
RUN mkdir -p /app/media /app/staticfiles /app/media/invoices

# Collect static files
RUN python manage.py collectstatic --noinput || true

# Make scripts executable (graceful - files are already executable in Git)
RUN chmod +x railway_start.sh start.sh || true

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings

# Expose port
EXPOSE 8000

# Use JSON array format for CMD to prevent signal issues
CMD ["bash", "railway_start.sh"]
