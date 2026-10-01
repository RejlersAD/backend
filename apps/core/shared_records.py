"""Scoped review and atomic, retry-safe linking of existing domain records."""
import hashlib
import json
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from .shared_record_models import SharedRecordLinkCommand
from .shared_record_project import ProjectClientAdapter
from .shared_record_targets import active, require_target


class LinkConflict(APIException):
    status_code = 409
    default_detail = 'This record changed. Refresh it before reviewing the link again.'
    default_code = 'shared_record_conflict'


def digest(value):
    return hashlib.sha256(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def adapters():
    result = {'project_client': ProjectClientAdapter()}
    for app in ('finance', 'planning_intelligence', 'project_control', 'project_organizer'):
        if apps.is_installed(f'apps.{app}'):
            result.update(import_module(f'apps.{app}.shared_record_links').ADAPTERS)
    return result


def require_access(user, action='read'):
    if (not active(user) or not module_action_allowed(user, 'project_control', 'read')
            or not module_action_allowed(user, 'project_control', action)):
        raise PermissionDenied('You do not have access to review shared project records.')


def adapter_for(key):
    adapter = adapters().get(key)
    if adapter is None:
        raise NotFound('This record source is unavailable.')
    return adapter


def source_row(adapter, identifier, user, *, lock=False):
    queryset = adapter.queryset(user)
    try:
        scoped = queryset.filter(pk=identifier)
        if lock:
            # Access queries can use DISTINCT for membership joins. PostgreSQL
            # cannot lock DISTINCT results; lock physical rows selected by the
            # scoped IDs, then reload scope/annotations after any lock wait.
            rows = list(queryset.model._base_manager.filter(
                pk__in=scoped.order_by().values('pk'),
            ).select_for_update(of=('self',))[:2])
            if not rows:
                raise NotFound('This record is unavailable.')
            if len(rows) > 1:
                raise LinkConflict('This source identity requires duplicate reconciliation before linking.')
        # Some restored source tables have legacy duplicate physical identifiers.
        rows = list(scoped[:2])
    except (ValueError, TypeError, DjangoValidationError):
        raise NotFound('This record is unavailable.')
    if len(rows) > 1:
        raise LinkConflict('This source identity requires duplicate reconciliation before linking.')
    if not rows:
        raise NotFound('This record is unavailable.')
    return rows[0]


def version_token(adapter, row, user):
    return signing.dumps({'actor': user.pk, 'source': adapter.key, 'id': str(row.pk),
                          'version': digest(adapter.fingerprint(row))}, salt='shared-record-link-v1', compress=True)


def record_payload(adapter, row, user, *, history=False):
    result = {**adapter.describe(row, user), 'source_type': adapter.key, 'id': str(row.pk),
              'expected_token': version_token(adapter, row, user)}
    result['can_link'] = bool(result.get('can_link') and module_action_allowed(user, 'project_control', 'update'))
    if history:
        result['history'] = list(SharedRecordLinkCommand.objects.filter(
            source_type=adapter.key, source_id=str(row.pk),
        ).values('reason', 'created_at')[:10])
    return result


def verify_token(adapter, row, user, token):
    try:
        value = signing.loads(token, salt='shared-record-link-v1', max_age=3600)
    except signing.BadSignature:
        raise LinkConflict()
    if value != {'actor': user.pk, 'source': adapter.key, 'id': str(row.pk),
                 'version': digest(adapter.fingerprint(row))}:
        raise LinkConflict()


def recheck_replay_targets(row, user, targets):
    from .project_models import Project
    project = row if isinstance(row, Project) else getattr(row, 'enterprise_project', None)
    if project is None:
        parent = getattr(row, 'project', None)
        project = parent if isinstance(parent, Project) else getattr(parent, 'enterprise_project', None)
    if targets.get('project_id'):
        project = require_target(user, 'project', targets['project_id'])
    for kind in ('client', 'employee'):
        if targets.get(f'{kind}_id'):
            require_target(user, kind, targets[f'{kind}_id'], project=project)


@transaction.atomic
def link_record(source_type, identifier, user, *, request_id, expected_token, targets, reason):
    require_access(user, 'update')
    # Serialize request-key retries, including two commands for different sources.
    # Keep ordinary domain inserts referencing this user free to complete while
    # their project lock is held; only competing review commands must wait here.
    user = get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    require_access(user, 'update')
    adapter = adapter_for(source_type)
    initial = source_row(adapter, identifier, user)
    if hasattr(adapter, 'lock_scope'):
        adapter.lock_scope(initial, user, targets)
    row = source_row(adapter, identifier, user, lock=True)
    adapter.require_write(row, user)
    request_hash = digest({'source': source_type, 'id': str(row.pk), 'targets': targets, 'reason': reason})
    previous = SharedRecordLinkCommand.objects.filter(actor=user, request_id=request_id).first()
    if previous:
        if previous.request_hash != request_hash:
            raise LinkConflict('This request ID was already used for a different review.')
        recheck_replay_targets(row, user, targets)
        if digest(previous.after) != digest(adapter.fingerprint(row)):
            raise LinkConflict('The source changed after this review was saved. Refresh to review the current connection.')
        return {'record': record_payload(adapter, row, user, history=True), 'replayed': True}
    verify_token(adapter, row, user, expected_token)
    before = adapter.fingerprint(row)
    adapter.apply(row, user, targets)
    # Refresh sidecar/related caches as well as source fields before issuing token.
    row = source_row(adapter, identifier, user)
    SharedRecordLinkCommand.objects.create(
        actor=user, request_id=request_id, source_type=source_type, source_id=str(row.pk),
        request_hash=request_hash, before=before, after=adapter.fingerprint(row), reason=reason,
    )
    return {'record': record_payload(adapter, row, user, history=True), 'replayed': False}
