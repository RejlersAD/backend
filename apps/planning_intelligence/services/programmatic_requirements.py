"""Build executable, source-scoped workflows without a provider call."""
from copy import deepcopy
from datetime import timedelta
import re

from django.db import transaction

from .document_intelligence import _extraction_source_manifest
from .activity_identifiers import assign_activity_identifiers, project_activity_prefix
from .requirement_activities import extract_requirement_activities, _narrative_work, _task
from .simple_planning import (
    _calendar, _calendar_record, _cancel_review, _dated_tasks, _error, _fingerprint,
    _locked, _persist, _seed_task, plan_state,
)


def _scope_identity(row):
    title = re.sub(r'^(?:prepare|develop|produce|design|review|verify|check)\s+(?:the\s+)?',
                   '', row['title'], flags=re.I)
    return re.sub(r'\W+', ' ', title).strip().casefold()


def _execution_work(source, facts):
    """Recognize explicit field/procurement work alongside document preparation."""
    text = source.extracted_text or ''
    action = re.compile(
        r'\b(?:shall|must|is required to)\s+(?P<work>(?:procure|purchase|fabricate|'
        r'construct|erect|install|inspect|test|commission|pre-commission|energize)\s+.+)', re.I)
    for fact in facts:
        locator = fact.source_locator or {}
        start, end = locator.get('character_start'), locator.get('character_end')
        if (not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(text)):
            continue
        quote = re.sub(r'\s+', ' ', text[start:end]).strip()
        if not isinstance(fact.value, str) or re.sub(r'\s+', ' ', fact.value).strip() != quote:
            continue
        match = action.search(quote)
        if not match:
            continue
        title = match.group('work').strip().rstrip('.;')
        if len(title) < 12 or len(title) > 350:
            continue
        yield _task(source, text, {'title': title[0].upper() + title[1:], 'start': start,
            'end': end, 'heading': 'General', 'task_type': 'task', 'requirement_ids': [fact.pk],
            'selection_basis': 'explicit_work_instruction'})


def _select_scope(files, facts):
    """Keep deliverable scope separate from work steps and contract conditions."""
    eligible = [fact for fact in facts if fact.status not in {'rejected', 'conflicted'}]
    selected = extract_requirement_activities(files, eligible)
    candidates = selected['tasks']
    # A register defines deliverables, but cannot suppress a separate explicit
    # installation, survey or testing instruction elsewhere in the scope.
    if selected['selection_summary']['selection_basis'] == 'explicit_deliverable_list':
        existing = {_scope_identity(row) for row in candidates}
        for source in files:
            rows, _ = _narrative_work(source.extracted_text or '',
                                     [fact for fact in eligible if fact.source_file_id == source.pk])
            for row in rows:
                task = _task(source, source.extracted_text or '', row)
                identity = _scope_identity(task)
                if identity not in existing:
                    candidates.append(task)
                    existing.add(identity)
    for source in files:
        candidates.extend(_execution_work(source, [fact for fact in eligible if fact.source_file_id == source.pk]))
    # Duplicate rows within one deliverable branch produce one workflow;
    # identical deliverables in different source branches remain distinct scope.
    unique = {}
    for candidate in candidates:
        key = (candidate['discipline'], candidate.get('source_heading', '').casefold(), candidate.get('document_number', ''),
               _scope_identity(candidate))
        if key in unique:
            unique[key]['source_references'].extend(candidate.get('source_references') or [])
            unique[key]['requirement_ids'].extend(candidate.get('requirement_ids') or [])
        else:
            unique[key] = candidate
    selected['tasks'] = list(unique.values())
    return selected


@transaction.atomic
def create_programmatic_draft(project, actor, *, revision, requirement_scope='all'):
    from .enterprise_schedule import (
        expand_enterprise_deliverables, generation_context, validate_enterprise_network,
    )

    project, state = _locked(project, actor, revision)
    if state['state'] == 'baselined' or state.get('legacy_version_import'):
        _error('Create a revision before changing the published baseline.', 'simple_plan_baselined')
    if state.get('tasks'):
        _error('This draft already contains activities. Your saved work has been retained.', 'programmatic_draft_not_empty')
    if requirement_scope != 'all':
        _error('Select all extracted statements for scope review.', 'programmatic_scope_invalid')
    start, end = project.effective_date, project.planned_end_date
    if not start or not end:
        _error('Set the project start and end dates before creating the draft.', 'dates_required')
    if end < start or (end - start).days > 36525:
        _error('The project end date must follow its start date within 100 years.', 'dates_invalid')
    calendar = _calendar(project)
    working_day_count = sum(calendar.is_working(start + timedelta(days=day))
                            for day in range((end - start).days + 1))
    if not working_day_count:
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
    if len(facts) > 2000:
        _error('Review up to 2,000 requirement statements per draft. Select a smaller source set.', 'programmatic_requirement_limit')
    sources = {source.pk: source for source in files}
    hashes = {row['file_id']: row['extracted_text_sha256'] for row in manifest}
    for fact in facts:
        source = sources.get(fact.source_file_id)
        expected = (fact.source_locator or {}).get('extracted_text_sha256')
        if not source or (expected and expected != hashes[source.pk]):
            _error('A requirement refers to changed or unavailable source text. Extract the current requirements again.', 'intelligence_sources_changed')
        if not captured and not expected and source.updated_at > run.started_at:
            _error('Source documents changed after extraction. Extract the current requirements again.', 'intelligence_sources_changed')
    scope_files = [source for source in files if source.category != 'output_schedule_sample']
    scope_ids = {source.pk for source in scope_files}
    extracted = _select_scope(scope_files, [fact for fact in facts if fact.source_file_id in scope_ids])
    selected_ids = set()
    fact_by_id = {fact.pk: fact for fact in facts}
    parents = []
    for candidate in extracted['tasks']:
        parent = _seed_task(candidate)
        identities = list(dict.fromkeys(parent.get('requirement_ids') or []))
        selected_ids.update(identities)
        parent.update(task_type='deliverable', evidence_policy='planning_assumptions',
                      duration_policy='planning_assumptions', source_title=parent['title'],
                      acceptance_criteria=parent.get('source_quote') or parent['title'])
        if identities:
            fact = fact_by_id[identities[0]]
            statement = fact.value if isinstance(fact.value, str) else parent.get('source_quote') or parent['title']
            parent.update(requirement_id=fact.pk, requirement_ids=identities,
                          requirement_value=deepcopy(fact.value), requirement_status=fact.status,
                          source_title=statement, acceptance_criteria=statement)
        parents.append(parent)
    excluded = [{
        'requirement_id': fact.pk, 'source_file_id': fact.source_file_id,
        'statement': deepcopy(fact.value), 'status': fact.status,
        'source_locator': deepcopy(fact.source_locator or {}),
        'reason': ('reference_only' if fact.source_file_id not in scope_ids else
                   'source_review_not_accepted' if fact.status in {'rejected', 'conflicted'} else
                   'contract_condition_or_scope_already_covered'),
    } for fact in facts if fact.pk not in selected_ids]
    selection_summary = {**extracted['selection_summary'],
        'requirements_found': len(facts), 'requirements_selected': len(selected_ids),
        'requirements_not_individually_materialized': len(excluded),
        'activities_selected': len(parents), 'deliverables_selected': len(parents),
        'explanation': 'Deliverable-register outputs and specific executable work instructions define scope. Duplicate work is consolidated with its source references; contract conditions and reference schedules remain outside the execution network.'}
    if not parents:
        _error('No executable deliverables or specific work instructions were found. Add the project deliverable scope; contract clauses and reference schedules do not create activities.',
               'programmatic_executable_scope_required', excluded_requirements=excluded)
    context = generation_context(project)
    deliverables, tasks, warnings = expand_enterprise_deliverables(parents, context)
    warnings = [({'code': 'enterprise_workflow_allowance', 'severity': 'warning', 'message': warning}
                 if isinstance(warning, str) else warning) for warning in warnings]
    if len(tasks) > 2000:
        _error('The decomposed schedule exceeds 2,000 tasks. Split the source scope into smaller work packages.',
               'programmatic_requirement_limit')
    activity_id_registry = assign_activity_identifiers(tasks, state.get('activity_id_registry'),
        prefix=project_activity_prefix(project))
    tasks = _dated_tasks(project, [_seed_task(task) for task in tasks])
    quality = validate_enterprise_network(tasks)
    finish = max((task.get('planned_finish_date') or '' for task in tasks), default='')
    configured_calendar = _calendar_record(project)
    calendar_basis = (f'Project calendar: {configured_calendar.name}' if configured_calendar
                      else 'Assumed Monday-Friday working week; no holidays configured')
    assumptions = [
        {'code': 'programmatic_scope_selection', 'message': f'{len(deliverables)} executable deliverables were selected from source scope. {len(excluded)} statements remain in scope review rather than becoming duplicate work or contract-clause activities.'},
        {'code': 'programmatic_workflow_estimates', 'message': 'Task durations use discipline workflows and complexity estimates. Review quantities, resources, execution interfaces and procurement lead times before approval.'},
        {'code': 'programmatic_calendar', 'message': calendar_basis},
        {'code': 'programmatic_network', 'message': 'Finish-to-start links define proposed execution logic. Dates and float are calculated from that network without compressing work to fit the project target.'},
    ]
    if finish > end.isoformat():
        warnings.append({'code': 'enterprise_target_overrun', 'severity': 'warning',
                         'message': f'The calculated schedule finishes {finish}, after the project target {end.isoformat()}. Review scope, resources or target dates; estimated durations were retained.'})
    before = deepcopy(state)
    _cancel_review(state, actor)
    for key in ('schedule_proposal', 'duration_review', 'calculation_run_id', 'programmatic_summary'):
        state.pop(key, None)
    disciplines = sorted({task['discipline'] for task in tasks})
    state.update(
        state='review', revision=state['revision'] + 1, tasks=tasks,
        deliverables=deliverables, workflow_mode='enterprise',
        disciplines=[{'code': code, 'name': code.replace('_', ' ').title()} for code in disciplines],
        method='programmatic_requirements', evidence_policy='planning_assumptions', duration_policy='planning_assumptions',
        activity_id_registry=activity_id_registry,
        input_fingerprint=_fingerprint(project), intelligence_run_id=run.pk,
        extraction_summary=deepcopy((run.summary or {}).get('extraction_summary') or {}),
        processing_coverage=deepcopy((run.summary or {}).get('processing_coverage') or {}),
        managed_task_ids=[task['id'] for task in tasks], version_id=None, review_id=None,
        document_deliverables=deepcopy(parents), programmatic_assumptions=assumptions,
        scope_selection={**selection_summary, 'excluded_requirements': excluded},
        warnings=[*warnings, {'code': 'programmatic_draft_review', 'severity': 'warning',
                   'message': 'Review estimated task durations, discipline ownership, deliverables and execution dependencies before submitting this generated draft.'}],
        enterprise_generation_summary={'requirement_count': len(facts), 'activity_count': len(deliverables),
            'task_count': len(tasks), 'excluded_requirement_count': len(excluded),
            'working_day_count': working_day_count, 'quality': quality,
            'project_start_date': start.isoformat(), 'target_finish_date': end.isoformat(),
            'calculated_finish_date': finish, 'calendar_basis': calendar_basis,
            'allocation_method': 'discipline_workflow_cpm', 'ai_used': False,
            'project_type': context.get('project_type'), 'requirement_scope': 'all'},
    )
    _persist(project, state, actor, 'simple_plan.programmatic_draft_created', before)
    return plan_state(project, actor)
