"""Schedule pipeline shared by HTTP orchestration and Celery workers."""
from collections import Counter
from apps.rbac.ai_telemetry import tracked_planning
from django.db import transaction
from django.db.models import Max

from ..models import PlanningGeneration, PlanningProject
from .document_plan import POLICY, project_document_plan


def _document_payload(project, intelligence):
    plan = project_document_plan(project, intelligence)
    intelligence = {**intelligence, 'schedule_engine': {
        'policy': POLICY, 'engine_version': plan['engine_version'],
        'date_authority': 'source_document', 'applied_dependency_rules': [],
        'ready_for_calculation': plan['ready_for_calculation'],
        'missing_information': plan['missing_information'],
        'unresolved_relationships': plan['unresolved_relationships'],
        'extraction_reports': plan['extraction_reports'],
        'source_summaries': plan['source_summaries'],
        'source_constraints': plan['source_constraints'],
        'register_inventory': plan['register_inventory'],
        'unmapped_register_count': plan['unmapped_register_count'],
        'additional_source_facts': plan['additional_source_facts'],
    }}
    return {
        'intelligence': intelligence, 'wbs': plan['wbs'], 'activities': plan['activities'],
        'logic_matrix': plan['logic_matrix'], 'eddr': [],
        'milestones': [row for row in plan['activities'] if row.get('is_milestone') is True],
        'manhours': {'basis': {'status': 'Not Specified'}, 'by_discipline': [], 'grand_total_man_hours': None},
        'validation': plan['validation'],
        'narrative': 'Document facts extracted for review. Missing information is Not Specified. No template durations, inferred dependencies or fallback calendar have been applied.',
    }


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
    payload = _document_payload(project, intelligence)
    wbs, activities, validation = payload['wbs'], payload['activities'], payload['validation']
    schedule = {**payload['intelligence']['schedule_engine'], 'logic_matrix': payload['logic_matrix']}
    return {
        'wbs_node_count': len(wbs),
        'deliverable_count': len(schedule['register_inventory']),
        'activity_count': len(activities),
        'relationship_count': len(schedule.get('logic_matrix') or []),
        'milestone_count': sum(1 for item in activities if item.get('is_milestone')),
        'configured_workflow_activity_count': 0,
        'evidence_policy': POLICY,
        'processing_coverage': intelligence.get('processing_coverage') or {},
        'missing_information': schedule['missing_information'],
        'extraction_reports': schedule['extraction_reports'],
        'sample_logic_matrix': schedule['logic_matrix'][:100],
        'unresolved_relationships': schedule['unresolved_relationships'],
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
                'source_activity_id', 'source_references', 'duration_unit',
                'start_date', 'finish_date', 'field_evidence',
            )
        } for item in activities][:20],
    }


@tracked_planning('generate_schedule')
def generate_schedule(project, *, user=None, overrides=None, input_fingerprint=None):
    if input_fingerprint:
        from .operational_jobs import canonical_fingerprint
        input_fingerprint = canonical_fingerprint({'policy': POLICY, 'input': input_fingerprint})
    if input_fingerprint:
        existing = project.generations.filter(
            is_deleted=False, input_fingerprint=input_fingerprint,
        ).first()
        if existing:
            return existing
    intelligence = analyze_documents(project, user=user)
    if isinstance(overrides, dict) and overrides:
        intelligence = apply_intelligence_overrides(intelligence, overrides)
    payload = _document_payload(project, intelligence)
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
