"""Settings-safe Celery beat entries; disabled unless explicitly configured."""
import os


def portfolio_beat_schedule(*, enabled=None, interval_seconds=None):
    if enabled is None:
        enabled = os.environ.get('PORTFOLIO_SYNC_ENABLED', 'false')
    if str(enabled).strip().lower() not in {'true', '1', 'yes', 'on'}:
        return {}
    if interval_seconds is None:
        interval_seconds = os.environ.get('PORTFOLIO_SYNC_INTERVAL_SECONDS', '3600')
    try:
        interval = max(60, int(interval_seconds))
    except (ValueError, TypeError):
        interval = 3600
    return {'portfolio-sharepoint-sync': {
        'task': 'apps.portfolio.tasks.sync_portfolio_sharepoint', 'schedule': float(interval),
        'options': {'expires': interval},
    }}


BEAT_SCHEDULE = portfolio_beat_schedule()
