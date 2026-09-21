"""Read-only, permission-gated totals from the audited invoice workbook.

This snapshot preserves spreadsheet rows and statuses, including duplicate
invoice numbers. It is independent of the live register's dashboard filters.
Only aggregates and source provenance are packaged; no invoice records are kept.
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'data' / 'invoice_workbook_summary.json'
AMOUNT_FIELDS = ('invoice_amount', 'invoice_amount_aed', 'actual_payment_received')
STATUS_GROUPS = (('paid', 'Paid'), ('cancelled', 'Cancelled'), ('pending', 'Pending'),
                 ('new', 'New'), ('other', 'Other statuses'))
COVERAGE_FIELDS = ('numeric_count', 'blank_count', 'text_count', 'error_count')


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


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
    return data


def build_workbook_summary(user):
    """Check the outgoing-register permission before opening the snapshot file."""
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return {'schema_version': '1.0', 'status': 'restricted',
                'reason': 'Read access to customer invoices is required.'}
    try:
        with SNAPSHOT_PATH.open(encoding='utf-8') as source:
            return validate_snapshot(json.load(source))
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        logger.exception('The invoice workbook summary could not be read.')
        return {'schema_version': '1.0', 'status': 'unavailable',
                'reason': 'The invoice workbook summary is unavailable.'}
