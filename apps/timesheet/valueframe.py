"""ValueFrame reconciliation: booked time vs real attendance.

ValueFrame's monthly "Wage Type Report" says how many hours each employee
booked as worked ("Normal") and as leave. RADAI knows which days each
employee was actually present:

* office badge-in (biometric)          DailyAttendanceSummary, source=biometric
* HR manual corrections                DailyAttendanceSummary, source=manual
* approved Work From Home              payroll.LeaveRequest, leave type WFH
* site visit check-ins (GPS)           site_visits.SiteVisitCheckIn

Booked office days = Normal hours / HOURS_PER_WORKDAY. An employee is flagged
when booked days exceed present days by at least VALUEFRAME_GAP_FLAG_DAYS.
The report is monthly, so the comparison is per employee per period; the
employee's daily attendance shows which dates are missing.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from .identity import norm_code

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
GAP_FLAG_DAYS = float(os.environ.get('VALUEFRAME_GAP_FLAG_DAYS', '1'))

# Report header -> ValueFrameEntry field (matched case-insensitively, punctuation ignored).
COLUMN_ALIASES = {
    'employee_code': ('employee number', 'employee no', 'employee id', 'emp no'),
    'employee_name': ('employee name', 'name'),
    'normal_hours': ('normal',),
    'flex_hours': ('working time flexibility free', 'working time flexibility'),
    'sick_hours': ('sick leave medical certificate', 'sick leave'),
    'annual_hours': ('annual vacation', 'annual leave'),
    'paternity_hours': ('paternity leave paid', 'paternity leave'),
    'bank_holiday_hours': ('bank holiday', 'public holiday'),
    'other_paid_hours': ('other paid leave',),
    'other_unpaid_hours': ('other unpaid leave', 'unpaid leave'),
    'billability': ('billability', 'billability %'),
    'invoicing_hours': ('invoicing hours',),
    'total_hours': ('total hours',),
}
_PERIOD = re.compile(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})')


def _key(value):
    return re.sub(r'[^a-z0-9 ]+', ' ', str(value or '').lower()).strip()


def _num(value):
    if value in (None, ''):
        return 0.0
    try:
        return float(str(value).replace(',', '').strip())
    except ValueError:
        return 0.0


def _code(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return norm_code(str(value or '').strip())


def workday_hours():
    try:
        from apps.payroll_engine.config import HOURS_PER_WORKDAY
        return float(HOURS_PER_WORKDAY) or 9.0
    except Exception:
        return 9.0


def parse_wage_type_report(uploaded_file):
    """Read a ValueFrame Wage Type Report (.xlsx).

    Returns {'period_start', 'period_end', 'company', 'rows': [...], 'warnings': [...], 'sha256'}.
    Raises ValueError with a message HR can act on.
    """
    import openpyxl

    name = getattr(uploaded_file, 'name', 'valueframe.xlsx')
    if not str(name).lower().endswith(('.xlsx', '.xlsm')):
        raise ValueError('Upload the ValueFrame Wage Type Report as an Excel .xlsx file.')
    if getattr(uploaded_file, 'size', 0) > MAX_UPLOAD_BYTES:
        raise ValueError('The file is larger than 5 MB. Export one month at a time.')
    content = uploaded_file.read()
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise ValueError('The file could not be opened as an Excel workbook.')
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True, max_row=MAX_ROWS + 40))

    period_start = period_end = None
    company = ''
    header_index = None
    for index, row in enumerate(rows):
        cells = [c for c in row if c not in (None, '')]
        if not cells:
            continue
        text = ' '.join(str(c) for c in cells)
        if period_start is None:
            match = _PERIOD.search(text)
            if match:
                d1, m1, y1, d2, m2, y2 = map(int, match.groups())
                try:
                    period_start, period_end = date(y1, m1, d1), date(y2, m2, d2)
                except ValueError:
                    pass
        if _key(row[0]) == 'company' and len(row) > 1 and row[1]:
            company = str(row[1]).strip()
        if any(_key(c) in COLUMN_ALIASES['employee_code'] for c in row):
            header_index = index
            break
    if header_index is None:
        raise ValueError('No "Employee Number" header was found. Export the Wage Type Report from ValueFrame.')
    if period_start is None or period_end is None or period_end < period_start:
        raise ValueError('The report period ("From d.m.yyyy - d.m.yyyy") was not found in the title.')
    if (period_end - period_start).days > 62:
        raise ValueError('Upload one month per file (the report covers more than two months).')

    header = rows[header_index]
    columns = {}
    for position, title in enumerate(header):
        key = _key(title)
        for field, aliases in COLUMN_ALIASES.items():
            if field not in columns and key in aliases:
                columns[field] = position
    if 'normal_hours' not in columns:
        raise ValueError('The "Normal" hours column is missing from the report.')

    parsed, warnings, seen = [], [], set()
    for row in rows[header_index + 1:]:
        if not any(c not in (None, '') for c in row):
            continue
        raw_code = row[columns['employee_code']] if columns['employee_code'] < len(row) else None
        if _key(raw_code) == 'total':
            continue
        code = _code(raw_code)
        if not code:
            continue
        if code in seen:
            warnings.append(f'Employee {code} appears more than once; the first row was used.')
            continue
        seen.add(code)
        values = {field: row[pos] if pos < len(row) else None for field, pos in columns.items()}
        entry = {field: _num(values.get(field)) for field in COLUMN_ALIASES if field not in ('employee_code', 'employee_name', 'billability')}
        entry['employee_code'] = code
        entry['employee_name'] = str(values.get('employee_name') or '').strip()[:255]
        entry['billability'] = _num(values['billability']) if values.get('billability') not in (None, '') else None
        entry['raw'] = {str(header[i] or f'col{i}'): (row[i] if i < len(row) else None) for i in range(len(header)) if header[i]}
        parsed.append(entry)
    if not parsed:
        raise ValueError('The report has no employee rows.')
    return {'period_start': period_start, 'period_end': period_end, 'company': company, 'rows': parsed,
            'warnings': warnings, 'sha256': hashlib.sha256(content).hexdigest()}


def _days(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


MATRIX_SOURCE_LABEL = 'Monthly matrix (real-time attendance)'
FALLBACK_SOURCE_LABEL = 'Stored daily attendance (Monthly matrix source unavailable)'
UNAVAILABLE_SOURCE_LABEL = 'Unavailable: the Monthly matrix source is not reachable and no stored attendance exists for this period'


def _months(start, end):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _matrix_office_days(codes, start, end):
    """Office presence exactly as the Monthly matrix shows it.

    Uses the same attendance service as GET /timesheet/monthly/ (SQL Server
    locally, the Postgres mirror in production). A day counts as present when
    the matrix records a badge-in (first_in) or any hours for it — a badge-in
    without a badge-out still means the employee was there.
    Returns {code: {date: hours}} with the same per-day `hours` the matrix
    displays, or None when the service is unavailable.
    """
    from . import config as ts_config
    from . import get_service
    if not ts_config.is_configured():
        return None
    wanted = set(codes)
    found = defaultdict(dict)
    matrix_rows = 0
    try:
        service = get_service()
        for year, month in _months(start, end):
            report = service.monthly_report(year=year, month=month)
            # Source down: an empty matrix must not read as 'nobody came to the office'.
            # cache_miss marks the timesheet cache's fallback reply after a failed query
            # (circuit_open is only set once the breaker has tripped).
            if (report.get('circuit_open') or report.get('cache_miss')
                    or report.get('configured') is False or report.get('error')):
                return None
            matrix_rows += len(report.get('rows') or [])
            for row in report.get('rows') or []:
                code = norm_code(row.get('employee_code'))
                if code not in wanted:
                    continue
                for day in row.get('days_detail') or []:
                    try:
                        when = date.fromisoformat(str(day.get('date'))[:10])
                    except ValueError:
                        continue
                    hours = float(day.get('hours') or 0)
                    if start <= when <= end and (day.get('first_in') or hours > 0):
                        found[code][when] = max(found[code].get(when, 0.0), hours)
    except Exception:
        return None
    # A matrix with nobody in it for the whole period has no attendance to compare against.
    return found if matrix_rows else None


def _presence(codes, start, end):
    """({code: {'office', 'manual', 'wfh', 'site': set(dates)}}, source label, available) within [start, end].

    `available` is False when neither the Monthly matrix nor stored badge rows
    cover the period: the comparison would then flag everyone, so it must not be acted on.

    Office presence comes from the Monthly matrix source; HR manual corrections,
    approved Work From Home and site visit check-ins are added on top.
    """
    from .models import DailyAttendanceSummary
    found = defaultdict(lambda: {'office': set(), 'manual': set(), 'wfh': set(), 'site': set(),
                                 'office_hours': {}, 'manual_hours': {}})
    matrix = _matrix_office_days(codes, start, end)
    source_label = MATRIX_SOURCE_LABEL if matrix is not None else FALLBACK_SOURCE_LABEL
    for code, days in (matrix or {}).items():
        found[code]['office'] |= set(days)
        found[code]['office_hours'].update(days)
    rows = (DailyAttendanceSummary.objects
            .filter(employee_code__in=codes, date__gte=start, date__lte=end)
            .values_list('employee_code', 'date', 'source', 'effective_hours', 'first_in'))
    for code, day, source, hours, first_in in rows:
        if source == DailyAttendanceSummary.SOURCE_MANUAL:
            if (hours or 0) > 0:
                found[code]['manual'].add(day)
                found[code]['manual_hours'][day] = float(hours)
        elif matrix is None and ((hours or 0) > 0 or first_in is not None):
            found[code]['office'].add(day)  # fallback only: matrix source unreachable
            found[code]['office_hours'][day] = float(hours or 0)
    available = matrix is not None or any(v['office'] for v in found.values())
    if not available:
        source_label = UNAVAILABLE_SOURCE_LABEL

    try:  # approved Work From Home (working days only)
        from apps.payroll.models import LeaveRequest
        for code, first, last in (LeaveRequest.objects
                                  .filter(employee_code__in=codes, status='APPROVED', leave_type__code='WFH',
                                          start_date__lte=end, end_date__gte=start)
                                  .values_list('employee_code', 'start_date', 'end_date')):
            for day in _days(max(first, start), min(last, end)):
                if day.weekday() < 5:
                    found[norm_code(code)]['wfh'].add(day)
    except Exception:
        pass

    try:  # site visit GPS check-ins
        from apps.site_visits.models import SiteVisitCheckIn
        for code, moment in (SiteVisitCheckIn.objects
                             .filter(employee_code__in=codes, check_in_time__date__gte=start, check_in_time__date__lte=end)
                             .values_list('employee_code', 'check_in_time')):
            found[norm_code(code)]['site'].add(moment.date())
    except Exception:
        pass
    return found, source_label, available


def compare_import(vf_import):
    """Per-employee comparison rows and a summary for one ValueFrame import."""
    from .models import BiometricUserMaster
    hours_per_day = workday_hours()
    entries = list(vf_import.entries.all())
    codes = [e.employee_code for e in entries]
    presence, source_label, available = _presence(codes, vf_import.period_start, vf_import.period_end)
    enrolled = set(BiometricUserMaster.objects.filter(employee_code__in=codes).values_list('employee_code', flat=True))

    rows = []
    for entry in entries:
        seen = presence.get(entry.employee_code) or {'office': set(), 'manual': set(), 'wfh': set(), 'site': set(),
                                                      'office_hours': {}, 'manual_hours': {}}
        present = seen['office'] | seen['manual'] | seen['wfh'] | seen['site']
        # Actual hours: what the Monthly matrix recorded, plus HR-corrected hours on days with no badge record.
        office_hours = round(sum(seen['office_hours'].values()), 2)
        manual_hours = round(sum(h for d, h in seen['manual_hours'].items() if d not in seen['office']), 2)
        actual_hours = round(office_hours + manual_hours, 2)
        booked_days = round(entry.normal_hours / hours_per_day, 2)
        gap_days = round(booked_days - len(present), 2)
        if booked_days <= 0:
            status = 'no_booking'
        elif not present and entry.employee_code not in enrolled:
            status = 'not_enrolled'
        elif gap_days >= GAP_FLAG_DAYS:
            status = 'flagged'
        else:
            status = 'ok'
        rows.append({
            'employee_code': entry.employee_code, 'employee_name': entry.employee_name,
            'booked_hours': entry.normal_hours, 'booked_days': booked_days,
            'leave_days': round(entry.leave_hours / hours_per_day, 2),
            'flex_hours': entry.flex_hours, 'total_hours': entry.total_hours,
            'present_days': len(present), 'office_days': len(seen['office']), 'manual_days': len(seen['manual'] - seen['office']),
            'actual_hours': actual_hours, 'office_hours': office_hours, 'manual_hours': manual_hours,
            'hours_difference': round(actual_hours - entry.normal_hours, 2),
            'wfh_days': len(seen['wfh'] - seen['office']), 'site_days': len(seen['site'] - seen['office']),
            'gap_days': gap_days, 'gap_hours': round(gap_days * hours_per_day, 1) if gap_days > 0 else 0,
            'status': status, 'enrolled': entry.employee_code in enrolled,
            # flagged with zero presence all period (often client-site / deputation) vs a partial gap
            'pattern': ('whole_period' if not present else 'partial') if status == 'flagged' else '',
        })
    order = {'flagged': 0, 'not_enrolled': 1, 'ok': 2, 'no_booking': 3}
    rows.sort(key=lambda r: (order[r['status']], -r['gap_days'], r['employee_name']))
    flagged = [r for r in rows if r['status'] == 'flagged']
    summary = {
        'employees': len(rows),
        'flagged': len(flagged),
        'flagged_partial': sum(1 for r in flagged if r['pattern'] == 'partial'),
        'flagged_whole_period': sum(1 for r in flagged if r['pattern'] == 'whole_period'),
        'not_enrolled': sum(1 for r in rows if r['status'] == 'not_enrolled'),
        'ok': sum(1 for r in rows if r['status'] == 'ok'),
        'booked_days': round(sum(r['booked_days'] for r in rows), 1),
        'present_days': sum(r['present_days'] for r in rows),
        'booked_hours': round(sum(r['booked_hours'] for r in rows), 1),
        'actual_hours': round(sum(r['actual_hours'] for r in rows), 1),
        'gap_days': round(sum(r['gap_days'] for r in flagged), 1),
        'hours_per_day': hours_per_day, 'flag_threshold_days': GAP_FLAG_DAYS,
        'attendance_source': source_label,
        'attendance_available': available,
    }
    return {'summary': summary, 'rows': rows}
