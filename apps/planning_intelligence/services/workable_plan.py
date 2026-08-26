"""Goal-oriented orchestration from Document Intelligence to an approvable plan."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..models import IntelligenceFact
from .generation_plan import (
    approve_generation_plan, build_generation_plan, classify_deliverable,
    refresh_generation_plan_readiness,
)
from .pipeline import generate_schedule
from .schedule_basis import approve_schedule_basis, build_schedule_basis, refresh_basis_readiness
from .schedule_materializer import materialize_generation
from .trustworthy_scheduling import run_schedule_assurance


def _candidate_fact(fact):
    return {
        'id': fact.id, 'value': fact.value, 'confidence': fact.confidence,
        'source_excerpt': fact.source_excerpt, 'source_locator': fact.source_locator,
    }


def _decision_sheet(basis):
    deliverables = list(basis.deliverables.filter(is_deleted=False, status='needs_review').order_by(
        'discipline', 'canonical_name',
    ))
    conflicts = list(basis.source_run.conflicts.filter(is_deleted=False, status__in=['open', 'ignored']))
    fact_map = {
        fact.id: fact for fact in IntelligenceFact.objects.filter(
            id__in={fact_id for conflict in conflicts for fact_id in conflict.fact_ids}, is_deleted=False,
        )
    }
    conflict_rows = []
    for conflict in conflicts:
        candidates = sorted(
            (fact_map[fact_id] for fact_id in conflict.fact_ids if fact_id in fact_map),
            key=lambda fact: (-fact.confidence, fact.id),
        )
        conflict_rows.append({
            'id': conflict.id, 'key': conflict.key, 'description': conflict.description,
            'candidates': [_candidate_fact(fact) for fact in candidates],
            'recommended_fact_id': candidates[0].id if candidates else None,
        })
    scenario_codes = sorted({
        classify_deliverable(item)['scenario_code']
        for item in basis.deliverables.filter(is_deleted=False).exclude(status='excluded')
        if classify_deliverable(item)['scenario_code'] != 'common'
    })
    missing_fields = []
    if not basis.effective_date:
        missing_fields.append({'field': 'effective_date', 'label': 'Project effective date', 'value': ''})
    if not basis.contractual_finish:
        missing_fields.append({'field': 'contractual_finish', 'label': 'Contractual finish date', 'value': ''})
    return {
        'basis_id': basis.id,
        'deliverables': [{
            'id': item.id, 'name': item.canonical_name, 'discipline': item.discipline,
            'confidence': item.confidence, 'document_number': item.document_number,
            'source_references': item.source_references,
        } for item in deliverables],
        'conflicts': conflict_rows, 'scenario_options': scenario_codes,
        'missing_fields': missing_fields,
    }


@transaction.atomic
def _apply_decisions(basis, decisions, user):
    included = {int(value) for value in decisions.get('included_deliverable_ids') or []}
    pending = basis.deliverables.select_for_update().filter(is_deleted=False, status='needs_review')
    now = timezone.now()
    for item in pending:
        item.status = 'confirmed' if item.id in included else 'excluded'
        item.reviewed_by = user
        item.reviewed_at = now
        item.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])

    conflict_decisions = {
        int(key): int(value) for key, value in (decisions.get('conflict_fact_ids') or {}).items() if value
    }
    for conflict in basis.source_run.conflicts.select_for_update().filter(is_deleted=False, status__in=['open', 'ignored']):
        selected_id = conflict_decisions.get(conflict.id)
        if selected_id not in conflict.fact_ids:
            continue
        facts = IntelligenceFact.objects.filter(id__in=conflict.fact_ids, run=basis.source_run)
        facts.exclude(pk=selected_id).update(status='rejected', reviewed_by=user, reviewed_at=now)
        facts.filter(pk=selected_id).update(status='confirmed', reviewed_by=user, reviewed_at=now)
        conflict.status = 'resolved'
        conflict.resolution = {'action': 'select_fact', 'selected_fact_id': selected_id}
        conflict.resolved_by = user
        conflict.resolved_at = now
        conflict.save(update_fields=['status', 'resolution', 'resolved_by', 'resolved_at', 'updated_at'])

    allowed_updates = {
        key: value for key, value in (decisions.get('basis_updates') or {}).items()
        if key in {'effective_date', 'contractual_finish'} and value
    }
    for key, value in allowed_updates.items():
        setattr(basis, key, value)
    if allowed_updates:
        basis.save(update_fields=[*allowed_updates.keys(), 'updated_at'])
    refresh_basis_readiness(basis)


def build_workable_plan(project, user, request_data, progress):
    """Run all intermediate planning phases, pausing only for exceptions."""
    run = project.intelligence_runs.filter(is_deleted=False, status='succeeded').first()
    if not run:
        raise ValueError('Run Document Intelligence successfully before building a workable project plan.')
    progress(8, 'Documents reviewed', 'documents', {
        'document_count': len(run.source_file_ids or []),
    })
    progress(12, 'Identifying scope and deliverables', 'schedule_basis')
    basis = project.schedule_bases.filter(is_deleted=False, source_run=run).exclude(status='superseded').first()
    if not basis:
        basis = build_schedule_basis(run)
    deliverable_count = basis.deliverables.filter(is_deleted=False).count()
    progress(22, 'Scope and deliverables identified', 'scope', {
        'deliverable_count': deliverable_count,
    })

    decisions = (request_data or {}).get('decisions') or {}
    sheet = _decision_sheet(basis)
    approved_plan = project.generation_plans.filter(
        is_deleted=False, basis=basis, status='approved',
    ).first()
    selected_scenario = decisions.get('selected_scenario') or getattr(approved_plan, 'selected_scenario', '')
    requires_decisions = bool(
        sheet['deliverables'] or sheet['conflicts'] or sheet['missing_fields']
        or (len(sheet['scenario_options']) > 1 and not selected_scenario)
    )
    if requires_decisions and not decisions.get('confirmed'):
        return {'state': 'needs_decisions', 'decision_sheet': sheet, 'basis_id': basis.id}
    if decisions.get('confirmed'):
        _apply_decisions(basis, decisions, user)
        sheet = _decision_sheet(basis)
        unresolved = bool(sheet['deliverables'] or sheet['conflicts'] or sheet['missing_fields'])
        if unresolved:
            return {'state': 'needs_decisions', 'decision_sheet': sheet, 'basis_id': basis.id}

    refresh_basis_readiness(basis)
    if not basis.readiness.get('ready'):
        return {'state': 'needs_decisions', 'decision_sheet': _decision_sheet(basis), 'basis_id': basis.id}
    if basis.status != 'approved':
        basis = approve_schedule_basis(basis, user)

    progress(30, 'Assigning deliverable-specific workflows', 'generation_plan')
    plan = approved_plan
    if not plan:
        plan = build_generation_plan(basis)
        plan.dependencies.filter(is_deleted=False, status='proposed').update(
            status='confirmed', reviewed_by=user, reviewed_at=timezone.now(), updated_at=timezone.now(),
        )
        scenarios = plan.readiness.get('available_scenarios') or sheet['scenario_options']
        selected = decisions.get('selected_scenario') or (scenarios[0] if len(scenarios) == 1 else '')
        if scenarios and selected not in scenarios:
            return {
                'state': 'needs_decisions', 'decision_sheet': {**sheet, 'scenario_options': scenarios},
                'basis_id': basis.id, 'generation_plan_id': plan.id,
            }
        plan.selected_scenario = selected
        plan.save(update_fields=['selected_scenario', 'updated_at'])
        refresh_generation_plan_readiness(plan)
        plan = approve_generation_plan(plan, user)

    progress(42, 'Workflows assigned', 'workflows', {
        'deliverable_count': plan.deliverables.filter(is_deleted=False).count(),
        'confirmed_logic_count': plan.dependencies.filter(is_deleted=False, status='confirmed').count(),
    })

    progress(50, 'Building activity logic', 'activity_logic')
    generation = generate_schedule(project, user=user, input_fingerprint=request_data.get('output_fingerprint'))
    progress(64, 'Building activity logic', 'activity_logic', {
        'deliverable_count': deliverable_count,
        'activity_count': len(generation.activities or []),
        'relationship_count': len(generation.logic_matrix or []),
    })
    progress(70, 'Calculating relational CPM', 'cpm')
    version, calculation_run, materialization_issues = materialize_generation(generation, requested_by=user)
    progress(86, 'CPM calculated; running final checks', 'final_checks', {
        'deliverable_count': deliverable_count,
        'activity_count': version.activities.filter(is_deleted=False).count(),
        'relationship_count': version.relationships.filter(is_deleted=False).count(),
    })
    review = run_schedule_assurance(version, requested_by=user)
    forecast_finish = version.calculated_finish or (calculation_run.project_finish if calculation_run else None)
    summary = {
        'generation_id': generation.id, 'schedule_id': version.schedule_id,
        'schedule_version_id': version.id, 'schedule_version': version.version,
        'calculation_run_id': calculation_run.id if calculation_run else None,
        'forecast_finish': forecast_finish.isoformat() if forecast_finish else None,
        'contractual_finish': project.planned_end_date.isoformat() if project.planned_end_date else None,
        'activity_count': version.activities.filter(is_deleted=False).count(),
        'relationship_count': version.relationships.filter(is_deleted=False).count(),
        'materialization_issues': materialization_issues,
        'warnings': review.warnings, 'blockers': review.blockers,
    }
    return {
        'state': 'needs_decisions' if review.blockers else 'ready_for_approval',
        'decision_sheet': {'schedule_blockers': review.blockers, 'schedule_warnings': review.warnings},
        'summary': summary,
    }


def approve_workable_baseline(project, user, version_id, name, progress):
    from ..models import ScheduleBaseline, ScheduleVersion
    from ..schedule_serializers import (
        ActivityRelationshipSerializer, ScheduleActivitySerializer, ScheduleAssuranceReviewSerializer,
        ScheduleBaselineSerializer, ScheduleVersionSerializer, ScheduleWBSNodeSerializer,
    )
    from .trustworthy_scheduling import approve_schedule_assurance, current_assurance

    version = ScheduleVersion.objects.select_related('schedule__project').get(
        pk=version_id, schedule__project=project, is_deleted=False,
    )
    existing = version.baselines.filter(is_deleted=False).first()
    if version.status == 'baselined' and existing:
        return {'state': 'baselined', 'baseline': ScheduleBaselineSerializer(existing).data}
    assurance = current_assurance(version)
    if not assurance or assurance.blockers:
        raise ValueError('Resolve the consolidated critical exceptions before baseline approval.')
    progress(25, 'Approving final schedule assurance', 'assurance_approval')
    with transaction.atomic():
        assurance = approve_schedule_assurance(version, user)
        version = ScheduleVersion.objects.select_for_update().get(pk=version.pk)
        if version.status != 'calculated':
            raise ValueError('Only a calculated workable plan can be approved.')
        version.status = 'approved'
        version.save(update_fields=['status', 'updated_at'])
        progress(60, 'Creating the controlled baseline snapshot', 'baseline_snapshot')
        snapshot = {
            'version': ScheduleVersionSerializer(version).data,
            'wbs': ScheduleWBSNodeSerializer(version.wbs_nodes.filter(is_deleted=False), many=True).data,
            'activities': ScheduleActivitySerializer(version.activities.filter(is_deleted=False), many=True).data,
            'relationships': ActivityRelationshipSerializer(version.relationships.filter(is_deleted=False), many=True).data,
            'schedule_assurance': ScheduleAssuranceReviewSerializer(assurance).data,
        }
        baseline = ScheduleBaseline.objects.create(
            schedule=version.schedule, source_version=version,
            name=str(name or f'Approved Workable Plan v{version.version}')[:255],
            data_date=version.schedule.data_date, snapshot=snapshot,
            approved_by=user, approved_at=timezone.now(),
        )
        version.status = 'baselined'
        version.save(update_fields=['status', 'updated_at'])
    return {'state': 'baselined', 'baseline': ScheduleBaselineSerializer(baseline).data}
