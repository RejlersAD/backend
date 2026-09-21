"""Invalidate obsolete draft evidence without reassigning work or guessing links."""
from copy import deepcopy
import hashlib


_PLANNER = {'planner', 'manual', 'user', 'confirmed'}
_SOURCE = {'source', 'source_document', 'source_requirement', 'document', 'imported', 'extracted'}
_TIMING = ('duration_days', 'duration_unit', 'duration_source', 'planned_start_date', 'planned_finish_date')


def _value(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _current_versions(files):
    versions = {}
    for source in files:
        identifier = _value(source, 'id')
        text = _value(source, 'extracted_text')
        if identifier is None or _value(source, 'is_deleted', False) or _value(source, 'parse_status') != 'done' or not text:
            continue
        versions[str(identifier)] = (hashlib.sha256(text.encode('utf-8')).hexdigest(), _value(source, 'project_id'))
    return versions


def _references_current(references, versions):
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, dict):
            return False
        current = versions.get(str(reference.get('file_id')))
        locator = reference.get('locator') or {}
        if not isinstance(locator, dict):
            return False
        hashes = [value for value in (reference.get('extracted_text_sha256'), locator.get('extracted_text_sha256')) if value]
        if current is None or not hashes or any(value != current[0] for value in hashes):
            return False
        if reference.get('project_id') is not None and str(reference['project_id']) != str(current[1]):
            return False
    return True


def _source_relationship(link):
    origin = link.get('source')
    if origin in _PLANNER | {'workflow_template'}:
        return False
    return bool(origin in _SOURCE or link.get('status') in _SOURCE or link.get('source_references'))


def _archive(row, entry):
    history = row.setdefault('source_evidence_history', [])
    if entry not in history:
        history.append(deepcopy(entry))


def invalidate_stale_source_evidence(rows, files):
    """Mutate draft rows and return the number whose current evidence changed.

    A current source requires a captured extracted-text hash and the same active,
    parsed file. Unversioned citations remain historical evidence, never current
    proof. This operation does not change IDs, assignments, actual work, source
    scope, or employee history. Explicit planner values remain planner values.
    """
    versions = _current_versions(files)
    affected = 0
    for row in rows:
        snapshot = {key: deepcopy(row.get(key)) for key in _TIMING}
        stale, current = [], []
        for key in ('source_evidence', 'duration_evidence'):
            evidence = row.get(key)
            if not isinstance(evidence, dict) or not evidence:
                continue
            if _references_current(evidence.get('source_references'), versions):
                current.append(evidence)
            else:
                stale.append(evidence)
                _archive(row, {'kind': 'timing', 'field': key, 'evidence': evidence, 'previous_values': snapshot})
                row.pop(key, None)
        # Older drafts sometimes store source values with only a row citation.
        source_values = row.get('duration_source') in _SOURCE or row.get('date_authority') in _SOURCE
        if source_values and not stale and not current and not _references_current(row.get('source_references'), versions):
            _archive(row, {'kind': 'timing', 'field': 'source_references',
                           'source_references': row.get('source_references') or [], 'previous_values': snapshot})
            stale.append({'values': {}, 'source_references': row.get('source_references') or []})

        def supported(field, value):
            return value is not None and any((item.get('values') or {}).get(field) == value for item in current)

        if stale:
            if row.get('duration_source') in _SOURCE and not supported('original_duration_days', row.get('duration_days')):
                row.update(duration_days=None, duration_unit=None, duration_source='missing_source')
                row.pop('duration_confirmed', None)
                row.pop('duration_confirmed_at', None)
            for endpoint in ('start', 'finish'):
                key = f'planned_{endpoint}_date'
                origin = row.get(key + '_source') or row.get(f'{endpoint}_date_source') or row.get('date_authority') or row.get('date_source')
                inherited = any((item.get('values') or {}).get(key) == row.get(key) and row.get(key) is not None for item in stale)
                if origin not in _PLANNER and (origin in _SOURCE or inherited or key in (row.get('schedule_generated_fields') or [])):
                    if not supported(key, row.get(key)):
                        row[key] = None
            row.update(duration_calendar_verified=False, duration_review_status='requires_review',
                       duration_review_reason='Source evidence is outdated, unavailable or unversioned. Review the current documents.')
            for key in ('date_authority', 'date_source'):
                if row.get(key) in _SOURCE:
                    row[key] = 'source_unverified'
            for key in ('source_start_date', 'source_finish_date', 'source_start_status', 'source_finish_status',
                        'source_date_status', 'source_date_references', 'source_date_evidence'):
                row.pop(key, None)

        removed, details = set(), []
        for link in row.get('dependency_details') or []:
            if _source_relationship(link) and not _references_current(link.get('source_references'), versions):
                removed.add(link['task_id'])
                _archive(row, {'kind': 'relationship', 'field': 'dependency_details', 'evidence': link})
            else:
                details.append(link)
        rationales = {}
        for predecessor, rationale in (row.get('dependency_rationales') or {}).items():
            if isinstance(rationale, dict) and _source_relationship(rationale) and not _references_current(rationale.get('source_references'), versions):
                removed.add(predecessor)
                _archive(row, {'kind': 'relationship', 'field': 'dependency_rationales',
                               'predecessor_id': predecessor, 'evidence': rationale})
            else:
                rationales[predecessor] = rationale
        retained = {link['task_id'] for link in details} | {
            key for key, rationale in rationales.items() if isinstance(rationale, dict)
            and (rationale.get('source') in _PLANNER | {'workflow_template'}
                 or _references_current(rationale.get('source_references'), versions))}
        if row.get('dependency_status') in _SOURCE:
            for predecessor in row.get('depends_on') or []:
                if predecessor not in retained | removed and not _references_current(row.get('source_references'), versions):
                    removed.add(predecessor)
                    _archive(row, {'kind': 'relationship', 'field': 'depends_on', 'predecessor_id': predecessor,
                                   'source_references': row.get('source_references') or []})
        if removed:
            row['depends_on'] = [key for key in row.get('depends_on') or [] if key not in removed or key in retained]
            row['dependency_details'] = details
            row['dependency_rationales'] = {key: value for key, value in rationales.items()
                                          if key not in removed or key in retained}
            row.update(dependency_status='not_specified', sequence_review_required=True,
                       dependency_review_reason='Obsolete document relationship evidence was withdrawn. Review the current source sequence.')

        if stale or removed:
            affected += 1
            row['source_evidence_review'] = {
                'status': 'requires_review', 'code': 'stale_source_evidence',
                'message': 'Previously used document evidence no longer has a verified current source version.',
                'blocks': ['calculation', 'approval'],
            }
            row.update(calculated=False, calculation_basis=None, is_critical=None,
                       total_float_days=None, free_float_days=None,
                       early_start=None, early_finish=None, late_start=None, late_finish=None)
            row.pop('summary', None)
    return affected
