"""Payroll Engine catalog — soft-coded dictionaries for components,
statuses, payment modes, and Excel column mappings.

Treat this file as the single source of truth. Views, services, and
the frontend ``payrollEngine.config.js`` mirror these definitions.
Never hard-code a label or code outside this module.
"""
from __future__ import annotations
from typing import Dict, List

# ── Workflow ────────────────────────────────────────────────────────
class Status:
    """Run + slip lifecycle. Backed by the WORKFLOW_STATUSES below."""
    DRAFT = 'draft'
    HR_APPROVED = 'hr_approved'
    FINANCE_APPROVED = 'finance_approved'
    RELEASED = 'released'


WORKFLOW_STATUSES: List[Dict] = [
    {'code': Status.DRAFT,           'label': 'Draft',            'tone': 'gray',   'order': 0},
    {'code': Status.HR_APPROVED,     'label': 'HR Approved',      'tone': 'blue',   'order': 1},
    {'code': Status.FINANCE_APPROVED,'label': 'Finance Approved', 'tone': 'amber',  'order': 2},
    {'code': Status.RELEASED,        'label': 'Released',         'tone': 'green',  'order': 3},
]

# from_status → list of allowed next states
WORKFLOW_TRANSITIONS: Dict[str, List[str]] = {
    Status.DRAFT:            [Status.HR_APPROVED],
    Status.HR_APPROVED:      [Status.FINANCE_APPROVED, Status.DRAFT],
    Status.FINANCE_APPROVED: [Status.RELEASED, Status.HR_APPROVED],
    Status.RELEASED:         [],
}

# Role gating per transition (intersect with RBAC; empty = any authenticated)
WORKFLOW_ROLES: Dict[str, List[str]] = {
    Status.HR_APPROVED:      ['HR Manager', 'HR', 'Admin'],
    Status.FINANCE_APPROVED: ['Finance Manager', 'Finance', 'Admin'],
    Status.RELEASED:         ['Finance Manager', 'Finance', 'Admin'],
    Status.DRAFT:            ['HR Manager', 'HR', 'Admin', 'Finance', 'Finance Manager'],
}


# ── Payment modes ───────────────────────────────────────────────────
PAYMENT_MODES: List[Dict] = [
    {'code': 'WPS',            'label': 'WPS'},
    {'code': 'WPS-RSI',        'label': 'WPS – RSI'},
    {'code': 'BANK_TRANSFER',  'label': 'Bank Transfer'},
    {'code': 'BANK_TRANSFER_RSI', 'label': 'Bank Transfer – RSI'},
    {'code': 'CHEQUE',         'label': 'Cheque'},
    {'code': 'CASH',           'label': 'Cash'},
]
DEFAULT_PAYMENT_MODE = 'WPS'


# ── Fixed earning columns (stored directly on Payslip) ──────────────
# These are the 4 standard earnings every payslip has, modelled as
# columns on the Payslip table for fast aggregation.
FIXED_EARNINGS: List[Dict] = [
    {'code': 'basic',      'label': 'Basic Salary',           'field': 'basic'},
    {'code': 'housing',    'label': 'Housing Allowance',      'field': 'housing'},
    {'code': 'transport',  'label': 'Transportation Allowance','field': 'transport'},
    {'code': 'home_leave', 'label': 'Home Leave Allowance',   'field': 'home_leave'},
]


# ── Free-form earning components (PayslipLineItem rows) ─────────────
EARNING_COMPONENTS: List[Dict] = [
    {'code': 'leave_payout',     'label': 'Leave Payout',          'taxable': False},
    {'code': 'overtime',         'label': 'Overtime',              'taxable': False},
    {'code': 'bonus',            'label': 'Bonus',                 'taxable': False},
    {'code': 'commission',       'label': 'Commission',            'taxable': False},
    {'code': 'reimbursement',    'label': 'Reimbursement',         'taxable': False},
    {'code': 'gratuity',         'label': 'Gratuity',              'taxable': False},
    {'code': 'eosb',             'label': 'End of Service Benefit','taxable': False},
    {'code': 'other_earning',    'label': 'Other',                 'taxable': False},
]


# ── Deduction components (PayslipLineItem rows) ─────────────────────
DEDUCTION_COMPONENTS: List[Dict] = [
    {'code': 'absent',                'label': 'Absent'},
    {'code': 'sick_leave',            'label': 'Sick Leave'},
    {'code': 'housing_advance',       'label': 'Housing Allowance Advance'},
    {'code': 'salary_advance',        'label': 'Salary Advance'},
    {'code': 'telephone',             'label': 'Telephone'},
    {'code': 'car_benefit',           'label': 'Car Benefit'},
    {'code': 'housing_benefit',       'label': 'Housing Benefit'},
    {'code': 'dependant_insurance',   'label': 'Dependant Insurance'},
    {'code': 'utility_bill',          'label': 'Utility Bill'},
    {'code': 'fine',                  'label': 'Fine / Penalty'},
    {'code': 'loan_repayment',        'label': 'Loan Repayment'},
    {'code': 'other_deduction',       'label': 'Other'},
]


# ── Line item kinds ────────────────────────────────────────────────
class LineItemKind:
    EARNING = 'earning'
    DEDUCTION = 'deduction'


LINE_ITEM_KINDS: List[Dict] = [
    {'code': LineItemKind.EARNING,   'label': 'Earning'},
    {'code': LineItemKind.DEDUCTION, 'label': 'Deduction'},
]


# ── Line item sources (provenance) ──────────────────────────────────
class LineItemSource:
    AUTO = 'auto'              # created by run_generator
    MANUAL = 'manual'          # added/edited in UI
    EXCEL = 'excel'            # uploaded via Excel import
    ADJUSTMENT = 'adjustment'  # materialised from PayrollAdjustment


LINE_ITEM_SOURCES: List[Dict] = [
    {'code': LineItemSource.AUTO,       'label': 'Auto-generated'},
    {'code': LineItemSource.MANUAL,     'label': 'Manual entry'},
    {'code': LineItemSource.EXCEL,      'label': 'Excel upload'},
    {'code': LineItemSource.ADJUSTMENT, 'label': 'Adjustment'},
]


# ── Adjustment statuses ─────────────────────────────────────────────
class AdjustmentStatus:
    PENDING = 'pending'
    APPLIED = 'applied'
    CANCELLED = 'cancelled'


ADJUSTMENT_STATUSES: List[Dict] = [
    {'code': AdjustmentStatus.PENDING,   'label': 'Pending', 'tone': 'amber'},
    {'code': AdjustmentStatus.APPLIED,   'label': 'Applied', 'tone': 'green'},
    {'code': AdjustmentStatus.CANCELLED, 'label': 'Cancelled','tone': 'gray'},
]


# ── Reference lists ─────────────────────────────────────────────────
GRADE_OPTIONS: List[str] = [
    'Senior Assistant',
    'Assistant',
    'Senior Engineer',
    'Engineer',
    'Junior Engineer',
    'Manager',
    'Senior Manager',
    'Director',
    'VP',
    'GM',
    'Other',
]

NATIONALITY_GROUPS: List[Dict] = [
    {'code': 'expats',   'label': 'Expats'},
    {'code': 'locals',   'label': 'UAE Nationals'},
    {'code': 'gcc',      'label': 'GCC Nationals'},
]


# ── Excel I/O — Master roster sheet ────────────────────────────────
# Maps spreadsheet column index (1-based, matching openpyxl) to the
# canonical PayrollEmployee field. The header row is row 4 in the
# April 2026 master sheet; data starts at row 5.
EXCEL_MASTER_HEADER_ROW = 4
EXCEL_MASTER_DATA_START_ROW = 5
EXCEL_MASTER_TOTAL_LABEL = 'TOTAL'  # row that signals end of data

EXCEL_MASTER_COLUMN_MAP: Dict[int, str] = {
    1:  'sr_no',
    2:  'full_name',
    3:  'mol_no',
    4:  'routing_code',
    5:  'iban',
    6:  'bank_name',
    7:  'employee_no',
    8:  'joining_date',
    9:  'department',
    10: 'discipline',
    11: 'designation',
    12: 'grade',
    13: 'nationality_group',
    14: 'basic',
    15: 'housing',
    16: 'transport',
    17: 'home_leave',
    18: 'other_allowance',          # rarely used; treated as earning line
    19: 'other_pay_amount',         # "Other Pay" → free-form earning line
    20: 'other_pay_details',        # description for other_pay_amount
    21: 'deduction_amount',         # free-form deduction
    22: 'deduction_details',        # description for deduction_amount
    23: 'final_remuneration',       # net (computed; used for validation)
    24: 'payment_mode',
    25: 'hours',                    # appended — monthly working hours (live biometric)
}

# Header label for the Hours column (kept here so header row + map stay in sync).
EXCEL_MASTER_HOURS_HEADER = 'Hours'


# ── Excel I/O — Per-employee payslip sheet ──────────────────────────
# Cell coordinates ARE the layout (matches Excel template exactly).
EXCEL_PAYSLIP_LAYOUT: Dict[str, tuple] = {
    'title_row':            (5, 1),   # "Payslip for the month of"
    'period_label':         (5, 2),   # "APRIL 2026"

    'name_label':           (8, 1),
    'name_value':           (8, 2),
    'joining_date_label':   (8, 3),
    'joining_date_value':   (8, 4),

    'employee_no_label':    (9, 1),
    'employee_no_value':    (9, 2),

    'title_label':          (10, 1),
    'title_value':          (10, 2),

    # Hours / Month — single-cell block above the SALARY header so the
    # biometric figure is visible at the top of every payslip.
    'hours_label':          (11, 1),
    'hours_value':          (11, 2),

    'salary_header':        (13, 1),  # "SALARY"
    'deductions_header':    (13, 3),  # "DEDUCTIONS"

    'col_headers_row':      14,

    'basic_label':          (15, 1),
    'basic_value':          (15, 2),
    'housing_label':        (16, 1),
    'housing_value':        (16, 2),
    'transport_label':      (17, 1),
    'transport_value':      (17, 2),
    'home_leave_label':     (18, 1),
    'home_leave_value':     (18, 2),

    'others_earning_label': (19, 1),  # "Others:"
    'others_earning_value': (19, 2),
    'others_earning_detail_row': 20,  # description row beneath "Others:"

    # Deduction column starts at column 3 (same rows 15..)
    'deduction_start_row':  15,
    'deduction_label_col':  3,
    'deduction_detail_col': 4,
    'deduction_value_col':  5,

    'total_earnings_label': (21, 1),
    'total_earnings_value': (21, 2),
    'total_deductions_label': (21, 3),
    'total_deductions_value': (21, 5),

    'net_label':            (23, 1),  # "NET PAYABLE Amount"
    'net_value':            (23, 3),

    'payment_mode_label':   (25, 1),
    'payment_mode_value':   (25, 2),
}


# ── Lookup helpers ─────────────────────────────────────────────────
def status_meta(code: str) -> Dict:
    return next((s for s in WORKFLOW_STATUSES if s['code'] == code), {})


def next_statuses(current: str) -> List[str]:
    return WORKFLOW_TRANSITIONS.get(current, [])


def is_terminal(code: str) -> bool:
    return len(next_statuses(code)) == 0


def fixed_earning_fields() -> List[str]:
    return [e['field'] for e in FIXED_EARNINGS]


def earning_codes() -> List[str]:
    return [c['code'] for c in EARNING_COMPONENTS]


def deduction_codes() -> List[str]:
    return [c['code'] for c in DEDUCTION_COMPONENTS]


def payment_mode_codes() -> List[str]:
    return [p['code'] for p in PAYMENT_MODES]


def normalise_payment_mode(raw) -> str:
    """Map any Excel string to a known code. Defaults to DEFAULT_PAYMENT_MODE."""
    if not raw:
        return DEFAULT_PAYMENT_MODE
    s = str(raw).strip().upper().replace(' ', '').replace('–', '-').replace('—', '-')
    aliases = {
        'WPS': 'WPS',
        'WPS-RSI': 'WPS-RSI',
        'WPSRSI': 'WPS-RSI',
        'BANKTRANSFER': 'BANK_TRANSFER',
        'BANKTRANSFER-RSI': 'BANK_TRANSFER_RSI',
        'CHQ': 'CHEQUE',
        'CHEQUE': 'CHEQUE',
        'CASH': 'CASH',
    }
    return aliases.get(s, DEFAULT_PAYMENT_MODE)


# ── Bulk percentage deduction ───────────────────────────────────────
# Single source of truth for the HR "Apply % deduction" tool.
# - PROTECTED_FIELDS can never be picked as a calculation base.
# - ALLOWED_FIELDS are the choices HR sees in the modal.
# - DEFAULT_FIELDS are pre-checked when the modal opens.
BULK_DEDUCTION_PROTECTED_FIELDS: List[str] = ['basic']
BULK_DEDUCTION_ALLOWED_FIELDS:   List[str] = [
    'housing', 'transport', 'home_leave', 'other_earnings',
]
BULK_DEDUCTION_DEFAULT_FIELDS:   List[str] = list(BULK_DEDUCTION_ALLOWED_FIELDS)

# The component code that tags the generated PayslipLineItem. Using a unique
# code lets us upsert/replace previous bulk runs idempotently.
BULK_DEDUCTION_COMPONENT_CODE = 'bulk_pct_deduction'
BULK_DEDUCTION_DEFAULT_LABEL  = 'Bulk Salary Deduction'

# Soft limits (kept as strings so we can convert to Decimal without import here).
BULK_DEDUCTION_MIN_PCT = '0.01'
BULK_DEDUCTION_MAX_PCT = '100.00'

