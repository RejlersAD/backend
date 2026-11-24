"""Data retention for RADAI Intelligence chats (Team & Enterprise policy `data_retention_days`).

Scope: conversations owned by members of the organization whose last activity (newest message,
or the conversation itself) is older than the retention period, together with their messages,
shares, uploaded attachments and generated documents (database rows and stored files).
Not in scope: workspace knowledge documents, audit logs and AI usage/cost records.

Nothing is deleted from the API. Deletion only happens through
`manage.py apply_data_retention --apply`, which previews by default.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

logger = logging.getLogger(__name__)
BATCH = 200


def expired_conversations(org, days, now=None):
    """Conversations of the organization's members with no activity for more than `days` days."""
    from apps.core.ai_workspace_models import AIWorkspaceConversation
    from apps.rbac.models import UserProfile
    cutoff = (now or timezone.now()) - timedelta(days=days)
    members = UserProfile.objects.filter(organization=org, is_deleted=False).values('user_id')
    return (AIWorkspaceConversation.objects.filter(owner_id__in=members)
            .annotate(last_activity=Greatest('updated_at', Coalesce(Max('messages__created_at'), 'updated_at')))
            .filter(last_activity__lt=cutoff))


def preview(org, days, now=None):
    """Counts of what a clean-up would delete right now. Deletes nothing."""
    from apps.core.ai_workspace_models import (AIWorkspaceAttachment, AIWorkspaceGeneratedDocument,
                                               AIWorkspaceMessage)
    if not days:
        return {'enabled': False, 'retention_days': None}
    now = now or timezone.now()
    ids = list(expired_conversations(org, days, now).values_list('pk', flat=True))
    oldest = (expired_conversations(org, days, now).order_by('last_activity')
              .values_list('last_activity', flat=True).first())
    files = (AIWorkspaceAttachment.objects.filter(conversation_id__in=ids).count()
             + AIWorkspaceGeneratedDocument.objects.filter(conversation_id__in=ids).count())
    return {
        'enabled': True,
        'retention_days': days,
        'cutoff': (now - timedelta(days=days)).isoformat(),
        'conversations': len(ids),
        'messages': AIWorkspaceMessage.objects.filter(conversation_id__in=ids).count(),
        'files': files,
        'oldest_activity': oldest.isoformat() if oldest else None,
    }


def _delete_files(rows):
    for row in rows:
        if row.file:
            try:
                row.file.delete(save=False)
            except Exception:  # a missing file must not block the clean-up
                logger.warning('Retention: could not delete stored file for %s %s', type(row).__name__, row.pk)


def apply(org, days, now=None):
    """Delete expired conversations in batches. Returns the counts actually deleted."""
    from apps.core.ai_workspace_models import (AIWorkspaceAttachment, AIWorkspaceConversation,
                                               AIWorkspaceGeneratedDocument, AIWorkspaceMessage)
    totals = {'conversations': 0, 'messages': 0, 'files': 0}
    if not days:
        return totals
    ids = list(expired_conversations(org, days, now).values_list('pk', flat=True))
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        attachments = list(AIWorkspaceAttachment.objects.filter(conversation_id__in=chunk))
        generated = list(AIWorkspaceGeneratedDocument.objects.filter(conversation_id__in=chunk))
        with transaction.atomic():
            totals['messages'] += AIWorkspaceMessage.objects.filter(conversation_id__in=chunk).count()
            totals['files'] += len(attachments) + len(generated)
            totals['conversations'] += AIWorkspaceConversation.objects.filter(pk__in=chunk).delete()[1].get(
                AIWorkspaceConversation._meta.label, 0)
        # Remove stored files only after the rows are gone, so a rollback never leaves dangling rows.
        _delete_files(attachments + generated)
    return totals
