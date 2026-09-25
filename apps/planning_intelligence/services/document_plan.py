"""Evidence-only planning snapshots, independent of document/project vocabulary.

This layer does not calculate a schedule. It records explicit activity facts and
preserves missing/ambiguous inputs. A source table is not a verified CPM network.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .source_timing_constraints import source_timing_evidence, _reference
from .register_rows import extract_register_rows, register_row_requires_review
from .identity_policy import (
    IDENTITY_POLICY_VERSION, identifier_key, identity_candidates, occurrence_key,
    same_source_location, stable_digest,
)


POLICY = 'document_driven'
VERSION = 'document-evidence-v2'
NOT_SPECIFIED = 'Not Specified'


def source_files(project):
    from .reference_schedule_geometry import cached_schedule_geometry
    from .register_geometry_cache import cached_register_geometry
    sources = []
    for item in project.files.filter(is_deleted=False).select_related('document_profile').order_by('pk'):
        source = {'id': item.pk, 'filename': item.original_filename, 'category': item.category,
                  'project_id': project.pk, 'parse_status': item.parse_status, 'text': item.extracted_text,
                  'updated_at': item.updated_at.isoformat()}
        geometry = cached_schedule_geometry(item)
        if geometry:
            source['structured_evidence'] = {'reference_schedule_geometry': geometry}
        register_geometry = cached_register_geometry(item)
        if register_geometry:
            source.setdefault('structured_evidence', {})['register_geometry'] = register_geometry
        sources.append(source)
    return sources


def _source_id(record):
    reference = (record.get('source_references') or [{}])[0]
    return reference.get('file_id')


def _key(record, *, unique_identifier=False):
    reference = (record.get('source_references') or [{}])[0]
    if unique_identifier:
        key = identifier_key(record.get('activity_id'), reference)
        return str(uuid5(NAMESPACE_URL, f'radai:{IDENTITY_POLICY_VERSION}:{stable_digest(key)}'))
    key = occurrence_key(reference, identifier=record.get('activity_id'), fact_id=record.get('source_fact_id'))
    return str(uuid5(NAMESPACE_URL, f'radai:{IDENTITY_POLICY_VERSION}:{key}:{stable_digest(record.get("values"))}'))


def _record_identity(record, identifier=None):
    reference = (record.get('source_references') or [{}])[0]
    return identifier_key(record.get('activity_id') if identifier is None else identifier, reference)


def _register_records(files):
    """Header-driven register rows; file names are not semantic evidence."""
    records = []
    for source in files:
        if (source.get('parse_status') != 'done' or source.get('id') is None
                or source.get('is_deleted') or source.get('category') == 'output_schedule_sample'):
            continue
        for row in extract_register_rows(source.get('text') or '', structured_evidence=source.get('structured_evidence')):
            records.append({
                'title': row['original_title'], 'activity_id': row.get('document_number') or None,
                'document_revision': row.get('document_revision'), 'discipline': row.get('discipline'),
                'discipline_label': row.get('discipline_label'),
                'source_group': row.get('source_group'),
                'title_boundary_status': row.get('title_boundary_status', 'explicit_columns'),
                'source_layout': row.get('source_layout', 'table_columns'),
                **({key: row[key] for key in ('applicability_status', 'applicability_marks', 'source_remarks', 'package_columns_status')
                    if key in row}),
                'explicit_dimensions': deepcopy(row.get('explicit_dimensions') or {}),
                'kind': 'activity', 'basis': 'document_register', 'values': {},
                'field_status': {'duration': 'not_specified', 'predecessors': 'not_specified'},
                'source_references': [_reference(source, row)],
                'relationships': None, 'calendar_verified': False, 'relationships_verified': False,
            })
    return records


def build_document_plan(files, *, project_name='', project_id=None, additional_records=None):
    """Return all recovered facts with per-field evidence and explicit gaps.

    IDs below are internal identities. They are never presented as extracted
    activity IDs. The single root is an application container, not inferred WBS.
    """
    files = [{**source, **({'project_id': project_id} if source.get('project_id') is None and project_id is not None else {})}
             for source in files]
    evidence = source_timing_evidence([], {'files': files})
    records = [deepcopy(row) for row in evidence.get('evidence_records') or []
               if row.get('kind') == 'activity']
    register_records = _register_records(files)
    ambiguous_register = [row for row in register_records if row['title_boundary_status'] == 'ambiguous']
    if not records:
        records = deepcopy([row for row in register_records if not register_row_requires_review(row)])
    if not records:
        records = deepcopy(additional_records or [])
    summaries = [row for row in evidence.get('evidence_records') or [] if row.get('kind') == 'summary']
    activities, issues = [], []
    if ambiguous_register:
        issues.append({'code': 'register_title_boundary_ambiguous',
                       'severity': 'error',
                       'message': 'Some register titles have ambiguous PDF text boundaries. Their source rows are retained for review and were not converted into activities.',
                       'count': len(ambiguous_register),
                       'source_references': [reference for row in ambiguous_register for reference in row['source_references']],
                       'blocks': ['calculation', 'approval']})
    unresolved_scope = [row for row in register_records if row.get('applicability_status', 'marked') != 'marked']
    if unresolved_scope:
        issues.append({'code': 'register_applicability_not_confirmed', 'severity': 'error',
                       'message': 'Some matrix rows are unmarked, conditional or have unresolved applicability. They remain source inventory and were not converted into activities.',
                       'count': len(unresolved_scope),
                       'source_references': [reference for row in unresolved_scope for reference in row['source_references']],
                       'blocks': ['calculation', 'approval']})
    identities = defaultdict(list)
    source_id_counts = Counter(_record_identity(record) for record in records if _record_identity(record) is not None)
    for record in records:
        identity = _record_identity(record)
        key = _key(record, unique_identifier=identity is not None and source_id_counts[identity] == 1)
        if identity is not None:
            identities[identity].append(key)
        values = record.get('values') or {}
        duration = values.get('original_duration_days')
        milestone = values.get('is_milestone')
        references = deepcopy(record.get('source_references') or [])
        statuses = deepcopy(record.get('field_status') or {})
        activity = {
            'id': key, 'name': record['title'], 'title': record['title'],
            'source_activity_id': record.get('activity_id'), 'wbs_code': '1',
            'discipline': record.get('discipline'), 'deliverable': None, 'responsible_role': None,
            'identity_policy': IDENTITY_POLICY_VERSION,
            'original_duration_days': duration, 'duration_days': duration,
            'duration_source': 'source_document' if duration is not None else 'missing_source',
            'duration_unit': values.get('duration_unit'),
            'start_date': values.get('planned_start_date'), 'finish_date': values.get('planned_finish_date'),
            'is_milestone': milestone, 'total_float_days': None,
            'predecessors': [], 'dependency_status': statuses.get('predecessors', 'not_specified'),
            'source_references': references, 'field_evidence': deepcopy(record.get('field_evidence') or {}),
            'source_evidence': deepcopy(record), 'source_values': deepcopy(values),
            'evidence_review_status': record.get('review_status', 'requires_review'),
            'date_authority': 'source_document', 'calendar_verified': False,
            'missing_fields': [],
        }
        for field, value in [('duration', duration), ('start', activity['start_date']),
                             ('finish', activity['finish_date']), ('milestone_type', milestone)]:
            if value is None:
                activity['missing_fields'].append(field)
        if activity['dependency_status'] != 'explicit_none' and record.get('relationships') is None:
            activity['missing_fields'].append('dependencies')
        activities.append(activity)
    by_id = {row['id']: row for row in activities}
    if len(by_id) != len(activities):
        issues.append({'code': 'duplicate_source_identity', 'message': 'Repeated source row identity requires review.'})
    for candidate in identity_candidates(records):
        issues.append({'code': 'duplicate_source_identifier', 'message': 'Repeated explicit identifiers remain distinct; review their identity before linking.',
                       'record_indexes': candidate['record_indexes'], 'source_activity_id': candidate['identifier'],
                       'blocks': ['calculation', 'approval']})
    register_inventory = []
    for register in register_records:
        reference = (register.get('source_references') or [{}])[0]
        # A register and a schedule may describe different entities despite
        # identical names. Only the very same extracted source row is a known
        # association; cross-document mapping requires explicit evidence.
        same_row = [activity['id'] for activity in activities
                    if any(same_source_location(reference, item) for item in activity['source_references'])]
        register_inventory.append({
            **deepcopy(register), 'id': _key(register), 'scope_basis': 'source_register',
            'identity_policy': IDENTITY_POLICY_VERSION, 'schedule_activity_ids': same_row,
            'schedule_association_status': 'same_source_row' if same_row else 'not_specified',
        })
    unmapped_register = [row for row in register_inventory if not row['schedule_activity_ids']]
    if unmapped_register:
        issues.append({'code': 'register_schedule_association_not_specified',
                       'message': f'{len(unmapped_register)} register rows have no explicit association to the extracted schedule. Their evidence is retained separately; names have not been used to guess a mapping.',
                       'count': len(unmapped_register), 'blocks': ['calculation', 'approval']})
    associated_ids = {identifier for row in register_inventory for identifier in row['schedule_activity_ids']}
    unmapped_schedule_ids = [row['id'] for row in activities if row['id'] not in associated_ids] if register_inventory else []
    if unmapped_schedule_ids:
        issues.append({'code': 'schedule_register_scope_not_verified',
                       'message': 'Extracted schedule activities remain separate from the primary deliverable register until explicit scope links are reviewed.',
                       'activity_ids': unmapped_schedule_ids, 'blocks': ['calculation', 'approval']})
    if not register_inventory:
        issues.append({'code': 'deliverable_register_not_provided',
                       'message': 'No supported deliverable register was extracted. Only explicit available source scope is retained.'})
    unresolved = []
    for record, activity in zip(records, activities):
        for link in record.get('relationships') or []:
            predecessor_id = link.get('predecessor_id')
            matches = identities.get(_record_identity(record, predecessor_id), []) if predecessor_id is not None else []
            kind, lag = link.get('relationship_type'), link.get('lag')
            lag_days = lag.get('value') if isinstance(lag, dict) and lag.get('unit') in {'days', 'working_days'} else None
            resolved = len(matches) == 1 and kind in {'FS', 'SS', 'FF', 'SF'} and lag_days is not None and matches[0] != activity['id']
            reference = {'successor_id': activity['id'], 'predecessor_source_id': predecessor_id,
                         'type': kind, 'lag': deepcopy(lag), 'source_references': activity['source_references'],
                         'source_excerpt': link.get('raw'), 'resolved': resolved}
            if not resolved:
                unresolved.append(reference)
                if 'dependency_details' not in activity['missing_fields']:
                    activity['missing_fields'].append('dependency_details')
                continue
            activity['predecessors'].append({'id': matches[0], 'type': kind, 'lag_days': lag_days,
                                            'lag_unit': lag.get('unit'),
                                            'source': 'source_document', 'source_references': activity['source_references'],
                                            'source_excerpt': link.get('raw')})
    missing = [{'activity_id': row['id'], 'source_activity_id': row['source_activity_id'],
                'name': row['name'], 'fields': row['missing_fields'], 'status': NOT_SPECIFIED,
                'source_references': row['source_references']}
               for row in activities if row['missing_fields']]
    if not records:
        issues.append({'code': 'source_activities_not_specified', 'message': 'No supported explicit activity rows were recovered. Unstructured requirements need evidence review.'})
    issues.extend({'code': 'source_fields_not_specified', 'message': f"{item['name']}: {', '.join(item['fields'])} — {NOT_SPECIFIED}",
                   'activity_id': item['activity_id']} for item in missing)
    issues.append({'code': 'source_calendar_not_verified', 'message': 'The source calendar and complete predecessor network require verification. No fallback calendar or calculated dates have been applied.'})
    relationships = [{'activity_id': row['id'], 'predecessor_id': link['id'],
                      'type': link['type'], 'lag_days': link['lag_days'], 'source_references': link['source_references'],
                      'lag_unit': link['lag_unit'],
                      'source_excerpt': link.get('source_excerpt')}
                     for row in activities for link in row['predecessors']]
    return {
        'policy': POLICY, 'engine_version': VERSION, 'activities': activities,
        'wbs': [{'code': '1', 'name': project_name or NOT_SPECIFIED, 'level': 0, 'parent_code': None,
                 'basis': 'project_record_container', 'project_id': project_id}],
        'logic_matrix': relationships, 'unresolved_relationships': unresolved,
        'source_summaries': summaries, 'source_project_summaries': deepcopy(evidence.get('project_summaries') or []),
        'source_constraints': evidence.get('source_constraints') or [],
        'register_inventory': register_inventory, 'unmapped_register_count': len(unmapped_register),
        'deliverables': deepcopy(register_inventory),
        'scope_authority': 'source_register' if register_inventory else 'available_source_evidence',
        'scope_activity_ids': sorted(associated_ids) if register_inventory else [row['id'] for row in activities],
        'unmapped_source_schedule_activity_ids': unmapped_schedule_ids,
        'identity_policy': IDENTITY_POLICY_VERSION,
        'additional_source_facts': deepcopy(additional_records or []),
        'extraction_reports': evidence.get('extraction_reports') or [],
        'missing_information': missing, 'validation': [{'severity': 'warning', **item} for item in issues],
        'date_authority': 'source_document', 'project_finish_date': None,
        'calculation_available': False, 'ready_for_calculation': False,
        'applied_dependency_rules': [],
    }


def project_document_plan(project, intelligence=None):
    files = source_files(project)
    facts = []
    run_id = (intelligence or {}).get('document_intelligence_run_id')
    run = project.intelligence_runs.filter(pk=run_id, status='succeeded', is_deleted=False).first() if run_id else None
    by_file = {source['id']: source for source in files if source['parse_status'] == 'done'}
    normalized_sources = {key: ' '.join((source.get('text') or '').split()).casefold()
                          for key, source in by_file.items()}
    if run:
        for fact in run.facts.filter(fact_type='deliverable', status__in=['detected', 'confirmed'], is_deleted=False):
            source = by_file.get(fact.source_file_id)
            value = fact.value if isinstance(fact.value, dict) else {}
            if register_row_requires_review(value):
                continue
            title = value.get('original_title') or value.get('name')
            excerpt = fact.source_excerpt or ''
            if fact.extraction_method == 'deterministic' and not value.get('source_register') and fact.status != 'confirmed':
                continue
            # A current project-owned source and a quoted title are required.
            # Unlocated AI/catalogue suggestions cannot become activities.
            if not source or not title or title.casefold() not in excerpt.casefold():
                continue
            if ' '.join(excerpt.split()).casefold() not in normalized_sources[source['id']]:
                continue
            facts.append({'title': title, 'activity_id': value.get('document_number'),
                          'kind': 'activity', 'basis': 'document_fact', 'values': {},
                          'field_status': {'duration': 'not_specified', 'predecessors': 'not_specified'},
                          'relationships': None, 'source_fact_id': fact.pk,
                          'review_status': fact.status, 'extraction_method': fact.extraction_method,
                          'source_references': [{**_reference(source, {'source_locator': fact.source_locator,
                                                                      'source_excerpt': excerpt}),
                                                 'fact_id': fact.pk}]})
    plan = build_document_plan(files, project_name=project.name, project_id=project.pk, additional_records=facts)
    plan['processing_coverage'] = deepcopy((intelligence or {}).get('processing_coverage') or {})
    return plan


def simple_tasks(plan):
    """Project a fact snapshot into an editable draft without manufacturing CPM."""
    return [{
        'id': row['id'], 'title': row['name'], 'discipline': row.get('discipline') or 'not_specified',
        'evidence_entity_id': str(row['id']),
        'source_activity_id': row['source_activity_id'], 'source_references': row['source_references'],
        'source_evidence': row['source_evidence'], 'duration_evidence': row['source_evidence'],
        'duration_days': row['duration_days'], 'duration_source': row['duration_source'],
        'duration_unit': row['duration_unit'],
        'duration_calendar_verified': False, 'is_milestone': row['is_milestone'] is True,
        'activity_type': ('start_milestone' if row['is_milestone'] is True
                          and str((row.get('source_evidence') or {}).get('record_type') or '').lower().replace('_', ' ') == 'start milestone'
                          else 'finish_milestone' if row['is_milestone'] is True else 'task'),
        'duration_review_status': 'source_verified' if row['duration_days'] is not None else 'missing_source',
        'duration_review_reason': 'Explicit document value.' if row['duration_days'] is not None else NOT_SPECIFIED,
        'depends_on': [link['id'] for link in row['predecessors']],
        'dependency_details': [{'task_id': link['id'], 'type': link['type'], 'lag_days': link['lag_days'],
                                'lag_unit': link['lag_unit'],
                                'source': 'source_document', 'status': 'source_document',
                                'source_references': link['source_references'], 'source_excerpt': link.get('source_excerpt')}
                               for link in row['predecessors']],
        'dependency_status': row['dependency_status'], 'source_missing_fields': row['missing_fields'],
        'evidence_policy': POLICY, 'duration_policy': 'source_only',
    } for row in plan['activities']]
