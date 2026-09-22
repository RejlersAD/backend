"""Explicitly requested requirement drafts, without a provider or inferred scope."""
from copy import deepcopy
from datetime import timedelta

from django.db import transaction

from .document_intelligence import _extraction_source_manifest
from .requirement_activities import extract_requirement_activities
from .simple_planning import (
    _calendar, _calendar_record, _cancel_review, _error, _fingerprint,
    _locked, _persist, _seed_task, plan_state,
)


def allocate_dates(rows, working_days):
    """Even source-order slots; overlap explicitly when rows outnumber days."""
    count = len(rows)
    for index, row in enumerate(rows):
        first = index * len(working_days) // count
        last = max(first, (index + 1) * len(working_days) // count - 1)
        start, finish = working_days[first].isoformat(), working_days[last].isoformat()
        row.update(
            duration_days=last - first + 1, duration_source='proposed',
            duration_unit='working_days', duration_policy='planning_assumptions',
            planned_start_date=start, planned_finish_date=finish,
            proposal_timing=True,
            planned_start_date_source='proposed', planned_finish_date_source='proposed',
            due_date=finish, due_date_source='schedule',
            constraint_type='start_no_earlier', constraint_date=start,
            schedule_generated_fields=['duration_days', 'planned_start_date', 'planned_finish_date', 'constraint_date'],
            schedule_rationale='Equal allocation in source order across the registered project working days; provisional planning assumption, not a source duration or dependency.',
        )


@transaction.atomic
def create_programmatic_draft(project, actor, *, revision, requirement_scope='all'):
    project, state = _locked(project, actor, revision)
    if state['state'] == 'baselined' or state.get('legacy_version_import'):
        _error('Create a revision before changing the published baseline.', 'simple_plan_baselined')
    if state.get('tasks'):
        _error('This draft already contains activities. Your saved work has been retained.', 'programmatic_draft_not_empty')
    if requirement_scope != 'all':
        _error('Select all extracted statements for this draft.', 'programmatic_scope_invalid')
    start, end = project.effective_date, project.planned_end_date
    if not start or not end:
        _error('Set the project start and end dates before creating the draft.', 'dates_required')
    if end < start or (end - start).days > 36525:
        _error('The project end date must follow its start date within 100 years.', 'dates_invalid')
    calendar = _calendar(project)
    working_days = [start + timedelta(days=day) for day in range((end - start).days + 1)
                    if calendar.is_working(start + timedelta(days=day))]
    if not working_days:
        _error('The project date range contains no working days. Adjust the dates or calendar.', 'programmatic_no_working_days')
    files = list(project.files.select_for_update().filter(is_deleted=False).order_by('id'))
    if not files or any(source.parse_status != 'done' for source in files):
        _error('Finish processing the source documents before creating the draft.', 'simple_plan_documents_processing')
    run = project.intelligence_runs.filter(is_deleted=False).order_by('-created_at', '-pk').first()
    if not run or run.status != 'succeeded':
        _error('Complete document extraction before creating activities from its requirements.', 'programmatic_requirements_unavailable')
    manifest = _extraction_source_manifest(files)
    captured = (run.summary or {}).get('extraction_source_manifest')
    if sorted(run.source_file_ids) != [source.pk for source in files] or (captured and captured != manifest):
        _error('Source documents changed after extraction. Extract the current requirements before creating the draft.', 'intelligence_sources_changed')
    facts = list(run.facts.filter(is_deleted=False, fact_type='requirement').order_by('source_file_id', 'id'))
    if not facts:
        _error('No extracted requirement statements are available for this draft.', 'programmatic_requirements_unavailable')
    if len(facts) > 2000:
        _error('This draft supports up to 2,000 requirement activities. Select a smaller source set.', 'programmatic_requirement_limit')
    sources = {source.pk: source for source in files}
    hashes = {row['file_id']: row['extracted_text_sha256'] for row in manifest}
    tasks = []
    for fact in facts:
        source = sources.get(fact.source_file_id)
        locator = deepcopy(fact.source_locator or {})
        expected = locator.get('extracted_text_sha256')
        if not source or (expected and expected != hashes[source.pk]):
            _error('A requirement refers to changed or unavailable source text. Extract the current requirements again.', 'intelligence_sources_changed')
        if not captured and not expected and source.updated_at > run.started_at:
            _error('Source documents changed after extraction. Extract the current requirements again.', 'intelligence_sources_changed')
        value = fact.value
        statement = value if isinstance(value, str) else str(value.get('text') or value.get('statement') or value.get('name') or value) if isinstance(value, dict) else str(value)
        statement = statement.strip()
        if not statement:
            _error('An extracted requirement has no text. Review the source findings.', 'programmatic_requirement_empty')
        references = [{'file_id': source.pk, 'filename': source.original_filename, 'category': source.category,
                       'locator': locator, 'excerpt': fact.source_excerpt,
                       'extracted_text_sha256': hashes[source.pk]}]
        task = _seed_task({
            'id': f'requirement-{fact.pk}', 'title': statement[:500],
            'source_title': statement, 'acceptance_criteria': statement,
            'discipline': 'requirements', 'task_type': 'task', 'priority': 'medium',
            'requirement_id': fact.pk, 'requirement_value': deepcopy(value), 'requirement_status': fact.status,
            'source_references': references, 'depends_on': [], 'dependency_details': [],
            'dependency_status': 'proposed', 'evidence_policy': 'planning_assumptions',
            'selection_basis': 'all_extracted_requirements', 'needs_review': True,
            'review_flags': ['requirement_activity_requires_review', 'provisional_dates_and_duration'],
        })
        tasks.append(task)
    # Preserve source order even if the extractor originally emitted fact types
    # in batches. Every occurrence remains a separate activity, including clauses.
    tasks.sort(key=lambda row: (row['source_references'][0]['file_id'],
        row['source_references'][0]['locator'].get('character_start', 0), row['requirement_id']))
    allocate_dates(tasks, working_days)
    extracted = extract_requirement_activities(files)
    deliverables = [deepcopy(row) for row in extracted['tasks'] if row.get('task_type') == 'deliverable']
    for row in deliverables:
        row['id'] = 'document-' + row['id']
    allocate_dates(deliverables, working_days)
    configured_calendar = _calendar_record(project)
    calendar_basis = (f'Project calendar: {configured_calendar.name}' if configured_calendar
                      else 'Assumed Monday–Friday working week; no holidays configured')
    assumptions = [
        {'code': 'all_requirements_included', 'message': f'This draft was created from all {len(tasks)} extracted statements, including contract clauses. Review applicability and wording before treating them as executable work.'},
        {'code': 'programmatic_date_allocation', 'message': 'Dates and durations are evenly allocated in source order within the project dates. No technical dependencies or resource capacity are inferred.'},
        {'code': 'programmatic_calendar', 'message': calendar_basis},
        {'code': 'source_wbs_review', 'message': 'Source deliverable headings and item numbers are retained for review. Date allocation does not establish an approved WBS or verify contractual milestones.'},
        {'code': 'separate_source_deliverables', 'message': f'{len(deliverables)} source deliverables are retained in a separate draft register. Their dates are provisional and they are not additional requirement activities.'},
    ]
    if len(tasks) > len(working_days):
        assumptions.append({'code': 'programmatic_parallel_allocation', 'message': f'{len(tasks)} statements share {len(working_days)} working days, so some draft activities overlap. This is date allocation, not a resource-feasible execution sequence.'})
    before = deepcopy(state)
    _cancel_review(state, actor)
    for key in ('schedule_proposal', 'duration_review', 'calculation_run_id', 'deliverables', 'workflow_mode'):
        state.pop(key, None)
    state.update(
        state='review', revision=state['revision'] + 1, tasks=tasks,
        disciplines=[{'code': 'requirements', 'name': 'Requirement statements'}],
        method='programmatic_requirements', evidence_policy='planning_assumptions', duration_policy='planning_assumptions',
        input_fingerprint=_fingerprint(project), intelligence_run_id=run.pk,
        extraction_summary=deepcopy((run.summary or {}).get('extraction_summary') or {}),
        processing_coverage=deepcopy((run.summary or {}).get('processing_coverage') or {}),
        managed_task_ids=[task['id'] for task in tasks], version_id=None, review_id=None,
        document_deliverables=deliverables, programmatic_assumptions=assumptions,
        warnings=[{'code': 'programmatic_draft_review', 'severity': 'warning',
                   'message': 'Draft created without AI. Review all requirement activities, provisional dates, deliverables and dependencies before submitting.'}],
        programmatic_summary={'requirement_count': len(facts), 'activity_count': len(tasks),
            'deliverable_count': len(deliverables), 'working_day_count': len(working_days),
            'project_start_date': start.isoformat(), 'project_end_date': end.isoformat(),
            'calendar_basis': calendar_basis, 'allocation_method': 'equal_source_order_slots',
            'ai_used': False, 'requirement_scope': 'all'},
    )
    _persist(project, state, actor, 'simple_plan.programmatic_draft_created', before)
    return plan_state(project, actor)
