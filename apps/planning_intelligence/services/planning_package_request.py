"""Scoped inputs for the explicit source-to-planning-proposal command."""
from ..models import DocumentIntelligenceRun, ProjectScheduleConfiguration
from .document_intelligence import _extraction_source_manifest


class PlanningPackageRequestError(ValueError):
    def __init__(self, message, *, code='invalid_generation_options', status_code=400, details=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = {'error': message, 'code': code, **(details or {})}


def package_preview_selection(run, *, review_token=None):
    """The effective saved scope selection is an input separate from fact review."""
    from .preview_confirmation import review_fingerprint
    confirmation = (run.summary or {}).get('preview_confirmation') or {}
    if (confirmation.get('confirmed_at') and confirmation.get('review_fingerprint') ==
            (review_token or review_fingerprint(run))):
        return confirmation.get('preview') or {}
    return None


def validate_package_sources(project, run):
    """Keep exact document identity while using current registered project inputs.

    A corrected project name or planning date does not alter saved document text.
    Such inputs belong to the separately fingerprinted planning proposal.
    """
    if run.project_id != project.pk or run.is_deleted or run.status != 'succeeded':
        raise PlanningPackageRequestError('Select a completed analysis in this project.',
                                          code='planning_analysis_unavailable', status_code=404)
    files = list(project.files.filter(is_deleted=False).order_by('pk'))
    manifest = (run.summary or {}).get('extraction_source_manifest')
    if (not files or any(row.parse_status != 'done' for row in files)
            or sorted(run.source_file_ids or []) != [row.pk for row in files]
            or not manifest or _extraction_source_manifest(files) != manifest):
        raise PlanningPackageRequestError(
            'Source documents changed. Complete Document Intelligence for the current files before building the plan.',
            code='planning_sources_changed', status_code=409)
    return files


def resolve_generation_options(project, payload):
    """Read-only preflight used by both the HTTP command and its worker."""
    if not isinstance(payload, dict):
        raise PlanningPackageRequestError('Generation inputs must be an object.')
    options = payload.get('generation_options')
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise PlanningPackageRequestError('generation_options must be an object.')
    mode = options.get('mode', 'document_driven')
    if mode not in {'document_driven', 'planning_package'}:
        raise PlanningPackageRequestError('Choose document_driven or planning_package generation mode.')
    expected = options.get('expected_configuration_version')
    if expected is not None:
        if isinstance(expected, bool) or not str(expected).isdigit():
            raise PlanningPackageRequestError('expected_configuration_version must be an integer.')
        configuration = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).first()
        if configuration and configuration.configuration_version != int(expected):
            raise PlanningPackageRequestError(
                'Schedule configuration changed after the preview. Refresh the preview before generating.',
                code='configuration_conflict', status_code=409,
                details={'current_configuration_version': configuration.configuration_version})
    if mode == 'document_driven':
        return {'mode': mode, 'intelligence_run': None}
    run_id = options.get('intelligence_run_id')
    if isinstance(run_id, bool) or not str(run_id).isdigit() or int(run_id) <= 0:
        raise PlanningPackageRequestError('Select the saved Document Intelligence analysis to plan.')
    run = DocumentIntelligenceRun.objects.filter(
        pk=int(run_id), project=project, is_deleted=False, status='succeeded',
    ).first()
    if run is None:
        raise PlanningPackageRequestError('The selected analysis is unavailable in this project.',
                                          code='planning_analysis_unavailable', status_code=404)
    validate_package_sources(project, run)
    if project.effective_date is None:
        raise PlanningPackageRequestError('Save the project start date before building a planning package.',
                                          code='planning_start_required')
    return {'mode': mode, 'intelligence_run': run}
