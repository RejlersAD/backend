"""A source-relative path has the same meaning on Windows and the backend."""
import hashlib
import json

from rest_framework.exceptions import ValidationError


def normalize_path(value, allow_empty=False):
    if not isinstance(value, str):
        raise ValidationError('A relative path must be text.')
    value = value.replace('\\', '/')
    if not value and allow_empty:
        return ''
    parts = value.split('/')
    if not value or len(value) > 2048 or any(
        part in ('', '.', '..') or part.endswith((' ', '.'))
        or any(ord(c) < 32 or c in ':*?"<>|' for c in part)
        for part in parts
    ):
        raise ValidationError('Use a source-relative path without traversal, drive letters, or special Windows characters.')
    return '/'.join(parts)


def path_key(path):
    return hashlib.sha256(path.casefold().encode('utf-8')).hexdigest()


def within(path, parent):
    path, parent = path.casefold(), parent.casefold()
    return path == parent or path.startswith(parent + '/')


def included(source, path):
    if any(within(path, excluded) for excluded in source.excluded_paths):
        return False
    if not source.included_paths:
        return '/' not in path
    # Ancestors are needed to navigate nested selected folders.
    return any(within(path, selected) or within(selected, path) for selected in source.included_paths)


def included_entries_query(source):
    """SQL equivalent of included(), for bounded-memory catalogue operations."""
    from django.db.models import Q
    if source.included_paths:
        allowed = Q(pk__in=[])
        for selected in source.included_paths:
            selected = selected.casefold()
            allowed |= Q(normalized_path=selected) | Q(normalized_path__startswith=selected + '/')
            parts = selected.split('/')
            for end in range(1, len(parts)):
                allowed |= Q(normalized_path='/'.join(parts[:end]))
    else:
        allowed = Q(parent_path='')
    for excluded in source.excluded_paths:
        excluded = excluded.casefold()
        allowed &= ~(Q(normalized_path=excluded) | Q(normalized_path__startswith=excluded + '/'))
    return allowed


def config_hash(source):
    payload = {key: getattr(source, key) for key in (
        'root_path', 'included_paths', 'excluded_paths', 'mode', 'enabled', 'max_file_size_mb',
    )}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
