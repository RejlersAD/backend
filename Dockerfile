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

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
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
