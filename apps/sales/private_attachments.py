"""Private RADAI attachments reuse the opportunity upload recovery ledger."""
import hashlib
import gzip
import re
from tempfile import TemporaryFile
import unicodedata
import zlib
from uuid import UUID

from django.core import signing
from django.core.files import File
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .attachment_storage import attachment_storage
from .attachment_streams import CHUNK_BYTES, prepare_upload
from .document_versions import (current_uploads, head_token, materialize_document, publish_version,
                               reserve_version, resolve_document, validate_version_input, version_projection)
from .models import Deal, OpportunityWorkspace, OpportunityWorkspaceUpload
from .opportunity_workspace import FOLDERS, WorkspaceAPIError, _actor, require_access, upload_limit, workspace_allowed
from .workflow import _audit


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
            'max_upload_bytes': upload_limit(), 'automatic_compression': 'lossless_if_smaller'}


def _name(attempt):
    return f'sales-opportunity-attachments/{attempt.workspace.opportunity_id}/{attempt.pk}/original'


def _identity(attempt):
    storage, fingerprint = _storage()
    if fingerprint != attempt.storage_fingerprint or attempt.storage_name != _name(attempt):
        raise WorkspaceAPIError('private_storage_changed', 409)
    return storage


def _project(attempt, viewer=None):
    from .document_classification import classification_projection
    actor = attempt.actor
    actor_name = (actor.get_full_name() or actor.username) if actor else None
    return {'id': 'radai-' + str(attempt.pk), 'name': attempt.name, 'size': attempt.size,
            'mime_type': attempt.mime_type or None, 'created_at': attempt.created_at.isoformat(),
            'modified_at': attempt.updated_at.isoformat(), 'created_by': actor_name, 'modified_by': actor_name,
            **version_projection(attempt, viewer), 'publication_level': None, 'is_folder': False, 'web_url': None,
            'storage_provider': 'radai', 'folder_key': attempt.folder_key,
            'classification': classification_projection(attempt, viewer),
            'storage_encoding': attempt.storage_encoding,
            'stored_size': attempt.stored_size if attempt.stored_size is not None else attempt.size}


def _read(attempt, storage):
    output = None
    try:
        output = TemporaryFile(mode='w+b')
        legacy = attempt.storage_encoding == 'identity' and attempt.stored_size is None and not attempt.stored_sha256
        stored_size = attempt.size if legacy else attempt.stored_size
        stored_hash = attempt.sha256 if legacy else attempt.stored_sha256
        if (attempt.storage_encoding not in ('identity', 'gzip') or type(stored_size) is not int
                or stored_size < 1 or not re.fullmatch(r'[a-f0-9]{64}', stored_hash)
                or attempt.size < 1):
            raise WorkspaceAPIError('attachment_integrity_failed')
        # Verify the stored representation before decoding. Saved sizes bound
        # both passes; no current upload policy can strand an existing document.
        with TemporaryFile(mode='w+b') as encoded:
            digest, length = hashlib.sha256(), 0
            with storage.open(attempt.storage_name, 'rb') as source:
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    length += len(chunk)
                    if length > stored_size:
                        raise WorkspaceAPIError('attachment_integrity_failed')
                    digest.update(chunk)
                    encoded.write(chunk)
            if length != stored_size or digest.hexdigest() != stored_hash:
                raise WorkspaceAPIError('attachment_integrity_failed')
            encoded.seek(0)
            source = gzip.GzipFile(fileobj=encoded, mode='rb') if attempt.storage_encoding == 'gzip' else encoded
            digest, length = hashlib.sha256(), 0
            try:
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    length += len(chunk)
                    if length > attempt.size:
                        raise WorkspaceAPIError('attachment_integrity_failed')
                    digest.update(chunk)
                    output.write(chunk)
            except (OSError, EOFError, zlib.error) as exc:
                raise WorkspaceAPIError('attachment_integrity_failed') from exc
            finally:
                if source is not encoded:
                    source.close()
            if length != attempt.size or digest.hexdigest() != attempt.sha256:
                raise WorkspaceAPIError('attachment_integrity_failed')
        output.seek(0)
        return output
    except Exception as exc:
        if output is not None:
            output.close()
        if isinstance(exc, WorkspaceAPIError):
            raise
        raise WorkspaceAPIError('attachment_missing' if isinstance(exc, FileNotFoundError) else 'private_storage_unavailable',
                                404 if isinstance(exc, FileNotFoundError) else 424) from None


def list_private_files(opportunity, actor, key, cursor=None):
    require_access(actor, opportunity)
    _folder(key)
    _, fingerprint = _storage()
    after_id, after_updated = None, None
    if cursor:
        try:
            payload = signing.loads(cursor, salt='sales-private-attachments', max_age=900)
            if (payload['opportunity'], payload['folder'], payload['provider'], payload['storage']) != (
                    str(opportunity.pk), key, 'radai', fingerprint):
                raise ValueError()
            after_id = UUID(payload['after'])
            after_updated = timezone.datetime.fromisoformat(payload['after_updated'])
            if timezone.is_naive(after_updated):
                after_updated = timezone.make_aware(after_updated, timezone.get_default_timezone())
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise WorkspaceAPIError('invalid_cursor', 400) from None
    query = OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=opportunity, provider='radai', folder_key=key, status='ready',
    )
    count = query.count()
    if after_id and after_updated:
        query = query.filter(Q(updated_at__lt=after_updated) | Q(updated_at=after_updated, pk__lt=after_id))
    rows = list(query.select_related('actor', 'document__head_upload').order_by('-updated_at', '-pk')[:101])
    next_cursor = signing.dumps({'opportunity': str(opportunity.pk), 'folder': key, 'provider': 'radai',
                                 'storage': fingerprint, 'after': str(rows[99].pk),
                                 'after_updated': rows[99].updated_at.isoformat()},
                                salt='sales-private-attachments') if len(rows) > 100 else None
    return {'folder_key': key, 'storage_provider': 'radai', 'files': [_project(row, actor) for row in rows[:100]],
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
    attempt = OpportunityWorkspaceUpload.objects.select_related('workspace', 'actor', 'document__head_upload').filter(
        pk=identity, workspace__opportunity=opportunity, folder_key=key, provider='radai', status='ready').first()
    if not attempt:
        raise WorkspaceAPIError('attachment_missing', 404)
    return attempt


def private_file_details(opportunity, actor, key, file_id):
    attempt = _file(opportunity, actor, key, file_id)
    _identity(attempt)
    return {**_project(attempt, actor), 'can_download': workspace_allowed(actor, 'export'),
            'can_delete': workspace_allowed(actor, 'create', 'update'),
            'max_download_bytes': None, 'max_upload_bytes': upload_limit()}


def delete_private_file(opportunity, actor, key, file_id):
    attempt = _file(opportunity, actor, key, file_id, 'create', 'update')
    _identity(attempt)
    document = resolve_document(attempt)
    with transaction.atomic():
        Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        locked = OpportunityWorkspaceUpload.objects.select_for_update().filter(
            pk=attempt.pk, workspace__opportunity=opportunity, folder_key=key, provider='radai', status='ready',
        ).select_related('actor', 'document__head_upload').first()
        if not locked:
            raise WorkspaceAPIError('attachment_missing', 404)
        _identity(locked)
        document = resolve_document(locked)
        if not document:
            raise WorkspaceAPIError('cannot_delete_last_version', 409)
        document = type(document).objects.select_for_update().get(pk=document.pk)
        if document.pending_upload_id:
            raise WorkspaceAPIError('upload_in_progress', 409)
        ready = OpportunityWorkspaceUpload.objects.select_for_update().filter(document=document, status='ready')
        if ready.count() <= 1:
            raise WorkspaceAPIError('cannot_delete_last_version', 409)
        replacement = document.head_upload
        if document.head_upload_id == locked.pk:
            replacement = ready.exclude(pk=locked.pk).order_by('-version_number', '-pk').first()
            document.head_upload = replacement
            document.name = replacement.name
            document.save(update_fields=['head_upload', 'name'])
        locked.status = 'deleted'
        locked.error_code = ''
        locked.save(update_fields=['status', 'error_code', 'updated_at'])
        _audit(opportunity, actor, 'workspace_file_deleted', data={
            'upload_id': str(locked.pk), 'folder_key': key, 'storage_provider': 'radai',
            'document_id': str(document.pk), 'version': locked.version_number,
            'head_upload_id': str(document.head_upload_id),
        })
        current = OpportunityWorkspaceUpload.objects.select_related('actor', 'document__head_upload').get(pk=document.head_upload_id)
    return {'deleted_id': 'radai-' + str(locked.pk), 'document_id': str(document.pk), 'current': _project(current, actor)}


def private_file_versions(opportunity, actor, key, file_id, cursor=None):
    attempt = _file(opportunity, actor, key, file_id)
    _identity(attempt)
    document = resolve_document(attempt)
    after = None
    if cursor:
        try:
            payload = signing.loads(cursor, salt='sales-private-versions', max_age=900)
            if payload['scope'] != [str(opportunity.pk), key, str(document.pk if document else attempt.pk), attempt.storage_fingerprint]:
                raise ValueError()
            after = payload['after']
            if type(after) is not int or after < 1:
                raise ValueError()
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise WorkspaceAPIError('invalid_cursor', 400) from None
    query = OpportunityWorkspaceUpload.objects.filter(document=document, status='ready') if document else OpportunityWorkspaceUpload.objects.filter(pk=attempt.pk)
    rows = list((query.filter(version_number__lt=after) if after else query).select_related('actor', 'document__head_upload').order_by('-version_number')[:101])
    versions = []
    for row in rows[:100]:
        detail = _project(row, actor)
        versions.append({**detail, 'id': str(row.version_number), 'file_id': 'radai-' + str(row.pk)})
    next_cursor = signing.dumps({'scope': [str(opportunity.pk), key, str(document.pk), attempt.storage_fingerprint],
                                 'after': rows[99].version_number}, salt='sales-private-versions') if len(rows) > 100 else None
    return {'file_id': file_id, 'document_id': str(document.pk if document else attempt.pk),
            'storage_provider': 'radai', 'versions': versions, 'next_cursor': next_cursor}


def download_private_file(opportunity, actor, key, file_id):
    attempt = _file(opportunity, actor, key, file_id, 'export')
    output = _read(attempt, _identity(attempt))
    try:
        current = _file(opportunity, _actor(actor.pk), key, file_id, 'export')
        _identity(current)
        if any(getattr(current, field) != getattr(attempt, field) for field in (
                'sha256', 'size', 'storage_name', 'storage_fingerprint', 'name',
                'storage_encoding', 'stored_size', 'stored_sha256')):
            raise WorkspaceAPIError('attachment_integrity_failed')
        return output, current.name
    except Exception:
        output.close()
        raise


def upload_private_file(opportunity, actor, key, uploaded, request_id):
    require_access(actor, opportunity, 'create', 'update')
    _folder(key)
    try:
        with prepare_upload(uploaded, compress=True) as prepared:
            return _upload_prepared(opportunity, actor, key, uploaded, request_id, prepared)
    except OSError:
        raise WorkspaceAPIError('private_storage_unavailable') from None


def upload_private_version(opportunity, actor, key, file_id, uploaded, request_id, expected_token, note=''):
    target = _file(opportunity, actor, key, file_id, 'create', 'update')
    _identity(target)
    validate_version_input(expected_token, note)
    try:
        with prepare_upload(uploaded, compress=True) as prepared:
            return _upload_prepared(opportunity, actor, key, uploaded, request_id, prepared,
                                    target=target, expected_token=expected_token, note=note)
    except OSError:
        raise WorkspaceAPIError('private_storage_unavailable') from None


def _upload_prepared(opportunity, actor, key, uploaded, request_id, prepared, *, target=None, expected_token='', note=''):
    digest = prepared['sha256']
    _, fingerprint = _storage()
    # Casefold can expand valid Unicode names beyond their input length. Keep a
    # fixed-size identity instead of truncating or rejecting an accepted name.
    normalized_name = hashlib.sha256(unicodedata.normalize('NFC', uploaded.name).casefold().encode('utf-8')).hexdigest()
    with transaction.atomic():
        Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        workspace, _ = OpportunityWorkspace.objects.get_or_create(opportunity=opportunity)
        OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
        attempt = OpportunityWorkspaceUpload.objects.select_related('workspace', 'actor').filter(workspace=workspace, request_id=request_id).first()
        if attempt and (attempt.provider, attempt.actor_id, attempt.folder_key, attempt.name, attempt.size, attempt.sha256) != (
                'radai', actor.pk, key, uploaded.name, prepared['size'], digest):
            raise WorkspaceAPIError('upload_conflict', 409)
        document = None
        if target:
            target = _file(opportunity, _actor(actor.pk), key, 'radai-' + str(target.pk), 'create', 'update')
            _identity(target)
            document = materialize_document(target)
        elif attempt:
            # Replay the reserved predecessor, not a head changed by this upload
            # or by a later revision.
            if attempt.previous_upload_id:
                target = _file(opportunity, _actor(actor.pk), key, 'radai-' + str(attempt.previous_upload_id), 'create', 'update')
                _identity(target)
                document = materialize_document(target)
                expected_token = attempt.expected_head_token
                note = 'Reupload from folder upload'
        else:
            existing = current_uploads(OpportunityWorkspaceUpload.objects.select_for_update(of=('self',)).filter(
                workspace=workspace, provider='radai', folder_key=key, normalized_name=normalized_name, status='ready',
            )).select_related('document__head_upload').order_by('-version_number', '-pk').first()
            if existing:
                _identity(existing)
                target = existing
                document = materialize_document(target)
                expected_token = head_token(target, document)
                if not note:
                    note = 'Reupload from folder upload'
        if attempt:
            if ((attempt.previous_upload_id is not None) != bool(target) or (target and (
                    attempt.document_id != document.pk or attempt.expected_head_token != expected_token or attempt.revision_note != note))):
                raise WorkspaceAPIError('upload_conflict', 409)
            _identity(attempt)
            if attempt.status == 'ready':
                with _read(attempt, _identity(attempt)):
                    pass
                require_access(_actor(actor.pk), opportunity, 'create', 'update')
                _identity(attempt)
                return _project(attempt, _actor(actor.pk)), False
            if attempt.status == 'uploading':
                raise WorkspaceAPIError('upload_in_progress', 409)
            if target and (document.pending_upload_id != attempt.pk or document.head_upload_id != attempt.previous_upload_id
                           or head_token(target, document) != expected_token):
                raise WorkspaceAPIError('version_stale', 409)
            attempt.status, attempt.error_code = 'uploading', ''
            attempt.save(update_fields=['status', 'error_code', 'updated_at'])
        else:
            if not target and OpportunityWorkspaceUpload.objects.filter(workspace=workspace, provider='radai', folder_key=key,
                                                                         normalized_name=normalized_name, version_number=1,
                                                                         status='ready').exists():
                raise WorkspaceAPIError('name_conflict', 409)
            mime = str(getattr(uploaded, 'content_type', '') or '')
            attempt = reserve_version(document, target, actor, uploaded, request_id, prepared, fingerprint,
                                      normalized_name, expected_token, note) if target else OpportunityWorkspaceUpload(workspace=workspace, request_id=request_id, actor=actor,
                folder_key=key, name=uploaded.name, size=prepared['size'], sha256=digest, provider='radai',
                storage_encoding=prepared['storage_encoding'], stored_size=prepared['stored_size'],
                stored_sha256=prepared['stored_sha256'],
                storage_fingerprint=fingerprint, normalized_name=normalized_name,
                mime_type='')
            attempt.mime_type = mime if re.fullmatch(r'[A-Za-z0-9!#$&^_.+\-/]{1,255}', mime) else ''
            attempt.storage_name = _name(attempt)
            attempt.save()
            if document:
                document.pending_upload = attempt
                document.save(update_fields=['pending_upload'])
        _audit(opportunity, actor, 'workspace_upload_started', data={'upload_id': str(attempt.pk), 'folder_key': key, 'storage_provider': 'radai'})
    # Durable identity is committed before any private object is written.
    try:
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        storage = _identity(attempt)
        if not storage.exists(attempt.storage_name):
            # An uncertain retry preserves the representation reserved by its
            # durable intent, including historical uncompressed attempts.
            content = prepared['original'] if attempt.storage_encoding == 'identity' else prepared['stored']
            if attempt.storage_encoding == 'gzip' and (
                    prepared['storage_encoding'], prepared['stored_size'], prepared['stored_sha256']) != (
                    attempt.storage_encoding, attempt.stored_size, attempt.stored_sha256):
                raise WorkspaceAPIError('attachment_integrity_failed')
            content.seek(0)
            saved = storage.save(attempt.storage_name, File(content))
            if saved != attempt.storage_name:
                raise WorkspaceAPIError('attachment_integrity_failed')
        with _read(attempt, storage):
            pass
        require_access(_actor(actor.pk), opportunity, 'create', 'update')
        _identity(attempt)
        with transaction.atomic():
            Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
            OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
            if document:
                document = type(document).objects.select_for_update().get(pk=document.pk)
            locked = OpportunityWorkspaceUpload.objects.select_for_update().get(pk=attempt.pk)
            require_access(_actor(actor.pk), opportunity, 'create', 'update')
            _identity(locked)
            if locked.status != 'uploading' or any(getattr(locked, field) != getattr(attempt, field) for field in (
                    'request_id', 'provider', 'workspace_id', 'actor_id', 'folder_key', 'name', 'size', 'sha256',
                    'storage_name', 'storage_fingerprint', 'normalized_name',
                    'document_id', 'version_number', 'previous_upload_id', 'revision_note', 'expected_head_token',
                    'storage_encoding', 'stored_size', 'stored_sha256')):
                raise WorkspaceAPIError('attachment_integrity_failed')
            locked.status, locked.error_code = 'ready', ''
            locked.save(update_fields=['status', 'error_code', 'updated_at'])
            if document:
                publish_version(locked, document)
            else:
                materialize_document(locked)
            _audit(opportunity, actor, 'workspace_file_uploaded', data={
                'upload_id': str(locked.pk), 'folder_key': key, 'item_id': 'radai-' + str(locked.pk), 'storage_provider': 'radai',
                'document_id': str(locked.document_id), 'version': locked.version_number,
                'previous_upload_id': str(locked.previous_upload_id) if locked.previous_upload_id else None,
                'revision_note': locked.revision_note})
            from .document_classification import queue_classification
            queue_classification(locked, actor)
            result = _project(locked, _actor(actor.pk))
        return result, True
    except Exception as exc:
        OpportunityWorkspaceUpload.objects.filter(
            pk=attempt.pk, provider='radai', status='uploading', sha256=attempt.sha256,
            storage_name=attempt.storage_name, storage_fingerprint=attempt.storage_fingerprint,
            storage_encoding=attempt.storage_encoding, stored_size=attempt.stored_size, stored_sha256=attempt.stored_sha256,
        ).update(status='uncertain', error_code='private_storage_unavailable', updated_at=timezone.now())
        if isinstance(exc, (WorkspaceAPIError, PermissionDenied)):
            raise
        raise WorkspaceAPIError('private_storage_unavailable') from None
