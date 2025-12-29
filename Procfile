web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 600 --access-logfile - --error-logfile - --log-level debug --preload
worker: celery -A config worker -l info --concurrency=2
beat: celery -A config beat -l info
