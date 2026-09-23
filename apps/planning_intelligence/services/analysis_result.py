"""Explain the distinction between extracted findings and usable draft activities."""
from .project_ai import ai_failure_guidance


def analysis_result(project, state):
    """Read an analysis outcome without changing historical drafts or evidence."""
    if state.get('viewing_history') or not state.get('intelligence_run_id'):
        return None
    summary = state.get('extraction_summary')
    coverage = state.get('processing_coverage') or {}
    if summary is None:
        # Older saved drafts kept coverage but not the extraction counts.
        run_summary = project.intelligence_runs.filter(
            pk=state['intelligence_run_id'], is_deleted=False, status='succeeded',
        ).values_list('summary', flat=True).first() or {}
        summary = run_summary.get('extraction_summary') or {}
        coverage = coverage or run_summary.get('processing_coverage') or {}
    ai = coverage.get('ai_processing') or {}
    count = len(state.get('tasks') or [])
    requirements = (summary.get('facts_by_type') or {}).get('requirement', 0)
    result = {
        'status': 'activities_created' if count else 'no_activities',
        'activity_count': count, 'requirement_count': requirements,
        'ai_status': ai.get('status', 'not_run'), 'code': None, 'next_action': None,
        'message': f'The draft contains {count} activities. Review their durations and dependencies before calculating the schedule.',
    }
    if count:
        if state.get('method') == 'programmatic_requirements':
            result.update(ai_status='not_used', requirement_count=(state.get('programmatic_summary') or {}).get('requirement_count', requirements),
                          message=f'Created {count} draft activities from all extracted requirements without AI. Dates and durations are provisional; review the activities and source deliverables.')
        return result
    findings = (f'Document analysis found {requirements} requirements, but no schedule activities were created. '
                if requirements else 'Document analysis did not create any schedule activities. ')
    provider_missing = (ai.get('reason') == 'No project AI provider is configured.'
                        or any(chunk.get('reason') == 'provider_not_configured' for chunk in ai.get('chunks') or []))
    if provider_missing:
        result.update(
            code='provider_not_configured', next_action='ai_settings',
            message=findings + 'AI analysis was not configured for this run. Open AI settings, configure a provider, then analyze again. You can also upload a deliverable register or activity schedule.',
        )
    elif ai.get('status') in {'partial', 'failed'} or any(ai.get(key) for key in ('chunks_failed', 'chunks_partial')):
        failure = next((guidance for chunk in ai.get('chunks') or []
                        if chunk.get('status') == 'failed'
                        and (guidance := ai_failure_guidance(chunk.get('error')))), None)
        result.update(
            code='ai_analysis_incomplete', next_action='retry_analysis',
            message=findings + 'AI analysis did not finish processing the source documents. Check AI settings and retry analysis.',
        )
        if failure:
            result.update(ai_error_code=failure['code'], ai_http_status=failure['http_status'],
                          next_action=failure['next_action'],
                          message=findings + failure['message'])
    else:
        result.update(
            code='source_activities_not_recovered', next_action='review_sources',
            message=findings + 'Review the extracted findings and provide explicit activities or a deliverable register, or add activities manually. Requirements alone do not define a schedule.',
        )
    return result
