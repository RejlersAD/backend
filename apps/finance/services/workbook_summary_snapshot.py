"""Reproducible aggregate-only snapshot generation; never imports invoices."""
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from .workbook_summary import (
    AMOUNT_FIELDS, COVERAGE_FIELDS, CURRENCY_AMOUNT_FIELDS, CURRENCY_METHOD,
    MONEY_QUANTUM, STATUS_GROUPS, validate_snapshot,
)


HEADERS = {'A': 'Invoice #', 'G': 'RAD Project #', 'L': 'Invoice Amount',
           'M': 'Inv Amt. (AED)', 'R': 'Payment Status', 'AA': 'Actual Payment Received',
           'AE': 'Inv. CUR'}
AMOUNT_INDICES = {'invoice_amount': 11, 'invoice_amount_aed': 12, 'actual_payment_received': 26}
FOOTER_LABELS = {'total', 'grand total', 'subtotal', 'sub total'}
CURRENCY_ALIASES = {
    '\u20ac': 'EUR', 'EURO': 'EUR', 'EUROS': 'EUR',
    'US$': 'USD', 'US DOLLAR': 'USD', 'US DOLLARS': 'USD',
    'U.S. DOLLAR': 'USD', 'U.S. DOLLARS': 'USD',
    'UAE DIRHAM': 'AED', 'UAE DIRHAMS': 'AED', 'DIRHAM': 'AED', 'DIRHAMS': 'AED',
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if number.is_finite() and number == number.to_integral_value():
            return str(int(number))
    return str(value).strip()


def _cell_kind(cell):
    value = cell.value
    if cell.data_type == 'e':
        return 'error_count'
    if value is None or (isinstance(value, str) and not value.strip()):
        return 'blank_count'
    if (not isinstance(value, bool) and isinstance(value, (int, float, Decimal))
            and Decimal(str(value)).is_finite()):
        return 'numeric_count'
    return 'text_count'


def _currency_code(value):
    normalized = _text(value).upper()
    normalized = CURRENCY_ALIASES.get(normalized, normalized)
    return normalized if re.fullmatch('[A-Z]{3}', normalized) else None


def _format_currencies(number_format):
    """Read explicit currency markers; never infer from locale or bare '$'."""
    number_format = number_format or ''
    markers = [marker.split('-', 1)[0] for marker in re.findall(r'\[\$([^\]]+)\]', number_format)]
    markers += re.findall(r'"([^\"]+)"', number_format)
    return {code for marker in markers if (code := _currency_code(marker))}


def _cell_currency(amount_cell, currency_cell):
    """Require agreement when both AE and this amount's format state a code."""
    formats = _format_currencies(amount_cell.number_format)
    if len(formats) > 1:
        return None, 'conflict'
    if currency_cell.data_type == 'e':
        return None, 'error'
    recorded_text = _text(currency_cell.value)
    recorded = _currency_code(recorded_text)
    if recorded_text and recorded is None and recorded_text.casefold() not in {'n/a', 'not recorded'}:
        return None, 'unrecognized'
    formatted = next(iter(formats), None)
    if recorded and formatted and recorded != formatted:
        return None, 'conflict'
    currency = recorded or formatted
    return (currency, 'recorded') if currency else (None, 'not_recorded')


def _currency_group():
    return {
        'row_counts': dict.fromkeys(CURRENCY_AMOUNT_FIELDS, 0),
        'totals': dict.fromkeys(CURRENCY_AMOUNT_FIELDS, Decimal('0')),
        'coverage': {key: dict.fromkeys(COVERAGE_FIELDS, 0) for key in CURRENCY_AMOUNT_FIELDS},
    }


def _money(value):
    return str(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def generate_workbook_snapshot(workbook_path, *, last_row, sheet='External Invoice ',
                               header_row=5, snapshot_at=None):
    """Sum cached numeric cells within a verified, contiguous invoice row range.

    Explicit bounds plus invoice-ID checks prevent the worksheet's later total
    formulas from entering the sum. Duplicate invoice IDs remain separate rows.
    Text amounts, errors, and blank cells are reported, never coerced into money.
    """
    path = Path(workbook_path)
    first_row = header_row + 1
    if header_row < 1 or last_row < first_row:
        raise ValueError('The invoice range must follow the header row.')
    captured = snapshot_at or datetime.now(timezone.utc)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError('snapshot_at must include a timezone.')
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise ValueError('The requested invoice sheet was not found.')
        worksheet = workbook[sheet]
        if last_row > worksheet.max_row:
            raise ValueError('The last invoice row is outside the worksheet.')
        headers = next(worksheet.iter_rows(min_row=header_row, max_row=header_row, max_col=31))
        for column, expected in HEADERS.items():
            if _text(headers[column_index_from_string(column) - 1].value) != expected:
                raise ValueError(f'Unexpected header at {column}{header_row}.')
        totals = {key: Decimal('0') for key in AMOUNT_FIELDS}
        coverage = {key: dict.fromkeys(COVERAGE_FIELDS, 0) for key in AMOUNT_FIELDS}
        currencies = {}
        statuses = Counter()
        status_amounts = {key: {'total': Decimal('0'), 'coverage': dict.fromkeys(COVERAGE_FIELDS, 0)}
                          for key, _ in STATUS_GROUPS}
        other_statuses = Counter()
        projects = set()
        project_excluded_rows = 0
        count = 0
        for row_number, row in enumerate(worksheet.iter_rows(min_row=first_row, max_col=31), first_row):
            invoice_id = _text(row[0].value)
            footer = invoice_id.casefold().rstrip(':') in FOOTER_LABELS
            if row_number > last_row:
                if invoice_id and not footer:
                    raise ValueError(f'An invoice row exists after the specified last row: {row_number}.')
                continue
            if not invoice_id or footer or row[0].data_type == 'e':
                raise ValueError(f'Row {row_number} is not an invoice row; exclude footers from the range.')
            count += 1
            for key, index in AMOUNT_INDICES.items():
                cell = row[index]
                kind = _cell_kind(cell)
                coverage[key][kind] += 1
                if kind == 'numeric_count':
                    totals[key] += Decimal(str(cell.value))
                if key in CURRENCY_AMOUNT_FIELDS:
                    identity = _cell_currency(cell, row[30])
                    group = currencies.setdefault(identity, _currency_group())
                    group['row_counts'][key] += 1
                    group['coverage'][key][kind] += 1
                    if kind == 'numeric_count':
                        group['totals'][key] += Decimal(str(cell.value))
            project = _text(row[6].value)
            if not project or project.casefold() == 'n/a' or row[6].data_type == 'e':
                project_excluded_rows += 1
            else:
                projects.add(project)
            status = _text(row[17].value).casefold()
            status_id = status if status in {'paid', 'cancelled', 'pending', 'new'} else 'other'
            statuses[status_id] += 1
            if status_id == 'other':
                label = (status.upper() if row[17].data_type == 'e'
                         else status.title() if status else 'Not recorded')
                other_statuses[label] += 1
            kind = _cell_kind(row[12])
            status_amounts[status_id]['coverage'][kind] += 1
            if kind == 'numeric_count':
                status_amounts[status_id]['total'] += Decimal(str(row[12].value))
    finally:
        workbook.close()
    currency_breakdown = []
    for (currency, status), group in sorted(currencies.items(), key=lambda item: (item[0][0] or 'ZZZZ', item[0][1])):
        known = {key: group['coverage'][key]['numeric_count'] > 0 for key in CURRENCY_AMOUNT_FIELDS}
        currency_breakdown.append({
            'currency': currency, 'currency_status': status, 'row_counts': group['row_counts'],
            'coverage': group['coverage'],
            **{key: _money(group['totals'][key]) if known[key] else None for key in CURRENCY_AMOUNT_FIELDS},
            'exact_amounts': {key: str(group['totals'][key]) if known[key] else None for key in CURRENCY_AMOUNT_FIELDS},
        })
    payment_status = []
    for key, label in STATUS_GROUPS:
        group = status_amounts[key]
        known = group['coverage']['numeric_count'] > 0
        payment_status.append({
            'id': key, 'label': label, 'count': statuses[key],
            'amount_aed': _money(group['total']) if known else None,
            'exact_amount_aed': str(group['total']) if known else None,
            'amount_coverage': group['coverage'],
        })
    return validate_snapshot({
        'schema_version': '1.0', 'status': 'available',
        'source': {'file_name': path.name, 'sheet': sheet, 'first_row': first_row,
                   'last_row': last_row, 'snapshot_at': captured.isoformat(),
                   'sha256': digest, 'scope': 'full_workbook',
                   'currency_column': 'AE', 'currency_header': 'Inv. CUR', 'currency_method': CURRENCY_METHOD},
        'invoice_count': count,
        'totals': {**{key: _money(value) for key, value in totals.items()}, 'project_count': len(projects)},
        'coverage': coverage,
        'currency_breakdown': currency_breakdown,
        'currency_rounding_adjustment': {
            key: _money(Decimal(_money(totals[key])) - sum((Decimal(group[key] or '0') for group in currency_breakdown), Decimal('0')))
            for key in CURRENCY_AMOUNT_FIELDS
        },
        'payment_status': payment_status,
        'payment_status_rounding_adjustment': _money(
            Decimal(_money(totals['invoice_amount_aed']))
            - sum((Decimal(group['amount_aed'] or '0') for group in payment_status), Decimal('0'))
        ),
        'other_statuses': [{'label': label, 'count': value}
                           for label, value in sorted(other_statuses.items())],
        'project_excluded_rows': project_excluded_rows,
        'currency_basis': 'mixed_original',
    })
