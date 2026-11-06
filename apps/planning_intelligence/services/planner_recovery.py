"""Confirmed draft adjustments; source identities and contractual dates stay intact."""
from copy import deepcopy
from datetime import date
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from ..access import can_write_project
from ..models import ScheduleActivity, ScheduleVersion
from .audit import record_event
from .schedule_approval import ScheduleApprovalError, current_schedule_version


def planning_target_finish(version, contractual_finish=None):
    stored = ((version.evidence_input_snapshot or {}).get('planner_adjustments') or {}).get('planning_target_finish')
    return date.fromisoformat(stored) if stored else contractual_finish


@transaction.atomic
def recover_planner_draft(version, actor, *, operation, expected_updated_at):
    version = ScheduleVersion.objects.select_for_update().get(pk=version.pk, is_deleted=False)
    if not can_write_project(actor, version.schedule.project):
        raise ScheduleApprovalError('You cannot modify this schedule.', code='planner_recovery_forbidden', status_code=403)
    if version.status not in {'draft', 'calculated'} or not current_schedule_version(version):
        raise ScheduleApprovalError('Create or open the current editable revision.', code='planner_recovery_read_only')
    if version.updated_at != expected_updated_at:
        raise ScheduleApprovalError('The schedule changed. Reload it before applying this adjustment.', code='version_conflict')
    before = deepcopy((version.evidence_input_snapshot or {}).get('planner_adjustments') or {})
    adjustments = deepcopy(before)
    details = {}
    if operation == 'extend_planning_target':
        from .planning_boundaries import calculation_inputs_current
        from .planning_package_boundary import package_context
        if version.status != 'calculated' or not version.calculated_finish or not calculation_inputs_current(version):
            from .cpm import calculate_schedule_version
            calculate_schedule_version(version, requested_by=actor)
            version.refresh_from_db()
        if version.status != 'calculated' or not version.calculated_finish or not calculation_inputs_current(version):
            raise ScheduleApprovalError('Calculate the current draft before adjusting its planning target.', code='planner_calculation_required')
        context = package_context(version)
        contractual = context['finish'] if context else version.schedule.project.planned_end_date
        current = planning_target_finish(version, contractual)
        if current and version.calculated_finish <= current:
            return {'changed': False, 'message': 'The planning target already covers the calculated finish.', 'version_id': version.pk}
        adjustments['planning_target_finish'] = version.calculated_finish.isoformat()
        details = {'previous_target': current.isoformat() if current else None,
                   'planning_target_finish': version.calculated_finish.isoformat(),
                   'extension_calendar_days': (version.calculated_finish - current).days if current else None,
                   'contractual_finish': contractual.isoformat() if contractual else None}
    elif operation == 'renumber_activity_ids':
        rows = list(version.activities.select_for_update().filter(is_deleted=False).order_by('sort_order', 'pk'))
        reserved = set(version.activities.filter(is_deleted=True).values_list('external_id', flat=True))
        sequence, changed = 100, []
        for row in rows:
            while str(sequence) in reserved:
                sequence += 10
            identifier = str(sequence)
            sequence += 10
            if row.external_id != identifier:
                changed.append((row, row.external_id, identifier))
        if not changed:
            return {'changed': False, 'message': 'Activity IDs already follow the numeric convention.', 'version_id': version.pk}
        prefix = f'RENUMBER-{uuid4().hex}-'
        for row, old, identifier in changed:
            row.external_id = f'{prefix}{row.pk}'
        ScheduleActivity.objects.bulk_update([row for row, _, _ in changed], ['external_id'])
        now = timezone.now()
        for row, old, identifier in changed:
            row.external_id = identifier
            row.metadata = {**(row.metadata or {}), 'original_external_id': (row.metadata or {}).get('original_external_id') or old,
                            'source_generation_activity_id': (row.metadata or {}).get('source_generation_activity_id') or old,
                            'identifier_convention': 'numeric-10'}
            row.updated_at = now
        ScheduleActivity.objects.bulk_update([row for row, _, _ in changed], ['external_id', 'metadata', 'updated_at'])
        details = {'activity_ids': [{'activity_id': row.pk, 'previous_id': old, 'external_id': identifier}
                                   for row, old, identifier in changed]}
        adjustments['identifier_convention'] = 'numeric-10'
    else:
        raise ScheduleApprovalError('Choose a supported planner adjustment.', code='planner_recovery_invalid', status_code=400)
    adjustments.update(adjusted_by_id=actor.pk, adjusted_at=timezone.now().isoformat())
    version.evidence_input_snapshot = {**(version.evidence_input_snapshot or {}), 'planner_adjustments': adjustments}
    version.status, version.calculated_at, version.calculated_finish = 'draft', None, None
    version.save(update_fields=['evidence_input_snapshot', 'status', 'calculated_at', 'calculated_finish', 'updated_at'])
    version.assurance_reviews.filter(status__in=['draft', 'ready', 'approved']).update(status='superseded')
    record_event(project=version.schedule.project, actor=actor, action=f'schedule.{operation}', entity=version,
                 before=before, after={**adjustments, **details},
                 metadata={'contractual_date_unchanged': True, 'source_identities_preserved': True})
    return {'changed': True, 'version_id': version.pk, **details}
