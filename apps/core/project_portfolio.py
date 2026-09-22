"""Compact portfolio facts from scoped projects and saved schedule evidence."""
from datetime import date

from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from apps.planning_intelligence.models import PlanningProject
from apps.planning_intelligence.schedule_models import ScheduleBaseline
from apps.project_control.services.project_metadata import confirmed_project_metadata


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _approved_baselines(project_ids):
    """Choose one saved approval per project, never a draft's current dates."""
    eligible = ScheduleBaseline.objects.filter(
        schedule__project__enterprise_project_id__in=project_ids,
        is_deleted=False, schedule__is_deleted=False,
        schedule__project__is_deleted=False, source_version__is_deleted=False,
        source_version__schedule_id=F('schedule_id'),
        approved_by__isnull=False, approved_at__isnull=False,
    )
    latest = eligible.filter(
        schedule__project__enterprise_project_id=OuterRef('schedule__project__enterprise_project_id'),
    ).order_by('-approved_at', '-pk').values('pk')[:1]
    rows = list(eligible.filter(pk=Subquery(latest)).order_by().values(
        'id', 'name', 'approved_at', 'schedule__project__enterprise_project_id',
        'snapshot__accepted_inputs__project_start', 'snapshot__version__calculated_finish',
    ))
    baselines = {}
    incomplete = {}
    for row in rows:
        baseline = {
            'approved': True, 'id': row['id'], 'name': row['name'],
            'approved_at': row['approved_at'],
            'start_date': _date(row['snapshot__accepted_inputs__project_start']),
            'finish_date': _date(row['snapshot__version__calculated_finish']),
        }
        baselines[row['schedule__project__enterprise_project_id']] = baseline
        if baseline['start_date'] is None or baseline['finish_date'] is None:
            incomplete[row['id']] = baseline
    # Older snapshots did not include a frozen schedule summary. Read just their
    # saved activities in one batch; missing dates remain unknown, not live dates.
    if incomplete:
        for row in eligible.filter(pk__in=incomplete).values('id', 'snapshot__activities'):
            baseline = incomplete[row['id']]
            activities = row['snapshot__activities']
            if not isinstance(activities, list) or not activities:
                continue
            for field, source, aggregate in (
                ('start_date', 'planned_start', min), ('finish_date', 'planned_finish', max),
            ):
                values = [_date(activity.get(source)) if isinstance(activity, dict) else None
                          for activity in activities]
                if baseline[field] is None and all(values):
                    baseline[field] = aggregate(values)
    return baselines


def _health(project, workspace, baseline, today):
    def result(key, label, reason):
        return {'key': key, 'label': label, 'reason': reason}

    if project.status in {'completed', 'cancelled'}:
        return result('unknown', 'Closed', 'The project is closed; current schedule performance is not assessed here.')
    if not confirmed_project_metadata(project)['operational_status_confirmed']:
        return result('needs_setup', 'Needs setup', 'The operational project status has not been confirmed.')
    if project.end_date and project.end_date < today:
        return result('at_risk', 'At risk', 'The project remains open beyond its registered finish date.')
    missing = []
    if project.owner_id is None:
        missing.append('owner')
    if project.start_date is None or project.end_date is None:
        missing.append('project dates')
    if workspace is None:
        missing.append('planning workspace')
    if missing:
        return result('needs_setup', 'Needs setup', 'Missing ' + ', '.join(missing) + '.')
    if (workspace['master_schedule_version_id'] is not None
            and (workspace['master_schedule_version__is_deleted']
                 or workspace['master_schedule_version__status'] not in {'approved', 'baselined'})):
        return result('needs_review', 'Needs review', 'The selected schedule revision has not been approved and saved as a baseline.')
    if baseline is None:
        return result('needs_review', 'Needs review', 'No approved schedule baseline has been saved.')
    return result('unknown', 'Not assessed', 'An approved baseline is saved; current performance evidence is not assessed here.')


def project_portfolio_facts(projects):
    """The caller passes only the authorized page; queries never widen that scope."""
    project_ids = [project.pk for project in projects]
    if not project_ids:
        return {}
    baselines = _approved_baselines(project_ids)
    workspaces = {row['enterprise_project_id']: row for row in PlanningProject.objects.filter(
        enterprise_project_id__in=project_ids, is_deleted=False,
    ).values('enterprise_project_id', 'master_schedule_version_id',
             'master_schedule_version__status', 'master_schedule_version__is_deleted')}
    today = timezone.localdate()
    return {project.pk: {
        'baseline': baselines.get(project.pk),
        'health': _health(project, workspaces.get(project.pk), baselines.get(project.pk), today),
        'missing_owner': project.owner_id is None,
        # No enterprise/entity field is recorded on core.Project.
        'entity': None,
    } for project in projects}
