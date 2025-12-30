# Procfile for multi-service deployment
# Used by Heroku-compatible platforms

web: bash railway_start.sh
worker: celery -A config worker --loglevel=info --concurrency=2
beat: celery -A config beat --loglevel=info
