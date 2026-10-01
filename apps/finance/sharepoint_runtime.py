"""Optional Finance-only Graph reader in the existing Railway backend service.

The SQL checkpoint owns the cadence and the ETL owns validation/publication.
This process starts no Celery scheduler and executes no other background jobs.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import logging
import os
import signal
import sys
from threading import Event


MAX_RETRIES = 3
POLL_SECONDS = 60
STATUSES = frozenset({
    'starting', 'disabled', 'synchronized', 'unchanged', 'not_due', 'busy',
    'superseded', 'retrying', 'failed', 'stopping', 'startup_failed',
})
COUNT_FIELDS = frozenset({
    'snapshot_id', 'row_count', 'retry_count', 'retry_after_seconds',
    'interval_seconds', 'http_status',
})


def emit(output, status, **details):
    """Never include exception text, source values, URLs or credentials."""
    record = {
        'event': 'finance_sharepoint_sync',
        'status': status if status in STATUSES else 'failed',
        'at': datetime.now(timezone.utc).isoformat(),
    }
    record.update({key: value for key, value in details.items()
                   if key in COUNT_FIELDS and type(value) is int and value >= 0})
    print(json.dumps(record, sort_keys=True), file=output, flush=True)


def sync_cycle(stop, output):
    """Retry only the same failed transient attempt, at most three times."""
    from django.db import connections
    from apps.finance.services.receivables_sharepoint import sync_finance_sharepoint
    from apps.portfolio.sync import MAX_RETRY_SECONDS, TransientGraphError

    retry_of = None
    for attempt in range(MAX_RETRIES + 1):
        if stop.is_set():
            return {'status': 'stopping'}
        delay = None
        try:
            options = {'scheduled': True}
            if retry_of is not None:
                options['retry_of'] = retry_of
            return sync_finance_sharepoint(**options)
        except TransientGraphError as exc:
            retry_of = getattr(exc, 'finance_retry_of', None)
            if attempt == MAX_RETRIES or retry_of is None:
                return {'status': 'failed', 'http_status': exc.status_code}
            delay = min(MAX_RETRY_SECONDS, max(1, exc.retry_after or min(300, 60 * 2 ** attempt)))
            emit(output, 'retrying', retry_count=attempt + 1,
                 retry_after_seconds=delay, http_status=exc.status_code)
        except Exception:
            # The ETL stores its sanitized last_error. Driver errors and other
            # unexpected exceptions must not expose configuration to stdout.
            return {'status': 'failed'}
        finally:
            connections.close_all()
        if stop.wait(delay):
            return {'status': 'stopping'}
    return {'status': 'failed'}


def run(stop, output):
    from apps.finance.services.receivables_sharepoint import sync_enabled, sync_interval_seconds

    if not sync_enabled():
        emit(output, 'disabled')
        return 0
    interval = sync_interval_seconds()
    emit(output, 'starting', interval_seconds=interval)
    while not stop.is_set():
        result = sync_cycle(stop, output)
        status = result.get('status', 'failed')
        if status == 'stopping':
            break
        emit(output, **result)
        if status == 'disabled':
            return 0
        # One quick checkpoint after work accounts for its elapsed time. A
        # not-due result then sleeps until the database-backed due time.
        delay = (result['retry_after_seconds'] if status == 'not_due'
                 else min(POLL_SECONDS, interval))
        if stop.wait(delay):
            break
    emit(output, 'stopping')
    return 0


def main():
    output = sys.stdout
    # The supervisor and this entry point use the registered service variable;
    # a disabled direct invocation must not initialize Django or its database.
    if os.environ.get('FINANCE_SHAREPOINT_SYNC_ENABLED', '').strip().lower() not in {'true', '1', 'yes', 'on'}:
        emit(output, 'disabled')
        return 0
    stop = Event()
    previous_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, lambda _signum, _frame: stop.set())
    previous_logging_level = logging.root.manager.disable
    try:
        # Existing settings/app imports print operational configuration. Keep
        # both startup and ETL output private; emit only our allowlisted JSON
        # through the original output stream for the entire process lifetime.
        with open(os.devnull, 'w', encoding='utf-8') as sink, redirect_stdout(sink), redirect_stderr(sink):
            logging.disable(logging.CRITICAL)
            try:
                os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
                # This child only imports Finance data; the web process owns
                # optional startup maintenance for S3 CORS and module grants.
                os.environ['SPEC_SKIP_CORS_ON_READY'] = 'true'
                os.environ['RBAC_AUTO_SYNC_MODULES'] = '0'
                import django

                django.setup()
                return run(stop, output)
            except Exception:
                emit(output, 'startup_failed')
                return 1
    finally:
        logging.disable(previous_logging_level)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
