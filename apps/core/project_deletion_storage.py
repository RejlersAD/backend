"""Retry exact file cleanup after a recorded project deletion has committed.

The deletion event is the durable work manifest. It is never updated. Separate
audit events record each completed or failed item; a retry rechecks surviving
references and storage, without relying on an in-process callback surviving.
"""
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath

from django.apps import apps
from django.db import connections, models, transaction

from apps.rbac.models import AuditLog


DELETE_COMMAND = 'permanently_delete_project'
CLEANUP_COMMAND = 'cleanup_deleted_project_files'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _storage_fingerprint(storage):
    # Bind an exact storage namespace without storing credentials or full paths.
    bucket = getattr(storage, 'bucket_name', None)
    return _digest({
        'class': f'{storage.__class__.__module__}.{storage.__class__.__qualname__}',
        'bucket': bucket,
        'location': str(getattr(storage, 'location', '')),
        'endpoint': str(getattr(storage, 'endpoint_url', '')) if bucket else '',
    })


def _file_fields(model):
    return [field for field in model._meta.concrete_fields if isinstance(field, models.FileField)]


def collect_project_files(collector):
    """Capture FileFields from the full ORM deletion closure, including fast deletes."""
    entries = {}

    def add(model, values):
        fields = _file_fields(model)
        for row in values:
            for field in fields:
                value = row[field.attname] if isinstance(row, dict) else getattr(row, field.attname)
                name = str(getattr(value, 'name', value) or '')
                if not name:
                    continue
                entry = {'model': model._meta.label, 'field': field.name, 'name': name,
                         'storage_fingerprint': _storage_fingerprint(field.storage)}
                entries[_digest(entry)] = entry

    for model, rows in collector.data.items():
        if _file_fields(model):
            add(model, rows)
    for queryset in collector.fast_deletes:
        fields = _file_fields(queryset.model)
        if fields:
            add(queryset.model, queryset.values(*(field.attname for field in fields)).iterator())
    return [entries[key] for key in sorted(entries)]


def _safe_name(name):
    return (isinstance(name, str) and bool(name.strip()) and '\\' not in name
            and not any(ord(char) < 32 for char in name)
            and not PurePosixPath(name).is_absolute() and not PureWindowsPath(name).drive
            and not any(part in {'', '.', '..'} for part in name.split('/')))


def _surviving_reference(name, *, using):
    # Conservatively retain an exact key referenced by ANY installed FileField,
    # including archived rows and differently configured storage instances.
    # Unknown storage equivalence must never remove another record's bytes.
    for model in apps.get_models():
        if model._meta.proxy or not model._meta.managed:
            continue
        for field in _file_fields(model):
            if model._base_manager.using(using).filter(**{field.name: name}).exists():
                return True
    return False


def _record(audit, key, outcome, *, using, success, trigger):
    return AuditLog.objects.using(using).create(
        user_id=audit.user_id, user_email=audit.user_email,
        action='file_delete', resource_type='ProjectFileCleanup', resource_id=audit.pk,
        resource_repr=audit.resource_repr, success=success,
        error_message='' if success else 'Project file cleanup remains pending; retry the recorded deletion.',
        metadata={'command': CLEANUP_COMMAND, 'deletion_audit_id': str(audit.pk),
                  'entry_key': key, 'outcome': outcome, 'trigger': trigger},
    )


def _cleanup_entry(entry, *, using):
    code = 'invalid_manifest_entry'
    try:
        # A failed reference query must roll back its savepoint before a durable
        # failure event is written (in particular on PostgreSQL).
        with transaction.atomic(using=using):
            if not isinstance(entry, dict) or not _safe_name(entry.get('name')):
                raise ValueError('Invalid file entry.')
            model = apps.get_model(entry['model'])
            field = model._meta.get_field(entry['field'])
            if not isinstance(field, models.FileField):
                raise ValueError('Not a file field.')
            storage = field.storage
            if entry.get('storage_fingerprint') != _storage_fingerprint(storage):
                code = 'storage_configuration_changed'
                raise ValueError('Storage changed.')
            name = entry['name']
            code = 'reference_check_failed'
            if _surviving_reference(name, using=using):
                return 'preserved_shared', None
            code = 'storage_cleanup_failed'
            if not storage.exists(name):
                return 'already_absent', None
            storage.delete(name)
            if storage.exists(name):
                raise OSError('Storage still contains the object.')
            return 'deleted', None
    except Exception:
        # Provider exception text may contain signed URLs or credentials.
        return None, code


def cleanup_project_files(audit_id, *, using='default', trigger='project_delete'):
    """Return truthful cleanup status; never operate on uncommitted deletion.

    PostgreSQL row locks serialize retries for one manifest. If storage succeeds
    but recording success fails, retry observes the absent object and records it
    without deleting it again. Cross-system atomic delivery is not claimed.
    """
    if connections[using].in_atomic_block:
        raise ValueError('Project file cleanup requires a committed deletion.')
    result = {'completed': False, 'deleted': 0, 'preserved_shared': 0,
              'already_absent': 0, 'remaining': 0, 'failures': []}
    with transaction.atomic(using=using):
        audit = AuditLog.objects.using(using).select_for_update().get(
            pk=audit_id, action='delete', resource_type='Project', success=True,
        )
        metadata = audit.metadata or {}
        entries = metadata.get('files')
        if (metadata.get('command') != DELETE_COMMAND or not isinstance(entries, list)
                or type(metadata.get('project_id')) is not int
                or not (audit.changes.get('after') or {}).get('permanently_deleted')):
            raise ValueError('This audit does not authorize project file cleanup.')
        project_model = apps.get_model('core', 'Project')
        if project_model._base_manager.using(using).filter(pk=metadata['project_id']).exists():
            raise ValueError('The recorded project still exists; file cleanup was not performed.')
        completed = {
            event['entry_key']: event['outcome']
            for event in AuditLog.objects.using(using).filter(
                action='file_delete', resource_type='ProjectFileCleanup', resource_id=audit.pk,
                success=True, metadata__command=CLEANUP_COMMAND,
            ).values_list('metadata', flat=True)
            if event.get('entry_key') and event.get('outcome') in {'deleted', 'preserved_shared', 'already_absent'}
        }
        seen = set()
        for entry in entries:
            key = _digest(entry)
            if key in seen:
                continue
            seen.add(key)
            outcome = completed.get(key)
            if outcome:
                result[outcome] += 1
                continue
            outcome, code = _cleanup_entry(entry, using=using)
            if code:
                _record(audit, key, code, using=using, success=False, trigger=trigger)
                result['failures'].append({'entry_key': key, 'code': code})
                result['remaining'] += 1
                continue
            _record(audit, key, outcome, using=using, success=True, trigger=trigger)
            result[outcome] += 1
        result['completed'] = result['remaining'] == 0
    return result
