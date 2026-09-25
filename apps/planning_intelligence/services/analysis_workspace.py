"""Read a completed analysis as schedule evidence without starting planning work."""
from .document_intelligence import compile_run_intelligence, validate_analysis_source
from .pipeline import _document_payload
from .preview_confirmation import review_fingerprint


class AnalysisWorkspaceUnavailable(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _validate_source(run):
    if run.is_deleted or run.status != 'succeeded':
        raise AnalysisWorkspaceUnavailable(
            'intelligence_workspace_run_unavailable',
            'Wait for this document analysis to complete before opening its schedule workspace.',
        )
    try:
        validate_analysis_source(run.project, run)
    except ValueError as exc:
        raise AnalysisWorkspaceUnavailable('intelligence_workspace_sources_changed', str(exc)) from exc


def analysis_schedule_workspace(run):
    """Project one exact run and unchanged source set into a read-only workspace.

    The run's current fact review state is retained. Preview confirmation is a
    separate command; neither reading this projection nor a newer run changes
    the selected analysis identity. No provider, generation or CPM is invoked.
    """
    _validate_source(run)
    reviewed = review_fingerprint(run)
    payload = _document_payload(run.project, compile_run_intelligence(run, include_confirmation=False))

    # Parsing the existing text is read-only, but it may take long enough for
    # an upload or review to change. Reject a mixed response instead of silently
    # combining an old analysis with newly changed inputs.
    run.refresh_from_db()
    _validate_source(run)
    if reviewed != review_fingerprint(run):
        raise AnalysisWorkspaceUnavailable(
            'intelligence_workspace_review_changed',
            'Evidence review changed while opening this workspace. Refresh this analysis workspace.',
        )
    return {
        'analysis_run_id': run.pk,
        'project': run.project_id,
        'state': 'analysis_evidence',
        'analysis_completed_at': run.finished_at,
        **payload,
    }
