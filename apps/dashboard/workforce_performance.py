"""Aggregate workforce source facts, separate from unconnected capacity plans.

Only database aggregates leave the employee table. Employee identities, salary,
contact details, protected-identity flags and talent assessments are not read.
"""
import hashlib
import logging
from collections import defaultdict
from datetime import date, timedelta

from django.apps import apps
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .executive import action, metric
from .executive_people import current_employee_filter


logger = logging.getLogger(__name__)
SOURCE = 'Canonical employee master aggregates'
ROUTE = '/hr/employees'
GAPS = (
    ('billable_utilisation', 'Billable utilisation', 'percent', 'Approved billable project hours and available working hours for a common period are not connected. Headcount and attendance are not billable utilisation.'),
    ('critical_vacancies', 'Critical vacancies', 'count', 'Approved open requisitions and a governed critical-role classification are not connected. Missing succession incumbents do not establish vacancies.'),
    ('voluntary_turnover', 'Voluntary turnover', 'percent', 'Validated voluntary exit reasons and average headcount for a common reporting period are not connected. Notice status and exit-date counts do not establish voluntary turnover.'),
    ('capacity_coverage', 'Capacity coverage', 'percent', 'Approved staffing supply, dated delivery demand and comparable availability are not connected. Employee counts are not FTE or capacity coverage.'),
)
QUALITY = (
    ('missing_department', 'Missing department', 'Current employee records without a recorded department.'),
    ('missing_office', 'Missing office', 'Current employee records without a recorded organizational office.'),
    ('missing_business_unit', 'Missing business unit', 'Current employee records without a recorded business unit.'),
    ('invalid_lifecycle_dates', 'Inconsistent lifecycle dates', 'Non-test master records whose recorded exit date precedes their joining date.'),
)


def _gap(identifier, label, description, unit='count'):
    return metric(identifier, label, status='unavailable', unit=unit, description=description,
                  source='Approved workforce reporting source not connected')


def _count(identifier, label, value=None, *, status='available', description=''):
    return metric(identifier, label, value, status=status, source=SOURCE,
                  route=None if status == 'restricted' else ROUTE, description=description)


def _distribution(status, description):
    return {'status': status, 'rows': [], 'total': None, 'unit': 'people', 'description': description}


def _empty(status, description, as_of):
    scope = {'label': 'Current authorized employee master; non-test employees only',
             'as_of_date': as_of.isoformat(), 'unit': 'people', 'aggregate_only': True}
    return {
        'status': status, 'scope': scope, 'source_updated_at': None, 'source_timestamp_kind': None,
        'source_timestamp_label': 'Latest non-test employee master record update',
        'kpis': [_count('headcount', 'Current headcount', status=status, description=description)]
                + [_gap(key, label, reason, unit) for key, label, unit, reason in GAPS],
        'actions': [], 'actions_status': status, 'action_count': None, 'actions_returned': 0, 'actions_truncated': False,
        'capacity_plan': {'status': status, 'basis': 'department_headcount', 'unit': 'people', 'rows': [],
                          'total_rows': None, 'returned_rows': 0, 'truncated': False, 'description': description},
        'supply_demand_outlook': {'status': 'unavailable', 'series': [],
                                 'description': 'Approved dated discipline supply, contracted delivery demand and probability-adjusted opportunity demand are not connected. Current employee counts cannot establish a future capacity plan.'},
        'workforce_movement': {'status': status, 'metrics': [
            _count('joiners_30d', 'Recorded joiners in 30 days', status=status, description=description),
            _count('recorded_exits_30d', 'Recorded exit dates in 30 days', status=status, description=description),
        ], 'series': [], 'period_start': None, 'period_end': as_of.isoformat(), 'description': description},
        'critical_roles': {'status': 'unavailable', 'rows': [],
                           'description': 'Approved critical vacancies, project role requirements and staffing commitments are not connected. Individual talent and succession records are not exposed in this executive summary.'},
        'project_coverage_risk': {'status': 'unavailable', 'rows': [],
                                 'description': 'Approved project staffing assignments and dated discipline demand are not connected. Department headcount and notice status do not establish project coverage risk.'},
        'retention_mobility': {'status': status, 'metrics': [
            _count('notice_period', 'Employees on notice', status=status, description=description),
            next(_gap(key, label, reason, unit) for key, label, unit, reason in GAPS if key == 'voluntary_turnover'),
            _gap('internal_mobility', 'Internal mobility', 'Approved and effective internal moves on a reconciled reporting period are not connected.'),
            _gap('retention_risk', 'Retention risk', 'A governed aggregate retention-risk assessment is not connected. Notice status is not a prediction or a voluntary-exit reason.'),
        ], 'description': description},
        'distribution': {
            'office': _distribution(status, description), 'branch': _distribution(status, description),
            'business_unit': _distribution(status, description),
            'employment_type': _distribution('unavailable', 'Employment type is not recorded in the canonical employee master. Account-profile classifications cannot represent workers without accounts and are not substituted.'),
        },
        'data_quality': {'status': status, 'metrics': [_count(key, label, status=status, description=reason) for key, label, reason in QUALITY],
                         'description': description},
    }


def _group_counts(queryset, field):
    grouped = defaultdict(int)
    missing = 0
    for row in queryset.values(field).annotate(count=Count('pk')).order_by(field):
        label = (row[field] or '').strip()
        if not label:
            missing += row['count']
        grouped[label or 'Unassigned'] += row['count']
    return [{'label': label, 'count': count} for label, count in sorted(grouped.items())], missing


def _six_months(as_of):
    index = as_of.year * 12 + as_of.month - 1
    return [date(value // 12, value % 12 + 1, 1) for value in range(index - 5, index + 1)]


def _date_counts(queryset, field, start, end):
    return {row['month'].strftime('%Y-%m-01'): row['count'] for row in
            queryset.filter(**{f'{field}__range': (start, end)}).annotate(month=TruncMonth(field))
            .values('month').annotate(count=Count('pk')).order_by('month')}


def _read_workforce(as_of):
    employee = apps.get_model('hr_core', 'EmployeeMaster')
    source = employee.objects.filter(is_test_person=False)
    current_filter = current_employee_filter(as_of)
    current = source.filter(current_filter)
    start = as_of - timedelta(days=29)
    counts = source.aggregate(
        records=Count('pk'), headcount=Count('pk', filter=current_filter), latest=Max('updated_at'),
        notice_period=Count('pk', filter=current_filter & Q(employment_status='notice_period')),
        joiners_30d=Count('pk', filter=Q(join_date__range=(start, as_of))),
        recorded_exits_30d=Count('pk', filter=Q(exit_date__range=(start, as_of), exit_date__gte=F('join_date'))),
        invalid_lifecycle_dates=Count('pk', filter=Q(exit_date__lt=F('join_date'))),
    )
    if not counts['records']:
        return None
    groups = {}
    for field in ['department', 'office', 'branch', 'business_unit']:
        groups[field], counts[f'missing_{field}'] = _group_counts(current, field)
    months = _six_months(as_of)
    joins = _date_counts(source, 'join_date', months[0], as_of)
    exits = _date_counts(source.filter(exit_date__gte=F('join_date')), 'exit_date', months[0], as_of)
    series = [{'month': month.isoformat(), 'label': month.strftime('%b %Y'),
               'joiners': joins.get(month.isoformat(), 0), 'exits': exits.get(month.isoformat(), 0)} for month in months]
    return counts, groups, series, months[0]


def build_workforce_performance(user, context):
    # Existing HR workforce-summary access is module-wide aggregate access;
    # neither an account link nor person-level directory access is required.
    del user
    as_of = timezone.localtime(context['generated_at']).date()
    if 'hr_management' not in context['allowed_modules']:
        return _empty('restricted', 'HR Management read access is required for workforce aggregates.', as_of)
    try:
        with transaction.atomic():
            data = _read_workforce(as_of)
    except LookupError:
        return _empty('unavailable', 'The canonical employee master is not configured.', as_of)
    except Exception:
        logger.exception('Executive workforce performance source unavailable')
        return _empty('error', 'The canonical employee master aggregates could not be read.', as_of)
    if data is None:
        return _empty('unavailable', 'The employee master contains no non-test workforce records; coverage is not established.', as_of)
    counts, groups, series, period_start = data
    result = _empty('available', 'Authorized employee master aggregates.', as_of)
    result.update({'source_updated_at': counts['latest'].isoformat() if counts['latest'] else None,
                   'source_timestamp_kind': 'latest_record_update' if counts['latest'] else None})
    result['kpis'][0] = _count('headcount', 'Current headcount', counts['headcount'], description=
                               'Non-test employee master records joined by today, with no exit date before today and active, probation, notice-period or suspended status. Includes workers without user accounts; this is people, not FTE. Exit dates represent last working days.')
    department_rows = [{
        'id': 'department-' + hashlib.sha256(row['label'].encode('utf-8')).hexdigest()[:16],
        'department': row['label'], 'headcount': row['count'], 'fte': None, 'billable_utilisation': None,
        'committed_demand': None, 'weighted_demand': None, 'capacity_gap': None,
        'critical_roles': None, 'owner': None, 'health': 'unavailable', 'route': ROUTE,
    } for row in groups['department']]
    result['capacity_plan'] = {
        'status': 'available', 'basis': 'department_headcount', 'unit': 'people',
        'rows': department_rows[:200], 'total_rows': len(department_rows),
        'returned_rows': min(len(department_rows), 200), 'truncated': len(department_rows) > 200,
        'description': 'Current people by recorded department, including an Unassigned bucket for missing values. Department labels are not validated engineering disciplines. FTE, utilisation, demand, capacity gaps and accountable department owners are not connected.',
    }
    for field, label in [('office', 'organizational office'), ('branch', 'recorded operating branch'), ('business_unit', 'business unit')]:
        result['distribution'][field] = {'status': 'available', 'rows': groups[field], 'total': counts['headcount'], 'unit': 'people',
                                          'description': f'Current employees grouped by {label} recorded in the employee master; missing values appear as Unassigned. This is headcount, not FTE or a legal-entity consolidation.'}
    movement = result['workforce_movement']
    movement.update({'series': series, 'period_start': period_start.isoformat(), 'period_end': as_of.isoformat(),
                     'description': 'Counts of joining and exit dates recorded in the current non-test master over six calendar months, through the as-of date. The current month is partial. Exit dates are last working days, not reasons for leaving; invalid exit-before-join dates are excluded. This is not a complete employment-event history or a reconstructed headcount bridge.'})
    for item in movement['metrics']:
        item.update({'value': counts[item['id']], 'period': 'trailing_30_calendar_days',
                     'period_start': (as_of - timedelta(days=29)).isoformat(), 'period_end': as_of.isoformat(),
                     'description': ('Recorded joining dates in the last 30 calendar days, including subsequent leavers; test persons excluded.'
                                     if item['id'] == 'joiners_30d' else 'Recorded last-working dates in the last 30 calendar days, including today; future dates and exit-before-join errors excluded. No voluntary reason is inferred.')})
        item.update({'definition': item['description'], 'reason': None})
    retention = result['retention_mobility']
    retention['metrics'][0] = _count('notice_period', 'Employees on notice', counts['notice_period'], description=
                                     'Current employee master records whose employment status is notice_period. Aggregate only; notice status does not establish an exit reason, retention-risk score or a project staffing gap.')
    retention['description'] = 'Only the current aggregate notice-period count is reported. Voluntary turnover, internal mobility and predicted retention risk need separate approved evidence and reporting periods.'
    quality = {'invalid_lifecycle_dates': counts['invalid_lifecycle_dates']}
    for field in ['department', 'office', 'business_unit']:
        # Do not confuse an explicitly recorded label "Unassigned" with a blank
        # field. Count missing mappings from source aggregates before display.
        quality[f'missing_{field}'] = counts[f'missing_{field}']
    result['data_quality'] = {'status': 'available',
                               'metrics': [_count(key, label, quality[key], description=reason) for key, label, reason in QUALITY],
                               'description': 'Specific master-data checks; they are not an overall people-risk rating. Mapping checks cover current employees; lifecycle consistency covers all non-test master records.'}
    actions = []
    for key, label, reason in QUALITY:
        if quality[key]:
            item = action(f'workforce-{key}', 'hr', f'{quality[key]} records: {label.lower()}',
                          owner='HR', route=ROUTE, detail=reason + ' Review and correct the authorized HR source.', severity='medium')
            item['impact'] = None
            actions.append(item)
    if counts['notice_period']:
        item = action('workforce-notice-review', 'hr', 'Review current notice-period transitions', owner='HR', route=ROUTE,
                      detail='Review aggregate transition planning in the authorized HR workflow. No project coverage gap or voluntary exit reason is inferred.', severity='medium')
        item['impact'] = None
        actions.append(item)
    result.update({'actions': actions, 'action_count': len(actions), 'actions_returned': len(actions), 'actions_status': 'available'})
    return result
