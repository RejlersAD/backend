"""Durable tender ZIP import: extract archive entries into private uploads and queue classification."""
import hashlib
import os
import zipfile
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from django.core.files import File
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from .document_classification import queue_classification
from .document_versions import materialize_document
from .models import OpportunityWorkspace, OpportunityWorkspaceUpload, OpportunityZipImport
from .opportunity_workspace import _actor, require_access, WorkspaceAPIError
from .private_attachments import _name, _read, _storage

# Longer than the task's hard time limit: a second worker must never start while one is extracting
# (a short lease let concurrent runs collide on already-stored entries and fail large tender imports).
LEASE_SECONDS = 1800
MAX_ZIP_ENTRIES = 5000
MAX_ENTRY_BYTES = 32 * 1024 * 1024


def max_total_bytes():
    """Total extracted size; SALES_ZIP_MAX_TOTAL_BYTES overrides the 512 MiB default (tender packages can exceed it)."""
    from django.conf import settings
    return int(getattr(settings, 'SALES_ZIP_MAX_TOTAL_BYTES', 0) or MAX_TOTAL_BYTES)


def max_entry_bytes():
    """Per-entry limit; SALES_ZIP_MAX_ENTRY_BYTES overrides the 32 MiB default (bid decks can exceed it)."""
    from django.conf import settings
    return int(getattr(settings, 'SALES_ZIP_MAX_ENTRY_BYTES', 0) or MAX_ENTRY_BYTES)
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_NESTING_DEPTH = 5
MAX_COMPRESSION_RATIO = 200


def due_zip_import_ids(limit=50):
    now = timezone.now()
    return list(OpportunityZipImport.objects.filter(
        Q(status='queued') | Q(status='running'),
        Q(lease_until__isnull=True) | Q(lease_until__lt=now),
    ).order_by('updated_at', 'pk').values_list('pk', flat=True)[:limit])


def _safe_path(name):
    parts = name.replace('\\', '/').split('/')
    return not (name.startswith('/') or os.path.isabs(name) or '..' in parts)


def _store_entry(storage, fingerprint, workspace, actor, job, entry_name, source_path, data, stats):
    size = len(data)
    digest = hashlib.sha256(data).hexdigest()
    upload = OpportunityWorkspaceUpload.objects.create(
        workspace=workspace, request_id=uuid4(), actor=actor,
        folder_key=job.folder_key, name=entry_name, size=size, sha256=digest,
        provider='radai', storage_encoding='identity', stored_size=size,
        stored_sha256=digest, storage_fingerprint=fingerprint,
        normalized_name=hashlib.sha256(entry_name.casefold().encode('utf-8')).hexdigest(),
        mime_type='', status='ready', version_number=1,
        zip_import=job, source_path=source_path,
    )
    upload.storage_name = _name(upload)
    upload.save(update_fields=['storage_name'])
    storage.save(upload.storage_name, File(BytesIO(data)))
    materialize_document(upload)
    queue_classification(upload, actor)
    stats['processed'] += 1


def _archive_error(job, stats, code, path):
    stats['skipped'] += 1
    job.errors.append({'code': code, 'path': path})


def _extract(archive, job, workspace, actor, prefix, stats, storage, fingerprint, depth=0):
    if depth > MAX_NESTING_DEPTH:
        _archive_error(job, stats, 'nested_archive_limit', prefix)
        return
    infos = archive.infolist()
    stats['seen'] += len(infos)
    if stats['seen'] > MAX_ZIP_ENTRIES:
        _archive_error(job, stats, 'archive_entry_limit', prefix or archive.filename)
        return
    for info in infos:
        if info.is_dir():
            continue
        name = info.filename.replace('\\', '/')
        full = (prefix + '/' + name) if prefix else name
        if not _safe_path(full):
            _archive_error(job, stats, 'unsafe_path', full)
            continue
        if info.flag_bits & 0x1:
            _archive_error(job, stats, 'encrypted_entry', full)
            continue
        mode = info.external_attr >> 16
        if mode and mode & 0o170000 == 0o120000:
            _archive_error(job, stats, 'symbolic_link_entry', full)
            continue
        if info.file_size < 0 or info.file_size > max_entry_bytes():
            _archive_error(job, stats, 'entry_size_limit', full)
            continue
        if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
            _archive_error(job, stats, 'compression_ratio_limit', full)
            continue
        if stats['bytes_read'] + info.file_size > max_total_bytes():
            _archive_error(job, stats, 'archive_size_limit', full)
            continue
        try:
            data = archive.read(info)
        except (zipfile.BadZipFile, RuntimeError, OSError):
            _archive_error(job, stats, 'entry_read_failed', full)
            continue
        stats['bytes_read'] += len(data)
        if name.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(BytesIO(data)) as nested:
                    _extract(
                        nested, job, workspace, actor, full, stats, storage, fingerprint,
                        depth + 1,
                    )
            except zipfile.BadZipFile:
                _archive_error(job, stats, 'invalid_nested_archive', full)
            continue
        _store_entry(storage, fingerprint, workspace, actor, job, name, full, data, stats)


def run_zip_import(zip_import_id):
    token, now = uuid4(), timezone.now()
    with transaction.atomic():
        job = OpportunityZipImport.objects.select_for_update().filter(pk=zip_import_id).first()
        if job is None or job.status == 'completed':
            return {'processed': False}
        if job.lease_until and job.lease_until > now:
            return {'processed': False}
        job.lease_token = token
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        job.status = 'running'
        job.errors = []
        job.save()
    try:
        actor = _actor(job.actor_id)
        opportunity = job.opportunity
        require_access(actor, opportunity, 'export')
        workspace, _ = OpportunityWorkspace.objects.get_or_create(opportunity=opportunity)
    except PermissionDenied:
        job.status = 'failed'
        job.error_code = 'source_or_access_unavailable'
        job.lease_token = job.lease_until = None
        job.save()
        return {'processed': True, 'status': 'failed'}
    stats = {'processed': 0, 'skipped': 0, 'failed': 0, 'seen': 0, 'bytes_read': 0}
    try:
        storage, fingerprint = _storage()
        source = job.source_upload
        if source is None:
            raise WorkspaceAPIError('source_or_access_unavailable')
        output = _read(source, storage)
        try:
            with zipfile.ZipFile(output) as archive:
                _extract(archive, job, workspace, actor, '', stats, storage, fingerprint)
        finally:
            output.close()
    except Exception:
        job.status = 'failed'
        job.error_code = 'source_or_access_unavailable'
        job.lease_token = job.lease_until = None
        job.save()
        return {'processed': True, 'status': 'failed'}
    processed = stats['processed']
    skipped = stats['skipped']
    failed = stats['failed']
    if failed:
        status, error_code = 'failed', 'zip_extraction_failed'
    elif processed and not skipped:
        status, error_code = 'completed', ''
    elif processed:
        status, error_code = 'partial', ''
    else:
        status, error_code = 'failed', 'zip_extraction_failed'
    job.status = status
    job.error_code = error_code
    job.processed = processed
    job.skipped = skipped
    job.failed = failed
    job.lease_token = job.lease_until = None
    job.save()
    return {'processed': bool(processed), 'status': status}
