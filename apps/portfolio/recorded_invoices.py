"""Read-only links from authorized portfolio identities to outgoing invoices.

The POC workbook has project totals, not invoice numbers. These links therefore
identify current register invoices by their recorded project number; they never
claim an invoice-level workbook reconciliation or allocate parent invoices to
multiple subprojects.
"""
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Trim, Upper
from django.utils import timezone

from apps.invoice_tracker.models import CustomerInvoice
from apps.invoice_tracker.services.receivable_balance import receivable_balance
from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
REGISTER_ROUTE = '/finance/outgoing-invoices'
EXCLUDED_STATUSES = {'cancelled', 'credit_note'}
FIELDS = (
    'id', 'invoice_number', 'rad_project_no', 'project_id', 'project_name',
    'company', 'account', 'category', 'currency', 'invoice_amount',
    'invoice_amount_aed', 'actual_payment_received', 'payment_status',
    'invoice_date', 'due_date', 'payment_date', 'updated_at',
)
DESCRIPTION = (
    'Current recorded outgoing invoices matched by exact project number. The '
    'POC workbook has no invoice numbers. Parent invoices are shown once and '
    'are not allocated to subprojects. Receipts and balances are current '
    'register values, not historical workbook-cutoff balances. No currency '
    'conversion or comparison of unverified tax bases is applied. Financial '
    'totals include only unambiguous invoices and exclude cancelled invoices '
    'and credit notes; excluded records remain visible for review.'
)


def normalize_invoice_project_code(value):
    # Match the database Upper(Trim()) operation. Keep leading zeroes,
    # punctuation and internal spacing; those can distinguish project codes.
    return str(value or '').strip(' ').upper()


_key = normalize_invoice_project_code


def _money(value):
    return format(Decimal(value).quantize(Decimal('0.01')), '.2f') if value is not None else None


def _empty(status, limit, offset, description):
    return {
        'status': status, 'source': None, 'coverage': None,
        'totals_by_currency': [], 'project_groups': [], 'rows': [],
        'total_rows': None, 'offset': offset, 'limit': limit,
        'returned_rows': 0, 'truncated': False, 'description': description,
    }


def _parent_access(user, keys):
    """A visible child alone does not authorize its parent's invoice register."""
    from apps.core.project_models import Project
    from apps.project_control.access import accessible_enterprise_projects

    records = list(Project.objects.annotate(_invoice_code=Upper(Trim('code')))
                   .filter(_invoice_code__in=keys).values('id', '_invoice_code'))
    counts = Counter(row['_invoice_code'] for row in records)
    visible = set(accessible_enterprise_projects(user).filter(
        pk__in=[row['id'] for row in records],
    ).values_list('pk', flat=True))
    return {row['_invoice_code'] for row in records
            if counts[row['_invoice_code']] == 1 and row['id'] in visible}


def source_identity_collisions(rows, *, normalize=normalize_invoice_project_code):
    """Filters and row visibility must never turn an ambiguous code into a match.

    Read identities only from the immutable source, not financial fields. Hidden
    identities are used solely to withhold unsafe links and never enter output.
    """
    from .models import PortfolioRow

    snapshots = {row['snapshot_id'] for row in rows if row.get('snapshot_id') is not None}
    if not snapshots:
        return set()
    identities = PortfolioRow.objects.filter(snapshot_id__in=snapshots).values_list(
        'project_code', 'subproject_code',
    )
    subprojects, parents = defaultdict(set), defaultdict(set)
    for parent, subproject in identities:
        subprojects[normalize(subproject)].add((parent, subproject))
        parents[normalize(parent)].add(parent)
    return {key for key in set(subprojects) | set(parents)
            if len(subprojects.get(key, set())) > 1 or len(parents.get(key, set())) > 1
            or (key in parents and any(normalize(parent) != key for parent, _ in subprojects.get(key, set())))}


def _matches(user, rows, full_source):
    identities, subprojects, parents = {}, defaultdict(set), defaultdict(set)
    spellings, parent_spellings = defaultdict(set), defaultdict(set)
    for row in rows:
        identity = (_key(row.get('project_code')), _key(row.get('subproject_code')))
        if not all(identity):
            continue
        identities.setdefault(identity, row)
        spellings[identity].add((row['project_code'], row['subproject_code']))
        parent_spellings[identity[0]].add(row['project_code'])
        subprojects[identity[1]].add(identity)
        parents[identity[0]].add(identity)
    parent_access = set(parents) if full_source else _parent_access(user, parents)
    source_collisions = source_identity_collisions(rows)
    matches, ambiguous = {}, set()
    for key in set(subprojects) | set(parents):
        subs = subprojects.get(key, set())
        # A code shared by different source identities is not a safe allocation.
        collision = (key in source_collisions or len(subs) > 1 or any(len(spellings[identity]) > 1 for identity in subs)
                     or len(parent_spellings.get(key, set())) > 1
                     or bool(subs and key in parents and next(iter(subs))[0] != key))
        if collision:
            if full_source or key in parent_access:
                ambiguous.add(key)
            continue
        if subs:
            identity = next(iter(subs))
            row = identities[identity]
            matches[key] = {
                'project_number': key, 'project_code': row['project_code'],
                'subproject_code': row['subproject_code'], 'title': row.get('title') or '',
                'match_level': 'subproject', '_identity': identity,
            }
        elif key in parent_access:
            row = identities[next(iter(parents[key]))]
            matches[key] = {
                'project_number': key, 'project_code': row['project_code'],
                'subproject_code': None, 'title': '', 'match_level': 'parent', '_identity': None,
            }
    withheld = len(set(parents) - parent_access - set(subprojects))
    return identities, matches, ambiguous, withheld


def _metric(values, *, basis, currency):
    values = list(values)
    known = [Decimal(value) for value in values if value is not None]
    missing = len(values) - len(known)
    subtotal = _money(sum(known, Decimal('0'))) if known else None
    return {
        'value': subtotal if not missing else None, 'known_value': subtotal,
        'status': 'partial' if missing else 'available' if values else 'unavailable',
        'missing_count': missing, 'included_rows': len(values),
        'basis': basis, 'currency': currency,
    }


def _totals(records):
    groups = defaultdict(list)
    for row in records:
        groups[row['currency']].append(row)
    totals = []
    for currency, members in sorted(groups.items()):
        included = [row for row in members if not row['excluded_from_totals']]
        item = {
            'currency': currency, 'invoice_count': len(members),
            'included_invoice_count': len(included),
            'conflict_count': sum(bool(row['conflict_codes']) for row in members),
            'excluded_invoice_count': sum(row['payment_status'] in EXCLUDED_STATUSES for row in members),
            'invoice_amount': _metric((row['invoice_amount'] for row in included),
                                      basis='recorded_invoice_amount', currency=currency),
            'actual_payment_received': _metric(
                (row['actual_payment_received'] if row['actual_payment_received'] is not None else '0'
                 for row in included), basis='recorded_receipts_blank_is_zero', currency=currency),
            'calculated_receivable_balance': _metric(
                (row['calculated_receivable_balance'] for row in included),
                basis='invoice_amount_less_current_receipts_blank_is_zero', currency=currency),
            'recorded_aed_invoice_amount': _metric((row['invoice_amount_aed'] for row in included),
                                                  basis='stored_aed_amount_no_conversion', currency='AED'),
        }
        if currency == 'UNSPECIFIED':
            for key in ('invoice_amount', 'actual_payment_received', 'calculated_receivable_balance'):
                # Missing currencies cannot safely be summed together, even
                # though each individual recorded amount is known.
                item[key].update(value=None, known_value=None, status='partial')
        totals.append(item)
    return totals


def build_recorded_invoices(user, rows, *, full_source=False, limit=50, offset=0):
    """Caller supplies authorized, filtered PortfolioRow dictionaries, before pagination."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError('Invoice page limit must be between 1 and 200.')
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise ValueError('Invoice offset must be between 0 and 100000.')
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return _empty('restricted', limit, offset, 'Outgoing Invoices read access is required.')
    try:
        with transaction.atomic():
            identities, matches, ambiguous, withheld = _matches(user, rows, full_source)
            source = CustomerInvoice.objects.annotate(
                _portfolio_project=Upper(Trim('rad_project_no')),
                _portfolio_number=Upper(Trim('invoice_number')),
            ).filter(_portfolio_project__in=set(matches) | ambiguous)
            invoices = list(source.order_by('invoice_number', 'rad_project_no', 'id').values(
                *FIELDS, '_portfolio_project', '_portfolio_number',
            ))
            # Check IDs against the unfiltered register. A second physical row
            # on another project must not become an apparently safe detail URL.
            id_counts = dict(CustomerInvoice._base_manager.filter(
                pk__in={row['id'] for row in invoices},
            ).order_by().values('pk').annotate(_records=Count('pk')).values_list('pk', '_records'))
            composite_counts = Counter((row['_portfolio_number'], row['_portfolio_project']) for row in invoices)
            records, groups, matched_identities = [], {}, set()
            for index, invoice in enumerate(invoices):
                key = invoice['_portfolio_project']
                match = matches.get(key)
                conflicts = []
                if id_counts.get(invoice['id'], 0) != 1:
                    conflicts.append('duplicate_invoice_id')
                if composite_counts[(invoice['_portfolio_number'], key)] > 1:
                    conflicts.append('duplicate_invoice_project')
                if not invoice['_portfolio_number']:
                    conflicts.append('missing_invoice_number')
                if key in ambiguous:
                    conflicts.append('ambiguous_project_identity')
                excluded_status = invoice['payment_status'] in EXCLUDED_STATUSES
                balance = receivable_balance(invoice['invoice_amount'], invoice['actual_payment_received'])
                record = {
                    **{field: invoice[field] for field in (
                        'invoice_number', 'rad_project_no', 'project_id', 'project_name',
                        'company', 'account', 'category', 'payment_status',
                    )},
                    'id': str(invoice['id']) if id_counts.get(invoice['id']) == 1 else None,
                    'record_key': f'invoice-{index}', 'project_number': key,
                    'project_code': match['project_code'] if match else None,
                    'subproject_code': match['subproject_code'] if match else None,
                    'match_level': match['match_level'] if match else None,
                    'match_status': 'conflict' if conflicts else 'matched', 'conflict_codes': conflicts,
                    'currency': _key(invoice['currency']) or 'UNSPECIFIED',
                    **{field: _money(invoice[field]) for field in
                       ('invoice_amount', 'invoice_amount_aed', 'actual_payment_received')},
                    'calculated_receivable_balance': _money(balance),
                    **{field: invoice[field].isoformat() if invoice[field] else None
                       for field in ('invoice_date', 'due_date', 'payment_date')},
                    'excluded_from_totals': bool(conflicts or excluded_status),
                    'exclusion_reason': 'identity_conflict' if conflicts else invoice['payment_status'] if excluded_status else None,
                    'detail_route': f'{REGISTER_ROUTE}/{invoice["id"]}' if not conflicts else None,
                }
                records.append(record)
                if match:
                    if key not in groups:
                        groups[key] = {
                            **{field: value for field, value in match.items() if field != '_identity'},
                            'invoice_count': 0, 'matched_invoice_count': 0, 'conflict_count': 0,
                            'register_route': f'{REGISTER_ROUTE}?{urlencode({"queue": "all", "project_exact": key})}',
                        }
                    groups[key]['invoice_count'] += 1
                    groups[key]['conflict_count'] += bool(conflicts)
                    groups[key]['matched_invoice_count'] += not conflicts
                    if not conflicts and match['_identity']:
                        matched_identities.add(match['_identity'])
            totals = _totals(records)
            conflict_count = sum(bool(row['conflict_codes']) for row in records)
            updated = max((row['updated_at'] for row in invoices if row['updated_at']), default=None)
            partial = conflict_count or ambiguous or withheld or any(
                metric['status'] == 'partial' for total in totals for metric in total.values() if isinstance(metric, dict)
            )
            return {
                'status': 'partial' if partial else 'available',
                'source': {'mode': 'operational', 'label': 'Recorded outgoing invoices',
                           'route': f'{REGISTER_ROUTE}?queue=all', 'updated_at': updated.isoformat() if updated else None,
                           'as_of_date': timezone.localdate().isoformat(), 'date_basis': 'current_recorded_values',
                           'currency_conversion_applied': False},
                'coverage': {
                    'source_identity_count': len(identities), 'matched_source_identity_count': len(matched_identities),
                    'unmatched_source_identity_count': len(identities) - len(matched_identities),
                    'parent_only_project_count': sum(group['match_level'] == 'parent' for group in groups.values()),
                    'invoice_count': len(records), 'matched_invoice_count': len(records) - conflict_count,
                    'conflicting_invoice_count': conflict_count,
                    'excluded_invoice_count': sum(row['payment_status'] in EXCLUDED_STATUSES for row in records),
                    'withheld_parent_project_count': withheld, 'ambiguous_project_key_count': len(ambiguous),
                },
                'totals_by_currency': totals,
                'project_groups': [groups[key] for key in sorted(groups)],
                'rows': records[offset:offset + limit], 'total_rows': len(records),
                'offset': offset, 'limit': limit, 'returned_rows': len(records[offset:offset + limit]),
                'truncated': offset + limit < len(records), 'description': DESCRIPTION,
            }
    except Exception:
        logger.exception('Recorded portfolio invoices unavailable')
        return _empty('error', limit, offset, 'Recorded outgoing invoices could not be read. Try again later.')
