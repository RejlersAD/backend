"""Expose current logic diagnostics without changing stored schedules."""
from .schedule_logic_review import logic_quality


def state_logic_quality(project, state, version=None):
    if version is None and state.get('version_id') and (
            state.get('state') in {'submitted', 'baselined'}
            or state.get('viewing_history') or state.get('legacy_version_import')):
        from ..models import ScheduleVersion
        version = ScheduleVersion.objects.filter(pk=state['version_id'], schedule__project=project,
                                                 is_deleted=False).first()
    return logic_quality(project, state.get('tasks') or [], state.get('deliverables'), version=version)


def enrich_logic_quality(project, state, version=None):
    quality = state_logic_quality(project, state, version)
    state['logic_quality'] = quality
    codes = {'parallel_workflow_review_required', 'unconnected_workflow_start', 'terminal_branch'}
    for field in ('blockers', 'warnings'):
        state[field] = [row for row in state.get(field) or []
                        if not isinstance(row, dict) or row.get('code') not in codes] + quality[field]
    if quality['blockers']:
        state.setdefault('permissions', {}).update(can_submit=False, can_approve_publish=False)
    return state
