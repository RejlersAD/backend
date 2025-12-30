#!/bin/bash
# Post-deployment tasks - runs AFTER health check passes
# This script should be triggered manually or via Railway webhook

echo "🔧 Running post-deployment tasks..."

# Wait for app to be fully ready
sleep 10

# Run migrations safely
echo "🔄 Running migrations..."
python manage.py migrate --noinput 2>&1

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput 2>&1

echo "✅ Post-deployment complete!"
