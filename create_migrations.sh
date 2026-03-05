#!/bin/bash
# Script to create migrations for electrical_datasheet app
# This script handles the interactive prompts automatically

echo "Creating migrations for electrical_datasheet app..."

# Use echo to provide input '2' (Ignore for now) to the makemigrations command
echo "2" | docker-compose exec -T backend_local python manage.py makemigrations process_datasheet

# Now create the electrical_datasheet migrations
docker-compose exec -T backend_local python manage.py makemigrations electrical_datasheet

# Apply all migrations
docker-compose exec -T backend_local python manage.py migrate

echo "Migrations completed!"
