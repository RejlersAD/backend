"""Explicitly register discovered server project names in the canonical registry."""

from collections import Counter
import re
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as PathValidationError

from apps.core.project_models import Project
from apps.rbac.action_policy import module_action_allowed
from .models import ReplicaEntry, ReplicaScope, ReplicaSource
from .paths import included, normalize_path, path_key
from .permissions import is_replica_admin


def folder_project_identity(folder_name):
    """Use the existing Project Links numeric-prefix convention, exactly."""
    match = re.fullmatch(r'([0-9]{1,50})(?:[ _-]+(.*))?', folder_name)
    if not match:
        return '', folder_name
    return match.group(1), (match.group(2) or folder_name).strip()


def _candidate(scope, source, entries):
    row = {
        'scope_id': str(scope.pk), 'folder_name': scope.relative_path,
        'code': '', 'name': '', 'action': 'skip', 'reason': '', 'project_id': None,
    }
    try:
        normalized = normalize_path(scope.relative_path)
    except PathValidationError:
        row['reason'] = 'invalid_folder_path'
        return row
    if '/' in normalized or normalized != scope.relative_path:
        row['reason'] = 'not_a_root_folder'
    elif not included(source, scope.relative_path):
        row['reason'] = 'outside_source_scope'
    else:
        entry = entries.get(path_key(scope.relative_path))
        if not entry or entry.scope_id != scope.pk or not entry.is_directory or entry.parent_path:
            row['reason'] = 'current_root_folder_not_found'
        elif entry.status in {'missing', 'failed'} or entry.error:
            row['reason'] = 'folder_missing_or_failed'
        else:
            row['code'], row['name'] = folder_project_identity(scope.relative_path)
            if not row['code']:
                row['reason'] = 'no_numeric_project_code'
            elif not row['name'] or len(row['name']) > Project._meta.get_field('name').max_length:
                row['reason'] = 'invalid_project_name'
    return row


@transaction.atomic
def register_server_projects(*, source_id, actor, apply=False):
    """Preview by default; applying creates only project identity and scope links.

    Locks agree with the source/scopes order used by discovery. An existing
    project's business fields and a folder's publication flag are never edited.
    """
    if not is_replica_admin(actor) or not module_action_allowed(actor, 'project_control', 'create'):
        raise PermissionDenied('Registration requires a file server administrator with Project Control create permission.')
    try:
        source_id = UUID(str(source_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('A valid source UUID is required.') from exc
    sources = ReplicaSource.objects.select_for_update() if apply else ReplicaSource.objects
    try:
        source = sources.get(pk=source_id)
    except ReplicaSource.DoesNotExist as exc:
        raise ValidationError('The file server source was not found.') from exc
    scopes = ReplicaScope.objects.filter(source=source).order_by('relative_path', 'pk')
    if apply:
        scopes = scopes.select_for_update()
    scopes = list(scopes)
    entries = {
        entry.path_key: entry for entry in ReplicaEntry.objects.filter(source=source, parent_path='')
        .only('path_key', 'scope_id', 'is_directory', 'parent_path', 'status', 'error')
    }
    rows = [_candidate(scope, source, entries) for scope in scopes]
    code_counts = Counter(row['code'] for row in rows if not row['reason'])
    counts = {key: 0 for key in (
        'folders', 'created', 'would_create', 'reused', 'mapped', 'would_map', 'already_linked', 'skipped',
    )}
    for scope, row in zip(scopes, rows):
        counts['folders'] += 1
        if not row['reason'] and code_counts[row['code']] > 1:
            row['reason'] = 'duplicate_folder_project_code'
        if row['reason']:
            counts['skipped'] += 1
            continue

        projects = Project.objects.select_for_update() if apply else Project.objects
        mapped = projects.filter(pk=scope.project_id).first() if scope.project_id else None
        existing = projects.filter(code=row['code']).first()
        if mapped and (mapped.is_deleted or mapped.code != row['code']):
            row['reason'] = 'mapped_project_deleted' if mapped.is_deleted else 'conflicting_scope_mapping'
        elif scope.project_id and mapped is None:
            row['reason'] = 'mapped_project_not_found'
        elif existing and existing.is_deleted:
            row['reason'] = 'project_code_is_deleted'
        elif mapped and existing and mapped.pk != existing.pk:
            row['reason'] = 'conflicting_project_code'
        if row['reason']:
            counts['skipped'] += 1
            continue

        project = mapped or existing
        if project is None and apply:
            # get_or_create handles another source registering the same numeric
            # code concurrently; only the winner's new project receives defaults.
            project, created = projects.get_or_create(code=row['code'], defaults={
                'name': row['name'], 'owner': actor, 'status': 'planning',
                'custom_fields': {
                    'control_setup': {'operational_status_confirmed': False},
                    'registration_origin': {
                        'type': 'file_server_folder', 'source_id': str(source.pk),
                        'scope_id': str(scope.pk), 'folder_name': scope.relative_path,
                        'registered_by_id': str(actor.pk),
                        'registered_at': timezone.now().isoformat(),
                    },
                },
            })
            if project.is_deleted:
                row['reason'] = 'project_code_is_deleted'
                counts['skipped'] += 1
                continue
        else:
            created = project is None

        if project:
            row['project_id'] = str(project.pk)
            row['name'] = project.name
        if created:
            row['action'] = 'create'
            counts['created' if apply else 'would_create'] += 1
        elif scope.project_id == project.pk:
            row['action'] = 'already_linked'
            counts['already_linked'] += 1
            continue
        else:
            row['action'] = 'reuse'
            counts['reused'] += 1
        counts['mapped' if apply else 'would_map'] += 1
        if apply:
            scope.project = project
            scope.save(update_fields=['project'])

    return {
        'mode': 'apply' if apply else 'dry_run',
        'source_id': str(source.pk), 'source_name': source.name,
        'actor_id': str(actor.pk), 'counts': counts, 'rows': rows,
    }
