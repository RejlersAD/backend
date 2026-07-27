"""
Manhour / Resource Estimator (MODULE 10) — deterministic basis-of-estimate.

Basis: DEFAULT_CALENDAR (22 man-days/month, 8 hrs/day => 176 man-hours/month
per RFT Appendix-4). Assumes one responsible engineer per discipline is
engaged for the full duration of that discipline's activities (soft-coded
FTE=1 assumption) — a defensible MVP estimate, not a resource-levelled plan.
"""
from __future__ import annotations

from ..config import DEFAULT_CALENDAR, DISCIPLINE_NAME_BY_CODE, DISCIPLINE_RESPONSIBLE_ROLE


def build_manhours(project, activities: list) -> dict:
    calendar = dict(DEFAULT_CALENDAR)
    calendar.update(getattr(project, 'calendar_overrides', None) or {})
    hours_per_day = calendar['hours_per_day']

    by_discipline: dict[str, dict] = {}
    for activity in activities:
        if activity.get('is_milestone'):
            continue
        disc = activity.get('discipline')
        duration = activity.get('original_duration_days') or 0
        if disc not in by_discipline:
            by_discipline[disc] = {
                'discipline': disc,
                'discipline_name': DISCIPLINE_NAME_BY_CODE.get(disc, disc),
                'responsible_role': DISCIPLINE_RESPONSIBLE_ROLE.get(disc, 'Engineer'),
                'total_working_days': 0,
            }
        by_discipline[disc]['total_working_days'] += duration

    rows = []
    grand_total_hours = 0
    for disc, row in by_discipline.items():
        manhours = row['total_working_days'] * hours_per_day
        man_days = row['total_working_days']
        rows.append({
            **row,
            'man_hours': manhours,
            'man_days': man_days,
            'man_months': round(man_days / calendar['man_days_per_month'], 2) if calendar['man_days_per_month'] else None,
        })
        grand_total_hours += manhours

    rows.sort(key=lambda r: r['discipline'])
    return {
        'basis': {
            'hours_per_day': hours_per_day,
            'man_days_per_month': calendar['man_days_per_month'],
            'assumption': 'One responsible engineer (FTE=1) per discipline for the duration of its scheduled activities.',
        },
        'by_discipline': rows,
        'grand_total_man_hours': grand_total_hours,
    }
