"""Read-only, permission-gated totals from the active invoice workbook.

This snapshot preserves spreadsheet rows and statuses, including duplicate
invoice numbers. It is independent of the live register's dashboard filters.
Totals and source facts are published together under one immutable source version.
"""
import json
import logging
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'data' / 'invoice_workbook_summary.json'
AMOUNT_FIELDS = ('invoice_amount', 'invoice_amount_aed', 'actual_payment_received')
STATUS_GROUPS = (('paid', 'Paid'), ('cancelled', 'Cancelled'), ('pending', 'Pending'),
                 ('new', 'New'), ('other', 'Other statuses'))
COVERAGE_FIELDS = ('numeric_count', 'blank_count', 'text_count', 'error_count')
CURRENCY_AMOUNT_FIELDS = ('invoice_amount', 'actual_payment_received')
CURRENCY_METHOD = 'strict_agreement_or_single_source'
MONEY_QUANTUM = Decimal('0.01')
_ACTIVE_SOURCE = object()


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def _validate_currency_breakdown(data):
    source = data['source']
    if (source['currency_column'] != 'AE' or source['currency_header'] != 'Inv. CUR'
            or source['currency_method'] != CURRENCY_METHOD):
        raise ValueError('Unexpected workbook currency source.')
    groups = data['currency_breakdown']
    if not isinstance(groups, list) or not groups:
        raise ValueError('Missing workbook currency breakdown.')
    identities = set()
    exact_totals = dict.fromkeys(CURRENCY_AMOUNT_FIELDS, Decimal('0'))
    rounded_totals = dict.fromkeys(CURRENCY_AMOUNT_FIELDS, Decimal('0'))
    combined_coverage = {key: dict.fromkeys(COVERAGE_FIELDS, 0) for key in CURRENCY_AMOUNT_FIELDS}
    for group in groups:
        currency, status = group['currency'], group['currency_status']
        if status == 'recorded':
            if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError('Invalid recorded workbook currency.')
        elif status not in {'conflict', 'not_recorded', 'error', 'unrecognized'} or currency is not None:
            raise ValueError('Invalid workbook currency classification.')
        identity = (currency, status)
        if identity in identities:
            raise ValueError('Duplicate workbook currency group.')
        identities.add(identity)
        for key in CURRENCY_AMOUNT_FIELDS:
            count = group['row_counts'][key]
            coverage = group['coverage'][key]
            if (not _nonnegative_integer(count)
                    or not all(_nonnegative_integer(coverage[field]) for field in COVERAGE_FIELDS)
                    or sum(coverage[field] for field in COVERAGE_FIELDS) != count):
                raise ValueError('Inconsistent workbook currency coverage.')
            for field in COVERAGE_FIELDS:
                combined_coverage[key][field] += coverage[field]
            amount, exact = group[key], group['exact_amounts'][key]
            if not coverage['numeric_count']:
                if amount is not None or exact is not None:
                    raise ValueError('Unknown currency amounts must remain null.')
                continue
            if (not isinstance(amount, str) or not isinstance(exact, str)
                    or not Decimal(amount).is_finite() or not Decimal(exact).is_finite()
                    or Decimal(exact).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) != Decimal(amount)):
                raise ValueError('Inconsistent workbook currency amount.')
            exact_totals[key] += Decimal(exact)
            rounded_totals[key] += Decimal(amount)
    for key in CURRENCY_AMOUNT_FIELDS:
        if combined_coverage[key] != data['coverage'][key]:
            raise ValueError('Currency coverage does not reconcile to workbook coverage.')
        total = Decimal(data['totals'][key])
        adjustment = data['currency_rounding_adjustment'][key]
        if (exact_totals[key].quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) != total
                or not isinstance(adjustment, str) or not Decimal(adjustment).is_finite()
                or total - rounded_totals[key] != Decimal(adjustment)):
            raise ValueError('Currency subtotals do not reconcile to the workbook total.')


def _validate_status_amounts(data):
    combined = dict.fromkeys(COVERAGE_FIELDS, 0)
    exact_total = rounded_total = Decimal('0')
    for group in data['payment_status']:
        coverage = group['amount_coverage']
        if (not all(_nonnegative_integer(coverage[key]) for key in COVERAGE_FIELDS)
                or sum(coverage[key] for key in COVERAGE_FIELDS) != group['count']):
            raise ValueError('Inconsistent payment status amount coverage.')
        for key in COVERAGE_FIELDS:
            combined[key] += coverage[key]
        amount, exact = group['amount_aed'], group['exact_amount_aed']
        if not coverage['numeric_count']:
            if amount is not None or exact is not None:
                raise ValueError('Unknown payment status amounts must remain null.')
            continue
        if (not isinstance(amount, str) or not isinstance(exact, str)
                or not Decimal(amount).is_finite() or not Decimal(exact).is_finite()
                or Decimal(exact).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) != Decimal(amount)):
            raise ValueError('Inconsistent payment status amount.')
        exact_total += Decimal(exact)
        rounded_total += Decimal(amount)
    adjustment = data['payment_status_rounding_adjustment']
    total = Decimal(data['totals']['invoice_amount_aed'])
    if (combined != data['coverage']['invoice_amount_aed']
            or exact_total.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) != total
            or not isinstance(adjustment, str) or not Decimal(adjustment).is_finite()
            or total - rounded_total != Decimal(adjustment)):
        raise ValueError('Payment status amounts do not reconcile to the AED total.')


def validate_snapshot(data):
    """Reject an incomplete or inconsistent packaged snapshot before exposure."""
    if data['schema_version'] != '1.0' or data['status'] != 'available':
        raise ValueError('Unsupported workbook summary schema.')
    source = data['source']
    if source['scope'] != 'full_workbook' or data['currency_basis'] != 'mixed_original':
        raise ValueError('Unexpected workbook summary scope.')
    if (not all(isinstance(source[key], str) and source[key]
                for key in ('file_name', 'sheet', 'snapshot_at', 'sha256'))
            or len(source['sha256']) != 64
            or any(char not in '0123456789abcdef' for char in source['sha256'])):
        raise ValueError('Incomplete workbook provenance.')
    count = data['invoice_count']
    if (not _nonnegative_integer(count) or count == 0
            or not _nonnegative_integer(source['first_row'])
            or source['first_row'] < 1
            or not _nonnegative_integer(source['last_row'])
            or source['last_row'] - source['first_row'] + 1 != count):
        raise ValueError('Inconsistent workbook row count.')
    for key in AMOUNT_FIELDS:
        amount = data['totals'][key]
        if not isinstance(amount, str) or not Decimal(amount).is_finite():
            raise ValueError('Invalid workbook amount.')
        coverage = data['coverage'][key]
        if (not all(_nonnegative_integer(coverage[field]) for field in COVERAGE_FIELDS)
                or sum(coverage[field] for field in COVERAGE_FIELDS) != count):
            raise ValueError('Inconsistent workbook amount coverage.')
    project_count = data['totals']['project_count']
    excluded = data['project_excluded_rows']
    if (not _nonnegative_integer(project_count) or not _nonnegative_integer(excluded)
            or project_count + excluded > count):
        raise ValueError('Inconsistent workbook project coverage.')
    statuses = data['payment_status']
    if ([(row['id'], row['label']) for row in statuses] != list(STATUS_GROUPS)
            or not all(_nonnegative_integer(row['count']) for row in statuses)
            or sum(row['count'] for row in statuses) != count):
        raise ValueError('Inconsistent workbook payment status counts.')
    other = data['other_statuses']
    if (not all(isinstance(row['label'], str) and row['label']
                and _nonnegative_integer(row['count']) for row in other)
            or sum(row['count'] for row in other) != statuses[-1]['count']):
        raise ValueError('Inconsistent other payment statuses.')
    _validate_currency_breakdown(data)
    _validate_status_amounts(data)
    return data


def validate_source_summary(data, source_snapshot):
    """Require the aggregate to describe exactly the selected source version."""
    validate_snapshot(data)
    source = data['source']
    if (source['sha256'] != source_snapshot.sha256
            or source['sheet'] != source_snapshot.sheet_name
            or source['first_row'] != source_snapshot.first_row
            or source['last_row'] != source_snapshot.last_row
            or source_snapshot.first_row != source_snapshot.header_row + 1
            or data['invoice_count'] != source_snapshot.row_count):
        raise ValueError('The workbook summary does not match the selected source version.')
    return data


def build_workbook_summary(user, *, source_snapshot=_ACTIVE_SOURCE):
    """Read the pinned source aggregate, checking access before any source data."""
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return {'schema_version': '1.0', 'status': 'restricted',
                'reason': 'Read access to customer invoices is required.'}
    try:
        if source_snapshot is _ACTIVE_SOURCE:
            from apps.finance.receivables_source_models import ReceivablesSourceSnapshot
            source_snapshot = ReceivablesSourceSnapshot.objects.filter(is_active=True).first()
        if source_snapshot is None:
            return {'schema_version': '1.0', 'status': 'unavailable',
                    'reason': 'Upload a receivables workbook to view its totals.'}
        summary = source_snapshot.reconciliation.get('workbook_summary')
        if summary is None:
            # Compatibility for the previously imported audited workbook only.
            # Never display this packaged aggregate for a different active file.
            with SNAPSHOT_PATH.open(encoding='utf-8') as source:
                summary = json.load(source)
        summary = deepcopy(validate_source_summary(summary, source_snapshot))
        summary['source']['file_name'] = source_snapshot.file_name
        return summary
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        logger.exception('The invoice workbook summary could not be read.')
        return {'schema_version': '1.0', 'status': 'unavailable',
                'reason': 'The invoice workbook summary is unavailable.'}
