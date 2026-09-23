"""Project activity identifiers, separate from WBS positions and source IDs."""
from copy import deepcopy
import re


_PREFIX = re.compile(r'^[A-Z0-9][A-Z0-9_-]{0,39}$')


def project_activity_prefix(project):
    """Choose an initial convention only; the registry owns it thereafter."""
    phase = str(getattr(project, 'phase', '') or '').strip().upper()
    name = str(getattr(project, 'name', '') or '').upper()
    if re.search(r'\bFEED\b', phase or name):
        phase = 'FEED'
    else:
        phase = re.sub(r'[^A-Z0-9]+', '-', phase).strip('-')[:20] or 'PRJ'
    return f'{phase}-REQ'


def assign_activity_identifiers(tasks, registry=None, *, prefix='FEED-REQ'):
    """Allocate once, retaining deleted IDs so inserts cannot recycle them.

    Callers pass only server-owned saved metadata. Internal task IDs, source
    activity IDs and relationship endpoints are never changed by this function.
    """
    result = deepcopy(registry or {})
    prefix = result.get('prefix') or prefix
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ValueError('Use an uppercase alphanumeric activity ID prefix with hyphens or underscores.')
    allocated = dict(result.get('allocated') or {})
    owners = {}
    for key, value in allocated.items():
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError('The saved activity ID register contains an invalid identifier.')
        folded = value.casefold()
        if folded in owners and owners[folded] != str(key):
            raise ValueError('Every activity must have a unique planning ID.')
        owners[folded] = str(key)
    task_ids = [str(task['id']) for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError('Every activity must have a unique internal identifier.')
    # Existing IDs predate some registries. Seed them before allocating anything
    # so a reordered or newly inserted row cannot claim an existing identifier.
    for task in tasks:
        key = str(task['id'])
        existing = task.get('planning_activity_id') or allocated.get(key)
        if not existing:
            continue
        if not isinstance(existing, str) or not existing or len(existing) > 64:
            raise ValueError('The saved activity ID is invalid.')
        if allocated.get(key) and allocated[key] != existing:
            raise ValueError('The activity ID conflicts with its saved identifier.')
        folded = existing.casefold()
        if folded in owners and owners[folded] != key:
            raise ValueError('Every activity must have a unique planning ID.')
        allocated[key], owners[folded] = existing, key
    occupied = set(owners)
    occupied.update(str(task[field]).casefold() for task in tasks
                    for field in ('source_activity_id', 'document_number', 'external_id') if task.get(field))
    matcher = re.compile(re.escape(prefix) + r'-(\d+)$', re.I)
    suffixes = [int(match.group(1)) for value in occupied if (match := matcher.fullmatch(value))]
    previous_next = result.get('next_sequence') or 10
    if not isinstance(previous_next, int) or isinstance(previous_next, bool) or previous_next < 10:
        raise ValueError('The saved activity ID sequence is invalid.')
    next_sequence = max(previous_next, (max(suffixes, default=0) // 10 + 1) * 10)
    for task in tasks:
        key = str(task['id'])
        if key not in allocated:
            while (candidate := f'{prefix}-{next_sequence:04d}').casefold() in occupied:
                next_sequence += 10
            if len(candidate) > 64:
                raise ValueError('The activity ID sequence exceeds the supported length.')
            allocated[key] = candidate
            occupied.add(candidate.casefold())
            next_sequence += 10
        task['planning_activity_id'] = allocated[key]
    return {'version': 1, 'prefix': prefix, 'increment': 10,
            'next_sequence': next_sequence, 'allocated': allocated}
