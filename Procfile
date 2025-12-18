web: python manage.py migrate --noinput && python railway_startup.py && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 600 --access-logfile - --error-logfile - --log-level info
worker: celery -A config worker -l info --concurrency=2
beat: celery -A config beat -l info
release: python manage.py migrate --noinput && python railway_startup.py
