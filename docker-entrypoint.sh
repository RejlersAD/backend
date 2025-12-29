#!/bin/bash
set -e

echo "🔧 Initializing RADAI Backend..."

# Ensure media directories exist with proper permissions
mkdir -p /app/media/pid_drawings /app/media/pid_reports /app/media/avatars 2>/dev/null || true
mkdir -p /app/staticfiles 2>/dev/null || true

# Fix permissions for media directory (in case volume is mounted)
chmod -R 775 /app/media 2>/dev/null || true
chmod -R 775 /app/staticfiles 2>/dev/null || true

echo "✅ Media directories initialized"

# Execute the main command
exec "$@"
