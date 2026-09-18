"""Persist planner selections separately from extracted document evidence."""
from copy import deepcopy

from .operational_jobs import canonical_fingerprint


def source_snapshot(project):
    return {
        'project_updated_at': project.updated_at.isoformat(),
        'files': list(project.files.filter(is_deleted=False).order_by('id').values(
            'id', 'parse_status', 'updated_at', 'size_bytes', 'category',
        )),
    }


def source_fingerprint(project):
    return canonical_fingerprint(source_snapshot(project))


def source_error(run):
    """Return a stable API error when a preview cannot describe current inputs."""
    if run.status != 'succeeded' or run.project.intelligence_runs.filter(
        is_deleted=False, status__in=['running', 'succeeded'], created_at__gt=run.created_at,
    ).exists():
        return ('intelligence_run_not_current', 'Open the latest completed Document Intelligence Preview.')
    files = list(run.project.files.filter(is_deleted=False))
    if (not files or any(item.parse_status != 'done' for item in files)
            or sorted(item.pk for item in files) != sorted(run.source_file_ids)):
        return ('intelligence_sources_changed', 'Source documents have changed. Finish parsing and run Document Intelligence again.')
    captured = (run.summary or {}).get('source_fingerprint')
    if captured:
        changed = captured != source_fingerprint(run.project)
    else:
        # Historical runs predate the fingerprint; timestamps retain their
        # usable reviewed evidence without permitting changed inputs through.
        changed = (run.project.updated_at > run.started_at
                   or any(item.updated_at > run.started_at for item in files))
    if changed:
        return ('intelligence_sources_changed', 'Project inputs or source documents have changed. Run Document Intelligence again.')
    return None


def review_fingerprint(run):
    return canonical_fingerprint({
        'facts': list(run.facts.filter(is_deleted=False).order_by('id').values(
            'id', 'status', 'value', 'updated_at',
        )),
        'conflicts': list(run.conflicts.filter(is_deleted=False).order_by('id').values(
            'id', 'status', 'resolution', 'updated_at',
        )),
    })


def confirmation_is_current(run):
    confirmation = (run.summary or {}).get('preview_confirmation') or {}
    return bool(
        confirmation.get('confirmed_at') and confirmation.get('preview')
        and not source_error(run)
        and not run.conflicts.filter(is_deleted=False, status__in=['open', 'ignored']).exists()
        and confirmation.get('source_fingerprint') == source_fingerprint(run.project)
        and confirmation.get('review_fingerprint') == review_fingerprint(run)
    )


def current_confirmed_preview(run):
    if confirmation_is_current(run):
        return deepcopy(run.summary['preview_confirmation']['preview'])
    return None


def confirmation_metadata(run):
    confirmation = (run.summary or {}).get('preview_confirmation')
    if not confirmation:
        return None
    return {
        'confirmed_at': confirmation['confirmed_at'],
        'confirmed_by': confirmation['confirmed_by'],
        'preview': deepcopy(confirmation['preview']),
        'is_current': confirmation_is_current(run),
    }


def apply_confirmed_preview(run, intelligence):
    selection = current_confirmed_preview(run)
    if not selection:
        return intelligence
    result = deepcopy(intelligence)
    for key in ('detected_project_name', 'detected_effective_date_text', 'detected_duration_months', 'hse_studies'):
        result[key] = selection[key]
    for code, choices in selection['disciplines'].items():
        result.setdefault('disciplines', {}).setdefault(code, {}).update(choices)
    return result
