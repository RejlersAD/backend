"""Build an editable planning proposal from one saved analysis, without AI calls.

Source scope and literal relationships remain evidence. Workflow durations,
technical sequencing and calculated dates are separately labelled proposals.
"""
from collections import defaultdict, deque
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

from ..config import DEFAULT_CALENDAR, DISCIPLINE_NAME_BY_CODE, DISCIPLINE_RESPONSIBLE_ROLE
from ..models import ProjectScheduleConfiguration, WorkCalendar, WorkflowTemplate
from .activity_generator import (
    _IdCounter, _build_chain, _categorize_deliverable, _configured_workflow,
    _determine_relationship_type, _merged_review_days,
)
from .cpm import WorkdayCalendar, _edge_weight, _finish_date, calculate_backward_pass
from .document_plan import source_files
from .planning_package_request import PlanningPackageRequestError


POLICY = 'planning_package'
VERSION = 'planning-package-proposal/1'


def _calendar(project):
    stored = WorkCalendar.objects.filter(project=project, is_deleted=False, is_default=True).order_by('pk').first()
    settings = {**DEFAULT_CALENDAR, **(project.calendar_overrides or {})}
    if stored:
        weekdays = list(stored.working_weekdays)
        exceptions = list(stored.exceptions.filter(is_deleted=False).order_by('date').values('date', 'is_working'))
        snapshot = {
            'id': stored.pk, 'name': stored.name, 'source': 'project_calendar',
            'working_weekdays': weekdays, 'hours_per_day': float(stored.hours_per_day),
            'timezone': stored.timezone,
            'exceptions': [{**item, 'date': item['date'].isoformat()} for item in exceptions],
        }
    else:
        count = int(settings['working_days_per_week'])
        if not 1 <= count <= 7:
            raise PlanningPackageRequestError('Choose between one and seven working days for the planning calendar.')
        weekdays = list(range(count))
        snapshot = {
            'id': None, 'name': 'Planning Package Calendar',
            'source': 'project_settings' if project.calendar_overrides else 'versioned_planning_default',
            'working_weekdays': weekdays, 'hours_per_day': float(settings['hours_per_day']),
            'timezone': settings.get('timezone') or 'Asia/Dubai', 'exceptions': [],
        }
        stored = SimpleNamespace(working_weekdays=weekdays, exceptions=SimpleNamespace(filter=lambda **_kwargs: []))
    if not weekdays or float(snapshot['hours_per_day']) <= 0:
        raise PlanningPackageRequestError('The planning calendar requires working weekdays and positive hours per day.')
    snapshot['rule_version'] = VERSION
    settings.update(working_days_per_week=len(weekdays), hours_per_day=snapshot['hours_per_day'])
    return WorkdayCalendar(stored, project.effective_date), settings, snapshot


def _configuration(project):
    configuration = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).select_related(
        'workflow_template', 'dependency_template',
    ).prefetch_related('workflow_template__stages', 'overrides__workflow_template__stages').first()
    if configuration:
        if configuration.workflow_template.is_deleted or configuration.workflow_template.status != 'active':
            raise PlanningPackageRequestError('Select an active workflow template before building the Planning Package.')
        if configuration.workflow_template.project_id not in (None, project.pk):
            raise PlanningPackageRequestError('The workflow template does not belong to this planning project.')
        overrides = list(configuration.overrides.filter(is_deleted=False, is_active=True).select_related('workflow_template'))
        if any(row.workflow_template.is_deleted or row.workflow_template.status != 'active'
               or row.workflow_template.project_id not in (None, project.pk) for row in overrides):
            raise PlanningPackageRequestError('A workflow override does not belong to this planning project.')
        return configuration, overrides
    template = WorkflowTemplate.objects.filter(
        project__isnull=True, is_system=True, is_default=True, status='active', is_deleted=False,
    ).prefetch_related('stages').order_by('-version').first()
    if not template:
        raise PlanningPackageRequestError('Configure an active project workflow or standard workflow before building the Planning Package.')
    # Resolve the installed versioned default without creating a configuration on preview.
    return SimpleNamespace(pk=None, configuration_version=None, workflow_template=template), []


def _proposal_link(predecessor, *, kind='FS', lag=0, rationale, source='planning_sequence_proposal', references=None):
    return {'id': predecessor, 'type': kind, 'lag_days': lag, 'lag_unit': 'working_days',
            'source': source, 'rationale': rationale, 'review_status': 'proposed',
            'rule_version': VERSION, 'source_references': deepcopy(references or [])}


def _has_path(activities, start, finish):
    outgoing = defaultdict(list)
    for row in activities:
        for link in row.get('predecessors') or []:
            outgoing[link['id']].append(row['id'])
    queue, visited = [start], set()
    while queue:
        current = queue.pop()
        if current == finish:
            return True
        if current not in visited:
            visited.add(current)
            queue.extend(outgoing[current])
    return False


def _calculate_proposal(activities, calendar):
    """Use the retained CPM day/edge conventions for a read-only draft preview."""
    by_id = {item['id']: item for item in activities}
    durations = {key: int(item['original_duration_days']) for key, item in by_id.items()}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    indegree = {key: 0 for key in by_id}
    for successor in activities:
        for edge in successor['predecessors']:
            predecessor = edge['id']
            if predecessor not in by_id:
                raise PlanningPackageRequestError('A proposed relationship references an unavailable activity.')
            weight = _edge_weight(edge['type'], durations[predecessor], durations[successor['id']], edge['lag_days'])
            outgoing[predecessor].append((successor['id'], weight))
            incoming[successor['id']].append((predecessor, weight))
            indegree[successor['id']] += 1
    queue = deque(key for key in by_id if not indegree[key])
    order, early = [], {}
    while queue:
        key = queue.popleft()
        order.append(key)
        early[key] = max([0] + [early[pred] + weight for pred, weight in incoming[key]])
        for successor, _weight in outgoing[key]:
            indegree[successor] -= 1
            if not indegree[successor]:
                queue.append(successor)
    if len(order) != len(activities):
        raise PlanningPackageRequestError('The selected source relationships contain a cycle. Review the conflicting relationships.')
    finish = max(early[key] + max(durations[key] - 1, 0) for key in order)
    late, _free = calculate_backward_pass(order, durations, early, outgoing, finish)
    for key, activity in by_id.items():
        activity.update(start_date=calendar.date_at(early[key]).isoformat(),
                        finish_date=_finish_date(calendar, early[key], durations[key]).isoformat(),
                        total_float_days=late[key] - early[key], is_critical=late[key] == early[key])
    return calendar.date_at(finish).isoformat()


def build_planning_package(project, run):
    if run is None or run.project_id != project.pk or run.is_deleted or run.status != 'succeeded':
        raise PlanningPackageRequestError('Select a completed analysis belonging to this planning project.')
    if not project.effective_date:
        raise PlanningPackageRequestError('Set the project start date before building the Planning Package.')
    from .planning_package_sources import build_planning_package_sources
    from .document_intelligence import compile_run_intelligence

    files = source_files(project)
    facts = list(run.facts.filter(is_deleted=False).values(
        'id', 'fact_type', 'value', 'status', 'extraction_method', 'source_file_id', 'source_locator', 'source_excerpt',
    ))
    scope = build_planning_package_sources(files, facts)
    from .preview_confirmation import review_fingerprint
    confirmation = (run.summary or {}).get('preview_confirmation') or {}
    if confirmation.get('confirmed_at') and confirmation.get('review_fingerprint') == review_fingerprint(run):
        choices = (confirmation.get('preview') or {}).get('disciplines') or {}
        retained = []
        for item in scope['deliverables']:
            choice = choices.get(item.get('discipline'))
            names = {' '.join(str(name).split()).casefold() for name in (choice or {}).get('deliverables', [])}
            excluded = choice is not None and (choice.get('in_scope') is False or
                       ('deliverables' in choice and ' '.join(item['title'].split()).casefold() not in names))
            if excluded:
                scope.setdefault('excluded_inventory', []).append({**item, 'exclusion_reason': 'confirmed_preview_selection'})
            else:
                retained.append(item)
        scope['deliverables'] = retained
        ids = {item['id'] for item in retained}
        selected_dependencies = []
        for link in scope['dependencies']:
            if link['predecessor_id'] in ids and link['successor_id'] in ids:
                selected_dependencies.append(link)
            else:
                scope['unresolved_dependencies'].append({**link, 'reason': 'An endpoint was excluded in the confirmed preview.'})
        scope['dependencies'] = selected_dependencies
    deliverables = scope['deliverables']
    if not deliverables:
        raise PlanningPackageRequestError('This analysis has no clear source deliverables available for a Planning Package. Review the source evidence.')
    calendar, calendar_settings, calendar_snapshot = _calendar(project)
    configuration, overrides = _configuration(project)
    assumptions = [
        {'field': 'workflow_durations', 'source': 'workflow_template', 'status': 'proposed',
         'message': 'Workflow stage durations are planning estimates from the selected template; they are not extracted source durations.'},
        {'field': 'calendar', 'source': calendar_snapshot['source'], 'status': 'proposed', 'value': calendar_snapshot,
         'message': 'Draft dates use this recorded working calendar. Review it before baseline approval.'},
        {'field': 'project_start', 'source': 'project_input', 'status': 'proposed', 'value': project.effective_date.isoformat()},
        {'field': 'sequence', 'source': VERSION, 'status': 'proposed',
         'message': 'Technical category gates are planning proposals within each discipline; parallel deliverables remain parallel.'},
    ]
    wbs = [{'code': '1', 'name': project.name, 'level': 0, 'parent_code': None}]
    discipline_nodes, chains, categories = {}, {}, {}
    activities = [{'id': 'PKG-START', 'name': 'Planning Package Start', 'wbs_code': '1', 'discipline': '',
                   'original_duration_days': 0, 'duration_unit': 'working_days', 'is_milestone': True,
                   'activity_type': 'start_milestone',
                   'predecessors': [], 'source_references': [], 'proposal_status': 'draft', 'evidence_policy': POLICY}]
    for index, item in enumerate(deliverables, 1):
        discipline = item.get('discipline') or 'not_specified'
        if discipline not in discipline_nodes:
            code = f'1.{len(discipline_nodes) + 1}'
            discipline_nodes[discipline] = code
            wbs.append({'code': code, 'name': DISCIPLINE_NAME_BY_CODE.get(discipline, 'General / discipline to review' if discipline in {'not_specified', 'Not Specified', 'general'} else discipline),
                        'level': 1, 'parent_code': '1', 'discipline': discipline})
        code = f'{discipline_nodes[discipline]}.{index}'
        wbs.append({'code': code, 'name': item['title'], 'level': 2, 'parent_code': discipline_nodes[discipline],
                    'discipline': discipline, 'source_entity_id': item['id'], 'source_references': item.get('source_references') or []})
        steps, template = _configured_workflow(project, discipline, item['title'], configuration=configuration, overrides=overrides)
        if not template or not steps:
            raise PlanningPackageRequestError('The selected planning workflow must contain explicit versioned stages.')
        if any(step.get('activity_type') not in {'task', 'start_milestone', 'finish_milestone'} for step in steps):
            raise PlanningPackageRequestError('This Planning Package supports task and milestone workflow stages; level-of-effort stages require separate planning.')
        if any(not Decimal(str(step.get('duration', 0))).is_finite()
               or Decimal(str(step.get('duration', 0))) != Decimal(str(step.get('duration', 0))).to_integral_value()
               or Decimal(str(step.get('duration', 0))) < 0
               or (step.get('milestone') and Decimal(str(step.get('duration', 0))) != 0)
               or (not step.get('milestone') and Decimal(str(step.get('duration', 0))) == 0) for step in steps):
            raise PlanningPackageRequestError('The current planning engine requires whole working-day workflow durations.')
        if any(not Decimal(str(step.get('lag_days', 0))).is_finite()
               or Decimal(str(step.get('lag_days', 0))) != Decimal(str(step.get('lag_days', 0))).to_integral_value()
               for step in steps):
            raise PlanningPackageRequestError('The current planning engine requires whole working-day workflow lags.')
        prefix = 'PKG-' + sha256(str(item['id']).encode()).hexdigest()[:12].upper()
        chain = _build_chain(ids=_IdCounter(), prefix=prefix, wbs_code=code, discipline=discipline,
                             role=DISCIPLINE_RESPONSIBLE_ROLE.get(discipline, 'Engineer'), steps=steps,
                             name=item['title'], start_date=project.effective_date, calendar=calendar_settings,
                             review_days=_merged_review_days(project), workflow_template=template,
                             predecessors=[_proposal_link('PKG-START', rationale='Planning start gate.', source='planning_package_gate')])
        for row, step in zip(chain, steps):
            category = _categorize_deliverable(item['title'])[0]
            row.update(source_entity_id=item['id'], source_references=deepcopy(item.get('source_references') or []),
                       source_fact_ids=list(item.get('source_fact_ids') or []), analysis_run_id=run.pk,
                       evidence_policy=POLICY, proposal_status='draft', duration_source='workflow_template',
                       activity_type=step['activity_type'],
                       duration_unit='working_days', duration_basis=deepcopy(template),
                       discipline_basis=deepcopy(item.get('discipline_basis')), document_number=item.get('document_number'),
                       source_group=item.get('source_group'), date_authority='planning_proposal',
                       workflow_family={'design_basis': 'plan_procedure', 'specifications': 'plan_procedure',
                                        'studies': 'technical_study', 'drawings': 'drawing'}.get(category, 'engineering_document'))
            for link in row['predecessors']:
                link.setdefault('review_status', 'proposed')
                link.setdefault('rule_version', VERSION)
                link.setdefault('rationale', 'Ordered stages from the selected workflow template.')
        chains[item['id']] = chain
        categories[item['id']] = _categorize_deliverable(item['title'])
        activities.extend(chain)

    explicit_successors = set()
    for dependency in scope['dependencies']:
        predecessor, successor = chains[dependency['predecessor_id']], chains[dependency['successor_id']]
        kind = dependency['type']
        if Decimal(str(dependency['lag_days'])) != Decimal(str(dependency['lag_days'])).to_integral_value():
            raise PlanningPackageRequestError('The current planning engine requires whole working-day relationship lags.')
        source_row = predecessor[0] if kind in {'SS', 'SF'} else predecessor[-1]
        target_row = successor[-1] if kind in {'FF', 'SF'} else successor[0]
        edge = _proposal_link(source_row['id'], kind=kind, lag=dependency['lag_days'],
                              rationale='Explicit relationship retained from source evidence.', source='document_fact',
                              references=dependency.get('source_references'))
        edge.update(source_fact_ids=dependency.get('source_fact_ids') or [], review_status='detected')
        target_row['predecessors'].append(edge)
        explicit_successors.add(dependency['successor_id'])

    skipped_gates = 0
    for item in deliverables:
        category, priority = categories[item['id']]
        if item['id'] in explicit_successors or category == 'other':
            continue
        earlier = [candidate for candidate in deliverables
                   if candidate.get('discipline') == item.get('discipline')
                   and categories[candidate['id']][0] != 'other' and categories[candidate['id']][1] < priority]
        if not earlier:
            continue
        nearest = max(categories[candidate['id']][1] for candidate in earlier)
        for predecessor in (candidate for candidate in earlier if categories[candidate['id']][1] == nearest):
            kind, lag = _determine_relationship_type(categories[predecessor['id']][0], category, predecessor['title'], item['title'])
            pred_chain, succ_chain = chains[predecessor['id']], chains[item['id']]
            pred_row = pred_chain[0] if kind in {'SS', 'SF'} else pred_chain[-1]
            succ_row = succ_chain[-1] if kind in {'FF', 'SF'} else succ_chain[0]
            if _has_path(activities, succ_row['id'], pred_row['id']):
                skipped_gates += 1
                continue
            succ_row['predecessors'].append(_proposal_link(pred_row['id'], kind=kind, lag=lag,
                rationale=f'Proposed {categories[predecessor["id"]][0]} to {category} sequence; review the technical dependency.',
                references=(predecessor.get('source_references') or []) + (item.get('source_references') or [])))

    activities.append({'id': 'PKG-FINISH', 'name': 'Planning Package Finish', 'wbs_code': '1', 'discipline': '',
                       'original_duration_days': 0, 'duration_unit': 'working_days', 'is_milestone': True,
                       'activity_type': 'finish_milestone',
                       'predecessors': [_proposal_link(chain[-1]['id'], rationale='All selected deliverable workflows finish before package completion.', source='planning_package_gate') for chain in chains.values()],
                       'source_references': [], 'proposal_status': 'draft', 'evidence_policy': POLICY})
    finish = _calculate_proposal(activities, calendar)
    logic = [{'predecessor_id': edge['id'], 'successor_id': row['id'], 'type': edge['type'], **deepcopy(edge)}
             for row in activities for edge in row['predecessors']]
    stage_labels = {'IFR': 'ifr_issue', 'COMPANY_REVIEW': 'company_review', 'IFA': 'ifa_issue',
                    'COMPANY_APPROVAL': 'company_approval', 'FINAL_ISSUE': 'final_issue'}
    eddr = []
    for item in deliverables:
        chain = chains[item['id']]
        row = {'source_entity_id': item['id'], 'discipline': chain[0]['discipline'], 'deliverable_name': item['title'],
               'document_number': item.get('document_number'), 'wbs_code': chain[0]['wbs_code'],
               'document_status': 'Planned', 'current_workflow_status': 'Proposed', 'source_references': item.get('source_references') or [],
               'date_authority': 'planning_proposal'}
        for activity in chain:
            label = stage_labels.get(activity.get('workflow_stage_code'))
            if label:
                row.update({f'{label}_activity_id': activity['id'], f'{label}_date': activity['finish_date']})
        row.update(final_issue_activity_id=chain[-1]['id'], final_issue_date=chain[-1]['finish_date'])
        eddr.append(row)
    hours = sum(row['original_duration_days'] for row in activities) * calendar_snapshot['hours_per_day']
    manhours = {'basis': {'status': 'proposed', 'hours_per_day': calendar_snapshot['hours_per_day'],
                         'assumption': 'One resource unit for each estimated stage duration; this is not an approved staffing or effort budget.'},
                'by_discipline': [], 'grand_total_man_hours': hours}
    for discipline in discipline_nodes:
        days = sum(row['original_duration_days'] for row in activities if row['discipline'] == discipline)
        manhours['by_discipline'].append({'discipline': discipline, 'man_hours': days * calendar_snapshot['hours_per_day'], 'total_working_days': days})
    validation = [{'code': 'planning_proposal_review_required', 'severity': 'warning', 'blocks': ['approval', 'export'],
                   'message': 'This is an editable planning proposal. Review workflow durations, calendar and proposed logic before baseline approval.'}]
    if scope['unresolved_dependencies']:
        validation.append({'code': 'unresolved_source_dependencies', 'severity': 'warning', 'blocks': ['approval'],
                           'message': f'{len(scope["unresolved_dependencies"])} source relationship statements require endpoint or timing review.'})
    if skipped_gates:
        validation.append({'code': 'proposed_gate_cycle_avoided', 'severity': 'warning', 'message': f'{skipped_gates} proposed category gates were omitted to preserve an acyclic source network.'})
    intelligence = compile_run_intelligence(run, include_confirmation=False)
    intelligence['schedule_engine'] = {
        'policy': POLICY, 'engine_version': VERSION, 'analysis_run_id': run.pk,
        'intelligence_run_id': run.pk, 'source_analysis_run_id': run.pk, 'generation_mode': POLICY,
        'date_authority': 'planning_proposal', 'project_start': project.effective_date.isoformat(),
        'contractual_finish': project.planned_end_date.isoformat() if project.planned_end_date else None,
        'project_finish_date': finish, 'calendar': calendar_snapshot, 'assumptions': assumptions,
        'configuration_id': configuration.pk, 'configuration_version': configuration.configuration_version,
        'ready_for_calculation': True, 'ready_for_approval': False, 'proposal_status': 'draft',
        'source_deliverables': deliverables, 'register_inventory': deliverables + scope.get('excluded_inventory', []),
        'excluded_inventory': scope.get('excluded_inventory', []), 'unresolved_relationships': scope['unresolved_dependencies'],
        'warnings': scope.get('warnings', []), 'missing_information': [], 'extraction_reports': [],
        'applied_dependency_rules': [], 'configured_workflow_activity_count': sum(len(chain) for chain in chains.values()),
    }
    return {'intelligence': intelligence, 'wbs': wbs, 'activities': activities, 'logic_matrix': logic,
            'eddr': eddr, 'milestones': [row for row in activities if row.get('is_milestone')], 'manhours': manhours,
            'validation': validation,
            'narrative': f'Planning Package proposed from saved analysis {run.pk}: {len(deliverables)} source deliverables, {len(activities)} activities and {len(logic)} relationships. Workflow durations, working calendar and technical sequencing remain reviewable planning assumptions. No source finding or baseline has been approved.'}
