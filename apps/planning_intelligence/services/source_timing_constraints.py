"""Conservative source timing evidence for a proposed schedule.

Printed dates/durations/float are independent source values. They are not CPM
inputs until the original calendar, time precision and relationships are known.
This module reads caller-provided, project-scoped parsed text only; no storage or
database access, task mutation, fuzzy matching or approval decisions occur here.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import json
import hashlib

from .reference_schedule_text import parse_reference_schedule_text
from .simple_schedule_proposal import source_constraints
from .structured_schedule_evidence import parse_structured_schedule_evidence
from .identity_policy import IDENTITY_POLICY_VERSION, identifier_key, same_source_location


_IDENTITY_ISSUES = {'duplicate_row_numbers', 'duplicate_activity_ids'}
_PRINTED_VALUES = (
    'original_duration_days', 'planned_start_date', 'planned_finish_date',
    'total_float_days', 'printed_single_date', 'date_columns_status',
)


def _title(value):
    # Preserve punctuation and every word: no approximate document matching.
    return ' '.join(str(value or '').split()).casefold()


def _task_source_references(task):
    if task.get('parent_deliverable_id') is not None and not task.get('source_activity_id'):
        # Workflow expansion carries parent scope citations into its children.
        # A citation to a package cannot supply an individual stage's timing.
        evidence = task.get('source_evidence') or task.get('duration_evidence') or {}
        references = evidence.get('source_references') or [] if evidence.get('activity_specific') is True else []
        return [reference for reference in references if isinstance(reference, dict)]
    references = task.get('source_references') or (task.get('source_evidence') or {}).get('source_references') or []
    return [reference for reference in references if isinstance(reference, dict)]


def _task_source_identity(task):
    keys = {key for reference in _task_source_references(task)
            if (key := identifier_key(task.get('source_activity_id'), reference)) is not None}
    return next(iter(keys)) if len(keys) == 1 else None


def _reference(source, row):
    return {
        'file_id': source['id'], 'filename': source.get('filename') or '',
        'category': source.get('category') or '',
        'project_id': source.get('project_id'),
        'document_version': source.get('document_version'),
        'checksum_sha256': source.get('checksum_sha256'),
        'extracted_text_sha256': source.get('extracted_text_sha256') or hashlib.sha256((source.get('text') or '').encode('utf-8')).hexdigest(),
        'namespace': source.get('namespace'), 'document_revision': source.get('document_revision'),
        'locator': deepcopy(row.get('source_locator') or {}),
        'excerpt': row.get('source_excerpt') if 'source_excerpt' in row else row.get('raw_text') or '',
    }


def _evidence(source, row):
    if 'values' in row:
        columns = row.get('field_columns') or {}
        raw_fields = row.get('raw_fields') or {}
        statuses = row.get('field_status') or {}
        fields = {
            key: {'raw_text': raw_fields.get(column['label'], ''), 'column': column['column'],
                  'header': column['label'], 'status': statuses.get(key, 'not_specified'),
                  'source_locator': deepcopy(row.get('source_locator') or {}),
                  'source_excerpt': row.get('source_excerpt') or ''}
            for key, column in columns.items()
        }
        return {
            'title': row['title'], 'activity_id': row.get('activity_id'),
            'kind': row['kind'], 'record_type': row.get('record_type'),
            'basis': 'structured_schedule_table', 'values': deepcopy(row['values']),
            'field_status': deepcopy(statuses), 'field_evidence': fields,
            'relationships': deepcopy(row['values'].get('predecessors')),
            'source_references': [_reference(source, row)],
            'can_apply_to_cpm': False, 'calendar_verified': False,
            'relationships_verified': False,
        }
    return {
        'title': row['title'], 'activity_id': row.get('activity_id'),
        'kind': row['kind'], 'basis': 'printed_schedule',
        'values': {key: deepcopy(row[key]) for key in _PRINTED_VALUES if key in row},
        'relationships': None,
        'field_status': {'predecessors': 'not_specified'},
        'field_evidence': {
            key: {'raw_text': row.get('raw_text') or '', 'status': 'extracted' if row.get(key) is not None else 'not_specified',
                  'source_locator': deepcopy(row.get('source_locator') or {}),
                  'source_excerpt': row.get('raw_text') or ''}
            for key in _PRINTED_VALUES if key in row
        },
        'source_references': [_reference(source, row)],
        'can_apply_to_cpm': False, 'calendar_verified': False,
        'relationships_verified': False,
    }


def source_timing_evidence(tasks, context):
    """Return unchanged source facts and explicit gaps for proposal display.

    ``context['files']`` must contain only current project uploads, using the
    ``proposal_context`` file shape. Each matched task must already reference
    the exact versioned source and explicit activity identifier or physical
    source locator. A title is never identity. ``matched_tasks`` holds
    *evidence*, never replacement tasks: all ``can_apply_to_cpm`` values remain
    false. A matched deliverable also retains its five printed stage rows.

    ``review_requirement`` is the single explicit working-day review requirement
    if sources agree. Callers may preserve it for proposed company-review stages;
    it does not establish a blanket requirement for every other review activity.
    Relative award milestones remain unanchored source constraints.
    """
    files = [source for source in context.get('files') or []
             if source.get('id') is not None and not source.get('is_deleted')
             and source.get('category') != 'output_schedule_sample']
    ready = [source for source in files if source.get('parse_status') == 'done']
    constraints = source_constraints([
        {'id': source['id'], 'filename': source.get('filename') or '',
         'category': source.get('category') or '', 'text': source.get('text') or ''}
        for source in ready
    ])
    result = {'matched_tasks': {}, 'project_summaries': [], 'source_constraints': constraints,
              'evidence_records': [], 'extraction_reports': [], 'identity_issues': [],
              'review_requirement': None, 'warnings': [], 'exact_import_verified': False,
              'identity_policy': IDENTITY_POLICY_VERSION,
              'match_method': 'Exact source-scoped identifier or physical source locator; no title matching, cross-version selection or automatic merge.'}
    review_constraints = [item for item in constraints if item['kind'] == 'review_days']
    review_values = {item['value'] for item in review_constraints if item['value'] > 0}
    if len(review_values) == 1:
        result['review_requirement'] = {
            'duration_days': next(iter(review_values)),
            'source_references': [deepcopy(reference) for item in review_constraints
                                  for reference in item['source_references']],
            'applicability': 'Company review; confirm applicability to the deliverable.',
        }
    elif len(review_values) > 1:
        result['warnings'].append('Source review periods disagree; no single review duration has been selected.')

    seen_records = set()

    def append_record(evidence):
        # Idempotent extraction of an identical assertion is not identity
        # resolution. Conflicting values at the same locator remain distinct.
        identity = json.dumps(evidence, sort_keys=True)
        if identity in seen_records:
            return
        seen_records.add(identity)
        result['evidence_records'].append(evidence)
    for source in ready:
        structured = parse_structured_schedule_evidence(source.get('text') or '')
        parsed = parse_reference_schedule_text(source.get('text') or '')
        result['extraction_reports'].append({
            'file_id': source['id'], 'filename': source.get('filename') or '',
            'adapters': [
                {'adapter': structured['adapter'], 'status': structured['status'],
                 'coverage': deepcopy(structured['coverage']), 'issues': deepcopy(structured['issues'])},
                {'adapter': 'printed_activity_table', 'status': parsed['status'],
                 'row_count': len(parsed['rows']), 'issues': deepcopy(parsed['issues'])},
            ],
            'complete_document_understanding': False,
            'original_document_coverage_verified': False,
        })
        for row in structured['rows']:
            evidence = _evidence(source, row)
            append_record(evidence)
            if _title(row.get('record_type')) == 'project summary':
                result['project_summaries'].append(evidence)
        if structured['rows'] and structured['status'] == 'partial':
            result['warnings'].append(
                f"{source.get('filename') or 'Uploaded document'}: supported table facts were extracted; "
                'unresolved cells or other content remain for review. Missing values have not been inferred.'
            )
        if parsed['status'] == 'not_detected':
            continue
        original = parsed.get('project_summary')
        if original:
            result['project_summaries'].append(_evidence(source, original))
        result['warnings'].append(
            f"{source.get('filename') or 'Reference schedule'}: printed timing is retained as source evidence. "
            'Its calendar, start/finish times and predecessor relationships are unavailable; '
            'these printed values have not replaced the calculated schedule.'
        )
        if any(issue['code'] in _IDENTITY_ISSUES for issue in parsed['issues']):
            result['warnings'].append(
                f"{source.get('filename') or 'Reference schedule'} has duplicate row or activity identities; ambiguous timing requires review."
            )
            # Retain every assertion. Duplicate identifiers block association,
            # not extraction of all other facts in the source.
        groups = {group['summary']['row_number']: group for group in parsed['deliverables']
                  if group['title_match_status'] == 'matched'}
        for row in parsed['rows']:
            evidence = _evidence(source, row)
            group = groups.get(row['row_number'])
            if group:
                evidence['workflow_activities'] = [_evidence(source, child) for child in group['activities']]
                evidence['workflow_stage_names'] = deepcopy(group['stage_names'])
            # Adapters may overlap a row; only identical facts from the same
            # physical source locator are duplicates. Other revisions remain
            # distinct and must be resolved by a reviewer.
            append_record(evidence)

    source_candidates = defaultdict(list)
    for record in result['evidence_records']:
        references = record.get('source_references') or []
        key = identifier_key(record.get('activity_id'), references[0]) if references else None
        if key is not None:
            source_candidates[key].append(record)
    task_identity_counts = Counter(identity for task in tasks if (identity := _task_source_identity(task)))
    for task in tasks:
        identity = _task_source_identity(task)
        if task.get('id') is None:
            continue
        matches = source_candidates.get(identity, []) if identity else []
        if task.get('source_activity_id') is None:
            references = _task_source_references(task)
            matches = [record for record in result['evidence_records']
                       if any(same_source_location(left, right) for left in references
                              for right in record.get('source_references') or [])]
        if len(matches) == 1 and (identity is None or task_identity_counts[identity] == 1):
            result['matched_tasks'][task['id']] = deepcopy(matches[0])
        else:
            result['identity_issues'].append({
                'code': 'ambiguous_source_identity' if len(matches) > 1 else 'source_identity_not_verified',
                'task_id': task['id'], 'source_activity_id': task.get('source_activity_id'),
                'candidate_count': len(matches), 'status': 'requires_review',
                'message': 'Select and approve the exact source record and version. Names do not establish identity.',
                'blocks': ['calculation', 'approval'],
            })
    if result['identity_issues']:
        result['warnings'].append(
            f"{len(result['identity_issues'])} task identities are unverified, ambiguous or conflicting; "
            'their timing has not been selected. Approve a source link instead of matching names.'
        )
    if not result['evidence_records']:
        result['warnings'].append(
            'No supported activity schedule was detected in the processed project inputs. '
            'MDR titles and SOW requirements alone do not establish exact activity dates, durations or predecessor links.'
        )
    return result
