"""Pure functions for payslip arithmetic. No DB access.
Use these from anywhere — services, views, tests.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Iterable

from ..config import QUANTUM, ROUNDING, STANDARD_WORKDAYS_PER_MONTH
from ..catalog import LineItemKind

ZERO = Decimal('0.00')


def to_decimal(value) -> Decimal:
    """Coerce anything Excel/JSON might throw at us into a Decimal."""
    if value is None or value == '':
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def quantize(value) -> Decimal:
    """Round a value to the configured decimal precision."""
    return to_decimal(value).quantize(QUANTUM, rounding=ROUNDING)


def sum_amounts(items: Iterable) -> Decimal:
    """Sum any iterable of amounts/Decimals/strings/None."""
    total = ZERO
    for it in items:
        total += to_decimal(it)
    return total


def compute_fixed_earnings(basic, housing, transport, home_leave) -> Decimal:
    return quantize(
        to_decimal(basic) + to_decimal(housing)
        + to_decimal(transport) + to_decimal(home_leave)
    )


def compute_other_earnings(line_items) -> Decimal:
    """`line_items` may be a queryset OR a list of dicts with kind/amount."""
    total = ZERO
    for item in line_items:
        kind = getattr(item, 'kind', None) or (item.get('kind') if isinstance(item, dict) else None)
        amount = getattr(item, 'amount', None)
        if amount is None and isinstance(item, dict):
            amount = item.get('amount')
        if kind == LineItemKind.EARNING:
            total += to_decimal(amount)
    return quantize(total)


def compute_total_deductions(line_items) -> Decimal:
    total = ZERO
    for item in line_items:
        kind = getattr(item, 'kind', None) or (item.get('kind') if isinstance(item, dict) else None)
        amount = getattr(item, 'amount', None)
        if amount is None and isinstance(item, dict):
            amount = item.get('amount')
        if kind == LineItemKind.DEDUCTION:
            total += to_decimal(amount)
    return quantize(total)


def compute_net(gross: Decimal, deductions: Decimal) -> Decimal:
    return quantize(to_decimal(gross) - to_decimal(deductions))


def recompute_payslip_totals(payslip) -> None:
    """Mutate a Payslip's aggregate fields in place. Caller saves."""
    items = list(payslip.line_items.all()) if payslip.pk else []
    fixed = compute_fixed_earnings(
        payslip.basic, payslip.housing, payslip.transport, payslip.home_leave,
    )
    other = compute_other_earnings(items)
    deductions = compute_total_deductions(items)
    payslip.other_earnings = other
    payslip.gross_earnings = quantize(fixed + other)
    payslip.total_deductions = deductions
    payslip.net_payable = compute_net(payslip.gross_earnings, deductions)


def recompute_run_totals(run) -> None:
    """Mutate a PayrollRun's aggregate fields in place. Caller saves."""
    agg = {'gross': ZERO, 'deductions': ZERO, 'net': ZERO, 'count': 0}
    for slip in run.payslips.all():
        agg['gross'] += to_decimal(slip.gross_earnings)
        agg['deductions'] += to_decimal(slip.total_deductions)
        agg['net'] += to_decimal(slip.net_payable)
        agg['count'] += 1
    run.total_gross = quantize(agg['gross'])
    run.total_deductions = quantize(agg['deductions'])
    run.total_net = quantize(agg['net'])
    run.employee_count = agg['count']


def prorate_for_days(amount, days_present: int, std_days: int = STANDARD_WORKDAYS_PER_MONTH) -> Decimal:
    """Pro-rate a fixed amount for partial-month attendance."""
    if std_days <= 0:
        return quantize(amount)
    factor = Decimal(days_present) / Decimal(std_days)
    return quantize(to_decimal(amount) * factor)
