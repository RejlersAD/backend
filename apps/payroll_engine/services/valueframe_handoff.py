"""ValueFrame → Payroll handoff.

After HR compares ValueFrame bookings with real attendance
(apps.timesheet.valueframe), flagged employees can be sent to Payroll
Management as PENDING "absent" deductions for a chosen payroll month.

Nothing is applied automatically: payroll reviews, edits or cancels each
adjustment in Payroll Management → Adjustments, and only pending adjustments
are materialised when a run is generated.

Amounts reuse the payroll engine's own rules, so a ValueFrame gap is priced
exactly like an unpaid day:
  * hours per day  -> calculation_config.get_employee_hours_per_day (8 or 9)
  * deduction      -> calculation_config.calculate_unpaid_leave_deduction
                      (monthly gross ÷ PAYROLL_STANDARD_WORKDAYS per day)
Thresholds, cap, rounding, component and label live in payroll_engine.config.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .. import catalog
from ..calculation_config import calculate_unpaid_leave_deduction, get_employee_hours_per_day
from ..config import (
    CURRENCY, VALUEFRAME_GAP_COMPONENT_CODE, VALUEFRAME_GAP_LABEL, VALUEFRAME_GAP_MAX_DAYS,
    VALUEFRAME_GAP_MIN_DAYS, VALUEFRAME_GAP_STEP_DAYS,
)
from ..models import PayrollAdjustment, PayrollEmployee, PayrollRun
from .adjustment_period import require_current_or_future

ZERO = Decimal('0')
CENT = Decimal('0.01')


def marker(vf_import):
    """Description prefix that ties an adjustment to one ValueFrame upload (keeps sends idempotent)."""
    return f'[ValueFrame #{vf_import.pk}]'


def _round_gap(days: Decimal) -> Decimal:
    step = VALUEFRAME_GAP_STEP_DAYS if VALUEFRAME_GAP_STEP_DAYS > 0 else Decimal('1')
    return (days / step).to_integral_value(rounding=ROUND_DOWN) * step


def build_proposal(vf_import, codes=None):
    """One line per flagged employee: payroll gap days, daily rate and proposed deduction."""
    from apps.timesheet.valueframe import compare_import

    comparison = compare_import(vf_import)
    flagged = [r for r in comparison['rows'] if r['status'] == 'flagged' and (codes is None or r['employee_code'] in codes)]
    entries = {e.employee_code: e for e in vf_import.entries.filter(employee_code__in=[r['employee_code'] for r in flagged])}
    payroll = {p.employee_no: p for p in PayrollEmployee.objects.filter(employee_no__in=list(entries), is_active=True)}
    sent = {a.employee.employee_no: a for a in PayrollAdjustment.objects.select_related('employee')
            .filter(description__startswith=marker(vf_import), status=catalog.AdjustmentStatus.PENDING)}

    lines = []
    for row in flagged:
        code = row['employee_code']
        employee = payroll.get(code)
        line = {'employee_code': code, 'employee_name': row['employee_name'], 'present_days': row['present_days'],
                'pattern': row.get('pattern', ''),
                'booked_hours': row['booked_hours'], 'gap_days': '0', 'daily_rate': '0', 'amount': '0',
                'hours_per_day': None, 'payroll_employee_id': employee.pk if employee else None}
        if employee is None:
            lines.append({**line, 'status': 'no_payroll_record'})
            continue
        hours_per_day = get_employee_hours_per_day(employee.basic, employee.housing, employee.transport, employee.home_leave)
        booked_days = Decimal(str(entries[code].normal_hours)) / hours_per_day if hours_per_day else ZERO
        gap = _round_gap(max(ZERO, booked_days - Decimal(row['present_days'])))
        gap = min(gap, VALUEFRAME_GAP_MAX_DAYS)
        gross = (employee.basic or ZERO) + (employee.housing or ZERO) + (employee.transport or ZERO) + (employee.home_leave or ZERO)
        amount = calculate_unpaid_leave_deduction(gap, gross).quantize(CENT, rounding=ROUND_HALF_UP)
        daily = calculate_unpaid_leave_deduction(Decimal('1'), gross).quantize(CENT, rounding=ROUND_HALF_UP)
        line.update({'hours_per_day': str(hours_per_day), 'gap_days': str(gap), 'daily_rate': str(daily),
                     'amount': str(amount), 'payroll_employee_id': employee.pk})
        if code in sent:
            line.update({'status': 'already_sent', 'adjustment_id': sent[code].pk,
                         'sent_amount': str(sent[code].amount), 'sent_month': f'{sent[code].target_year}-{sent[code].target_month:02d}'})
        elif gross <= 0:
            line['status'] = 'no_salary'
        elif gap < VALUEFRAME_GAP_MIN_DAYS:
            line['status'] = 'below_threshold'
        else:
            line['status'] = 'ready'
        lines.append(line)

    ready = [l for l in lines if l['status'] == 'ready']
    return {
        'currency': CURRENCY,
        'component_code': VALUEFRAME_GAP_COMPONENT_CODE, 'label': VALUEFRAME_GAP_LABEL,
        'rules': {'min_days': str(VALUEFRAME_GAP_MIN_DAYS), 'max_days': str(VALUEFRAME_GAP_MAX_DAYS),
                  'step_days': str(VALUEFRAME_GAP_STEP_DAYS),
                  'formula': 'gap days × (monthly gross ÷ standard workdays), same as unpaid leave'},
        'lines': lines,
        'summary': {'flagged': len(lines), 'ready': len(ready),
                    'already_sent': sum(1 for l in lines if l['status'] == 'already_sent'),
                    'no_payroll_record': sum(1 for l in lines if l['status'] == 'no_payroll_record'),
                    'total_days': str(sum((Decimal(l['gap_days']) for l in ready), ZERO)),
                    'total_amount': str(sum((Decimal(l['amount']) for l in ready), ZERO))},
    }


@transaction.atomic
def send_to_payroll(vf_import, *, user, target_year, target_month, codes=None):
    """Create PENDING absent deductions for ready lines. Re-sending refreshes this upload's pending ones."""
    require_current_or_future(target_year, target_month)
    target_year, target_month = int(target_year), int(target_month)
    run = PayrollRun.objects.filter(year=target_year, month=target_month).first()
    if run is not None and not run.is_editable:
        # Pending adjustments are only materialised by (re)generating a Draft run.
        raise ValidationError({'target_month': f'The {target_year}-{target_month:02d} payroll run is already '
                                               f'"{run.status}". Choose the next payroll month.'})
    proposal = build_proposal(vf_import, codes=codes)
    period = f'{vf_import.period_start:%d %b} – {vf_import.period_end:%d %b %Y}'
    created = updated = 0
    for line in proposal['lines']:
        if line['status'] not in ('ready', 'already_sent') or Decimal(line['gap_days']) < VALUEFRAME_GAP_MIN_DAYS:
            continue
        description = (f'{marker(vf_import)} ValueFrame {period}: booked {line["booked_hours"]} h, present '
                       f'{line["present_days"]} d → {line["gap_days"]} d not supported by attendance '
                       f'× {proposal["currency"]} {line["daily_rate"]}/day.')
        values = {'target_year': target_year, 'target_month': target_month, 'amount': Decimal(line['amount']),
                  'description': description, 'label': VALUEFRAME_GAP_LABEL}
        if line['status'] == 'already_sent':
            PayrollAdjustment.objects.filter(pk=line['adjustment_id'], status=catalog.AdjustmentStatus.PENDING).update(**values)
            updated += 1
        else:
            PayrollAdjustment.objects.create(employee_id=line['payroll_employee_id'], kind=catalog.LineItemKind.DEDUCTION,
                                             component_code=VALUEFRAME_GAP_COMPONENT_CODE, created_by=user, **values)
            created += 1
    return {'created': created, 'updated': updated, 'target_year': target_year, 'target_month': target_month,
            'skipped': sum(1 for l in proposal['lines'] if l['status'] not in ('ready', 'already_sent')),
            # A Draft run that already exists only picks pending adjustments up when regenerated.
            'draft_run_id': run.pk if run is not None else None}
