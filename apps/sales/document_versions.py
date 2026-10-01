"""Canonical private document identity and explicit immutable revision commands."""
import hashlib
import json

from django.db.models import F, Q
from rest_framework.exceptions import ValidationError

from .models import OpportunityDocument, OpportunityWorkspaceUpload
from .opportunity_workspace import WorkspaceAPIError, workspace_allowed


def current_uploads(query):
    return query.filter(Q(document__isnull=True) | Q(document__head_upload_id=F('pk')))


def resolve_document(upload):
    """Read only. Historical ungrouped uploads are implicit single-version files."""
    document = upload.document if upload.document_id else None
    if document and (document.workspace_id != upload.workspace_id or document.folder_key != upload.folder_key
                     or document.pk != document.root_upload_id):
        raise WorkspaceAPIError('attachment_integrity_failed')
    if document:
        head = document.head_upload
        if (head.document_id != document.pk or head.workspace_id != document.workspace_id
                or head.folder_key != document.folder_key or head.provider != 'radai' or head.status != 'ready'):
            raise WorkspaceAPIError('attachment_integrity_failed')
    return document


def materialize_document(upload):
    """Caller holds Deal then workspace locks; never used by GET projections."""
    if upload.document_id:
        upload.document = OpportunityDocument.objects.select_for_update().get(pk=upload.document_id)
        return resolve_document(upload)
    if upload.provider != 'radai' or upload.status != 'ready' or upload.version_number != 1:
        raise WorkspaceAPIError('attachment_integrity_failed')
    document = OpportunityDocument.objects.create(
        id=upload.pk, workspace_id=upload.workspace_id, folder_key=upload.folder_key,
        name=upload.name, normalized_name=upload.normalized_name, root_upload=upload, head_upload=upload,
    )
    upload.document = document
    upload.save(update_fields=['document'])
    return document


def head_token(upload, document=None):
    document = document or resolve_document(upload)
    head = document.head_upload if document else upload
    values = [str(document.pk if document else upload.pk), str(head.pk), head.version_number,
              head.sha256, head.size, head.storage_fingerprint]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


def version_projection(upload, actor=None):
    document = resolve_document(upload)
    head = document.head_upload if document else upload
    return {
        'document_id': str(document.pk if document else upload.pk),
        'document_name': document.name if document else upload.name,
        'version': str(upload.version_number), 'is_current': head.pk == upload.pk,
        'head_file_id': 'radai-' + str(head.pk), 'head_token': head_token(upload, document),
        'revision_note': upload.revision_note,
        'can_upload_version': bool(actor and workspace_allowed(actor, 'create', 'update')
                                   and not (document and document.pending_upload_id)),
    }


def validate_version_input(expected_token, note):
    if not isinstance(expected_token, str) or len(expected_token) != 64:
        raise ValidationError({'expected_token': 'Refresh the document and provide its current head token.'})
    if not isinstance(note, str) or len(note) > 1000 or '\x00' in note:
        raise ValidationError({'revision_note': 'Use a revision note of at most 1000 characters without NUL characters.'})


def reserve_version(document, target, actor, uploaded, request_id, prepared, fingerprint, normalized_name, expected_token, note):
    """Caller holds the document lock; reservation survives uncertain object I/O."""
    if expected_token != head_token(target, document):
        raise WorkspaceAPIError('version_stale', 409)
    if document.pending_upload_id:
        raise WorkspaceAPIError('upload_in_progress', 409)
    head = document.head_upload
    attempt = OpportunityWorkspaceUpload(
        workspace_id=document.workspace_id, request_id=request_id, actor=actor,
        folder_key=document.folder_key, name=uploaded.name, size=prepared['size'], sha256=prepared['sha256'],
        provider='radai', storage_encoding=prepared['storage_encoding'], stored_size=prepared['stored_size'],
        stored_sha256=prepared['stored_sha256'], storage_fingerprint=fingerprint, normalized_name=normalized_name,
        document=document, version_number=head.version_number + 1, previous_upload=head,
        revision_note=note, expected_head_token=expected_token,
    )
    return attempt


def publish_version(upload, document):
    if (document.pending_upload_id != upload.pk or document.head_upload_id != upload.previous_upload_id
            or upload.expected_head_token != head_token(document.head_upload, document)):
        raise WorkspaceAPIError('version_stale', 409)
    document.head_upload, document.pending_upload = upload, None
    document.save(update_fields=['head_upload', 'pending_upload'])
    upload.document = document
