"""Schedule pipeline shared by HTTP orchestration and Celery workers."""
from collections import Counter
from django.db import transaction
from django.db.models import Max

from ..models import PlanningGeneration, PlanningProject
from .activity_generator import build_activities
from .eddr_generator import build_eddr
from .manhour_estimator import build_manhours
from .narrative_generator import build_narrative
from .validation_engine import validate
from .wbs_generator import build_wbs


def apply_intelligence_overrides(intelligence, overrides):
    merged = dict(intelligence)
    for key in ('detected_project_name', 'detected_effective_date_text', 'detected_duration_months'):
        if key in overrides and overrides[key] not in (None, ''):
            merged[key] = overrides[key]
    if isinstance(overrides.get('disciplines'), dict):
        disciplines = {code: dict(info) for code, info in (merged.get('disciplines') or {}).items()}
        for code, override in overrides['disciplines'].items():
            if code not in disciplines or not isinstance(override, dict):
                continue
            if isinstance(override.get('deliverables'), list):
                disciplines[code]['deliverables'] = [str(item).strip() for item in override['deliverables'] if str(item).strip()]
            if 'in_scope' in override:
                disciplines[code]['in_scope'] = bool(override['in_scope'])
        merged['disciplines'] = disciplines
    if isinstance(overrides.get('hse_studies'), list):
        merged['hse_studies'] = [str(item).strip() for item in overrides['hse_studies'] if str(item).strip()]
    return merged


def analyze_documents(project, user=None, *, force=False):
    from .document_intelligence import get_or_run_document_intelligence
    _run, intelligence = get_or_run_document_intelligence(project, user=user, force=force)
    return intelligence


def preview_schedule(project, *, user=None, overrides=None):
    """Build a deterministic, non-persistent generation-wizard preview."""
    intelligence = analyze_documents(project, user=user)
    if isinstance(overrides, dict) and overrides:
        intelligence = apply_intelligence_overrides(intelligence, overrides)
    from .schedule_basis import apply_approved_basis
    intelligence = apply_approved_basis(project, intelligence)
    from .generation_plan import apply_approved_generation_plan
    intelligence = apply_approved_generation_plan(project, intelligence)
    wbs = build_wbs(project, intelligence)
    schedule = build_activities(project, wbs, intelligence)
    activities = schedule['activities']
    eddr = build_eddr(activities)
    validation = validate(project, wbs, activities, eddr, intelligence)
    deliverables = {
        (item.get('discipline'), item.get('deliverable'))
        for item in activities if item.get('deliverable') and item.get('workflow_template_code')
    }
    return {
        'wbs_node_count': len(wbs),
        'deliverable_count': len(deliverables),
        'activity_count': len(activities),
        'relationship_count': len(schedule.get('logic_matrix') or []),
        'milestone_count': sum(1 for item in activities if item.get('is_milestone')),
        'configured_workflow_activity_count': sum(
            1 for item in activities if item.get('workflow_template_code')
        ),
        'project_finish_hint': schedule.get('project_finish_date'),
        'date_authority': schedule.get('date_authority', 'relational_cpm'),
        'applied_dependency_rules': schedule.get('applied_dependency_rules') or [],
        'generation_plan_id': intelligence.get('generation_plan_id'),
        'generation_plan_version': intelligence.get('generation_plan_version'),
        'selected_scenario': intelligence.get('selected_scenario'),
        'workflow_family_counts': dict(Counter(
            item.get('workflow_family') for item in activities if item.get('workflow_family')
        )),
        'validation': validation,
        'sample_activities': [{
            key: item.get(key) for key in (
                'id', 'name', 'discipline', 'deliverable', 'workflow_stage_code',
                'original_duration_days', 'predecessors',
            )
        } for item in activities if item.get('workflow_template_code')][:20],
    }


def generate_schedule(project, *, user=None, overrides=None, input_fingerprint=None):
    if input_fingerprint:
        existing = project.generations.filter(
            is_deleted=False, input_fingerprint=input_fingerprint,
        ).first()
        if existing:
            return existing
    intelligence = analyze_documents(project, user=user)
    if isinstance(overrides, dict) and overrides:
        intelligence = apply_intelligence_overrides(intelligence, overrides)
    from .schedule_basis import apply_approved_basis
    intelligence = apply_approved_basis(project, intelligence)
    from .generation_plan import apply_approved_generation_plan
    intelligence = apply_approved_generation_plan(project, intelligence)
    wbs = build_wbs(project, intelligence)
    schedule = build_activities(project, wbs, intelligence)
    activities = schedule['activities']
    intelligence = dict(intelligence)
    intelligence['schedule_engine'] = {
        'date_authority': schedule.get('date_authority', 'relational_cpm'),
        'applied_dependency_rules': schedule.get('applied_dependency_rules', []),
    }
    eddr = build_eddr(activities)
    manhours = build_manhours(project, activities)
    validation = validate(project, wbs, activities, eddr, intelligence)
    narrative = build_narrative(project, activities, eddr, validation, user=user)

    payload = {
        'intelligence': intelligence, 'wbs': wbs, 'activities': activities,
        'logic_matrix': schedule['logic_matrix'], 'eddr': eddr,
        'milestones': [activity for activity in activities if activity.get('is_milestone')],
        'manhours': manhours, 'validation': validation, 'narrative': narrative,
    }
    with transaction.atomic():
        locked_project = PlanningProject.objects.select_for_update().get(pk=project.pk)
        if input_fingerprint:
            existing = locked_project.generations.filter(
                is_deleted=False, input_fingerprint=input_fingerprint,
            ).first()
            if existing:
                return existing
        next_version = (locked_project.generations.aggregate(value=Max('version'))['value'] or 0) + 1
        return PlanningGeneration.objects.create(
            project=locked_project, version=next_version, generated_by=user,
            input_fingerprint=input_fingerprint, **payload,
        )
