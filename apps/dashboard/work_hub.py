"""Personal Work Hub reads: no staff expansion, ledger sync or workflow writes."""
import calendar
from datetime import date, datetime, timedelta
import logging
import math

from django.apps import apps
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .work_hub_activity import project_work_action, work_action_records
from .work_hub_views import VIEW_DEDUPLICATION_SECONDS, workspace_view_summary


logger = logging.getLogger(__name__)
TASK_LIMIT = 100
ACTIVITY_LIMIT = 10
CALENDAR_SOURCE_LIMIT = 50


class SourceUnavailable(Exception):
    pass


def _model(app, name):
    try:
        return apps.get_model(app, name)
    except LookupError as exc:
        raise SourceUnavailable('This source is not enabled.') from exc


def _section(source, **fields):
    return {'status': 'unavailable', 'reason': None, 'source': source, **fields}


def _read(base, builder):
    """A failed optional read cannot abort other sources or become measured zero."""
    try:
        with transaction.atomic():
            return {**base, **builder(), 'status': 'ready', 'reason': None}
    except SourceUnavailable as exc:
        return {**base, 'status': 'unavailable', 'reason': str(exc)}
    except Exception as exc:
        logger.warning('Work Hub source %s unavailable (%s)', base['source'], type(exc).__name__)
        return {**base, 'status': 'error', 'reason': 'This source could not be loaded. Please retry.'}


def _self_code(user):
    """Exact self-service link; never guess from a name or choose another ledger."""
    Profile = _model('rbac', 'UserProfile')
    profile = Profile.objects.filter(user=user, is_deleted=False, status='active').only('employee_id').first()
    code = str(profile.employee_id or '').strip() if profile else ''
    if not code:
        return None
    if Profile.objects.filter(employee_id=code, is_deleted=False).exclude(user=user).exists():
        return None
    return code


def _tasks(user, today):
    base = _section('assigned_project_tasks', route=None, counts={key: None for key in ('open', 'due_today', 'overdue', 'due')},
                    rows=[], total_rows=None, returned_rows=0, truncated=False)

    def build():
        if not module_action_allowed(user, 'project_control', 'read'):
            raise SourceUnavailable('Project Control read access is required for assigned project tasks.')
        from apps.project_control.access import accessible_enterprise_projects
        Task = _model('core', 'ProjectTask')
        rows = Task.objects.filter(assigned_to=user, is_deleted=False,
                                   project__in=accessible_enterprise_projects(user)).exclude(status='completed')
        counts = rows.aggregate(open=Count('id'), due_today=Count('id', filter=Q(due_date=today)),
                                overdue=Count('id', filter=Q(due_date__lt=today)))
        counts['due'] = counts['due_today'] + counts['overdue']
        preview = rows.select_related('project').order_by(F('due_date').asc(nulls_last=True), '-created_at', 'pk')[:TASK_LIMIT]
        return {'route': '/projects', 'counts': counts, 'total_rows': counts['open'],
                'returned_rows': min(counts['open'], TASK_LIMIT), 'truncated': counts['open'] > TASK_LIMIT,
                'rows': [{'id': task.pk, 'title': task.title, 'project_id': task.project_id,
                          'project_code': task.project.code, 'project_name': task.project.name,
                          'status': task.status, 'priority': task.priority,
                          'due_date': task.due_date.isoformat() if task.due_date else None,
                          'route': f'/projects?project={task.project_id}'} for task in preview]}
    return _read(base, build)


def _leave(user, year):
    base = _section('personal_annual_leave_ledger', route='/profile?tab=leave', balance=None,
                    unit='days', year=year, source_updated_at=None, basis='recorded_annual_ledger',
                    description='Recorded annual ledger balance for the selected year, not a selected-month projection. No accrual or ledger synchronization runs on this read.')

    def build():
        code = _self_code(user)
        if not code:
            raise SourceUnavailable('A unique employee code must be linked to your profile before your leave balance can be shown.')
        Ledger = _model('payroll', 'EmployeeLeaveRecord')
        ledger = Ledger.objects.filter(employee_code=code, year=year).first()
        if ledger is None:
            raise SourceUnavailable('No annual leave ledger is recorded for your employee code and selected year.')
        balance = float(ledger.leave_balance) if ledger.leave_balance is not None else None
        if balance is None or not math.isfinite(balance):
            raise SourceUnavailable('The recorded annual leave balance is incomplete.')
        return {'balance': balance, 'source_updated_at': ledger.imported_at.isoformat() if ledger.imported_at else None,
                'source_timestamp_kind': 'ledger_import_or_save_timestamp'}
    return _read(base, build)


def _hours(user, first, last, today):
    through = min(last, today)
    base = _section('personal_daily_work_logs', route='/profile?tab=daily_tracker', value=None, unit='hours',
                    entry_count=None, approved_hours=None, basis='logged_work_hours', through_date=through.isoformat(),
                    description='Hours entered in your Daily Tracker in this month through the stated date, across all approval states. Approved hours are separate; these are not attendance, billable hours or a capacity target.')

    def build():
        if first > today:
            raise SourceUnavailable('The selected month has not started.')
        Log = _model('payroll', 'DailyWorkLog')
        logs = Log.objects.filter(user=user, log_date__gte=first, log_date__lte=through)
        result = logs.aggregate(total=Sum('hours_spent'), approved=Sum('hours_spent', filter=Q(approval_status='approved')),
                                count=Count('id'), invalid=Count('id', filter=Q(hours_spent__lt=0)))
        value, approved = float(result['total'] or 0), float(result['approved'] or 0)
        if result['invalid'] or not math.isfinite(value) or not math.isfinite(approved):
            raise SourceUnavailable('Recorded work hours include invalid entries; the total is withheld.')
        return {'value': value, 'approved_hours': approved, 'entry_count': result['count']}
    return _read(base, build)


def _calendar(user, first, last):
    def own_leave():
        Leave = _model('payroll', 'LeaveRequest')
        identity = Q(employee=user) | Q(employee__isnull=True, canonical_employee__user=user)
        code = _self_code(user)
        if code:
            identity |= Q(employee__isnull=True, canonical_employee__isnull=True, employee_code=code)
        rows = Leave.objects.filter(identity, status='APPROVED', start_date__lte=last,
                                    end_date__gte=first).filter(end_date__gte=F('start_date')).order_by('start_date', 'pk')
        count = rows.count()
        return {'total_rows': count, 'returned_rows': min(count, CALENDAR_SOURCE_LIMIT), 'truncated': count > CALENDAR_SOURCE_LIMIT,
                'rows': [{'id': f'leave:{row.pk}', 'type': 'leave', 'title': 'Your approved leave',
                          'start_date': row.start_date.isoformat(), 'end_date': row.end_date.isoformat(),
                          'region': None, 'status': 'approved', 'route': '/profile?tab=leave'}
                         for row in rows[:CALENDAR_SOURCE_LIMIT]]}

    def holidays():
        Holiday = _model('payroll', 'PublicHoliday')
        rows = Holiday.objects.filter(is_active=True, date__gte=first, date__lte=last).order_by('date', 'region', 'pk')
        count = rows.count()
        return {'total_rows': count, 'returned_rows': min(count, CALENDAR_SOURCE_LIMIT), 'truncated': count > CALENDAR_SOURCE_LIMIT,
                'rows': [{'id': f'holiday:{row.pk}', 'type': 'public_holiday', 'title': row.name,
                          'start_date': row.date.isoformat(), 'end_date': row.date.isoformat(),
                          'region': row.region, 'status': 'published', 'route': '/profile?tab=schedule'}
                         for row in rows[:CALENDAR_SOURCE_LIMIT]]}

    sources = [_read(_section(identifier, id=identifier, rows=[], total_rows=None, returned_rows=0, truncated=False), builder)
               for identifier, builder in [('own_leave', own_leave), ('public_holidays', holidays)]]
    ready = [source for source in sources if source['status'] == 'ready']
    rows = sorted([row for source in ready for row in source['rows']], key=lambda row: (row['start_date'], row['id']))
    complete = len(ready) == len(sources)
    status = 'ready' if ready else 'error' if any(source['status'] == 'error' for source in sources) else 'unavailable'
    return {'status': status, 'reason': None if complete else 'Some calendar sources are unavailable; their events are not included.',
            'sources': [{key: value for key, value in source.items() if key != 'rows'} for source in sources],
            'rows': rows, 'total_rows': sum(source['total_rows'] for source in ready) if complete else None,
            'returned_rows': len(rows), 'truncated': any(source['truncated'] for source in ready), 'partial': not complete,
            'description': 'Your approved leave and active entries in the published holiday calendar. Holiday regions are shown; applicability to your employment is not inferred.'}


def _activity(user, now):
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    base = _section('personal_system_activity', total_count=None, series=[], rows=[], returned_rows=0, truncated=False,
                    period_start=start.isoformat(), period_end=now.isoformat(),
                    basis='recorded_user_actions', coverage='work_actions_and_workspace_views',
                    work_action_count=None, view_count=None, collapsed_view_count=None,
                    deduplication_window_seconds=VIEW_DEDUPLICATION_SECONDS,
                    description='Your recorded work actions and workspace visits over seven days. Repeated visits within 30 seconds are grouped. Background requests and session activity are excluded.')

    def build():
        Activity = _model('activity', 'SystemActivity')
        Event = _model('rbac', 'ActivityEvent')
        records = work_action_records(Activity, user, start, now)
        daily = {row['date'].isoformat(): row['count'] for row in records.annotate(
            date=TruncDate('timestamp', tzinfo=timezone.get_current_timezone())).values('date').annotate(count=Count('id'))}
        work_count = records.count()
        views = workspace_view_summary(Event, user, start, now, ACTIVITY_LIMIT)
        for day, value in views['daily'].items():
            daily[day] = daily.get(day, 0) + value
        count = work_count + views['count']
        preview = [project_work_action(row) for row in records.order_by('-timestamp', '-pk')[:ACTIVITY_LIMIT]] + views['rows']
        preview.sort(key=lambda row: (datetime.fromisoformat(row['timestamp']), str(row['id'])), reverse=True)
        return {'total_count': count, 'returned_rows': min(count, ACTIVITY_LIMIT), 'truncated': count > ACTIVITY_LIMIT,
                'work_action_count': work_count, 'view_count': views['count'], 'collapsed_view_count': views['collapsed_count'],
                'series': [{'date': (start.date() + timedelta(days=index)).isoformat(),
                            'count': daily.get((start.date() + timedelta(days=index)).isoformat(), 0)} for index in range(7)],
                'rows': preview[:ACTIVITY_LIMIT]}
    return _read(base, build)


class WorkHubView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        if not request.user.is_active:
            return Response({'detail': 'An active account is required.'}, status=403)
        now = timezone.now()
        today = timezone.localdate(now)
        try:
            year = int(request.query_params.get('year', today.year))
            month = int(request.query_params.get('month', today.month))
            if not 2000 <= year <= 2100 or not 1 <= month <= 12:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError({'period': 'Use a year from 2000 to 2100 and a month from 1 to 12.'})
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        return Response({'schema_version': '1.0', 'as_of': now.isoformat(),
                         'period': {'year': year, 'month': month, 'start': first.isoformat(), 'end': last.isoformat(),
                                    'timezone': timezone.get_current_timezone_name()},
                         'tasks': _tasks(request.user, today), 'leave': _leave(request.user, year),
                         'hours': _hours(request.user, first, last, today), 'calendar': _calendar(request.user, first, last),
                         'activity': _activity(request.user, now)})
