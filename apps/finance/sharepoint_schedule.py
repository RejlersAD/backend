"""Settings-safe schedule for the explicitly enabled Finance workbook reader."""


def finance_sharepoint_beat_schedule(*, enabled=False, interval_seconds=3600):
    if str(enabled).strip().lower() not in {'true', '1', 'yes', 'on'}:
        return {}
    try:
        interval = max(60, int(interval_seconds))
    except (TypeError, ValueError):
        interval = 3600
    return {'finance-sharepoint-sync': {
        'task': 'finance.sync_receivables_sharepoint',
        'schedule': float(interval),
        'options': {'expires': interval},
    }}
