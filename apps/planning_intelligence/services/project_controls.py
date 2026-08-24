"""Schedule-linked progress, earned value, forecast, and S-curve calculations."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum

from ..models import ActivityProgressUpdate, ScheduleControlSnapshot


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
            'external_id': activity.external_id,
            'name': activity.name,
            'wbs_code': wbs_key,
            'planned_start': activity.planned_start,
            'planned_finish': activity.planned_finish,
            'is_critical': activity.is_critical,
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
    latest = _latest_updates(version, data_date)
    result = _calculate_at(version, data_date, activities, latest)
    curve = []
    for curve_date in _curve_dates(version, data_date, activities):
        point = _calculate_at(version, curve_date, activities, _latest_updates(version, curve_date))
        curve.append({
            'date': curve_date, 'planned_value': point['planned_value'],
            'earned_value': point['earned_value'], 'actual_cost': point['actual_cost'],
            'planned_progress_pct': point['planned_progress_pct'], 'progress_pct': point['progress_pct'],
        })
    result['curve'] = curve
    result['snapshot_count'] = version.control_snapshots.filter(is_deleted=False).count()
    return result


def capture_control_snapshot(version, data_date, user):
    dashboard = build_control_dashboard(version, data_date)
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
    }), captured_by=user, is_deleted=False, deleted_at=None)
    snapshot, _ = ScheduleControlSnapshot.objects.update_or_create(
        version=version, data_date=data_date, defaults=values,
    )
    return snapshot
