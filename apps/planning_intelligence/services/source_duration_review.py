"""Reconcile editable duration estimates with project-scoped uploaded evidence.

This is a pure review operation. Printed durations can be copied independently
of dates: copying a number does not establish the source calendar or its links.
Missing duration facts remain missing; effort, title classification, templates
and the project horizon are never used to manufacture a replacement duration.
"""
from collections import defaultdict
from copy import deepcopy
from math import isfinite

from .fixed_horizon_proposal import protected_work_ids
from .source_timing_constraints import source_timing_evidence


_ESTIMATED_SOURCES = {
    'proposed', 'template', 'default', 'estimated', 'estimate', 'ai',
    'workflow_template', 'missing_source', 'source_requirement', 'source_document',
    'milestone_definition',
}
_GENERATED_DATES = {'planned_start_date', 'planned_finish_date'}


def _duration(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number >= 0 else None


def _milestone(task):
    return bool(task.get('is_milestone') or task.get('activity_type') in {'start_milestone', 'finish_milestone'})


def _manual(task):
    """Unknown legacy provenance is retained for review, not silently overwritten."""
    if _duration(task.get('duration_days')) is None:
        return False
    if task.get('duration_confirmed') or task.get('duration_confirmed_at'):
        return True
    source = str(task.get('duration_source') or '').casefold()
    if source in {'planner', 'manual', 'user', 'confirmed', 'imported'}:
        return True
    if source in _ESTIMATED_SOURCES:
        return False
    return 'duration_days' not in (task.get('schedule_generated_fields') or [])


def _remove_generated_dates(task):
    generated = set(task.get('schedule_generated_fields') or [])
    removed = sorted(_GENERATED_DATES & generated)
    for key in removed:
        task[key] = None
    task['schedule_generated_fields'] = sorted(generated - _GENERATED_DATES - {'duration_days'})
    return removed


def _package_reviews(tasks, context):
    groups = defaultdict(list)
    for task in tasks:
        if task.get('parent_deliverable_id') is not None:
            groups[task['parent_deliverable_id']].append(task)
    # These are evidence-only identities, independent of the first-stage IDs.
    parents = [{'id': key, 'title': rows[0].get('deliverable') or ''} for key, rows in groups.items()]
    evidence = source_timing_evidence(parents, context)['matched_tasks'] if parents else {}
    reviews = []
    for key, rows in groups.items():
        durations = [_duration(row.get('duration_days')) for row in rows]
        complete = all(value is not None for value in durations)
        original = evidence.get(key) or {}
        reviews.append({
            'parent_deliverable_id': key, 'title': rows[0].get('deliverable') or '',
            'task_ids': [row['id'] for row in rows],
            'duration_complete': complete,
            'duration_days': sum(durations) if complete else None,
            'duration_basis': 'sum_of_activity_durations_not_elapsed_package_duration',
            'missing_duration_count': sum(value is None for value in durations),
            'source_original_duration_days': original.get('values', {}).get('original_duration_days'),
            'source_references': deepcopy(original.get('source_references') or []),
            'calendar_verified': False, 'relationships_verified': False,
        })
    return reviews


def reconcile_source_durations(tasks, context):
    """Return ``(tasks, audit_rows, summary, warnings)`` without mutating inputs.

    The caller supplies only the current project's uploads in ``context`` using
    ``proposal_context``'s file shape. Matching is uniquely exact (only case and
    whitespace are normalized). A source's IFT/IFM is never aliased to Final
    Issue. Missing, ambiguous, deleted and unparsed evidence cannot set timing.

    Started work and its upstream chain, historical work, and manual durations
    are preserved for human review. A general SOW company-review period is kept
    as a *requirement*, explicitly distinct from an activity-specific duration.
    No source dates, float, calendars or predecessor links are applied here.
    """
    output = deepcopy(tasks)
    evidence = source_timing_evidence(output, context)
    protected = protected_work_ids(output)
    rows = []
    counts = defaultdict(int)
    for task in output:
        before = deepcopy(task)
        match = evidence['matched_tasks'].get(task['id'])
        source_value = _duration((match or {}).get('values', {}).get('original_duration_days'))
        if source_value == 0 and not _milestone(task):
            # A zero-duration source activity may be a milestone. Changing the
            # activity type is a separate decision and cannot be inferred here.
            source_value = None
        if source_value is not None and _milestone(task) and source_value != 0:
            source_value = None
        historical = bool(task.get('is_historical') or task.get('is_baseline')
                          or task.get('baseline_id') or context.get('read_only')
                          or context.get('historical') or context.get('is_baseline'))
        protected_task = historical or task['id'] in protected
        manual = _manual(task)
        cleared = []
        duration_evidence = deepcopy(match) if match else None
        if protected_task or manual:
            status = 'started_unverified' if protected_task else 'manual_unverified'
            reason = ('Started, historical or upstream work retained; review the source comparison before changing its duration.'
                      if protected_task else 'Existing planner duration retained; it is not certified as a document duration.')
            if source_value is not None and source_value == _duration(task.get('duration_days')):
                reason += ' The retained duration agrees with the uniquely matched printed value.'
            elif source_value is not None:
                reason += f' The uploaded schedule states {source_value:g} days; no automatic change was made.'
        elif source_value is not None:
            task['duration_days'] = source_value
            task['duration_source'] = 'source_document'
            status = 'source_verified'
            reason = 'Duration copied from the uniquely matched uploaded activity row. Calendar, dates and relationships remain unverified.'
            duration_evidence.update(activity_specific=True, applied_fields=['original_duration_days'])
            cleared = _remove_generated_dates(task)
        elif _milestone(task) and _duration(task.get('duration_days')) == 0:
            task['duration_days'] = 0
            task['duration_source'] = 'milestone_definition'
            status = 'milestone_definition'
            reason = 'Explicit milestone definition retains zero duration; no uploaded duration has been claimed.'
            cleared = _remove_generated_dates(task)
        elif task.get('workflow_stage_code') == 'COMPANY_REVIEW' and evidence.get('review_requirement'):
            requirement = evidence['review_requirement']
            task['duration_days'] = None
            task['duration_source'] = 'source_requirement'
            status = 'requirement_needs_review'
            reason = 'SOW company-review allowance retained as evidence only. No activity-specific planned duration is supplied; confirm applicability.'
            duration_evidence = {
                'basis': 'source_requirement', 'activity_specific': False,
                'values': {'required_review_days': requirement['duration_days']},
                'source_references': deepcopy(requirement['source_references']),
                'applicability': requirement['applicability'],
                'calendar_verified': False, 'relationships_verified': False,
                'can_apply_to_cpm': False,
            }
            cleared = _remove_generated_dates(task)
        else:
            task['duration_days'] = None
            task['duration_source'] = 'missing_source'
            status = 'missing_source'
            reason = 'No unique activity-specific duration was found in the processed project uploads; the template estimate has been removed.'
            cleared = _remove_generated_dates(task)
        task['duration_review_status'] = status
        task['duration_review_reason'] = reason
        if protected_task or manual:
            # A new comparison is not permission to downgrade previously
            # verified input provenance or hide dates of work already started.
            task['duration_comparison_evidence'] = duration_evidence
        else:
            task['duration_evidence'] = duration_evidence
            task['duration_calendar_verified'] = False
        counts[status] += 1
        changed = before.get('duration_days') != task.get('duration_days') or before.get('duration_source') != task.get('duration_source')
        counts['changed_count'] += int(changed)
        counts['cleared_generated_date_count'] += len(cleared)
        rows.append({
            'task_id': task['id'], 'title': task.get('title') or '',
            'parent_deliverable_id': task.get('parent_deliverable_id'),
            'workflow_stage_code': task.get('workflow_stage_code'),
            'previous_duration_days': before.get('duration_days'),
            'duration_days': task.get('duration_days'), 'duration_source': task.get('duration_source'),
            'status': status, 'reason': reason, 'changed': changed,
            'source_original_duration_days': source_value,
            'source_references': deepcopy((duration_evidence or {}).get('source_references') or []),
            'cleared_generated_date_fields': cleared,
            'calendar_verified': bool(task.get('duration_calendar_verified')),
            'relationships_verified': False,
        })
    summary = {
        'policy': 'uploaded_document_durations_only', 'task_count': len(output),
        'source_document_count': counts['source_verified'],
        'source_requirement_count': counts['requirement_needs_review'],
        'missing_source_count': counts['missing_source'],
        'manual_unverified_count': counts['manual_unverified'],
        'started_unverified_count': counts['started_unverified'],
        'milestone_definition_count': counts['milestone_definition'],
        'changed_count': counts['changed_count'],
        'cleared_generated_date_count': counts['cleared_generated_date_count'],
        'exact_import_verified': False,
        'package_reviews': _package_reviews(output, context),
    }
    warnings = [message for message in evidence['warnings']
                if 'these printed values have not replaced the calculated schedule' not in message]
    if summary['source_document_count']:
        warnings.append('Only printed activity durations have been copied. The uploaded calendar, date precision and predecessor network remain unverified; this is not an exact schedule import.')
    if summary['missing_source_count']:
        warnings.append(f"{summary['missing_source_count']} activities have no unique source duration and remain unspecified. No template, effort conversion or guessed duration has been substituted.")
    if summary['source_requirement_count']:
        warnings.append(f"{summary['source_requirement_count']} company-review activities have a source review requirement but no activity-specific planned duration. The requirement is retained as evidence only; applicability requires review.")
    if summary['manual_unverified_count'] or summary['started_unverified_count']:
        warnings.append('Existing planner durations and work already started have been retained for source review, without certifying them as document-derived.')
    return output, rows, summary, list(dict.fromkeys(warnings))
