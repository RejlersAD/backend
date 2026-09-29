"""Fenced, resumable mailbox reads. Only SQL state determines completed work."""

from contextlib import contextmanager
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Count, Min, Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed

from .email_permissions import require_email_capture_access, visible_mailbox_connections
from .mailbox_capture import EmailCaptureConflict, mailbox_identity, store_captured_message
from .mailbox_sync_graph import SalesMailboxSyncGraphService
from .microsoft_graph import SalesMailboxReadError
from .models import SalesMailboxConnection, SalesMailboxSyncState, SalesMailboxSyncFolder, SalesMailboxSyncItem


LEASE_SECONDS = 180
POLL_SECONDS = 60
DISCOVERY_SECONDS = 300
MAX_STEPS = 30
RUN_SECONDS = 60
CURSOR_SALT = 'sales.mailbox.sync.checkpoint.v1'
SAFE_ERRORS = frozenset({
    'authorization_required', 'configuration_changed', 'automation_disabled',
    'worker_unavailable', 'provider_unavailable', 'source_unavailable',
    'throttled', 'invalid_response', 'invalid_checkpoint', 'checkpoint_expired',
    'unsupported_message', 'internal_error',
})


class SyncStopped(Exception):
    """Another command owns this mailbox now; perform no further writes."""


class SyncBlocked(Exception):
    def __init__(self, code):
        self.code = code


def automation_available():
    return bool(
        getattr(settings, 'SALES_MAILBOX_SYNC_ENABLED', False)
        and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', True)
        and getattr(settings, 'CELERY_BROKER_URL', '')
    )


def _identity(connection):
    return dict(zip(('auth_mode', 'tenant_id', 'client_id', 'mailbox_address'), mailbox_identity(connection)))


def _actor(connection, state):
    user = get_user_model().objects.filter(pk=state.authorized_by_id).first()
    try:
        require_email_capture_access(user)
    except PermissionDenied:
        raise SyncBlocked('authorization_required') from None
    if not visible_mailbox_connections(user).filter(pk=connection.pk).exists():
        raise SyncBlocked('authorization_required')
    if (connection.auth_mode != 'application' or _identity(connection) != state.identity
            or not connection.tenant_id.strip() or not connection.client_id.strip()):
        raise SyncBlocked('configuration_changed')
    return user


@contextmanager
def _locked(connection_id, token=None):
    # All commands lock connection before state, including nested capture writes.
    with transaction.atomic():
        connection = SalesMailboxConnection.objects.select_for_update().get(pk=connection_id)
        state = SalesMailboxSyncState.objects.select_for_update().get(connection=connection)
        if token is not None and (
            state.lease_token != token or not state.lease_expires_at
            or state.lease_expires_at <= timezone.now()
        ):
            raise SyncStopped()
        yield connection, state


@contextmanager
def _authorized(connection_id, token):
    with _locked(connection_id, token) as (connection, state):
        if not connection.enabled or state.status in {'paused', 'blocked'} or not automation_available():
            raise SyncStopped()
        user = _actor(connection, state)
        state.lease_expires_at = timezone.now() + timedelta(seconds=LEASE_SECONDS)
        state.save(update_fields=['lease_expires_at', 'updated_at'])
        yield connection, state, user


def configure_mailbox_sync(*, connection, user, enabled):
    """Explicitly authorize or pause automation; every toggle fences old jobs."""
    if not isinstance(enabled, bool):
        raise ValidationError({'enabled': 'Provide true or false.'})
    with transaction.atomic():
        current = SalesMailboxConnection.objects.select_for_update().get(pk=connection.pk)
        actor = get_user_model().objects.get(pk=user.pk)
        require_email_capture_access(actor)
        if not module_action_allowed(actor, 'sales_email_intake', 'update'):
            raise PermissionDenied('You do not have access to configure mailbox sync.')
        if not visible_mailbox_connections(actor).filter(pk=current.pk).exists():
            raise Http404()
        if enabled and current.auth_mode != 'application':
            raise ValidationError({'enabled': 'Automatic sync requires an application mailbox.'})
        if enabled and (not current.tenant_id.strip() or not current.client_id.strip()):
            raise ValidationError({'enabled': 'Save tenant and application IDs on this mailbox before enabling sync.'})
        if enabled and not automation_available():
            raise ValidationError({'enabled': 'Automatic mailbox sync is not enabled on this server.'})
        state, _ = SalesMailboxSyncState.objects.get_or_create(connection=current)
        if enabled and state.identity and state.identity != _identity(current):
            # Checkpoints and queued IDs belong exclusively to the old identity.
            # Captured evidence prevents identity changes at the configuration API.
            if state.items.filter(intake__isnull=False).exists() or current.email_intakes.exists():
                raise ValidationError({'enabled': 'This mailbox identity changed. Restore its saved configuration.'})
            state.folders.all().delete()
            state.items.all().delete()
            state.folder_cursor = ''
            state.folder_discovery_completed_at = None
            state.folder_discovery_due_at = None
            state.initial_sync_completed_at = None
        current.enabled = enabled
        current.save(update_fields=['enabled', 'updated_at'])
        state.authorized_by = actor
        state.identity = _identity(current)
        state.status = 'queued' if enabled else 'paused'
        state.lease_token = None
        state.lease_expires_at = None
        state.next_attempt_at = timezone.now() if enabled else None
        state.last_error_code = ''
        state.consecutive_failures = 0
        state.save()
        return state


def sync_projection(connection):
    state = SalesMailboxSyncState.objects.filter(connection_id=connection.pk).first()
    counts = dict(state.items.values_list('status').annotate(total=Count('pk'))) if state else {}
    enabled = bool(connection.enabled and state)
    status = state.status if state else 'not_configured'
    error = state.last_error_code if state else ''
    if state and not enabled:
        status = 'paused'
    elif enabled and not automation_available():
        status, error = 'paused', 'automation_disabled'
    return {
        'enabled': enabled, 'status': status,
        'saved_count': connection.email_intakes.count(),
        'pending_count': counts.get('pending', 0),
        'failed_count': counts.get('error', 0) + counts.get('unavailable', 0),
        'last_successful_sync_at': state.last_successful_sync_at if state else None,
        'initial_sync_complete': bool(state and state.initial_sync_completed_at),
        'error_code': error if error in SAFE_ERRORS else '',
    }


def _checkpoint(state, cursor, *, folder_id=''):
    if not cursor:
        return ''
    return signing.dumps({'connection': str(state.connection_id), 'identity': state.identity,
                          'folder': folder_id, 'url': cursor}, salt=CURSOR_SALT, compress=True)


def _open_checkpoint(state, value, *, folder_id=''):
    if not value:
        return ''
    try:
        payload = signing.loads(value, salt=CURSOR_SALT)
        if (not isinstance(payload, dict) or payload.get('connection') != str(state.connection_id)
                or payload.get('identity') != state.identity or payload.get('folder') != folder_id
                or not isinstance(payload.get('url'), str)):
            raise ValueError()
        return payload['url']
    except (signing.BadSignature, ValueError, TypeError):
        raise SyncBlocked('invalid_checkpoint') from None


def _claim(connection_id):
    if not automation_available():
        return None
    try:
        with _locked(connection_id) as (connection, state):
            now = timezone.now()
            if (not connection.enabled or state.status in {'blocked', 'paused'}
                    or (state.next_attempt_at and state.next_attempt_at > now)
                    or (state.lease_token and state.lease_expires_at and state.lease_expires_at > now)):
                return None
            try:
                _actor(connection, state)
            except SyncBlocked as exc:
                state.status, state.last_error_code = 'blocked', exc.code
                state.next_attempt_at = state.lease_token = state.lease_expires_at = None
                state.save()
                return None
            token = uuid4()
            state.status, state.lease_token = 'running', token
            state.last_started_at = now
            state.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            state.save()
            return token
    except (SalesMailboxConnection.DoesNotExist, SalesMailboxSyncState.DoesNotExist):
        return None


def _due(queryset, now):
    return queryset.filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))


def _select_work(connection_id, token, turn):
    with _authorized(connection_id, token) as (connection, state, user):
        now = timezone.now()
        # Round-robin among discovery, folders and items keeps a large backfill
        # from starving actual capture or additional folder discovery.
        for kind in [((turn + n) % 3) for n in range(3)]:
            if kind == 0 and (not state.folder_discovery_due_at or state.folder_discovery_due_at <= now):
                return connection, state, user, 'discovery', None, _open_checkpoint(state, state.folder_cursor)
            if kind == 1:
                folder = _due(state.folders.filter(is_removed=False), now).order_by('updated_at', 'pk').first()
                if folder:
                    return connection, state, user, 'folder', folder.pk, _open_checkpoint(state, folder.cursor, folder_id=folder.folder_id)
            if kind == 2:
                item = _due(state.items.filter(status__in=['pending', 'error']), now).order_by('updated_at', 'pk').first()
                if item:
                    return connection, state, user, 'item', item.pk, ''
        return None


def _read_and_commit(connection, state, user, kind, record_id, cursor, token, service):
    folder_id = ''
    if kind == 'discovery':
        page = service.read_folder_changes(cursor=cursor)
    elif kind == 'folder':
        folder_id = state.folders.get(pk=record_id).folder_id
        page = service.read_message_changes(folder_id, cursor=cursor)
    else:
        item = state.items.get(pk=record_id)
        message = service.get_message_for_capture(item.message_id)
    with _authorized(connection.pk, token) as (current, saved, actor):
        now = timezone.now()
        if kind == 'item':
            item = saved.items.get(pk=record_id)
            if item.status == 'captured':
                return
            intake, _ = store_captured_message(
                connection=current, user=actor, message=message,
                expected_identity=mailbox_identity(connection),
            )
            item.intake, item.status = intake, 'captured'
            item.attempts += 1
            item.next_attempt_at, item.last_error_code = None, ''
            item.save()
        elif kind == 'discovery':
            for record in page['records']:
                folder, created = saved.folders.get_or_create(folder_id=record['id'])
                if record['removed']:
                    folder.is_removed = True
                elif created or folder.is_removed:
                    folder.is_removed, folder.cursor = False, ''
                    folder.last_synced_at, folder.next_attempt_at = None, now
                folder.save()
            saved.folder_cursor = _checkpoint(saved, page['next_link'] or page['delta_link'])
            saved.folder_discovery_due_at = now if page['next_link'] else now + timedelta(seconds=DISCOVERY_SECONDS)
            if page['delta_link']:
                saved.folder_discovery_completed_at = now
                saved.last_error_code = ''
            saved.save()
        else:
            folder = saved.folders.get(pk=record_id)
            for record in page['records']:
                # A folder tombstone may be a move. Keep the saved source and
                # any queued full-message read by its mailbox immutable ID.
                if record['removed']:
                    continue
                item, created = saved.items.get_or_create(message_id=record['id'])
                # An ID-only replay does not establish a new content revision.
                # Failed items remain retryable at their durable backoff time.
                if not created and item.status in {'skipped', 'unavailable'}:
                    item.status, item.next_attempt_at, item.last_error_code = 'pending', now, ''
                    item.save()
            folder.cursor = _checkpoint(saved, page['next_link'] or page['delta_link'], folder_id=folder_id)
            folder.next_attempt_at = now if page['next_link'] else now + timedelta(seconds=POLL_SECONDS)
            folder.consecutive_failures, folder.last_error_code = 0, ''
            if page['delta_link']:
                folder.last_synced_at = now
            folder.save()


def _retry_delay(exc, attempts):
    value = getattr(exc, 'retry_after', None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 86400:
        return max(1, value)
    return min(3600, 60 * 2 ** min(attempts, 6))


def _record_failure(connection_id, token, kind, record_id, exc):
    status = getattr(exc, 'status_code', 502)
    code = getattr(exc, 'code', '')
    if status in {401, 403} or code == 'authorization_required':
        raise SyncBlocked('authorization_required')
    if getattr(exc, 'retry_after_exceeds_limit', False):
        raise SyncBlocked('provider_unavailable')
    if isinstance(exc, (PermissionDenied, Http404)):
        raise SyncBlocked('authorization_required')
    if isinstance(exc, EmailCaptureConflict):
        raise SyncBlocked('configuration_changed')
    if code == 'unsupported_source':
        code = 'unsupported_message'
    if code not in SAFE_ERRORS:
        code = 'invalid_response' if isinstance(exc, SalesMailboxReadError) else 'internal_error'
    with _authorized(connection_id, token) as (_connection, state, _actor_user):
        now = timezone.now()
        retry_at = now + timedelta(seconds=_retry_delay(exc, state.consecutive_failures))
        if kind == 'item':
            item = state.items.get(pk=record_id)
            item.attempts += 1
            if getattr(exc, 'code', '') == 'not_incoming' or (
                status == 400 and str(exc) == 'Only confirmed incoming emails can be saved to intake.'
            ):
                item.status, item.next_attempt_at, item.last_error_code = 'skipped', None, ''
            elif status == 404:
                item.status, item.next_attempt_at, item.last_error_code = 'unavailable', None, 'source_unavailable'
            else:
                item.status, item.next_attempt_at, item.last_error_code = 'error', retry_at, code
            item.save()
            if item.status in {'skipped', 'unavailable'}:
                return False
        elif kind == 'folder':
            folder = state.folders.get(pk=record_id)
            folder.consecutive_failures += 1
            folder.next_attempt_at, folder.last_error_code = retry_at, code
            if code == 'checkpoint_expired':
                folder.cursor, folder.last_synced_at = '', None
            elif status == 404:
                folder.is_removed = True
            folder.save()
        else:
            state.folder_discovery_due_at = retry_at
            state.folder_discovery_completed_at = None
            if code == 'checkpoint_expired':
                state.folder_cursor = ''
        state.consecutive_failures += 1
        state.last_error_code = code
        # Throttling applies to this provider mailbox, not just a single item.
        throttled = status == 429 or code == 'throttled' or getattr(exc, 'retry_after', None) is not None
        if throttled:
            state.next_attempt_at = retry_at
        state.save()
        return throttled


def _finish(connection_id, token, *, blocked='', throttled=False):
    try:
        with _locked(connection_id, token) as (connection, state):
            now = timezone.now()
            if blocked:
                state.status, state.last_error_code, state.next_attempt_at = 'blocked', blocked, None
            elif not connection.enabled or not automation_available():
                state.status, state.next_attempt_at = 'paused', None
            else:
                try:
                    _actor(connection, state)
                except SyncBlocked as exc:
                    state.status, state.last_error_code, state.next_attempt_at = 'blocked', exc.code, None
                else:
                    pending = state.items.filter(status__in=['pending', 'error'])
                    folders = state.folders.filter(is_removed=False)
                    unavailable = state.items.filter(status='unavailable').exists()
                    errors = (unavailable or pending.filter(status='error').exists() or folders.exclude(last_error_code='').exists()
                              or bool(not state.folder_discovery_completed_at and state.last_error_code))
                    complete = bool(state.folder_discovery_completed_at and not folders.filter(last_synced_at__isnull=True).exists() and not pending.exists() and not errors)
                    if unavailable:
                        state.last_error_code = 'source_unavailable'
                    elif errors and not state.last_error_code:
                        failed = pending.filter(status='error').first()
                        failed = failed or folders.exclude(last_error_code='').first()
                        if failed:
                            state.last_error_code = failed.last_error_code
                    if complete and not state.initial_sync_completed_at:
                        state.initial_sync_completed_at = now
                    if throttled:
                        state.status = 'retrying'
                    else:
                        times = [state.folder_discovery_due_at]
                        for queryset in (folders, pending):
                            if queryset.filter(next_attempt_at__isnull=True).exists():
                                times.append(now)
                            value = queryset.aggregate(earliest=Min('next_attempt_at'))['earliest']
                            if value is not None:
                                times.append(value)
                        due_at = min((value or now for value in times), default=now + timedelta(seconds=POLL_SECONDS))
                        state.next_attempt_at = max(now, due_at)
                        work_due = due_at <= now
                        state.status = 'retrying' if errors else ('queued' if work_due or not complete else 'up_to_date')
                        if complete and not errors and not work_due:
                            state.last_successful_sync_at = now
                            state.last_error_code, state.consecutive_failures = '', 0
            state.lease_token = state.lease_expires_at = None
            state.save()
            return state.status
    except (SyncStopped, SalesMailboxConnection.DoesNotExist, SalesMailboxSyncState.DoesNotExist):
        return 'stopped'


def run_mailbox_sync(connection_id, *, max_steps=None, max_seconds=None):
    """Bounded work; a future Beat tick resumes SQL state after any process exit."""
    max_steps = max_steps if max_steps is not None else getattr(settings, 'SALES_MAILBOX_SYNC_MAX_STEPS', MAX_STEPS)
    max_seconds = max_seconds if max_seconds is not None else getattr(settings, 'SALES_MAILBOX_SYNC_WORK_SECONDS', RUN_SECONDS)
    token = _claim(connection_id)
    if token is None:
        return {'status': 'not_claimed'}
    started, steps, throttled, blocked, service = monotonic(), 0, False, '', None
    try:
        while steps < min(MAX_STEPS, max_steps) and monotonic() - started < min(RUN_SECONDS, max_seconds):
            work = _select_work(connection_id, token, steps)
            if work is None:
                break
            connection, state, user, kind, record_id, cursor = work
            if service is None:
                # Reuse the bounded run's app token; a new run obtains a new one.
                service = SalesMailboxSyncGraphService(connection)
            remaining = min(RUN_SECONDS, max_seconds) - (monotonic() - started)
            if remaining <= 0:
                break
            # Token acquisition and the message GET share the remaining budget.
            service.timeout = max(0.1, min(30, remaining / 2))
            try:
                _read_and_commit(connection, state, user, kind, record_id, cursor, token, service)
            except (SyncStopped, SyncBlocked, SoftTimeLimitExceeded):
                raise
            except Exception as exc:
                # No exception text/provider payload is stored or logged.
                throttled = _record_failure(connection_id, token, kind, record_id, exc)
            steps += 1
            if throttled:
                break
    except SyncStopped:
        return {'status': 'stopped', 'steps': steps}
    except SyncBlocked as exc:
        blocked = exc.code
    except SoftTimeLimitExceeded:
        # A partially processed page/item remains queued; never mark it done.
        pass
    return {'status': _finish(connection_id, token, blocked=blocked, throttled=throttled), 'steps': steps}


def due_mailbox_ids(*, limit=100):
    if not automation_available():
        return []
    now = timezone.now()
    return list(SalesMailboxSyncState.objects.filter(
        connection__enabled=True, connection__auth_mode='application',
    ).exclude(status__in=['paused', 'blocked']).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
    ).filter(
        Q(lease_token__isnull=True) | Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
    ).order_by('next_attempt_at', 'updated_at').values_list('connection_id', flat=True)[:limit])
