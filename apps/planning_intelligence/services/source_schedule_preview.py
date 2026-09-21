"""Read-only source activity preview, independent of the editable MDR scope.

Seeing a printed date does not require an accepted calendar or a calculated
network. Applying it to another document's deliverable does require an explicit
reviewed association. This projection deliberately does neither calculation nor
cross-document matching.
"""
from collections import Counter
from copy import deepcopy

from .document_plan import project_document_plan
from .source_date_read_model import source_date_fields


def document_schedule_summary(plan):
    activities = plan.get('activities') or []
    # Register fallback rows are scope, not evidence of an extracted schedule.
    activities = [row for row in activities
                  if (row.get('source_evidence') or {}).get('basis') != 'document_register']
    dated = [source_date_fields(row) for row in activities]
    register = plan.get('register_inventory') or []
    activity_ids = {row['id'] for row in activities}
    matched_register_count = sum(bool(activity_ids.intersection(row.get('schedule_activity_ids') or []))
                                 for row in register)
    return {
        'activity_count': len(activities),
        'duration_count': sum(row.get('duration_days') is not None for row in activities),
        'start_date_count': sum(row['source_start_date'] is not None for row in dated),
        'finish_date_count': sum(row['source_finish_date'] is not None for row in dated),
        'relationship_count': len(plan.get('logic_matrix') or []),
        'register_count': len(register),
        'matched_register_count': matched_register_count,
        'unmapped_register_count': len(register) - matched_register_count,
        'unmapped_schedule_count': len(activity_ids.intersection(plan.get('unmapped_source_schedule_activity_ids') or [])),
        'validation_counts': dict(Counter(row['code'] for row in plan.get('validation') or [])),
    }


def _validation_groups(plan):
    groups = {}
    for issue in plan.get('validation') or []:
        code = issue['code']
        group = groups.setdefault(code, {
            'code': code, 'message': issue.get('message', ''), 'count': 0,
            'severity': issue.get('severity', 'warning'), 'blocks': issue.get('blocks', []),
        })
        group['count'] += 1
    return list(groups.values())


def source_schedule_preview(project, *, offset=0, limit=100, search='', source_file_id=None, actor=None):
    """Parse saved extracted text only; never write or start an extraction job."""
    plan = project_document_plan(project)
    from django.http import Http404
    from .source_schedule_import import can_import_source, source_rows
    files = list(project.files.filter(is_deleted=False).order_by('pk').values(
        'id', 'original_filename', 'category', 'parse_status'))
    if source_file_id is not None and source_file_id not in {row['id'] for row in files}:
        raise Http404
    all_activities = source_rows(plan)
    counts = Counter(ref_id for row in all_activities
                     for ref_id in {ref.get('file_id') for ref in row.get('source_references') or []})
    for file in files:
        file['activity_count'] = counts[file['id']]
    activities = source_rows(plan, source_file_id)
    activity_ids = {row['id'] for row in activities}
    summary_plan = {**plan, 'activities': activities,
                    'logic_matrix': [row for row in plan.get('logic_matrix') or []
                                     if row.get('activity_id') in activity_ids]}
    summary = document_schedule_summary(summary_plan)
    if search:
        term = search.casefold()
        activities = [row for row in activities if term in str(row.get('source_activity_id') or '').casefold()
                      or term in row['name'].casefold()]
    total = len(activities)
    rows = []
    for row in activities[offset:offset + limit]:
        dates = source_date_fields(row)
        rows.append({
            'id': row['id'], 'title': row['name'], 'source_activity_id': row.get('source_activity_id'),
            'duration_days': row.get('duration_days'), 'duration_unit': row.get('duration_unit'),
            'is_milestone': row.get('is_milestone'),
            'printed_single_date': (row.get('source_values') or {}).get('printed_single_date'),
            'date_columns_status': (row.get('source_values') or {}).get('date_columns_status'),
            **{key: value for key, value in dates.items() if key in {
                'source_start_date', 'source_finish_date', 'source_start_status',
                'source_finish_status', 'source_date_status'}},
            'source_references': deepcopy(row.get('source_references') or []),
            'predecessors': deepcopy(row.get('predecessors') or []),
            'missing_fields': deepcopy(row.get('missing_fields') or []),
            'calendar_verified': False, 'total_float_days': None,
        })
    return {
        'project_id': project.pk, 'policy': 'source_document_preview', 'applied': False,
        'master_revision': project.master_schedule_revision,
        'can_import': bool(actor and can_import_source(project, actor)),
        'source_file_id': source_file_id,
        'calculation_available': False, 'summary': summary,
        'project_window': {
            'start_date': project.effective_date.isoformat() if project.effective_date else None,
            'finish_date': project.planned_end_date.isoformat() if project.planned_end_date else None,
        },
        'rows': rows, 'pagination': {
            'offset': offset, 'limit': limit, 'total': total, 'has_next': offset + limit < total,
        },
        'source_summaries': [{
            'title': row.get('title'), 'values': deepcopy(row.get('values') or {}),
            'source_references': deepcopy(row.get('source_references') or []),
        } for row in plan.get('source_summaries') or []],
        'extraction_reports': deepcopy(plan.get('extraction_reports') or []),
        'validation': _validation_groups(plan),
        'source_files': files,
    }
