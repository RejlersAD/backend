"""
Celery configuration for RADAI project.
Smart task queue configuration for background processing.
"""
import os
from celery import Celery

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('radai')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# settings.CELERY_BEAT_SCHEDULE (built in config/settings.py by merging each
# app's own BEAT_SCHEDULE dict) is picked up here via the CELERY_ namespace —
# periodic tasks are NOT merged in this file. This module is imported from
# config/__init__.py the moment Django's settings module is first touched,
# often before django.setup() has populated the app registry; importing
# apps.<name>.config here (as this file used to) intermittently raised
# AppRegistryNotReady and/or landed in a not-yet-finalized `app.conf` view
# whose beat_schedule assignment doesn't reliably stick before finalize.
# Doing the merge in settings.py — which always finishes executing before
# any app-registry-dependent code runs, and which these config.py modules
# don't depend on anyway (they only build crontab() schedules) — avoids
# both problems.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery configuration."""
    print(f'Request: {self.request!r}')
