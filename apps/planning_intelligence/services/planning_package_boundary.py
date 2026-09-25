"""Editable planning proposals are calculable drafts, never source approvals."""
from datetime import date

SCHEMA = 'planning-package-proposal/1'


def package_origin(version):
    """Only a persisted package generation can establish this boundary.

    Revisions retain the snapshot and inherit the same source generation through
    their parent; a client flag alone cannot bypass the evidence-only boundary.
    """
    snapshot = version.evidence_input_snapshot or {}
    if snapshot.get('schema') != SCHEMA:
        return None
    seen = set()
    current = version
    while current is not None and current.pk not in seen and current.schedule_id == version.schedule_id:
        seen.add(current.pk)
        if current.source_generation_id:
            generation = current.source_generation
            engine = (generation.intelligence or {}).get('schedule_engine') or {}
            if (generation.project_id == version.schedule.project_id
                    and engine.get('policy') == 'planning_package'
                    and engine.get('source_analysis_run_id') == snapshot.get('source_analysis_run_id')):
                return generation
        current = current.parent_version
    return None


def package_context(version):
    if package_origin(version) is None:
        return None
    snapshot = version.evidence_input_snapshot
    from ..models import WorkCalendar
    calendar = WorkCalendar.objects.filter(
        pk=(snapshot.get('calendar') or {}).get('id'), project_id=version.schedule.project_id,
        is_deleted=False,
    ).first()
    return {
        'calendar': calendar,
        'start': date.fromisoformat(snapshot['project_start']) if snapshot.get('project_start') else None,
        'finish': date.fromisoformat(snapshot['contractual_finish']) if snapshot.get('contractual_finish') else None,
    }


def package_readiness(version):
    from .planning_boundaries import BOUNDARY_RULE_VERSION, _whole_number
    issues = []

    def issue(code, message, *, blocks=None, entity_id=None):
        issues.append({'code': code, 'message': message, 'entity_id': entity_id,
                       'severity': 'error' if blocks is None else 'warning',
                       'blocks': ['calculation', 'approval', 'export'] if blocks is None else blocks})

    context = package_context(version)
    if context is None:
        issue('planning_package_origin_invalid', 'This proposal has no matching saved planning generation.')
    else:
        calendar = context['calendar']
        if not calendar or not calendar.working_weekdays or calendar.hours_per_day <= 0:
            issue('calendar_not_specified', 'Select a valid working calendar before calculating.')
        if context['start'] is None:
            issue('project_start_not_specified', 'Save a project start before calculating.')
        if calendar and calendar.exceptions.filter(is_deleted=False, is_working=True).exclude(
                working_hours=None).exclude(working_hours=calendar.hours_per_day).exists():
            issue('partial_day_calendar_unsupported', 'The working-day engine cannot preserve partial-day exceptions.')
        for activity in version.activities.filter(is_deleted=False):
            metadata = activity.metadata or {}
            if (metadata.get('duration_pending') or metadata.get('duration_source') == 'missing_source'
                    or not _whole_number(activity.duration_days) or activity.duration_days < 0):
                issue('duration_not_specified', 'Supply a nonnegative whole working-day duration.', entity_id=activity.external_id)
            if activity.is_milestone and activity.duration_days != 0:
                issue('milestone_duration_conflict', 'A milestone must have zero duration.', entity_id=activity.external_id)
            if calendar and activity.calendar_id and activity.calendar_id != calendar.pk:
                issue('mixed_calendars_unsupported', 'Mixed activity calendars are not supported.', entity_id=activity.external_id)
            if activity.activity_type == 'level_of_effort':
                issue('level_of_effort_calculation_unsupported', 'Dynamic level-of-effort calculation is not supported.', entity_id=activity.external_id)
        for relationship in version.relationships.filter(is_deleted=False):
            if not _whole_number(relationship.lag_days):
                issue('lag_resolution_unsupported', 'Use whole working-day relationship lags.', entity_id=relationship.pk)
        from ..models import DocumentIntelligenceRun
        from .planning_package_request import validate_package_sources, PlanningPackageRequestError, package_preview_selection
        from .operational_jobs import canonical_fingerprint
        from .preview_confirmation import review_fingerprint
        run = DocumentIntelligenceRun.objects.filter(
            pk=version.evidence_input_snapshot.get('source_analysis_run_id'),
            project_id=version.schedule.project_id, is_deleted=False,
        ).first()
        try:
            if run is None:
                raise PlanningPackageRequestError('The source analysis is no longer available.')
            validate_package_sources(version.schedule.project, run)
            if review_fingerprint(run) != version.evidence_input_snapshot.get('review_fingerprint'):
                raise PlanningPackageRequestError('Source review changed. Generate a new proposal before approval.')
            if (version.evidence_input_snapshot.get('preview_selection_fingerprint') and
                    canonical_fingerprint(package_preview_selection(run)) != version.evidence_input_snapshot['preview_selection_fingerprint']):
                raise PlanningPackageRequestError('Saved scope selection changed. Generate a new proposal before approval.')
            if run.conflicts.filter(is_deleted=False, status='open').exists():
                issue('planning_package_source_conflicts',
                      'Resolve the recorded source conflicts before approving this planning proposal.',
                      blocks=['approval', 'export'])
        except PlanningPackageRequestError as exc:
            issue('planning_package_source_changed', str(exc), blocks=['approval', 'export'])
        from .trustworthy_scheduling import current_assurance
        assurance = current_assurance(version) if version.calculated_at else None
        if not assurance or assurance.status != 'approved' or assurance.blockers:
            issue('planning_proposal_review_required',
                  'Review the proposed workflow, durations, calendar and logic through Schedule Assurance before approval.',
                  blocks=['approval', 'export'])
    return {'policy': 'planning_package', 'rule_version': BOUNDARY_RULE_VERSION,
            'ready_for_calculation': not any('calculation' in row['blocks'] for row in issues),
            'ready_for_approval': not any('approval' in row['blocks'] for row in issues),
            'ready_for_export': not any('export' in row['blocks'] for row in issues), 'issues': issues}
