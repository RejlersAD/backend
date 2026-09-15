"""Schedule-linked progress, earned value, forecast, and S-curve calculations."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Max, Q, Sum
from rest_framework.exceptions import ValidationError

from ..models import ActivityProgressUpdate, ScheduleControlSnapshot, ScheduleVersion


ZERO = Decimal('0')
HUNDRED = Decimal('100')


def _money(value):
    return (value or ZERO).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _ratio(numerator, denominator):
    if not denominator:
        return None
    return (numerator / denominator).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _planned_fraction(activity, data_date):
    start = activity.planned_start
    finish = activity.planned_finish
    if not start or not finish or data_date < start:
        return ZERO
    if data_date >= finish:
        return Decimal('1')
    span = max(1, (finish - start).days + 1)
    elapsed = max(0, (data_date - start).days + 1)
    return Decimal(elapsed) / Decimal(span)


def _latest_updates(version, data_date):
    updates = ActivityProgressUpdate.objects.filter(
        version=version, data_date__lte=data_date, is_deleted=False,
    ).select_related('activity').order_by('activity_id', '-data_date', '-id')
    latest = {}
    for update in updates:
        latest.setdefault(update.activity_id, update)
    return latest


def _activity_budgets(version):
    rows = version.activities.filter(is_deleted=False).annotate(
        budget_cost=Sum('assignments__budgeted_cost', filter=Q(assignments__is_deleted=False)),
        budget_hours=Sum('assignments__budgeted_hours', filter=Q(assignments__is_deleted=False)),
    ).select_related('wbs_node')
    return list(rows)


def _ownership_scope(version, data_date, activities):
    """Use frozen contractual membership, never activity titles or progress values."""
    from apps.core.project_models import Project
    from apps.project_control.epc_models import IntegratedBaseline, WBSActivityLink, control_scope
    from apps.project_control.services.epc import wbs_options, wbs_phase

    project = Project.objects.filter(pk=version.schedule.project.enterprise_project_id, is_deleted=False).first()
    scope = control_scope(project.scope_type if project else 'epc')
    current_ids = {row.pk for row in activities}
    scope.update(ready=True, blockers=[], source='schedule', integrated_baseline_id=None,
                 owned_activity_ids=sorted(current_ids), dependency_activity_ids=[])
    if not project or project.scope_type != 'detailed_engineering':
        return scope

    scope.update(source='activity_links', owned_activity_ids=[], dependency_activity_ids=[])
    baseline = IntegratedBaseline.objects.filter(project=project,
        schedule_baseline__source_version=version, data_date__lte=data_date).order_by('-data_date', '-revision', '-id').first()
    if baseline:
        scope.update(source='integrated_baseline', integrated_baseline_id=baseline.pk)
        manifest = baseline.manifest if isinstance(baseline.manifest, dict) else {}
        frozen = manifest.get('control_scope') or {}
        links = manifest.get('activity_links') or []
        try:
            owned_ids = {int(value) for value in manifest['owned_activity_ids']}
            dependency_ids = {int(value) for value in manifest['dependency_activity_ids']}
            all_ids = {int(row['activity']) for row in links}
            link_owned = {int(row['activity']) for row in links if row.get('control_role') == 'owned' and row.get('link_type') == 'engineering'}
            link_external = {int(row['activity']) for row in links if row.get('control_role') == 'dependency'
                             and row.get('link_type') in scope['dependency_phases']}
            valid = (frozen.get('scope_type') == 'detailed_engineering'
                     and owned_ids and not owned_ids & dependency_ids
                     and len(links) == len(all_ids) and owned_ids | dependency_ids == all_ids == current_ids
                     and owned_ids == link_owned and dependency_ids == link_external)
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            scope['blockers'].append('The effective integrated baseline does not provide complete Engineering ownership evidence for this schedule version. Review and capture the approved scope.')
        else:
            scope.update(owned_activity_ids=sorted(owned_ids), dependency_activity_ids=sorted(dependency_ids))
    else:
        links = list(WBSActivityLink.objects.filter(project=project, activity__version=version,
            activity__is_deleted=False, is_deleted=False).select_related('wbs_node'))
        nodes = {row['id']: row for row in wbs_options(project)}
        valid = {row.activity_id for row in links} == current_ids
        try:
            valid = valid and all(wbs_phase(row.wbs_node_id, nodes) == row.link_type for row in links)
        except ValidationError:
            valid = False
        owned_ids = {row.activity_id for row in links if row.link_type == 'engineering'}
        dependency_ids = {row.activity_id for row in links if row.link_type in scope['dependency_phases']}
        if not valid or not owned_ids or owned_ids | dependency_ids != current_ids:
            scope['blockers'].append('Map every activity to its EPC WBS phase, including at least one owned Engineering activity. Unmapped or invalid dependency scope cannot earn project progress.')
        else:
            scope.update(owned_activity_ids=sorted(owned_ids), dependency_activity_ids=sorted(dependency_ids))
    scope['ready'] = not scope['blockers']
    return scope


def _calculate_at(version, data_date, activities, latest):
    bac = pv = ev = ac = ZERO
    budget_hours = earned_hours = actual_hours = ZERO
    duration_weight = planned_duration = earned_duration = ZERO
    forecast_dates = []
    wbs = defaultdict(lambda: {
        'bac': ZERO, 'planned_value': ZERO, 'earned_value': ZERO, 'actual_cost': ZERO,
        'activity_count': 0, 'progress_weight': ZERO, 'planned_weight': ZERO, 'weight': ZERO,
    })

    activity_rows = []
    for activity in activities:
        update = latest.get(activity.id)
        cost_budget = activity.budget_cost or ZERO
        hours_budget = activity.budget_hours or ZERO
        planned_fraction = _planned_fraction(activity, data_date)
        progress_fraction = min(HUNDRED, max(ZERO, update.physical_progress_pct if update else ZERO)) / HUNDRED
        activity_ac = update.actual_cost if update else ZERO
        activity_actual_hours = update.actual_hours if update else ZERO
        duration = max(Decimal(str(activity.duration_days or 0)), Decimal('1'))

        bac += cost_budget
        pv += cost_budget * planned_fraction
        ev += cost_budget * progress_fraction
        ac += activity_ac
        budget_hours += hours_budget
        earned_hours += hours_budget * progress_fraction
        actual_hours += activity_actual_hours
        duration_weight += duration
        planned_duration += duration * planned_fraction
        earned_duration += duration * progress_fraction
        if update and update.forecast_finish:
            forecast_dates.append(update.forecast_finish)
        elif activity.planned_finish:
            forecast_dates.append(activity.planned_finish)

        wbs_key = activity.wbs_node.code if activity.wbs_node else 'Unassigned'
        wbs_name = activity.wbs_node.name if activity.wbs_node else 'Unassigned'
        bucket = wbs[(wbs_key, wbs_name)]
        bucket['bac'] += cost_budget
        bucket['planned_value'] += cost_budget * planned_fraction
        bucket['earned_value'] += cost_budget * progress_fraction
        bucket['actual_cost'] += activity_ac
        bucket['activity_count'] += 1
        bucket['progress_weight'] += duration * progress_fraction
        bucket['planned_weight'] += duration * planned_fraction
        bucket['weight'] += duration

        activity_rows.append({
            'id': activity.id,
            'progress_update_id': update.pk if update else None,
            'progress_reported_by_id': update.reported_by_id if update else None,
            'progress_updated_at': update.updated_at if update else None,
            'external_id': activity.external_id,
            'name': activity.name,
            'wbs_code': wbs_key,
            'planned_start': activity.planned_start,
            'planned_finish': activity.planned_finish,
            'is_critical': activity.is_critical,
            'duration_days': activity.duration_days,
            'budgeted_cost': _money(cost_budget),
            'budgeted_hours': _money(hours_budget),
            'planned_progress_pct': _money(planned_fraction * HUNDRED),
            'physical_progress_pct': _money(progress_fraction * HUNDRED),
            'actual_cost': _money(activity_ac),
            'actual_hours': _money(activity_actual_hours),
            'remaining_duration_days': update.remaining_duration_days if update else None,
            'actual_start': update.actual_start if update else None,
            'actual_finish': update.actual_finish if update else None,
            'forecast_finish': update.forecast_finish if update else activity.planned_finish,
            'notes': update.notes if update else '',
            'last_reported_date': update.data_date if update else None,
        })

    schedule_variance = ev - pv
    cost_variance = ev - ac
    spi = _ratio(ev, pv)
    cpi = _ratio(ev, ac)
    eac = _money(bac / cpi) if cpi and cpi > 0 else None
    etc = _money(eac - ac) if eac is not None else None
    vac = _money(bac - eac) if eac is not None else None
    progress_pct = _money((earned_duration / duration_weight) * HUNDRED) if duration_weight else ZERO
    planned_progress_pct = _money((planned_duration / duration_weight) * HUNDRED) if duration_weight else ZERO

    breakdown = []
    for (code, name), bucket in sorted(wbs.items()):
        weight = bucket.pop('weight')
        breakdown.append({
            'code': code, 'name': name, 'activity_count': bucket['activity_count'],
            'bac': _money(bucket['bac']), 'planned_value': _money(bucket['planned_value']),
            'earned_value': _money(bucket['earned_value']), 'actual_cost': _money(bucket['actual_cost']),
            'schedule_variance': _money(bucket['earned_value'] - bucket['planned_value']),
            'cost_variance': _money(bucket['earned_value'] - bucket['actual_cost']),
            'progress_pct': _money((bucket['progress_weight'] / weight) * HUNDRED) if weight else ZERO,
            'planned_progress_pct': _money((bucket['planned_weight'] / weight) * HUNDRED) if weight else ZERO,
        })

    return {
        'data_date': data_date,
        'bac': _money(bac), 'planned_value': _money(pv), 'earned_value': _money(ev),
        'actual_cost': _money(ac), 'schedule_variance': _money(schedule_variance),
        'cost_variance': _money(cost_variance), 'spi': spi, 'cpi': cpi,
        'eac': eac, 'etc': etc, 'vac': vac,
        'budgeted_hours': _money(budget_hours), 'earned_hours': _money(earned_hours),
        'actual_hours': _money(actual_hours), 'progress_pct': progress_pct,
        'planned_progress_pct': planned_progress_pct,
        'forecast_finish': max(forecast_dates) if forecast_dates else version.calculated_finish,
        'activities': activity_rows, 'wbs_breakdown': breakdown,
    }


def _curve_dates(version, data_date, activities):
    starts = [row.planned_start for row in activities if row.planned_start]
    finishes = [row.planned_finish for row in activities if row.planned_finish]
    if not starts:
        return [data_date]
    start = min(starts)
    finish = max(finishes + [data_date])
    span = (finish - start).days
    step = max(1, (span + 51) // 52)
    dates = []
    cursor = start
    while cursor < finish:
        dates.append(cursor)
        cursor += dt.timedelta(days=step)
    dates.extend([data_date, finish])
    return sorted(set(dates))


def build_control_dashboard(version, data_date=None):
    data_date = data_date or version.schedule.data_date or dt.date.today()
    activities = _activity_budgets(version)
    scope = _ownership_scope(version, data_date, activities)
    owned_ids = set(scope['owned_activity_ids'])
    dependency_ids = set(scope['dependency_activity_ids'])
    owned = [row for row in activities if row.pk in owned_ids]
    latest = _latest_updates(version, data_date)
    result = _calculate_at(version, data_date, activities, latest)
    all_rows = result['activities']
    for row in all_rows:
        row['control_role'] = 'owned' if row['id'] in owned_ids else ('dependency' if row['id'] in dependency_ids else 'unmapped')
    if scope['scope_type'] == 'detailed_engineering':
        result = _calculate_at(version, data_date, owned, latest)
        result['activities'] = all_rows
        if not any((latest.get(row.pk) and latest[row.pk].forecast_finish) or row.planned_finish for row in owned):
            result['forecast_finish'] = None
    result['control_scope'] = scope
    if not scope['ready']:
        for field in ('bac', 'planned_value', 'earned_value', 'actual_cost', 'schedule_variance',
                      'cost_variance', 'spi', 'cpi', 'eac', 'etc', 'vac', 'budgeted_hours',
                      'earned_hours', 'actual_hours', 'progress_pct', 'planned_progress_pct', 'forecast_finish'):
            result[field] = None
        result.update(curve=[], wbs_breakdown=[], snapshot_count=version.control_snapshots.filter(is_deleted=False).count())
        return result
    curve = []
    for curve_date in _curve_dates(version, data_date, owned):
        point = _calculate_at(version, curve_date, owned, _latest_updates(version, curve_date))
        curve.append({
            'date': curve_date, 'planned_value': point['planned_value'],
            'earned_value': point['earned_value'], 'actual_cost': point['actual_cost'],
            'planned_progress_pct': point['planned_progress_pct'], 'progress_pct': point['progress_pct'],
        })
    result['curve'] = curve
    result['snapshot_count'] = version.control_snapshots.filter(is_deleted=False).count()
    return result


@transaction.atomic
def capture_control_snapshot(version, data_date, user):
    # A stable parent lock also covers the first capture, when no observation
    # exists to lock. Progress writers acquire this same lock before posting.
    version = ScheduleVersion.objects.select_for_update().select_related('schedule').get(pk=version.pk)
    revision = (ScheduleControlSnapshot.objects.filter(
        version=version, data_date=data_date,
    ).aggregate(value=Max('revision'))['value'] or 0) + 1
    dashboard = build_control_dashboard(version, data_date)
    if not dashboard['control_scope']['ready']:
        raise ValidationError({'control_scope': dashboard['control_scope']['blockers']})
    values = {
        field: dashboard[field] for field in (
            'bac', 'planned_value', 'earned_value', 'actual_cost', 'schedule_variance',
            'cost_variance', 'spi', 'cpi', 'eac', 'etc', 'vac', 'progress_pct',
            'planned_progress_pct', 'forecast_finish',
        )
    }
    values.update(payload=_json_safe({
        'curve': dashboard['curve'], 'wbs_breakdown': dashboard['wbs_breakdown'],
        'budgeted_hours': dashboard['budgeted_hours'], 'earned_hours': dashboard['earned_hours'],
        'actual_hours': dashboard['actual_hours'],
        'control_scope': dashboard['control_scope'],
        # Progress rows remain editable operational records. Preserve the
        # values used here as well as their IDs so later edits cannot rewrite
        # the evidence behind an earlier published observation.
        'activities': dashboard['activities'],
        'source_manifest': {
            'schema_version': 1, 'schedule_version_id': version.pk,
            'schedule_id': version.schedule_id,
            'planning_project_id': version.schedule.project_id,
            'schedule_version_status': version.status,
            'data_date': data_date, 'revision': revision,
            'captured_by_id': getattr(user, 'pk', None),
        },
    }), captured_by=user)
    return ScheduleControlSnapshot.objects.create(
        version=version, data_date=data_date, revision=revision, **values,
    )
