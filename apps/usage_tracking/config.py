"""Log retention settings for usage_log and system_activity (soft-coded, env-overridable).

Old rows are first rolled into daily summary tables (UsageDailySummary,
ActivityDailySummary) and only then deleted, one calendar day per transaction,
so reports over any past month or quarter keep showing the same numbers.

Nothing is deleted unless LOG_RETENTION_ENABLED is true.

This module must not import models: settings.py merges BEAT_SCHEDULE at import time.
"""
from celery.schedules import crontab
from decouple import config

# Master switch. Off by default: switch on once the database has its indexes.
LOG_RETENTION_ENABLED = config('LOG_RETENTION_ENABLED', default=False, cast=bool)

# usage_log keeps this many days of per-request detail. The longest dashboard
# range is 90 days (views.RANGE_DAYS), so the default leaves a 10-day margin.
USAGE_LOG_RETENTION_DAYS = config('USAGE_LOG_RETENTION_DAYS', default=100, cast=int)

# system_activity rows whose type is in this list are high-volume per-request
# telemetry (one row per API call, duplicating usage_log). Kept for fewer days.
ACTIVITY_NOISE_TYPES = tuple(t.strip() for t in config('ACTIVITY_NOISE_TYPES', default='api_request').split(',') if t.strip())
ACTIVITY_NOISE_RETENTION_DAYS = config('ACTIVITY_NOISE_RETENTION_DAYS', default=30, cast=int)

# Every other system_activity row (logins, uploads, AI calls, ...) is a real user
# action. 0 = keep forever.
ACTIVITY_RETENTION_DAYS = config('ACTIVITY_RETENTION_DAYS', default=0, cast=int)

# Safety limit for one run: at most this many calendar days per table (each day
# is one transaction), so the first run on a large backlog is spread over nights.
LOG_RETENTION_MAX_DAYS_PER_RUN = config('LOG_RETENTION_MAX_DAYS_PER_RUN', default=31, cast=int)

# Runs nightly; the task itself does nothing while LOG_RETENTION_ENABLED is false.
BEAT_SCHEDULE = {
    'usage-tracking-log-retention': {
        'task': 'usage_tracking.apply_log_retention',
        'schedule': crontab(
            hour=config('LOG_RETENTION_HOUR', default=1, cast=int),
            minute=config('LOG_RETENTION_MINUTE', default=30, cast=int)),
        'options': {'expires': 6 * 60 * 60},
    },
}
