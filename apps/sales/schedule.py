"""Settings-safe, explicitly opt-in mailbox scheduling."""


def mailbox_sync_beat_schedule(*, enabled=False, interval_seconds=60):
    if str(enabled).strip().lower() not in {'true', '1', 'yes', 'on'}:
        return {}
    try:
        interval = max(60, int(interval_seconds))
    except (TypeError, ValueError):
        interval = 60
    return {'sales-mailbox-sync': {
        'task': 'apps.sales.tasks.dispatch_mailbox_sync', 'schedule': float(interval),
        'options': {'expires': interval},
    }}
