#!/bin/bash
set -e

echo "🔧 Initializing AIFlow Backend..."

# Ensure media directories exist with proper permissions
mkdir -p /app/media/pid_drawings /app/media/pid_reports /app/media/avatars
mkdir -p /app/staticfiles

# Fix permissions for media directory (in case volume is mounted)
if [ -w /app/media ]; then
    chmod -R 775 /app/media 2>/dev/null || true
fi

echo "✅ Media directories initialized"

# Execute the main command
exec "$@"
