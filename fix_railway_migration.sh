#!/bin/bash
# Fix Railway migration conflict
# Run this script on Railway to fake the problematic migration

echo "🔧 Faking pid_analysis migration 0002..."
python manage.py migrate pid_analysis 0002 --fake

echo "✅ Migration faked!"
echo "🚀 Running all migrations..."
python manage.py migrate

echo "✅ All migrations applied!"
