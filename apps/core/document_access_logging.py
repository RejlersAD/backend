"""
Document access logging.

Writes to the pre-existing `document_access_logs` table
(apps.core.models.DocumentAccessLog). That table's document_id has a real
DB-level FK to `documents` — a table currently unused by any app in this
codebase. get_or_create_shadow_document() bridges an app-specific document
record (e.g. apps.crs_documents.CRSDocument) into a matching `documents` row
using a deterministic UUID, so the FK can be satisfied without altering the
source app's own model, table, or behaviour.

This module is purely additive and defensive: it never modifies the source
document, and every public function swallows its own exceptions so a
logging failure can never break document view/download/edit for the caller.
"""
import logging
import uuid

from django.utils import timezone

logger = logging.getLogger(__name__)

# Fixed namespace so the same (owner_service, source_pk) always maps to the
# same `documents` row, across calls and process restarts.
_SHADOW_DOC_NAMESPACE = uuid.UUID('c9f6a3e2-8b1d-4e77-9c2a-1c9a2e7b6f10')


def shadow_document_id(owner_service: str, source_pk) -> uuid.UUID:
    """Deterministic `documents.id` for a given (owner_service, source_pk) pair."""
    return uuid.uuid5(_SHADOW_DOC_NAMESPACE, f'{owner_service}:{source_pk}')


def get_or_create_shadow_document(
    *, owner_service, source_pk, filename='', file_field='', file_size=0,
    mime_type='', status='', created_by_user_id=None, document_type='document',
):
    """
    Ensure a `documents` row exists for a source app's document record and
    return its id. Never raises — returns None on failure.
    """
    try:
        from .models import Document

        doc_id = shadow_document_id(owner_service, source_pk)
        now = timezone.now()
        Document.objects.get_or_create(
            id=doc_id,
            defaults=dict(
                document_type=(document_type or 'document')[:50],
                owner_service=owner_service[:100],
                file=(file_field or '')[:100],
                filename=(filename or '')[:255],
                file_size=file_size or 0,
                mime_type=(mime_type or '')[:100],
                checksum='',
                metadata={},
                tags=[],
                current_version=1,
                is_latest=True,
                status=(status or '')[:20],
                created_by_user_id=created_by_user_id or 0,
                created_by_role='',
                is_public=False,
                allowed_roles=[],
                created_at=now,
                updated_at=now,
            ),
        )
        return doc_id
    except Exception:
        logger.exception('get_or_create_shadow_document failed for %s:%s', owner_service, source_pk)
        return None


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_document_access(request, *, owner_service, source_pk, access_type, **shadow_kwargs):
    """
    Log a view/download/edit event for a source app's document. Bridges the
    source record into the shadow `documents` table if needed, then writes a
    DocumentAccessLog row. Never raises.

    shadow_kwargs are passed through to get_or_create_shadow_document():
    filename, file_field, file_size, mime_type, status, created_by_user_id,
    document_type.
    """
    try:
        from .models import DocumentAccessLog

        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return  # user_id is NOT NULL on this table — nothing to log for anonymous requests

        doc_id = get_or_create_shadow_document(
            owner_service=owner_service, source_pk=source_pk, **shadow_kwargs
        )
        if doc_id is None:
            return

        DocumentAccessLog.objects.create(
            user_id=user.id,
            document_id=doc_id,
            access_type=access_type,
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:255],
            accessed_at=timezone.now(),
        )
    except Exception:
        logger.exception('log_document_access failed for %s:%s (%s)', owner_service, source_pk, access_type)
