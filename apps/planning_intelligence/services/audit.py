"""Small append-only audit writer for planning mutations."""
from ..models import PlanningAuditEvent


def record_event(*, project, actor, action, entity, before=None, after=None, metadata=None):
    return PlanningAuditEvent.objects.create(
        project=project,
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action,
        entity_type=entity.__class__.__name__,
        entity_id=str(getattr(entity, 'pk', '') or ''),
        before=before or {},
        after=after or {},
        metadata=metadata or {},
    )
