"""Read-only, deterministic scheduling proposals for the existing task register.

The registered horizon is a planning assumption, not evidence of contract award.
No inference here confirms a source requirement or changes a project record.
"""
from collections import deque
from copy import deepcopy
from math import ceil
import re
from types import SimpleNamespace

from django.db.models import Q

from ..models import ProjectScheduleConfiguration, WorkflowTemplate
from .generation_plan import FAMILY_TEMPLATE_CODES, classify_deliverable
from .operational_jobs import canonical_fingerprint
from .fixed_horizon_proposal import protected_work_ids

ALGORITHM_VERSION = '4-generic-document-evidence'
_WINDOWS = {
    'mobilization': (0, .08), 'survey': (.05, .18), 'basis': (.12, .28),
    'study': (.25, .55), 'engineering': (.40, .75), 'review': (.68, .85),
    'package': (.76, .94), 'closeout': (.90, 1), 'recurring': (0, 1),
}
_DURATIONS = {'mobilization': 8, 'survey': 10, 'basis': 12, 'study': 20,
              'engineering': 27, 'review': 10, 'package': 15, 'closeout': 10, 'recurring': 1}


def proposal_context(project, state, calendar_record):
    """Read all scheduling inputs once; hashes include values, not only timestamps."""
    configuration = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).first()
    overrides = list(configuration.overrides.filter(is_deleted=False, is_active=True).order_by('priority', 'pk').values(
        'scope_type', 'scope_key', 'workflow_template_id', 'priority',
    )) if configuration else []
    selected_ids = [row['workflow_template_id'] for row in overrides]
    if configuration:
        selected_ids.append(configuration.workflow_template_id)
    templates = []
    for template in WorkflowTemplate.objects.filter(
        Q(project=project) | Q(project__isnull=True) | Q(pk__in=selected_ids),
        is_deleted=False, status='active',
    ).prefetch_related('stages').order_by('code', '-version', '-pk'):
        templates.append({
            'id': template.pk, 'code': template.code, 'version': template.version,
            'project_id': template.project_id,
            'stages': [{'code': stage.code, 'name': stage.name, 'duration_days': float(stage.duration_days),
                        'activity_type': stage.activity_type, 'relationship': stage.relationship_to_previous,
                        'lag_days': float(stage.lag_days), 'sequence': stage.sequence,
                        'responsible_party': stage.responsible_party, 'activity_name_template': stage.activity_name_template,
                        'progress_weight': float(stage.progress_weight), 'is_release_gate': stage.is_release_gate}
                       for stage in template.stages.all() if not stage.is_deleted],
        })
    files = [{'id': item.pk, 'filename': item.original_filename, 'category': item.category,
              'parse_status': item.parse_status, 'text': item.extracted_text, 'updated_at': item.updated_at.isoformat()}
             for item in project.files.filter(is_deleted=False).order_by('pk')]
    calendar = {
        'id': calendar_record.pk if calendar_record else None,
        'weekdays': calendar_record.working_weekdays if calendar_record else [0, 1, 2, 3, 4],
        'hours_per_day': float(calendar_record.hours_per_day) if calendar_record else 8,
        'exceptions': list(calendar_record.exceptions.filter(is_deleted=False).order_by('date').values(
            'date', 'is_working', 'working_hours', 'name',
        )) if calendar_record else [],
    }
    values = {
        'algorithm': ALGORITHM_VERSION, 'project_id': project.pk, 'name': project.name,
        'scope': project.scope_summary, 'phase': project.phase, 'exclusions': project.exclusions,
        'start_date': project.effective_date, 'finish_date': project.planned_end_date,
        'calendar_overrides': project.calendar_overrides, 'review_cycle_overrides': project.review_cycle_overrides,
        'calendar': calendar, 'files': files, 'templates': templates, 'overrides': overrides,
        'default_template_id': configuration.workflow_template_id if configuration else None,
        'configuration_settings': configuration.settings if configuration else {},
        'dependency_policy': 'source_or_planner',
        'duration_policy': 'source_only',
    }
    return values, canonical_fingerprint(values)


def source_constraints(files):
    """Quote explicit requirements without turning nearby numbers into logic."""
    constraints, seen = [], set()
    for source in files:
        if (source.get('is_deleted') or source.get('parse_status', 'done') != 'done'
                or source.get('category') == 'output_schedule_sample'):
            continue
        text = source.get('text') or ''

        def add(kind, value, message, start, end, **details):
            identity = (source['id'], kind, value, start)
            if identity in seen:
                return
            seen.add(identity)
            constraints.append({'kind': kind, 'value': value, 'message': message,
                                'status': 'requires_review', 'executable': False,
                                'applicability_verified': False, **details,
                                'anchor_status': 'unconfirmed' if kind == 'relative_weeks' else None,
                                'source_references': [{'file_id': source['id'], 'filename': source['filename'],
                                    'category': source.get('category', 'other'),
                                    'locator': {'line': text[:start].count('\n') + 1,
                                                'character_start': start, 'character_end': end},
                                    'excerpt': text[start:end][:650]}]})

        # Literal grammatical expressions may wrap across lines. No neighboring
        # row or section heading supplies a missing subject, unit or anchor.
        review_patterns = (
            r'\breview(?:[ \t]+(?:period|cycle))?[ \t]*[:=]?[ \t]+(?P<days>\d{1,3})[ \t]+working[ \t]+days?\b',
            r'\breview(?:\s+(?:period|cycle))?(?:\s+(?:shall|must|will|be|is|of|take|takes|require|requires|within|allow|allowed)){1,6}\s+(?P<days>\d{1,3})\s+working\s+days?\b',
            r'\b(?P<days>\d{1,3})\s+working\s+days?\)?(?:\s+(?:time|duration|required|by|company|client|allowed)){0,8}\s+for(?:\s+(?:the|company|client)){0,2}\s+review\b',
        )
        for pattern in review_patterns:
            for match in re.finditer(pattern, text, re.I):
                days = int(match.group('days'))
                add('review_days', days, f'Source states a review requirement of {days} working days. This is not an individual activity planned duration.', match.start(), match.end())
        for match in re.finditer(r'\b(?P<weeks>\d{1,3})\s+weeks?\)?\s+(?:after|from|following|of)\s+(?P<anchor>[^.!?\f|;]+)', text, re.I):
            anchor = re.sub(r'\s+', ' ', match.group('anchor')).strip()
            if not anchor or len(anchor) > 180:
                continue
            weeks = int(match.group('weeks'))
            add('relative_weeks', weeks, f'Source states {weeks} weeks relative to the cited event. The anchor date and applicability remain unconfirmed.', match.start(), match.end(), anchor_text=anchor)
        for match in re.finditer(r'^[^\n\f]*(?:\bshall\b|\bmust\b|\bbefore\b|\bafter\b|\bprior to\b|\bconstraint\b|\bno later than\b|\bdepends on\b)[^\n\f]*$', text, re.I | re.M):
            quote = match.group(0).strip()
            add('constraint_candidate', quote, 'Review this quoted requirement. No activity relationship has been inferred.', match.start(), match.end())
    return constraints


def _classification(task):
    title = re.sub(r'\s+', ' ', task['title']).casefold()
    normal = re.sub(r'[^a-z0-9]+', ' ', title)
    base = classify_deliverable(SimpleNamespace(canonical_name=task['title'], document_number=task.get('document_number', '')))
    setup_titles = ('master deliverable register', 'master document register', 'engineering document deliverable register',
                    'engineering deliverable register', 'planning package', 'document numbering procedure',
                    'document coding procedure', 'document control procedure')
    if normal in {'mdr', 'eddr'} or any(normal == name or normal.startswith(name + ' ') for name in setup_titles):
        return 'mobilization', 'plan_procedure', None
    percent = re.search(r'\b(30|60|90)\s*%', title)
    if ('audit' in title or 'review' in title) and percent:
        return 'review', base['workflow_family'], int(percent.group(1)) / 100
    if any(term in normal for term in ('close out', 'closeout', 'final dossier', 'final documentation', 'handover')):
        return 'closeout', 'final_dossier', None
    if re.search(r'\b(weekly|monthly|recurring)\b', title) and ('report' in title or 'meeting' in title):
        return 'recurring', 'recurring_report', None
    if any(term in title for term in ('project definition report', 'pdr', 'epc enquiry', 'tender package', 'cost estimate')):
        return 'package', 'tender_package' if 'cost estimate' not in title else 'cost_estimate', None
    if 'audit' in title or 'design review' in title:
        return 'review', 'technical_study', None
    if any(term in title for term in ('site visit', 'site survey', 'data collection', 'existing facilities survey')):
        return 'survey', 'inspection_report', None
    if any(term in title for term in ('design basis', 'basis of design')) or re.search(r'\bphilosophy\b', title):
        return 'basis', 'plan_procedure', None
    if base['workflow_family'] == 'plan_procedure':
        return ('engineering' if 'specification' in title or 'scope of work' in title else 'mobilization'), base['workflow_family'], None
    if base['workflow_family'] in {'technical_study', 'inspection_report'}:
        return 'study', base['workflow_family'], None
    return 'engineering', base['workflow_family'], None


def _template_estimate(task, phase, family, context, review_days):
    templates = context['templates']
    chosen_id = None
    for scope in ('deliverable', 'discipline'):
        match = next((row for row in context['overrides'] if row['scope_type'] == scope and row['scope_key'].casefold() ==
                      (task['title'] if scope == 'deliverable' else task['discipline']).casefold()), None)
        if match:
            chosen_id = match['workflow_template_id']
            break
    if chosen_id is None:
        chosen_id = context['default_template_id']
    chosen = next((row for row in templates if row['id'] == chosen_id), None)
    if chosen is None:
        options = [row for row in templates if row['code'] == FAMILY_TEMPLATE_CODES.get(family)]
        chosen = next((row for row in options if row['project_id'] == context['project_id']), None) or next(iter(options), None)
    if chosen and chosen['stages'] and phase not in {'recurring', 'review'}:
        duration = 0
        for stage in chosen['stages']:
            if stage['activity_type'] in {'start_milestone', 'finish_milestone'}:
                continue
            value = stage['duration_days']
            if review_days and re.search(r'(company|client).*review|review.*(company|client)', f"{stage['code']} {stage['name']}", re.I):
                value = review_days
            duration += max(0, ceil(value))
        return max(1, duration), f"Configured workflow {chosen['code']} v{chosen['version']} stage estimates, aggregated as a proposed deliverable duration."
    duration = review_days if phase == 'review' and review_days else _DURATIONS[phase]
    return duration, f'Proposed {phase} effort window; this duration is an estimate, not an extracted source duration.'


def _validate_network(tasks):
    ids = {task['id'] for task in tasks}
    if len(ids) != len(tasks):
        raise ValueError('Each scheduled task must have a unique ID.')
    incoming = {task['id']: set(task.get('depends_on') or []) for task in tasks}
    if any(deps - ids or key in deps for key, deps in incoming.items()):
        raise ValueError('Existing dependencies must reference other tasks in this plan.')
    remaining = {key: len(value) for key, value in incoming.items()}
    outgoing = {key: [] for key in ids}
    for successor, dependencies in incoming.items():
        for predecessor in dependencies:
            outgoing[predecessor].append(successor)
    queue = deque(key for key, value in remaining.items() if not value)
    visited = set()
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        for successor in outgoing[key]:
            remaining[successor] -= 1
            if not remaining[successor]:
                queue.append(successor)
    if visited != ids:
        raise ValueError('Existing dependencies contain a cycle. Correct the links before building a schedule.')


def retain_supported_dependencies(tasks):
    """Remove only unconfirmed generated cross-deliverable guesses in a new preview.

The user's five-stage workflow and planner/source relationships are retained.
Title similarity or a citation to a deliverable name is not predecessor evidence.
"""
    tasks, removed = deepcopy(tasks), 0
    by_id = {task['id']: task for task in tasks}
    protected = protected_work_ids(tasks)
    for task in tasks:
        if task['id'] in protected or 'depends_on' not in (task.get('schedule_generated_fields') or []):
            continue
        keep = []
        for predecessor in task.get('depends_on') or []:
            other = by_id.get(predecessor, {})
            internal = (task.get('parent_deliverable_id') is not None
                        and task.get('parent_deliverable_id') == other.get('parent_deliverable_id'))
            details = [link for link in task.get('dependency_details') or [] if link.get('task_id') == predecessor]
            rationale = (task.get('dependency_rationales') or {}).get(predecessor) or {}
            evidence = details or ([rationale] if rationale else [])
            inferred = evidence and all(link.get('status') == 'proposed'
                                        and link.get('evidence_type') == 'planning_inference'
                                        and link.get('source') != 'workflow_template' for link in evidence)
            if inferred and not internal:
                removed += 1
            else:
                keep.append(predecessor)
        task['depends_on'] = keep
        task['dependency_details'] = [link for link in task.get('dependency_details') or [] if link.get('task_id') in keep]
        task['dependency_rationales'] = {key: value for key, value in (task.get('dependency_rationales') or {}).items() if key in keep}
    return tasks, removed


def build_proposed_tasks(tasks, context, calendar):
    """Propose phase windows and a sparse technical network; never row-order logic."""
    source_only = context.get('dependency_policy') == 'source_or_planner'
    proposed, removed = retain_supported_dependencies(tasks) if source_only else (deepcopy(tasks), 0)
    _validate_network(proposed)
    classified = {task['id']: _classification(task) for task in proposed}
    protected = protected_work_ids(proposed)
    constraints = source_constraints(context['files'])
    review_values = {row['value'] for row in constraints if row['kind'] == 'review_days'}
    review_days = next(iter(review_values)) if len(review_values) == 1 else None
    warnings = []
    if source_only:
        warnings.append('MDR titles alone do not define predecessor links. Only existing source/planner links and the selected workflow are retained; missing cross-deliverable logic needs review.')
        if removed:
            warnings.append(f'{removed} unconfirmed inferred predecessor links were removed from this proposal because the documents do not establish those relationships.')
    if len(review_values) > 1:
        warnings.append('Sources contain different review periods; no single period has been applied automatically.')
    if any(row['kind'] == 'relative_weeks' for row in constraints):
        warnings.append('Source milestones are relative to contract award/effective date. Confirm that anchor separately; the registered project dates remain the planning horizon.')
    last_day = calendar.on_or_before(context['finish_date'])
    horizon = calendar.index_of(last_day) + 1
    if horizon < 1:
        raise ValueError('The registered project dates contain no working days.')
    assumptions = [
        'Phase windows use the registered project start and finish as a proposed planning horizon, not as proof of contract-award dates.',
        'Activities within the same phase may run in parallel. Resource capacity has not been levelled.',
        'New links are proposed technical gates, not confirmed contractual dependencies. Review every proposed link and estimate before approval.',
        'Workflow stage estimates are aggregated at the existing deliverable level; no source deliverable is replaced or multiplied.',
    ]
    for task in proposed:
        phase, family, percentage = classified[task['id']]
        if task['id'] in protected:
            task['schedule_phase'] = phase
            task['schedule_rationale'] = 'Work already started and its upstream schedule are retained.'
            continue
        generated = set(task.get('schedule_generated_fields') or [])
        rationale = []
        duration, duration_reason = _template_estimate(task, phase, family, context, review_days)
        if phase == 'recurring':
            duration = horizon
            duration_reason = 'Recurring reporting spans the planning horizon; this is a coverage period, not continuous employee effort.'
        if task.get('duration_days') is None and task.get('effort_hours'):
            task['duration_days'] = max(1, ceil(float(task['effort_hours']) / max(1, context['calendar']['hours_per_day'])))
            task['duration_source'] = 'proposed'
            generated.add('duration_days')
            rationale.append('Duration proposed from the recorded effort and working hours per day.')
        elif task.get('duration_days') is None or (task.get('duration_source') == 'proposed' and not task.get('effort_hours')):
            task['duration_days'] = duration
            task['duration_source'] = 'proposed'
            generated.add('duration_days')
            rationale.append(duration_reason)
        else:
            rationale.append('Existing planner duration retained.')
        if not task.get('planned_start_date') or 'planned_start_date' in generated:
            actual_duration = max(1, ceil(float(task.get('duration_days') or 1)))
            fraction = percentage if percentage is not None else _WINDOWS[phase][0]
            index = round(max(0, horizon - 1) * fraction)
            if phase == 'closeout':
                index = max(0, horizon - actual_duration)
            if percentage is not None:
                index = max(0, index - actual_duration + 1)
            task['planned_start_date'] = calendar.date_at(index).isoformat()
            generated.add('planned_start_date')
            rationale.append(f"Proposed {'%d%% review' % round(percentage * 100) if percentage is not None else phase} window within the registered horizon.")
        else:
            rationale.append('Existing planner start constraint retained.')
        task['schedule_phase'] = phase
        task['schedule_rationale'] = ' '.join(rationale)
        task['schedule_generated_fields'] = sorted(generated)
        task.setdefault('dependency_rationales', {})

    # Only unambiguous same-discipline technical gates are proposed. Multiple
    # drawings/studies are peers: do not chain them by spreadsheet position.
    for task in ([] if source_only else proposed):
        if task['id'] in protected or task.get('depends_on'):
            continue
        phase = classified[task['id']][0]
        prerequisite = {'study': 'basis', 'engineering': 'basis', 'basis': 'survey', 'package': 'review', 'closeout': 'package'}.get(phase)
        if not prerequisite:
            continue
        candidates = [entry for entry in proposed if entry['id'] != task['id'] and entry['discipline'] == task['discipline']
                      and classified[entry['id']][0] == prerequisite]
        if not candidates and prerequisite == 'survey':
            candidates = [entry for entry in proposed if classified[entry['id']][0] == 'survey' and entry['discipline'] in {'general', 'survey'}]
        if not 1 <= len(candidates) <= 3:
            continue
        for predecessor in sorted(candidates, key=lambda item: str(item['id'])):
            task.setdefault('depends_on', []).append(predecessor['id'])
            try:
                _validate_network(proposed)
            except ValueError:
                task['depends_on'].remove(predecessor['id'])
                continue
            task['dependency_rationales'][predecessor['id']] = {
                'status': 'proposed', 'relationship_type': 'FS', 'lag_days': 0,
                'rationale': f"Proposed technical gate: {predecessor['title']} provides the {prerequisite} input for this {phase} activity. Confirm applicability.",
                'source_references': deepcopy(predecessor.get('source_references') or []),
                'evidence_type': 'planning_inference',
            }
            task['schedule_generated_fields'] = sorted(set(task['schedule_generated_fields']) | {'depends_on'})
    _validate_network(proposed)
    return proposed, constraints, assumptions, warnings


def schedule_status(tasks, *, applied=False):
    provisional = sum(task.get('duration_source') == 'proposed' for task in tasks)
    relationships = sum(len(task.get('depends_on') or []) for task in tasks)
    defaults = sum(task.get('duration_source') == 'proposed' and not task.get('effort_hours') and
                   float(task.get('duration_days') or 0) == 5 for task in tasks)
    parent_ids = {task['id']: task.get('parent_deliverable_id') for task in tasks}
    workflow_tasks = [task for task in tasks if task.get('parent_deliverable_id')]
    cross_deliverable = sum(parent_ids.get(predecessor) != task.get('parent_deliverable_id')
                            for task in workflow_tasks for predecessor in task.get('depends_on') or [])
    workflow_windows = sum(bool(task.get('planned_start_date')) for task in workflow_tasks
                           if task.get('workflow_stage_sequence') == 1)
    if not tasks:
        state, message = 'empty', 'Add activities or analyse the project documents.'
    elif workflow_tasks and not cross_deliverable and not workflow_windows:
        state, message = 'unsequenced', 'Workflow stages are connected, but deliverables have no individual start constraints or cross-deliverable links. Build a schedule proposal to review the project sequence.'
    elif applied or any(task.get('schedule_generated_fields') for task in tasks):
        state, message = 'proposed', 'Proposed schedule — review dates, estimates and dependency links before approval.'
    elif provisional and relationships == 0 and all(not task.get('planned_start_date') for task in tasks):
        label = f'{defaults} activities have five-day placeholder durations' if defaults else 'Activities have proposed durations'
        state, message = 'unsequenced', f'{label} and the plan has no predecessor links or individual start constraints, so activities currently start together. Build a schedule proposal to review the sequence.'
    else:
        state, message = 'planned', 'Current planner schedule; follow the normal review and approval process.'
    return {'state': state, 'provisional_count': provisional, 'relationship_count': relationships, 'message': message,
            'cross_deliverable_relationship_count': cross_deliverable, 'workflow_start_constraint_count': workflow_windows}
