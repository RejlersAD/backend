"""Read-only comparison with saved document evidence, never a schedule certification.

Register titles can be reconciled from parsed source rows. A document upload, a
planner-entered date, or a calculated schedule does not prove that an original
schedule's calendars, relationships and dates have been imported and validated.
"""
from collections import defaultdict
from copy import deepcopy
from pathlib import PurePosixPath
import re

from ..models import ScheduleVersion
from .reference_schedule_text import parse_reference_schedule_text
from .register_rows import extract_legacy_register_rows, extract_register_rows
from .simple_schedule_proposal import source_constraints


_REGISTER_CATEGORIES = {'mdr', 'eddr'}
_NATIVE_SCHEDULE_EXTENSIONS = {'.xer', '.xml', '.mpp', '.mpx'}


def _value(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _safe_file(item):
    return {
        'id': _value(item, 'id', _value(item, 'pk')),
        'name': _value(item, 'original_filename') or _value(item, 'filename') or _value(item, 'name') or '',
        'category': _value(item, 'category') or '',
    }


def _has_schedule_table_header(text):
    """Find adjacent schedule columns, not isolated scheduling words in prose."""
    for match in re.finditer(r'\bActivity\s+ID\b', text or '', re.I):
        header = text[max(0, match.start() - 100):match.end() + 500]
        if all(re.search(pattern, header, re.I) for pattern in (
            r'\bActivity\s+Name\b', r'\bDuration\b', r'\bStart\b', r'\bFinish\b',
        )):
            return True
    return False


def reference_schedule_files(files):
    """Identify candidate reference uploads without opening bytes or trusting text."""
    result = []
    for item in files:
        if _value(item, 'is_deleted', False):
            continue
        safe = _safe_file(item)
        extension = PurePosixPath(safe['name'].replace('\\', '/')).suffix.lower()
        text = _value(item, 'extracted_text') or _value(item, 'text') or ''
        if (safe['category'] == 'reference_schedule' or extension in _NATIVE_SCHEDULE_EXTENSIONS
                or (safe['category'] != 'output_schedule_sample' and _has_schedule_table_header(text))):
            result.append(safe)
    return result


def reference_schedule_blocker(files):
    """Expose unsupported reference imports for callers' existing action guards."""
    references = reference_schedule_files(files)
    if not references:
        return None
    return {
        'code': 'reference_schedule_not_imported',
        'message': ('A reference schedule is uploaded, but its activity dates, calendars and '
                    'relationships have not been imported and validated. Uploaded text alone '
                    'cannot establish an exact schedule match.'),
        'files': references,
    }


def _printed_schedules(files):
    """Summarize cached printed evidence without importing or opening originals.

    Full table rows are deliberately excluded from the regular planning read
    model. The printed summary is separate from the current calculated plan;
    neither missing source values nor current values are filled from the other.
    """
    summaries = []
    for source in files:
        if not reference_schedule_files([source]):
            continue
        if _value(source, 'parse_status') != 'done':
            parsed = {
                'status': 'not_parsed',
                'issues': [{'code': 'file_not_parsed',
                            'message': 'Saved document text is not ready for a printed-table comparison.'}],
            }
        else:
            parsed = parse_reference_schedule_text(_value(source, 'extracted_text') or '')
        original = parsed.get('project_summary')
        summary = None if original is None else {
            key: deepcopy(original.get(key)) for key in (
                'title', 'original_duration_days', 'planned_start_date',
                'planned_finish_date', 'total_float_days', 'source_locator',
                'date_columns_status',
            )
        }
        if original and 'printed_single_date' in original:
            summary['printed_single_date'] = original['printed_single_date']
        issue_groups = {}
        for issue in parsed.get('issues') or []:
            code = issue.get('code') or 'unknown'
            group = issue_groups.setdefault(code, {
                'code': code, 'message': issue.get('message') or '',
                'count': 0, 'source_locators': [],
            })
            group['count'] += 1
            locator = issue.get('source_locator')
            if locator and len(group['source_locators']) < 3:
                group['source_locators'].append(deepcopy(locator))
        summaries.append({
            **_safe_file(source), 'status': parsed['status'], 'project_summary': summary,
            'row_count': parsed.get('row_count', 0),
            'activity_count': parsed.get('activity_count', 0),
            'deliverable_count': parsed.get('deliverable_count', 0),
            'page_count': parsed.get('page_count'),
            'issues': list(issue_groups.values())[:12],
            'issue_count': len(parsed.get('issues') or []),
            'issues_truncated': len(issue_groups) > 12,
            'extraction_basis': 'saved_text', 'title_geometry_verified': False,
            'logic_verified': False, 'calendar_verified': False,
        })
    return summaries


def _source_keys(reference):
    if not isinstance(reference, dict):
        return []
    file_id = reference.get('file_id') or reference.get('source_file_id')
    if file_id is None:
        return []
    locator = reference.get('locator') or reference.get('source_locator') or {}
    if not isinstance(locator, dict):
        return []
    prefix = str(file_id), str(locator.get('sheet') or '')
    return [(*prefix, field, str(locator[field]))
            for field in ('row', 'line', 'register_item') if locator.get(field) is not None]


def _task_row(task):
    return {
        'id': task.get('id'), 'code': task.get('activity_code') or task.get('document_number') or '',
        'title': task.get('title') or '', 'discipline': task.get('discipline') or '',
        'source_references': deepcopy(task.get('source_references') or []),
    }


def _register_rows(files):
    expected, unparsed, unrecognized = [], [], []
    registers = [item for item in files if item.category in _REGISTER_CATEGORIES]
    for source in registers:
        if source.parse_status != 'done':
            unparsed.append({**_safe_file(source), 'parse_status': source.parse_status})
            continue
        text = source.extracted_text or ''
        rows = extract_register_rows(text) or extract_legacy_register_rows(text)
        if not rows:
            unrecognized.append(_safe_file(source))
        for index, row in enumerate(rows):
            locator = {**row.get('source_locator', {})}
            if row.get('sheet'):
                locator['sheet'] = row['sheet']
            if row.get('register_item') is not None:
                locator['register_item'] = row['register_item']
            if row.get('row_number') is not None:
                locator['row'] = row['row_number']
            expected.append({
                'id': f'register:{source.pk}:{index + 1}',
                'code': row.get('document_number') or '',
                'title': row['original_title'], 'discipline': row['discipline'],
                'source_references': [{
                    'file_id': source.pk, 'filename': source.original_filename,
                    'category': source.category, 'locator': locator,
                }],
            })
    return expected, registers, unparsed, unrecognized


def _reconcile_register(files, tasks):
    expected, sources, unparsed, unrecognized = _register_rows(files)
    expected_by_key = defaultdict(list)
    for index, row in enumerate(expected):
        for key in _source_keys(row['source_references'][0]):
            expected_by_key[key].append(index)

    used, matched, changed, extra = set(), [], [], []
    # Attribute each source-linked row before consuming title-only legacy rows.
    # Otherwise a manually added duplicate could conceal an edited source row.
    indexed = list(enumerate(tasks))
    indexed.sort(key=lambda pair: not any(_source_keys(ref) for ref in pair[1].get('source_references') or []))
    for _task_index, task in indexed:
        actual = _task_row(task)
        keys = [key for ref in actual['source_references'] for key in _source_keys(ref)]
        candidates = []
        for key in keys:
            candidates = [index for index in expected_by_key.get(key, []) if index not in used]
            if candidates:
                break
        if not keys:
            candidates = [index for index, row in enumerate(expected) if index not in used
                          and row['title'] == actual['title'] and row['discipline'] == actual['discipline']]
        if not candidates:
            extra.append(actual)
            continue
        index = next((i for i in candidates if expected[i]['title'] == actual['title']
                      and expected[i]['discipline'] == actual['discipline']), candidates[0])
        used.add(index)
        original = expected[index]
        if original['title'] == actual['title'] and original['discipline'] == actual['discipline']:
            matched.append(index)
        else:
            changed.append({
                **actual, 'expected_id': original['id'], 'expected_title': original['title'],
                'actual_title': actual['title'], 'expected_discipline': original['discipline'],
                'expected_source_references': original['source_references'],
            })
    missing = [row for index, row in enumerate(expected) if index not in used]
    status = ('missing' if not sources else 'incomplete' if unparsed or unrecognized
              else 'mismatch' if missing or extra or changed else 'matched')
    return {
        'status': status, 'expected_count': len(expected), 'matched_count': len(matched),
        'missing': missing, 'extra': extra, 'changed': changed,
        'files': [_safe_file(item) for item in sources],
        'unparsed_files': unparsed, 'unrecognized_files': unrecognized,
        'comparison': 'Exact titles and disciplines; repeated source rows remain separate.',
    }


def _calendar_status(project, state):
    calendar = None
    if state.get('version_id'):
        version = ScheduleVersion.objects.filter(
            pk=state['version_id'], schedule__project=project, schedule__is_deleted=False,
            is_deleted=False,
        ).select_related('schedule__default_calendar').first()
        if version:
            calendar = version.schedule.default_calendar
    if calendar is None:
        schedule = project.schedules.filter(is_deleted=False, code='MASTER').select_related('default_calendar').first()
        calendar = schedule.default_calendar if schedule else None
    if calendar is None:
        calendar = project.work_calendars.filter(is_deleted=False, is_default=True).first()
    return {
        'status': 'configured_unverified' if calendar else 'default_unverified',
        'name': calendar.name if calendar else 'Monday–Friday',
        'exception_count': calendar.exceptions.filter(is_deleted=False).count() if calendar else 0,
    }


def verify_plan_sources(project, state):
    """Compare the current read model with parsed evidence using SELECTs only."""
    files = list(project.files.filter(is_deleted=False).only(
        'id', 'original_filename', 'category', 'parse_status', 'extracted_text', 'is_deleted',
    ).order_by('id'))
    tasks = state.get('tasks') or []
    register_rows = [*(state.get('deliverables') or []),
                     *(task for task in tasks if not task.get('parent_deliverable_id'))]
    register = _reconcile_register(files, register_rows)
    references = reference_schedule_files(files)
    requirements = source_constraints([{
        'id': item.pk, 'filename': item.original_filename, 'category': item.category,
        'text': item.extracted_text or '',
    } for item in files if item.parse_status == 'done'])
    proposed = sum(task.get('duration_source') == 'proposed' for task in tasks)
    inferred = 0
    for task in tasks:
        rationales = task.get('dependency_rationales') or {}
        for predecessor in set(task.get('depends_on') or []):
            rationale = rationales.get(predecessor) or rationales.get(str(predecessor)) or {}
            if isinstance(rationale, dict) and (rationale.get('status') == 'proposed'
                                               or rationale.get('evidence_type') == 'planning_inference'):
                inferred += 1
    calendar = _calendar_status(project, state)
    messages = []
    if register['status'] == 'matched':
        messages.append(f"All {register['matched_count']} parsed register rows match the draft's exact titles and disciplines. This verifies register content only.")
    elif register['status'] == 'missing':
        messages.append('No MDR or EDDR is uploaded; task names cannot be reconciled with an original register.')
    else:
        messages.append(f"Register comparison: {register['matched_count']} of {register['expected_count']} parsed rows match; {len(register['missing'])} missing, {len(register['extra'])} extra and {len(register['changed'])} changed.")
        if register['unparsed_files'] or register['unrecognized_files']:
            messages.append('Some register files are unparsed or have no recognizable rows. Register verification is incomplete.')
    messages.append('Reference schedule files are uploaded but have not been imported and validated.' if references
                    else 'No original reference schedule is uploaded. Original activity dates, durations and relationships are unavailable for exact comparison.')
    if proposed or inferred:
        messages.append(f'{proposed} task durations and {inferred} predecessor links are planning proposals, not verified original schedule values.')
    messages.append(f"The {'configured' if calendar['status'] == 'configured_unverified' else 'default'} {calendar['name']} calendar has {calendar['exception_count']} exceptions and has not been compared with the original schedule calendar.")
    return {
        'status': 'unverified', 'document_register': register,
        'schedule_reference': {'status': 'not_imported' if references else 'missing',
                               'files': references, 'blocker': reference_schedule_blocker(files),
                               'printed_schedules': _printed_schedules(files)},
        'timing': {'proposed_duration_count': proposed, 'inferred_relationship_count': inferred,
                   'source_date_count': 0, 'dates_verified': False, 'dependencies_verified': False,
                   'source_date_reason': 'No validated structured source-schedule import is available; entered and calculated dates are not counted as source dates.'},
        'calendar': calendar, 'source_requirements': requirements, 'messages': messages,
    }
