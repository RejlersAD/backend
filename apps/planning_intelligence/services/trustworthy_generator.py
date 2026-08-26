"""Build schedules exclusively from an approved Generation Plan."""
from __future__ import annotations

import datetime
import re

from ..config import (
    DELIVERABLE_WORKFLOW_STEPS, DISCIPLINE_PREFIX_BY_CODE, DISCIPLINE_RESPONSIBLE_ROLE,
)
from ..models import GenerationPlan, WorkflowTemplate
from .activity_generator import (
    _IdCounter, _assign_float, _build_chain, _build_wbs_lookup, _merged_calendar,
    _merged_review_days, _milestone_activity, _resolve_deliverable_wbs,
)


def _family_workflow_steps(family: str, discipline: str):
    from .generation_plan import FAMILY_TEMPLATE_CODES
    code = FAMILY_TEMPLATE_CODES.get(family, 'STANDARD_5_STAGE')
    template = WorkflowTemplate.objects.filter(
        project__isnull=True, code=code, status='active', is_deleted=False,
    ).prefetch_related('stages').order_by('-version').first()
    if not template:
        return DELIVERABLE_WORKFLOW_STEPS, None
    stages = sorted((stage for stage in template.stages.all() if not stage.is_deleted), key=lambda stage: stage.sequence)
    steps = [{
        'sequence': stage.sequence, 'code': stage.code, 'status': stage.code,
        'suffix': stage.activity_name_template.format(
            deliverable='{name}', stage=stage.name, discipline=discipline,
        ),
        'duration': float(stage.duration_days), 'responsible_party': stage.responsible_party,
        'activity_type': stage.activity_type,
        'milestone': stage.activity_type in {'start_milestone', 'finish_milestone'},
        'relationship_to_previous': stage.relationship_to_previous,
        'lag_days': float(stage.lag_days), 'progress_weight': float(stage.progress_weight),
    } for stage in stages]
    return steps, {'code': template.code, 'version': template.version, 'id': template.id}


def _phase_for(entry):
    if entry.workflow_family == 'final_dossier':
        return 'closeout'
    if 20 <= entry.technical_sequence < 40:
        return 'fieldwork'
    if entry.technical_sequence <= 15:
        return 'mobilization'
    return 'engineering'


def build_trustworthy_activities(project, wbs, intelligence):
    plan = GenerationPlan.objects.filter(
        pk=intelligence.get('generation_plan_id'), project=project,
        status__in=['approved', 'superseded'], is_deleted=False,
    ).select_related('basis').prefetch_related(
        'phases', 'decision_gates', 'deliverables__basis_deliverable',
        'dependencies__predecessor__basis_deliverable',
        'dependencies__successor__basis_deliverable',
    ).first()
    if not plan:
        raise ValueError('The approved Generation Plan is unavailable or no longer active.')

    calendar, review_days = _merged_calendar(project), _merged_review_days(project)
    effective_date = plan.basis.effective_date or project.effective_date or datetime.date.today()
    ids, activities = _IdCounter(), []
    disc_wbs, deliverable_wbs = _build_wbs_lookup(wbs)
    start = _milestone_activity(ids, 'Project Start', effective_date, wbs_code='1')
    start['generation_plan_id'] = plan.id
    activities.append(start)

    phase_milestones = {}
    phase_offset_days = 0
    previous_phase_duration = None
    for phase in plan.phases.filter(is_deleted=False).order_by('sequence'):
        if previous_phase_duration is not None:
            phase_offset_days += round(float(previous_phase_duration) * int(calendar.get('man_days_per_month', 22)))
        milestone = _milestone_activity(
            ids, f'{phase.name} - Start', effective_date, wbs_code='1', discipline='pm',
            predecessors=[{
                'id': start['id'], 'type': 'SS', 'lag_days': phase_offset_days,
                'source': 'generation_plan_phase',
                'rationale': f'Approved Generation Plan phase offset: {phase_offset_days} working days.',
                'source_references': phase.source_references,
            }],
        )
        milestone.update({'phase_code': phase.code, 'generation_plan_id': plan.id})
        phase_milestones[phase.code] = milestone
        activities.append(milestone)
        previous_phase_duration = phase.duration_months

    selected = plan.selected_scenario
    entries = [
        entry for entry in plan.deliverables.filter(is_deleted=False).select_related('basis_deliverable')
        if entry.scenario_code in ('', 'common', selected)
    ]
    common = [entry for entry in entries if entry.scenario_code in ('', 'common') and entry.workflow_family not in ('final_dossier', 'recurring_report')]
    branch = [entry for entry in entries if entry.scenario_code not in ('', 'common')]
    closing = [entry for entry in entries if entry.workflow_family == 'final_dossier']
    recurring = [entry for entry in entries if entry.workflow_family == 'recurring_report']
    common.sort(key=lambda item: (item.technical_sequence, item.basis_deliverable_id))
    branch.sort(key=lambda item: (item.technical_sequence, item.basis_deliverable_id))
    closing.sort(key=lambda item: item.basis_deliverable_id)
    recurring.sort(key=lambda item: item.basis_deliverable_id)

    included_ids = {entry.id for entry in entries}
    links = [
        link for link in plan.dependencies.filter(is_deleted=False, status='confirmed')
        if link.predecessor_id in included_ids and link.successor_id in included_ids
    ]
    incoming = {}
    for link in links:
        incoming.setdefault(link.successor_id, []).append(link)
    chains_by_entry = {}

    def build_entry(entry, extra_predecessors=None, occurrence=None):
        item = entry.basis_deliverable
        discipline = item.discipline or 'general'
        role = DISCIPLINE_RESPONSIBLE_ROLE.get(discipline, 'Project Engineer')
        prefix = DISCIPLINE_PREFIX_BY_CODE.get(discipline, re.sub(r'[^A-Z]', '', discipline.upper())[:2] or 'GN')
        display_name = item.canonical_name if occurrence is None else f'{item.canonical_name} #{occurrence}'
        steps, template = _family_workflow_steps(entry.workflow_family, discipline)
        predecessors, predecessor_finishes = [], []
        for link in incoming.get(entry.id, []):
            predecessor_chains = chains_by_entry.get(link.predecessor_id) or []
            if not predecessor_chains:
                continue
            predecessor = predecessor_chains[-1][-1]
            predecessors.append({
                'id': predecessor['id'], 'type': link.relationship_type,
                'lag_days': float(link.lag_days), 'source': 'generation_plan_dependency',
                'generation_dependency_id': link.id, 'rationale': link.rationale,
                'source_references': link.source_references,
            })
            predecessor_finishes.append(datetime.date.fromisoformat(predecessor['finish_date']))
        predecessors.extend(extra_predecessors or [])
        phase_code = _phase_for(entry)
        phase_milestone = phase_milestones.get(phase_code) or start
        if not any(predecessor.get('id') == phase_milestone['id'] for predecessor in predecessors):
            predecessors.append({
                'id': phase_milestone['id'], 'type': 'FS', 'lag_days': 0,
                'source': 'generation_plan_phase', 'rationale': f'{phase_code.title()} phase entry.',
            })
        chain = _build_chain(
            ids=ids, prefix=prefix,
            wbs_code=_resolve_deliverable_wbs(disc_wbs, deliverable_wbs, discipline, item.canonical_name),
            discipline=discipline, role=role, steps=steps, name=display_name,
            start_date=max(predecessor_finishes, default=effective_date), calendar=calendar,
            review_days=review_days, predecessors=predecessors, workflow_template=template,
        )
        for activity in chain:
            activity.update({
                'generation_plan_id': plan.id, 'plan_deliverable_id': entry.id,
                'basis_deliverable_id': item.id, 'workflow_family': entry.workflow_family,
                'scenario_code': entry.scenario_code, 'phase_code': _phase_for(entry),
                'document_number': item.document_number, 'document_revision': item.document_revision,
                'source_references': item.source_references, 'recurrence': entry.recurrence,
                'recurrence_occurrence': occurrence,
            })
        activities.extend(chain)
        chains_by_entry.setdefault(entry.id, []).append(chain)
        return chain

    for entry in common:
        build_entry(entry)

    decision = None
    if branch:
        gate = plan.decision_gates.filter(is_deleted=False).first()
        gate_predecessors = []
        for entry in [row for row in common if row.technical_sequence <= 40]:
            rows = chains_by_entry.get(entry.id) or []
            if rows:
                gate_predecessors.append({
                    'id': rows[-1][-1]['id'], 'type': 'FS', 'lag_days': 0,
                    'source': 'generation_decision_gate',
                })
        decision = _milestone_activity(
            ids, gate.name if gate else 'Client Decision', effective_date,
            wbs_code='1', discipline='pm', role='Project Manager',
            predecessors=gate_predecessors or [{'id': start['id'], 'type': 'FS', 'lag_days': 0}],
        )
        decision.update({
            'generation_plan_id': plan.id,
            'decision_gate_code': gate.code if gate else 'DECISION',
            'selected_scenario': selected,
        })
        activities.append(decision)
    for entry in branch:
        build_entry(entry, extra_predecessors=[{
            'id': decision['id'], 'type': 'FS', 'lag_days': 0,
            'source': 'generation_decision_gate', 'rationale': f'Selected scenario: {selected}.',
        }] if decision else None)
    for entry in closing:
        build_entry(entry)
    for entry in recurring:
        interval = calendar['working_days_per_week'] if entry.recurrence == 'weekly' else int(calendar.get('man_days_per_month', 22))
        for occurrence in range(1, entry.recurrence_count + 1):
            build_entry(entry, extra_predecessors=[{
                'id': start['id'], 'type': 'SS', 'lag_days': (occurrence - 1) * interval,
                'source': 'recurrence_rule',
                'rationale': f'{entry.recurrence.title()} occurrence {occurrence} of {entry.recurrence_count}.',
            }], occurrence=occurrence)

    predecessor_ids = {
        predecessor['id'] for activity in activities for predecessor in activity.get('predecessors', [])
    }
    terminal_ids = [activity['id'] for activity in activities if activity['id'] not in predecessor_ids and activity['id'] != start['id']]
    finish_hint = max((datetime.date.fromisoformat(activity['finish_date']) for activity in activities), default=effective_date)
    complete = _milestone_activity(
        ids, 'Project Complete', finish_hint, wbs_code='1', discipline='closeout', role='Project Manager',
        predecessors=[{
            'id': activity_id, 'type': 'FS', 'lag_days': 0, 'source': 'generation_plan_completion',
        } for activity_id in terminal_ids],
    )
    complete['generation_plan_id'] = plan.id
    activities.append(complete)
    project_finish = max(datetime.date.fromisoformat(activity['finish_date']) for activity in activities)
    _assign_float(activities, project_finish, calendar)
    logic_matrix = [{
        'activity_id': activity['id'], 'predecessor_id': predecessor['id'],
        'type': predecessor.get('type', 'FS'), 'lag_days': predecessor.get('lag_days', 0),
        'source': predecessor.get('source', 'generated'),
        'generation_dependency_id': predecessor.get('generation_dependency_id'),
        'rationale': predecessor.get('rationale', ''),
        'source_references': predecessor.get('source_references', []),
        'requires_confirmation': False,
    } for activity in activities for predecessor in activity.get('predecessors', []) if predecessor.get('id')]
    return {
        'activities': activities, 'logic_matrix': logic_matrix,
        'project_finish_date': project_finish.isoformat(), 'date_authority': 'relational_cpm',
        'applied_dependency_rules': [{
            'generation_dependency_id': link.id, 'status': link.status, 'rationale': link.rationale,
        } for link in links],
        'generation_plan_id': plan.id, 'selected_scenario': selected,
    }
