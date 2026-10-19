"""Permission-gated metadata for the configured Finance workbook connection."""
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from apps.finance.receivables_source_models import ReceivablesSourceSnapshot, ReceivablesSyncState
from .receivables_sharepoint import sync_enabled


_ACTIVE_SNAPSHOT = object()


def finance_sharepoint_status(user, *, snapshot_id=_ACTIVE_SNAPSHOT):
    """Expose check/publication times without source identifiers or raw errors.

    Dashboard callers can pin the publication to their already selected source.
    A manual replacement must not inherit another snapshot's successful check.
    """
    result = {
        'status': 'restricted', 'last_checked_at': None, 'last_attempt_at': None,
        'snapshot_id': None, 'snapshot_published_at': None, 'can_refresh': False,
    }
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return result

    snapshots = ReceivablesSourceSnapshot.objects.only('pk', 'imported_at')
    if snapshot_id is _ACTIVE_SNAPSHOT:
        snapshot = snapshots.filter(is_active=True).first()
    else:
        snapshot = snapshots.filter(pk=snapshot_id).first() if snapshot_id is not None else None
    state = ReceivablesSyncState.objects.filter(pk=1).first()
    enabled = sync_enabled()
    result['status'] = 'idle' if enabled else 'disabled'
    result['can_refresh'] = enabled and module_action_allowed(user, 'finance_outgoing', 'update')
    if snapshot is not None:
        result['snapshot_id'] = snapshot.pk
        result['snapshot_published_at'] = snapshot.imported_at.isoformat()
    if state is None:
        return result

    result['last_attempt_at'] = state.last_attempt_at.isoformat() if state.last_attempt_at else None
    if (snapshot is not None and state.remote_snapshot_id == snapshot.pk
            and state.remote_identity and state.etag and state.last_success_at):
        result['last_checked_at'] = state.last_success_at.isoformat()
    if enabled:
        if state.sync_token and (state.sync_expires_at is None or state.sync_expires_at > timezone.now()):
            result['status'] = 'syncing'
        elif state.last_error:
            result['status'] = 'failed'
    return result
