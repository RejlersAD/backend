"""Distinguish explicitly unconfirmed setup defaults from reporting evidence."""


def confirmed_project_metadata(project, *, snapshot=None):
    custom_fields = project.custom_fields if isinstance(project.custom_fields, dict) else {}
    setup = custom_fields.get('control_setup')
    setup = setup if isinstance(setup, dict) else {}
    status_confirmed = setup.get('operational_status_confirmed') is not False
    progress_confirmed = snapshot is not None or setup.get('progress_confirmed') is not False
    progress = snapshot.progress_pct if snapshot is not None else project.progress
    return {
        'operational_status_confirmed': status_confirmed,
        'progress_confirmed': progress_confirmed,
        'progress_pct': progress if progress_confirmed else None,
    }
