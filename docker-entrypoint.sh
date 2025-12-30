#!/bin/bash

# Docker entrypoint script for Django application
# Handles database migrations and starts the application

set -e

echo "🚀 Starting Django application..."

# Wait for database to be ready
echo "⏳ Waiting for PostgreSQL..."
until PGPASSWORD=$DB_PASSWORD psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c '\q' 2>/dev/null; do
  echo "PostgreSQL is unavailable - sleeping"
  sleep 2
done

echo "✅ PostgreSQL is up!"

# Run database migrations
echo "🔄 Running database migrations..."
python manage.py migrate --noinput

# Collect static files
echo "📦 Collecting static files..."
python manage.py collectstatic --noinput --clear

# Create superuser if it doesn't exist (for development)
if [ "$DEBUG" = "True" ]; then
  echo "👤 Creating superuser (if not exists)..."
  python manage.py shell << EOF
from apps.users.models import User
if not User.objects.filter(email='admin@aiflow.com').exists():
    User.objects.create_superuser('admin@aiflow.com', 'admin123', first_name='Admin', last_name='User')
    print('Superuser created!')
else:
    print('Superuser already exists')
EOF
fi

# Start application
echo "✅ Starting application..."
exec "$@"
