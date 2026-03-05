#!/bin/bash
# Setup script for Excel Quality Checker in Docker container

echo "🚀 Setting up Excel Quality Checker..."

# Run migrations
echo "📦 Creating database tables..."
python manage.py makemigrations electrical_datasheet
python manage.py migrate electrical_datasheet

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput

# Create media directories
echo "📂 Creating media directories..."
mkdir -p media/electrical_datasheets

# Set permissions
echo "🔐 Setting permissions..."
chmod -R 755 media/electrical_datasheets

echo "✅ Excel Quality Checker setup complete!"
echo ""
echo "📍 Access the quality checker at:"
echo "   http://localhost:5173/engineering/electrical/datasheet/quality-checker"
echo ""
echo "📡 API endpoints available at:"
echo "   http://localhost:8000/api/electrical-datasheet/excel/excel-documents/"
