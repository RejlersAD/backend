"""Read-only executive summaries from authorized engineering, HR and quality registers.

The caller supplies effective module *read* decisions, including user denies.
These are register snapshots, not a consolidated Rejlers group reporting ledger.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone


logger = logging.getLogger(__name__)


def _metric(identifier, label, value=None, *, source, description, status='available', unit='count'):
    return {
        'id': identifier, 'label': label, 'value': value, 'unit': unit,
        'status': status, 'source': source, 'description': description,
    }


def _department(identifier, label, route, metrics, actions, limitations):
    states = {metric['status'] for metric in metrics}
    status = next((state for state in ('available', 'error', 'unavailable', 'restricted')
                   if state in states), 'unavailable')
    return {
        'id': identifier, 'label': label, 'route': route, 'status': status,
        'metrics': metrics, 'actions': actions, 'limitations': limitations,
    }


def _restricted(identifier, label, route):
    return {
        'id': identifier, 'label': label, 'route': route, 'status': 'restricted',
        'metrics': [], 'actions': [],
        'limitations': ['Read access to this department register is required.'],
    }


def _updated(section, timestamps, label):
    latest = max((value for value in timestamps if value is not None), default=None)
    section.update({
        'source_updated_at': latest.isoformat() if latest else None,
        'source_timestamp_kind': 'latest_record_update' if latest else None,
        'source_timestamp_label': label,
    })
    return section


def _action(identifier, department, title, route, detail, *, owner='Unassigned', severity='medium'):
    return {
        'id': identifier, 'department': department, 'title': title,
        'severity': severity, 'owner': owner or 'Unassigned',
        'action_label': 'Review register', 'route': route, 'detail': detail,
    }


def _engineering(user, context):
    allowed = context['allowed_modules']
    modules = {'process_datasheet', 'electrical_datasheet'}
    if not allowed.intersection(modules):
        return _restricted('engineering', 'Engineering', '/engineering/process/datasheet')

    metrics, actions, source_updates = [], [], []
    registers = (
        ('process_datasheet', 'ProcessDatasheet', 'process_reviews', 'Process awaiting review / approval',
         Q(status__in=['ifr', 'ifa']), {}, '/engineering/process/datasheet',
         'Current process datasheets with issued-for-review or issued-for-approval status.'),
        ('electrical_datasheet', 'ElectricalDatasheet', 'electrical_rework', 'Electrical requiring revision',
         Q(status__in=['rejected', 'revision_required']), {'is_deleted': False},
         '/engineering/electrical/datasheet',
         'Current electrical datasheets marked rejected or revision required; deleted records excluded.'),
    )
    for module, model_name, identifier, label, condition, filters, route, description in registers:
        source = f'{model_name} register'
        if module not in allowed:
            metrics.append(_metric(identifier, label, source=source, description='Register read access is required.', status='restricted'))
            continue
        try:
            model = apps.get_model(module, model_name)
            with transaction.atomic():
                counts = model.objects.filter(**filters).aggregate(
                    records=Count('pk'), count=Count('pk', filter=condition), latest=Max('updated_at'),
                )
            source_updates.append(counts['latest'])
            populated = counts['records'] > 0
            metric = _metric(identifier, label, counts['count'] if populated else None,
                             source=source, description=description if populated else 'No records are available in this register.',
                             status='available' if populated else 'unavailable')
            metrics.append(metric)
            if populated and counts['count']:
                actions.append(_action(
                    f'engineering-{identifier}', 'engineering', f"{counts['count']} {label.lower()}",
                    route, description + ' Review the register and confirm the responsible discipline owner.',
                ))
        except LookupError:
            metrics.append(_metric(identifier, label, source=source, description='This source is not configured.', status='unavailable'))
        except Exception:
            logger.exception('Executive engineering source failed: %s', module)
            metrics.append(_metric(identifier, label, source=source, description='This register could not be read.', status='error'))

    metrics.append(_metric(
        'engineering_on_time', 'On-time deliverables', source='Delivery baseline required',
        description='A reconciled deliverable register with committed and actual issue dates is required.',
        status='unavailable', unit='percent',
    ))
    route = '/engineering/process/datasheet' if 'process_datasheet' in allowed else '/engineering/electrical/datasheet'
    section = _department('engineering', 'Engineering', route, metrics, actions, [
        'Coverage is limited to the authorized process and electrical datasheet registers; other disciplines are not consolidated.',
        'Review status does not establish an overdue delivery, engineering acceptance rate or total project progress.',
        'Project identifiers and revision meanings differ between registers. Counts are kept separate.',
    ])
    return _updated(section, source_updates, 'Latest authorized datasheet register update')


def current_employee_filter(today):
    """Canonical current-headcount rule shared by executive workforce summaries."""
    return (Q(join_date__lte=today)
            & (Q(exit_date__isnull=True) | Q(exit_date__gte=today))
            & Q(employment_status__in=['active', 'probation', 'notice_period', 'suspended']))


def _hr(user, context):
    route = '/hr/employees'
    if 'hr_management' not in context['allowed_modules']:
        return _restricted('hr', 'Human Resources', route)

    source = 'Employee master'
    today = timezone.localtime(context['generated_at']).date()
    start = today - timedelta(days=29)
    try:
        employee = apps.get_model('hr_core', 'EmployeeMaster')
        current = current_employee_filter(today)
        with transaction.atomic():
            source_records = employee.objects.filter(is_test_person=False)
            counts = source_records.aggregate(
                records=Count('pk'), current=Count('pk', filter=current),
                joiners=Count('pk', filter=Q(join_date__range=(start, today))),
                unmapped=Count('pk', filter=current & Q(department='')), latest=Max('updated_at'),
            )
            allocation = list(source_records.filter(current).values('department').annotate(headcount=Count('pk')).order_by('department'))
        populated = counts['records'] > 0
        status = 'available' if populated else 'unavailable'
        metrics = [
            _metric('headcount', 'Current employees', counts['current'] if populated else None,
                    source=source, status=status,
                    description='Employee master records joined by today and not yet exited, including probation, notice and suspension; test persons excluded.'
                    if populated else 'The employee master contains no workforce records.'),
            _metric('joiners_30d', 'Joiners in 30 days', counts['joiners'] if populated else None,
                    source=source, status=status,
                    description=f'Employees with recorded joining dates from {start.isoformat()} to {today.isoformat()}, including subsequent leavers; test persons excluded.'
                    if populated else 'Joining-date data is unavailable.'),
        ]
        actions = []
        if counts['unmapped']:
            actions.append(_action(
                'hr-department-mapping', 'hr', f"{counts['unmapped']} employees need department mapping", route,
                'Current employee records have no department value. Complete the master data before reporting department totals.',
            ))
        section = _department('hr', 'Human Resources', route, metrics, actions, [
            'Aggregate workforce access follows the HR Management read grant; no individual employee or salary details are returned.',
            'Current headcount is not FTE or a historical average. Legal entities are not reconciled in this source.',
            'Attendance hours do not establish billable utilization. Approved project hours and available capacity are required.',
        ])
        # Keep recorded department labels; whitespace-only values share the unknown bucket.
        grouped = defaultdict(int)
        for row in allocation:
            grouped[(row['department'] or '').strip() or 'Unassigned'] += row['headcount']
        section['workforce_by_department'] = [
            {'department': label, 'headcount': count} for label, count in sorted(grouped.items())
        ]
        return _updated(section, [counts['latest']], 'Latest non-test employee master record update')
    except LookupError:
        status, description = 'unavailable', 'The employee master source is not configured.'
    except Exception:
        logger.exception('Executive workforce source failed')
        status, description = 'error', 'The employee master could not be read.'
    return _department('hr', 'Human Resources', route, [
        _metric('headcount', 'Current employees', source=source, description=description, status=status),
    ], [], [description])


def _qhse(user, context):
    allowed = context['allowed_modules']
    route = '/qhse/general/detailed' if 'qhse_detailed' in allowed else '/qhse'
    if not allowed.intersection({'qhse', 'qhse_detailed'}):
        return _restricted('qhse', 'QHSE', route)

    source = 'QHSE running project register'
    source_updates = []
    try:
        project = apps.get_model('qhse', 'QHSERunningProject')
        with transaction.atomic():
            projects = project.objects.filter(is_active=True)
            overdue_cars = Q(cars_open__gt=0, cars_delayed_closing_no_days__gt=0)
            counts = projects.aggregate(
                records=Count('pk'), cars=Sum('cars_open'),
                overdue_car_projects=Count('pk', filter=overdue_cars),
                delayed_audits=Count('pk', filter=Q(delay_in_audits_no_days__gt=0)),
                latest=Max('updated_at'),
            )
            issues = list(projects.filter(overdue_cars).order_by('-cars_delayed_closing_no_days', 'pk').values(
                'pk', 'project_no', 'project_manager', 'cars_open', 'cars_delayed_closing_no_days',
            )[:3])
        source_updates.append(counts['latest'])
        populated = counts['records'] > 0
        status = 'available' if populated else 'unavailable'
        metrics = [
            _metric('open_cars', 'Open corrective actions', counts['cars'] if populated else None,
                    source=source, status=status, description='Sum of recorded open quality corrective-action counts on active QHSE project records.'),
            _metric('overdue_car_projects', 'Projects with delayed corrective actions', counts['overdue_car_projects'] if populated else None,
                    source=source, status=status, description='Active project records with open corrective actions and a recorded closure delay greater than zero days.'),
            _metric('delayed_audit_projects', 'Projects with delayed audits', counts['delayed_audits'] if populated else None,
                    source=source, status=status, description='Active project records with a recorded audit delay greater than zero days; this is a project count, not an audit count.'),
        ]
        actions = [
            _action(
                f"qhse-delayed-cars-{issue['pk']}", 'qhse', f"{issue['project_no']}: delayed corrective actions", route,
                f"{issue['cars_open']} open corrective actions; recorded closure delay {issue['cars_delayed_closing_no_days']} days. Confirm a recovery date in the project register.",
                owner=issue['project_manager'], severity='high',
            ) for issue in issues
        ]
    except LookupError:
        metrics, actions = [_metric('open_cars', 'Open corrective actions', source=source,
                                    description='The QHSE project source is not configured.', status='unavailable')], []
    except Exception:
        logger.exception('Executive QHSE source failed')
        metrics, actions = [_metric('open_cars', 'Open corrective actions', source=source,
                                    description='The QHSE project register could not be read.', status='error')], []

    metrics.append(_metric(
        'safety_incidents', 'Verified safety incidents', source='Safety incident register required',
        description='Quality corrective actions and observations are not verified safety incidents or near misses.', status='unavailable',
    ))
    section = _department('qhse', 'QHSE', route, metrics, actions, [
        'Quality counts are project-level register snapshots. Repeated corrective actions cannot be reconciled without an item-level register.',
        'Project manager is the recorded project owner, not a verified corrective-action assignee.',
        'Safety rates and environmental measurements are unavailable; no estimates are inferred from quality issues or manhours.',
        'This shared QHSE register has no legal-entity consolidation key.',
    ])
    return _updated(section, source_updates, 'Latest active QHSE project register update')


def build_people_departments(user, context):
    """Return aggregate-only sections using the caller's effective read grants."""
    return [_engineering(user, context), _hr(user, context), _qhse(user, context)]
