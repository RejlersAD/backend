"""Private RADAI attachments reuse the opportunity upload recovery ledger."""
import hashlib
import re
from tempfile import SpooledTemporaryFile
from time import monotonic
import unicodedata
from uuid import UUID

from django.core import signing
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .attachment_storage import attachment_storage
from .models import Deal, OpportunityWorkspace, OpportunityWorkspaceUpload
from .opportunity_workspace import FOLDERS, WorkspaceAPIError, _actor, require_access, upload_limit, workspace_allowed
from .workflow import _audit
from .workspace_graph import valid_name


def _folder(key):
    if key not in dict((key, name) for key, name, _ in FOLDERS):
        raise ValidationError({'folder_key': 'Choose one of the six opportunity folders.'})


def _storage():
    try:
        return attachment_storage()
    except Exception:
        raise WorkspaceAPIError('private_storage_unavailable') from None


def private_storage_projection(opportunity, actor):
    require_access(actor, opportunity)
    available = True
    try:
        _storage()
    except WorkspaceAPIError:
        available = False
    counts = dict(OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=opportunity, provider='radai', status='ready',
    ).values('folder_key').annotate(total=Count('pk')).values_list('folder_key', 'total'))
    return {'status': 'ready' if available else 'unavailable',
            'message': 'RADAI attachments are stored privately.' if available else 'Private attachment storage is unavailable. Contact your administrator.',
            'can_upload': available and workspace_allowed(actor, 'create', 'update'),
            'folders': [{'key': key, 'item_count': counts.get(key, 0)} for key, _, _ in FOLDERS],
            'max_upload_bytes': upload_limit()}


def _name(attempt):
    return f'sales-opportunity-attachments/{attempt.workspace.opportunity_id}/{attempt.pk}/original'


def _identity(attempt):
    storage, fingerprint = _storage()
    if fingerprint != attempt.storage_fingerprint or attempt.storage_name != _name(attempt):
        raise WorkspaceAPIError('private_storage_changed', 409)
    return storage


def _project(attempt):
    actor = attempt.actor
    actor_name = (actor.get_full_name() or actor.username) if actor else None
    return {'id': 'radai-' + str(attempt.pk), 'name': attempt.name, 'size': attempt.size,
            'mime_type': attempt.mime_type or None, 'created_at': attempt.created_at.isoformat(),
            'modified_at': attempt.updated_at.isoformat(), 'created_by': actor_name, 'modified_by': actor_name,
            'version': '1', 'publication_level': None, 'is_folder': False, 'web_url': None,
            'storage_provider': 'radai', 'folder_key': attempt.folder_key}


def _read(attempt, storage):
    output = SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b')
    try:
        digest, length, started = hashlib.sha256(), 0, monotonic()
        if attempt.size > 10 * 1024 * 1024:
            raise WorkspaceAPIError('attachment_integrity_failed')
        with storage.open(attempt.storage_name, 'rb') as source:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                length += len(chunk)
                if length > attempt.size or monotonic() - started > 60:
                    raise WorkspaceAPIError('attachment_integrity_failed')
                digest.update(chunk)
                output.write(chunk)
        if length != attempt.size or digest.hexdigest() != attempt.sha256:
            raise WorkspaceAPIError('attachment_integrity_failed')
        output.seek(0)
        return output
    except Exception as exc:
        output.close()
        if isinstance(exc, WorkspaceAPIError):
            raise
        raise WorkspaceAPIError('attachment_missing' if isinstance(exc, FileNotFoundError) else 'private_storage_unavailable',
                                404 if isinstance(exc, FileNotFoundError) else 424) from None


def list_private_files(opportunity, actor, key, cursor=None):
    require_access(actor, opportunity)
    _folder(key)
    _, fingerprint = _storage()
    after = None
    if cursor:
        try:
            payload = signing.loads(cursor, salt='sales-private-attachments', max_age=900)
            if (payload['opportunity'], payload['folder'], payload['provider'], payload['storage']) != (
                    str(opportunity.pk), key, 'radai', fingerprint):
                raise ValueError()
            after = UUID(payload['after'])
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise WorkspaceAPIError('invalid_cursor', 400) from None
    query = OpportunityWorkspaceUpload.objects.filter(workspace__opportunity=opportunity,
                                                     provider='radai', folder_key=key, status='ready')
    count = query.count()
    rows = list((query.filter(pk__gt=after) if after else query).select_related('actor').order_by('pk')[:101])
    next_cursor = signing.dumps({'opportunity': str(opportunity.pk), 'folder': key, 'provider': 'radai',
                                'storage': fingerprint, 'after': str(rows[99].pk)}, salt='sales-private-attachments') if len(rows) > 100 else None
    return {'folder_key': key, 'storage_provider': 'radai', 'files': [_project(row) for row in rows[:100]],
            'item_count': count, 'next_cursor': next_cursor}


def _file(opportunity, actor, key, file_id, *actions):
    require_access(actor, opportunity, *actions)
    _folder(key)
    try:
        if not isinstance(file_id, str) or not file_id.startswith('radai-'):
            raise ValueError()
        identity = UUID(file_id[6:])
    except (ValueError, TypeError, AttributeError):
        raise WorkspaceAPIError('attachment_missing', 404) from None
    attempt = OpportunityWorkspaceUpload.objects.select_related('workspace', 'actor').filter(
        pk=identity, workspace__opportunity=opportunity, folder_key=key, provider='radai', status='ready').first()
    if not attempt:
        raise WorkspaceAPIError('attachment_missing', 404)
    return attempt


def private_file_details(opportunity, actor, key, file_id):
    attempt = _file(opportunity, actor, key, file_id)
    _identity(attempt)
    return {**_project(attempt), 'can_download': workspace_allowed(actor, 'export'), 'max_download_bytes': 10 * 1024 * 1024}


def private_file_versions(opportunity, actor, key, file_id, cursor=None):
    attempt = _file(opportunity, actor, key, file_id)
    _identity(attempt)
    if cursor:
        raise WorkspaceAPIError('invalid_cursor', 400)
    detail = _project(attempt)
    return {'file_id': file_id, 'storage_provider': 'radai', 'versions': [{
        'id': '1', 'size': attempt.size, 'modified_at': detail['modified_at'],
        'modified_by': detail['modified_by'], 'publication_level': None, 'is_current': True,
    }], 'next_cursor': None}


def download_private_file(opportunity, actor, key, file_id):
    attempt = _file(opportunity, actor, key, file_id, 'export')
    output = _read(attempt, _identity(attempt))
    try:
        current = _file(opportunity, _actor(actor.pk), key, file_id, 'export')
        _identity(current)
        if any(getattr(current, field) != getattr(attempt, field) for field in ('sha256', 'size', 'storage_name', 'storage_fingerprint', 'name')):
            raise WorkspaceAPIError('attachment_integrity_failed')
        return output, current.name
    except Exception:
        output.close()
        raise


def upload_private_file(opportunity, actor, key, uploaded, request_id):
    require_access(actor, opportunity, 'create', 'update')
    _folder(key)
    if not uploaded or not valid_name(uploaded.name) or uploaded.size <= 0 or uploaded.size > upload_limit():
        raise ValidationError({'file': f'Choose a nonempty file with a valid filename, up to {upload_limit()} bytes.'})
    content = uploaded.read(upload_limit() + 1)
    if len(content) != uploaded.size or len(content) > upload_limit():
        raise ValidationError({'file': 'The file size is invalid.'})
    digest = hashlib.sha256(content).hexdigest()
    _, fingerprint = _storage()
    # Casefold can expand valid Unicode names beyond their input length. Keep a
    # fixed-size identity instead of truncating or rejecting an accepted name.
    normalized_name = hashlib.sha256(unicodedata.normalize('NFC', uploaded.name).casefold().encode('utf-8')).hexdigest()
    with transaction.atomic():
        Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
        workspace, _ = OpportunityWorkspace.objects.get_or_create(opportunity=opportunity)
        OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
        attempt = OpportunityWorkspaceUpload.objects.select_related('workspace', 'actor').filter(workspace=workspace, request_id=request_id).first()
        if attempt:
            if (attempt.provider, attempt.actor_id, attempt.folder_key, attempt.name, attempt.size, attempt.sha256) != (
                    'radai', actor.pk, key, uploaded.name, len(content), digest):
                raise WorkspaceAPIError('upload_conflict', 409)
            _identity(attempt)
            if attempt.status == 'ready':
                with _read(attempt, _identity(attempt)):
                    pass
                require_access(_actor(actor.pk), opportunity, 'create', 'update')
                _identity(attempt)
                return _project(attempt), False
            if attempt.status == 'uploading':
                raise WorkspaceAPIError('upload_in_progress', 409)
            attempt.status, attempt.error_code = 'uploading', ''
            attempt.save(update_fields=['status', 'error_code', 'updated_at'])
        else:
            if OpportunityWorkspaceUpload.objects.filter(workspace=workspace, provider='radai', folder_key=key,
                                                         normalized_name=normalized_name).exists():
                raise WorkspaceAPIError('name_conflict', 409)
            mime = str(getattr(uploaded, 'content_type', '') or '')
            attempt = OpportunityWorkspaceUpload(workspace=workspace, request_id=request_id, actor=actor,
                folder_key=key, name=uploaded.name, size=len(content), sha256=digest, provider='radai',
                storage_fingerprint=fingerprint, normalized_name=normalized_name,
                mime_type=mime if re.fullmatch(r'[A-Za-z0-9!#$&^_.+\-/]{1,255}', mime) else '')
            attempt.storage_name = _name(attempt)
            attempt.save()
        _audit(opportunity, actor, 'workspace_upload_started', data={'upload_id': str(attempt.pk), 'folder_key': key, 'storage_provider': 'radai'})
    # Durable identity is committed before any private object is written.
    try:
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        storage = _identity(attempt)
        if not storage.exists(attempt.storage_name):
            saved = storage.save(attempt.storage_name, ContentFile(content))
            if saved != attempt.storage_name:
                raise WorkspaceAPIError('attachment_integrity_failed')
        with _read(attempt, storage):
            pass
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        _identity(attempt)
        with transaction.atomic():
            locked = OpportunityWorkspaceUpload.objects.select_for_update().get(pk=attempt.pk)
            require_access(_actor(actor.pk), opportunity, 'create', 'update')
            _identity(locked)
            if locked.status != 'uploading' or any(getattr(locked, field) != getattr(attempt, field) for field in (
                    'request_id', 'provider', 'workspace_id', 'actor_id', 'folder_key', 'name', 'size', 'sha256',
                    'storage_name', 'storage_fingerprint', 'normalized_name')):
                raise WorkspaceAPIError('attachment_integrity_failed')
            locked.status, locked.error_code = 'ready', ''
            locked.save(update_fields=['status', 'error_code', 'updated_at'])
            _audit(opportunity, actor, 'workspace_file_uploaded', data={
                'upload_id': str(locked.pk), 'folder_key': key, 'item_id': 'radai-' + str(locked.pk), 'storage_provider': 'radai'})
            result = _project(locked)
        return result, True
    except Exception as exc:
        OpportunityWorkspaceUpload.objects.filter(
            pk=attempt.pk, provider='radai', status='uploading', sha256=attempt.sha256,
            storage_name=attempt.storage_name, storage_fingerprint=attempt.storage_fingerprint,
        ).update(status='uncertain', error_code='private_storage_unavailable', updated_at=timezone.now())
        if isinstance(exc, (WorkspaceAPIError, PermissionDenied)):
            raise
        raise WorkspaceAPIError('private_storage_unavailable') from None
