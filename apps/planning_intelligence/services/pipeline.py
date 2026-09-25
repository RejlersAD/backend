"""Schedule pipeline shared by HTTP orchestration and Celery workers."""
from collections import Counter
from apps.rbac.ai_telemetry import tracked_planning
from django.db import transaction
from django.db.models import Max

from ..models import PlanningGeneration, PlanningProject
from .document_plan import POLICY, project_document_plan
from .planning_package_request import PlanningPackageRequestError


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


def analyze_documents(project, user=None, *, force=False, progress_callback=None):
    from .document_intelligence import get_or_run_document_intelligence
    _run, intelligence = get_or_run_document_intelligence(project, user=user, force=force, progress_callback=progress_callback)
    return intelligence


def _package_input_fingerprint(project, run):
    from .operational_jobs import generation_fingerprint
    current = PlanningProject.objects.get(pk=project.pk)
    if current.updated_at != project.updated_at:
        raise PlanningPackageRequestError('Planning project inputs changed. Refresh before building the Planning Package.')
    return generation_fingerprint(current, {'generation_options': {
        'mode': 'planning_package', 'intelligence_run_id': run.pk,
    }})


def _generation_payload(project, *, user=None, overrides=None, mode=POLICY, intelligence_run=None):
    if mode == 'planning_package':
        from .planning_package import build_planning_package
        from .planning_package_request import validate_package_sources
        from .preview_confirmation import review_fingerprint
        if intelligence_run is None:
            raise PlanningPackageRequestError('Select the completed analysis for this Planning Package.')
        validate_package_sources(project, intelligence_run)
        reviewed = review_fingerprint(intelligence_run)
        planned = _package_input_fingerprint(project, intelligence_run)
        payload = build_planning_package(project, intelligence_run)
        intelligence_run.refresh_from_db()
        validate_package_sources(project, intelligence_run)
        if reviewed != review_fingerprint(intelligence_run):
            raise PlanningPackageRequestError('Evidence review changed while building the Planning Package. Refresh and try again.')
        if planned != _package_input_fingerprint(project, intelligence_run):
            raise PlanningPackageRequestError('Planning configuration changed while building this package. Refresh and try again.')
        payload['intelligence']['schedule_engine']['review_fingerprint'] = reviewed
        from .planning_package_request import package_preview_selection
        from .operational_jobs import canonical_fingerprint
        payload['intelligence']['schedule_engine']['preview_selection_fingerprint'] = canonical_fingerprint(
            package_preview_selection(intelligence_run, review_token=reviewed))
        payload['intelligence']['schedule_engine']['planning_input_fingerprint'] = planned
        return payload
    if mode != POLICY:
        raise PlanningPackageRequestError('Unknown planning generation mode.')
    intelligence = analyze_documents(project, user=user)
    if isinstance(overrides, dict) and overrides:
        intelligence = apply_intelligence_overrides(intelligence, overrides)
    return _document_payload(project, intelligence)


def preview_schedule(project, *, user=None, overrides=None, mode=POLICY, intelligence_run=None):
    """Build a deterministic, non-persistent generation-wizard preview."""
    payload = _generation_payload(project, user=user, overrides=overrides, mode=mode, intelligence_run=intelligence_run)
    intelligence = payload['intelligence']
    wbs, activities, validation = payload['wbs'], payload['activities'], payload['validation']
    schedule = {**payload['intelligence']['schedule_engine'], 'logic_matrix': payload['logic_matrix']}
    return {
        'wbs_node_count': len(wbs),
        'deliverable_count': len(schedule.get('source_deliverables', schedule['register_inventory'])),
        'activity_count': len(activities),
        'relationship_count': len(schedule.get('logic_matrix') or []),
        'milestone_count': sum(1 for item in activities if item.get('is_milestone')),
        'configured_workflow_activity_count': schedule.get('configured_workflow_activity_count', 0),
        'evidence_policy': schedule['policy'],
        'generation_mode': mode,
        'intelligence_run_id': getattr(intelligence_run, 'pk', None),
        'assumptions': schedule.get('assumptions', []),
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
def generate_schedule(project, *, user=None, overrides=None, input_fingerprint=None, mode=POLICY, intelligence_run=None):
    if input_fingerprint:
        from .operational_jobs import canonical_fingerprint
        input_fingerprint = canonical_fingerprint({'policy': mode, 'input': input_fingerprint})
    if input_fingerprint:
        existing = project.generations.filter(
            is_deleted=False, input_fingerprint=input_fingerprint,
        ).first()
        if existing:
            return existing
    payload = _generation_payload(project, user=user, overrides=overrides, mode=mode, intelligence_run=intelligence_run)
    with transaction.atomic():
        locked_project = PlanningProject.objects.select_for_update().get(pk=project.pk)
        if mode == 'planning_package':
            from .planning_package_request import validate_package_sources
            from .preview_confirmation import review_fingerprint
            intelligence_run.refresh_from_db()
            validate_package_sources(locked_project, intelligence_run)
            engine = payload['intelligence']['schedule_engine']
            if (locked_project.updated_at != project.updated_at
                    or engine['review_fingerprint'] != review_fingerprint(intelligence_run)):
                raise PlanningPackageRequestError('Planning inputs changed while generating this package. Refresh and try again.')
            if engine['planning_input_fingerprint'] != _package_input_fingerprint(locked_project, intelligence_run):
                raise PlanningPackageRequestError('Planning configuration changed while generating this package. Refresh and try again.')
            if engine.get('configuration_id'):
                from ..models import ProjectScheduleConfiguration
                current_version = ProjectScheduleConfiguration.objects.filter(
                    pk=engine['configuration_id'], project=locked_project, is_deleted=False,
                ).values_list('configuration_version', flat=True).first()
                if current_version != engine['configuration_version']:
                    raise PlanningPackageRequestError('The workflow configuration changed while generating this package. Refresh and try again.')
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
