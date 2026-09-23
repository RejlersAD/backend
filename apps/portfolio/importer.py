"""Atomic source publication shared by manual imports and the SharePoint job."""
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from .workbook import read_workbook

EXPECTED_SNAPSHOT_UNSET = object()


class PortfolioSnapshotChanged(ValueError):
    """The shared source changed after the user reviewed the upload preview."""


def import_workbook(path_or_bytes, *, source_key='poc', original_filename=None,
                    dry_run=False, expected_sync_token=None, expected_active_snapshot=EXPECTED_SNAPSHOT_UNSET):
    parsed = read_workbook(path_or_bytes)
    filename = Path(str(original_filename or parsed['file_name']).replace('\\', '/')).name
    if not filename or len(filename) > 255:
        raise ValueError('Workbook filename is empty or too long.')
    source_key = str(source_key).strip()
    if not source_key or len(source_key) > 80:
        raise ValueError('Portfolio source key is empty or too long.')
    metadata = {key: parsed[key] for key in ('sha256', 'parser_version', 'reporting_date',
                                            'row_count', 'warnings', 'reconciliation')}
    metadata['file_name'] = filename
    result = {**metadata, 'reporting_date': parsed['reporting_date'].isoformat(), 'source_key': source_key,
              'dry_run': dry_run, 'snapshot_id': None, 'created': False, 'activated': False}
    if dry_run:
        return result
    with transaction.atomic():
        source, _ = PortfolioSource.objects.get_or_create(key=source_key)
        source = PortfolioSource.objects.select_for_update().get(pk=source.pk)
        if expected_active_snapshot is not EXPECTED_SNAPSHOT_UNSET and source.active_snapshot_id != expected_active_snapshot:
            raise PortfolioSnapshotChanged('The portfolio changed after this preview. Preview the workbook again before importing.')
        now = timezone.now()
        live_lease = source.sync_token is not None and source.sync_expires_at is not None and source.sync_expires_at > now
        if expected_sync_token is not None:
            if not live_lease or str(source.sync_token) != str(expected_sync_token):
                raise ValueError('Portfolio sync lease expired or was replaced; publication refused.')
        elif live_lease:
            raise ValueError('Portfolio synchronization is in progress; retry this manual import after it completes.')
        if source.active_snapshot_id and source.active_snapshot.reporting_date > parsed['reporting_date']:
            raise ValueError('Workbook reporting date is older than the active portfolio snapshot.')
        snapshot = PortfolioSnapshot.objects.filter(source=source, sha256=parsed['sha256'],
                                                     parser_version=parsed['parser_version']).first()
        created = snapshot is None
        if created:
            snapshot = PortfolioSnapshot.objects.create(source=source, **metadata)
            PortfolioRow.objects.bulk_create([PortfolioRow(snapshot=snapshot, **row) for row in parsed['rows']], batch_size=250)
        elif snapshot.row_count != parsed['row_count'] or snapshot.rows.count() != parsed['row_count']:
            raise ValueError('Existing portfolio source version has an inconsistent row count.')
        activated = source.active_snapshot_id != snapshot.pk
        source.active_snapshot = snapshot
        source.last_success_at = now
        source.last_error = ''
        fields = ['active_snapshot', 'last_success_at', 'last_error']
        if expected_sync_token is None:
            source.etag = source.remote_identity = ''
            source.sync_token = source.sync_expires_at = None
            source.last_attempt_at = now
            fields += ['etag', 'remote_identity', 'sync_token', 'sync_expires_at', 'last_attempt_at']
        source.save(update_fields=fields)
        result.update(snapshot_id=snapshot.pk, created=created, activated=activated, file_name=snapshot.file_name)
    return result
