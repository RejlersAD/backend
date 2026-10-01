"""Read one Finance SharePoint workbook into immutable receivables snapshots."""
from datetime import timedelta
from math import ceil
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import uuid

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.finance.receivables_source_models import ReceivablesSourceSnapshot, ReceivablesSyncState
from .receivables_source import (
    ReceivablesSourceChanged, import_receivables_source, require_sync_publication,
)


LEASE_SECONDS = 900


class FinanceSharePointSyncError(RuntimeError):
    """Only sanitized, operator-safe failure descriptions leave this adapter."""


def sync_enabled():
    return str(getattr(settings, 'FINANCE_SHAREPOINT_SYNC_ENABLED', False)).strip().lower() in {
        'true', '1', 'yes', 'on',
    }


def sync_interval_seconds():
    """Use the same interval bounds as the existing Celery schedule."""
    from apps.finance.sharepoint_schedule import finance_sharepoint_beat_schedule

    entry = finance_sharepoint_beat_schedule(
        enabled=True,
        interval_seconds=getattr(settings, 'FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS', 3600),
    )['finance-sharepoint-sync']
    return int(entry['schedule'])


def finance_sharepoint_configuration(*, require_item=True):
    # Portfolio is not installed in some existing Finance test/settings bundles.
    # Import only when this adapter is actually used; its HTTP transport is reused.
    from apps.portfolio.sync import SharePointConfiguration

    values = [str(getattr(settings, 'FINANCE_SHAREPOINT_' + name, '')).strip()
              for name in ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 'DRIVE_ID', 'ITEM_ID')]
    if not all(values if require_item else values[:3]):
        raise FinanceSharePointSyncError('Finance SharePoint configuration is incomplete.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', values[0]):
        raise FinanceSharePointSyncError('Finance SharePoint tenant identifier is invalid.')
    return SharePointConfiguration(*values)


def _lease(*, scheduled=False, retry_of=None):
    with transaction.atomic():
        state = ReceivablesSyncState.locked()
        now = timezone.now()
        if state.sync_token and (state.sync_expires_at is None or state.sync_expires_at > now):
            return None
        if scheduled:
            # Check cadence while holding the publication lock. Replicas and
            # restarted processes cannot all begin the same scheduled attempt.
            if retry_of is not None:
                if (retry_of != (state.last_attempt_at, state.generation)
                        or not state.last_error
                        or (state.last_success_at and state.last_attempt_at
                            and state.last_success_at >= state.last_attempt_at)):
                    return {'status': 'superseded'}
            elif state.last_attempt_at:
                remaining = (state.last_attempt_at + timedelta(seconds=sync_interval_seconds()) - now).total_seconds()
                if remaining > 0:
                    return {'status': 'not_due', 'retry_after_seconds': ceil(remaining)}
        state.sync_token = uuid.uuid4()
        state.sync_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        state.last_attempt_at = now
        state.save(update_fields=['sync_token', 'sync_expires_at', 'last_attempt_at'])
        active_id = ReceivablesSourceSnapshot.objects.filter(is_active=True).values_list('pk', flat=True).first()
        return state, active_id


def _checked_state(generation, token):
    state = ReceivablesSyncState.locked()
    require_sync_publication(state, generation, token)
    return state


def _safe_result(result, status):
    return {'status': status, **{key: result[key] for key in ('snapshot_id', 'created', 'activated', 'row_count')}}


def sync_finance_sharepoint(*, dry_run=False, scheduled=False, retry_of=None):
    """Scheduled calls share a durable cadence; manual calls remain immediate."""
    if scheduled and not sync_enabled():
        return {'status': 'disabled'}
    from apps.portfolio.sync import PortfolioSyncError, SharePointWorkbookClient, TransientGraphError

    acquired = _lease(scheduled=scheduled, retry_of=retry_of)
    if acquired is None:
        return {'status': 'busy'}
    if isinstance(acquired, dict):
        return acquired
    source, active_id = acquired
    token, generation = source.sync_token, source.generation
    try:
        configuration = finance_sharepoint_configuration()
        client = SharePointWorkbookClient(configuration)
        cached = bool(not dry_run and active_id and source.remote_snapshot_id == active_id
                      and source.remote_identity == configuration.identity and source.etag)
        before = client.metadata(source.etag if cached else None)
        if before is None:
            if not cached:
                raise FinanceSharePointSyncError('SharePoint returned unchanged status without a matching Finance source.')
            with transaction.atomic():
                current = _checked_state(generation, token)
                if (current.remote_snapshot_id != active_id or current.remote_identity != configuration.identity
                        or current.etag != source.etag
                        or not ReceivablesSourceSnapshot.objects.filter(pk=active_id, is_active=True).exists()):
                    raise ReceivablesSourceChanged('The Finance source changed; retry synchronization.')
                current.last_success_at = timezone.now()
                current.last_error = ''
                current.save(update_fields=['last_success_at', 'last_error'])
            return {'status': 'unchanged', 'snapshot_id': active_id}
        if len(before['eTag']) > 512:
            raise FinanceSharePointSyncError('SharePoint returned unsupported workbook version metadata.')
        content = client.download(before['size'])
        after = client.metadata()
        if after is None or any(before[key] != after[key] for key in ('id', 'eTag', 'size', 'name')):
            raise FinanceSharePointSyncError('SharePoint workbook changed during download; retry synchronization.')
        with TemporaryDirectory(prefix='radai-finance-sharepoint-') as directory:
            path = Path(directory) / 'source.xlsx'
            path.write_bytes(content)
            # The importer checks generation and lease after parsing while
            # holding the same lock every manual publication uses.
            with transaction.atomic():
                try:
                    result = import_receivables_source(
                        path, last_row=None, original_filename=before['name'], dry_run=dry_run,
                        expected_generation=generation, expected_sync_token=token,
                    )
                except ReceivablesSourceChanged:
                    raise
                except Exception:
                    raise FinanceSharePointSyncError(
                        'Finance workbook validation or publication failed; the previous source remains available.',
                    ) from None
                current = _checked_state(generation + int(result['activated']), token)
                if dry_run:
                    return _safe_result(result, 'validated')
                if not ReceivablesSourceSnapshot.objects.filter(pk=result['snapshot_id'], is_active=True).exists():
                    raise FinanceSharePointSyncError('The Finance workbook was not published as the active source.')
                current.remote_identity = configuration.identity
                current.etag = before['eTag']
                current.remote_snapshot_id = result['snapshot_id']
                current.last_success_at = timezone.now()
                current.last_error = ''
                current.save(update_fields=['remote_identity', 'etag', 'remote_snapshot', 'last_success_at', 'last_error'])
        return _safe_result(result, 'synchronized')
    except Exception as exc:
        if isinstance(exc, (FinanceSharePointSyncError, PortfolioSyncError)):
            error = exc
        elif isinstance(exc, ReceivablesSourceChanged):
            error = FinanceSharePointSyncError('The Finance source or synchronization lease changed; retry synchronization.')
        elif isinstance(exc, requests.RequestException):
            error = FinanceSharePointSyncError('Finance SharePoint connection failed; check connectivity and retry synchronization.')
        else:
            error = FinanceSharePointSyncError('Finance synchronization failed; the previous source remains available.')
        ReceivablesSyncState.objects.filter(pk=1, sync_token=token).update(last_error=str(error))
        if scheduled and isinstance(error, TransientGraphError):
            # In-process retry identity only; never serialized or logged. A
            # manual attempt/publication or another due worker invalidates it.
            error.finance_retry_of = (source.last_attempt_at, generation)
        raise error from None
    finally:
        ReceivablesSyncState.objects.filter(pk=1, sync_token=token).update(sync_token=None, sync_expires_at=None)


def resolve_finance_sharepoint_link(share_url=None):
    """Resolve a configured URL using existing grants, without redeeming access."""
    from apps.portfolio.sync import PortfolioSyncError, SharePointWorkbookClient, _download_url_allowed

    url = str(share_url if share_url is not None else getattr(settings, 'FINANCE_SHAREPOINT_URL', '')).strip()
    if not _download_url_allowed(url):
        raise FinanceSharePointSyncError('Configure a valid HTTPS Finance SharePoint URL before resolving identifiers.')
    configuration = finance_sharepoint_configuration(require_item=False)
    try:
        return SharePointWorkbookClient(configuration).resolve_link(url)
    except PortfolioSyncError:
        raise
    except Exception:
        raise FinanceSharePointSyncError('Finance SharePoint link resolution failed; check the configured application access.') from None
