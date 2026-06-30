"""Soft-coded runtime configuration for the Payroll Engine.

Every threshold, default, and tunable knob lives here so it can be
overridden via environment variables without code changes.
"""
import os
from decimal import Decimal, ROUND_HALF_UP


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name, default)
    try:
        return Decimal(str(raw))
    except Exception:
        return Decimal(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


# ── Money ───────────────────────────────────────────────────────────
CURRENCY = os.environ.get('PAYROLL_CURRENCY', 'AED')
CURRENCY_SYMBOL = os.environ.get('PAYROLL_CURRENCY_SYMBOL', 'AED')
DECIMAL_PLACES = _env_int('PAYROLL_DECIMAL_PLACES', 2)
ROUNDING = ROUND_HALF_UP
QUANTUM = Decimal(10) ** -DECIMAL_PLACES  # e.g. Decimal('0.01')

# ── Calendar ────────────────────────────────────────────────────────
STANDARD_WORKDAYS_PER_MONTH = _env_int('PAYROLL_STANDARD_WORKDAYS', 22)
STANDARD_HOURS_PER_DAY = _env_int('PAYROLL_STANDARD_HOURS_PER_DAY', 8)

# ── Workflow ────────────────────────────────────────────────────────
# Allow re-opening an approved run back to draft (HR can revoke).
ALLOW_REVERT_TO_DRAFT = _env_bool('PAYROLL_ALLOW_REVERT', True)

# ── Generation ──────────────────────────────────────────────────────
# When generating a new run, copy free-form line items from the prior run.
CARRY_FORWARD_LINE_ITEMS = _env_bool('PAYROLL_CARRY_FORWARD_LINE_ITEMS', False)

# ── Excel ───────────────────────────────────────────────────────────
EXCEL_MAX_UPLOAD_MB = _env_int('PAYROLL_EXCEL_MAX_MB', 25)
EXCEL_DATE_FORMAT = os.environ.get('PAYROLL_EXCEL_DATE_FORMAT', '%Y-%m-%d')

# ── Notifications ───────────────────────────────────────────────────
NOTIFY_ON_TRANSITION = _env_bool('PAYROLL_NOTIFY_TRANSITIONS', True)

# ── RBAC: who may edit / delete employee master records ────────────
# Comma-separated RBAC role codes (matched case-insensitively against
# user.roles[].code). Django superusers and is_staff users are always
# allowed regardless of this list. Override via env if you want HR
# managers to edit employees in addition to super-admins.
EMPLOYEE_WRITE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_EMPLOYEE_WRITE_ROLES',
        'superadmin,super_admin,admin',
    ).split(',')
    if code.strip()
]

# ── RBAC: who may create / edit / cancel payroll adjustments ───────
# Defaults to a slightly broader list than employee writes, since HR
# Managers typically queue adjustments long before super-admins finalise
# the run. Override via env without code changes.
ADJUSTMENT_WRITE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_ADJUSTMENT_WRITE_ROLES',
        'superadmin,super_admin,admin,hr_manager,senior_hr,hr',
    ).split(',')
    if code.strip()
]

# ── RBAC: who may FORCE-edit / FORCE-delete approved or released runs ──
# This is an emergency override (e.g. correcting an erroneous approval).
# Defaults to super-admins only. Django superusers are always allowed.
# Every force operation is recorded in PayrollWorkflowLog for audit.
RUN_FORCE_OVERRIDE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_RUN_FORCE_OVERRIDE_ROLES',
        'superadmin,super_admin',
    ).split(',')
    if code.strip()
]
