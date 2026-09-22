"""Review and archive physical duplicate IDs without touching workbook facts.

Normal ORM operations assume IDs are unique. This service instead identifies
physical rows, binds a review to their complete contents, and deletes only the
reviewed extras while preserving the selected row and its shared public ID.
"""
import base64
import hashlib
import json

from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from apps.invoice_tracker.models import (
    CustomerInvoice, InvoiceAttachment, InvoiceDuplicateResolution,
)


REVIEW_TOKEN_MAX_AGE = 15 * 60
MAX_BULK_GROUPS = 50
TOKEN_SALT = 'invoice_tracker.physical_duplicate_review.v1'
DISPLAY_FIELDS = (
    'invoice_number', 'company', 'account', 'rad_project_no', 'project_name',
    'invoice_date', 'due_date', 'payment_status', 'currency', 'invoice_amount',
    'invoice_amount_aed', 'balance_to_be_received', 'actual_payment_received',
    'created_at', 'updated_at',
)
MONEY_FIELDS = {
    'invoice_amount', 'invoice_amount_aed', 'balance_to_be_received', 'actual_payment_received',
}


class DuplicateReviewError(Exception):
    def __init__(self, detail, status_code=400, *, stale_invoice_ids=None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.stale_invoice_ids = sorted(set(stale_invoice_ids)) if stale_invoice_ids is not None else None


class _ArchiveEncoder(DjangoJSONEncoder):
    def default(self, value):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {'type': 'binary', 'base64': base64.b64encode(bytes(value)).decode('ascii')}
        return super().default(value)


def _json(value):
    return json.dumps(value, cls=_ArchiveEncoder, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _actor(user):
    if not getattr(user, 'is_authenticated', False) or getattr(user, 'pk', None) is None:
        raise DuplicateReviewError('Authentication is required to review duplicate invoices.', 403)
    return str(user.pk)


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DuplicateReviewError(f'{label} must be an integer.')
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise DuplicateReviewError(f'{label} must be an integer.') from None
    if result < minimum or result > maximum:
        raise DuplicateReviewError(f'{label} is outside the supported range.')
    return result


def _check_backend():
    if connection.vendor not in {'postgresql', 'sqlite'}:
        raise DuplicateReviewError('Duplicate invoice review is unavailable for this database.', 503)


def _table_identity():
    return f'{connection.alias}:{connection.vendor}:{CustomerInvoice._meta.db_table}'


def _read_records(invoice_id, *, lock=False):
    table = connection.ops.quote_name(CustomerInvoice._meta.db_table)
    if connection.vendor == 'postgresql':
        locator = 'i.ctid::text, i.xmin::text, i.tableoid::text'
    else:
        locator = 'i.rowid, NULL, NULL'
    suffix = ' FOR UPDATE' if lock and connection.vendor == 'postgresql' else ''
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {locator}, i.* FROM {table} i WHERE i.id = %s{suffix}', [invoice_id])
        names = [column[0] for column in cursor.description][3:]
        records = []
        for row in cursor.fetchall():
            # Capture every physical column, including fields left by old imports
            # that are absent from Django's current model definition.
            data = json.loads(_json(dict(zip(names, row[3:]))))
            physical = {'locator': str(row[0]), 'version': row[1], 'table_oid': row[2], 'data': data}
            physical['fingerprint'] = _digest(physical)
            records.append(physical)
    return sorted(records, key=lambda item: item['fingerprint'])


def _group_digest(invoice_id, records):
    return _digest({'table': _table_identity(), 'invoice_id': invoice_id,
                    'members': [record['fingerprint'] for record in records]})


def _token(kind, actor, invoice_id, fingerprint, record=None):
    payload = {'v': 1, 'kind': kind, 'actor': actor, 'invoice_id': invoice_id,
               'fingerprint': fingerprint, 'table': _table_identity()}
    if record is not None:
        payload['record'] = record
    return signing.dumps(payload, salt=TOKEN_SALT, compress=True)


def _decode(token, kind, actor):
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise DuplicateReviewError('A valid duplicate review token is required.')
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=REVIEW_TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise DuplicateReviewError('This duplicate review has expired. Refresh the review and try again.', 409) from None
    except (signing.BadSignature, ValueError, TypeError):
        raise DuplicateReviewError('The duplicate review token is invalid.') from None
    if not isinstance(payload, dict) or payload.get('v') != 1 or payload.get('kind') != kind:
        raise DuplicateReviewError('The duplicate review token is invalid.')
    if payload.get('actor') != actor:
        raise DuplicateReviewError('This duplicate review belongs to another user.', 403)
    if payload.get('table') != _table_identity():
        raise DuplicateReviewError('The invoice source changed. Refresh the duplicate review.', 409)
    return payload


def list_duplicate_invoices(user, invoice_id=None, page=1, page_size=20):
    """Preview duplicate physical IDs, never repeated workbook invoice numbers."""
    actor = _actor(user)
    _check_backend()
    page = _integer(page, 'page', 1, 2147483647)
    page_size = _integer(page_size, 'page_size', 1, 100)
    table = connection.ops.quote_name(CustomerInvoice._meta.db_table)
    where, params = 'id IS NOT NULL', []
    if invoice_id is not None:
        invoice_id = _integer(invoice_id, 'invoice_id', 1, 9223372036854775807)
        where, params = 'id = %s', [invoice_id]
    grouped = f'SELECT id FROM {table} WHERE {where} GROUP BY id HAVING COUNT(*) > 1'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM ({grouped}) duplicate_groups', params)
        total = cursor.fetchone()[0]
        cursor.execute(f'{grouped} ORDER BY id LIMIT %s OFFSET %s',
                       [*params, page_size, (page - 1) * page_size])
        identities = [row[0] for row in cursor.fetchall()]
    groups = []
    for identity in identities:
        records = _read_records(identity)
        if len(records) < 2:
            # Another completed resolution can disappear between preview reads.
            continue
        fingerprint = _group_digest(identity, records)
        display = []
        for record in records:
            fields = {key: record['data'].get(key) for key in DISPLAY_FIELDS}
            for key in MONEY_FIELDS:
                if fields[key] is not None:
                    fields[key] = str(fields[key])
            display.append({**fields, 'record_key': record['fingerprint'], 'record_token': _token(
                'record', actor, identity, fingerprint, record['fingerprint'])})
        groups.append({
            'invoice_id': identity, 'record_count': len(records),
            'identical': len({_digest(record['data']) for record in records}) == 1,
            'review_key': fingerprint,
            'group_token': _token('group', actor, identity, fingerprint), 'records': display,
            'attachments_count': InvoiceAttachment.objects.filter(invoice_id=identity).count(),
        })
    return {'schema_version': '1.0',
            'pagination': {'page': page, 'page_size': page_size, 'total_groups': total,
                           'has_next': page * page_size < total},
            'groups': groups}


def _lock_invoice_writes():
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            table = connection.ops.quote_name(CustomerInvoice._meta.db_table)
            # Row locks cannot exclude a newly inserted duplicate. This lock also
            # serializes resolutions and blocks all concurrent invoice writes
            # until the archive and exact physical deletes commit together.
            cursor.execute(f'LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE')
        else:
            # SQLite reserves its database write lock before the invoice reread.
            # Use the independent archive table so broken legacy invoice keys
            # cannot affect acquisition of the lock; no audit row is changed.
            table = connection.ops.quote_name(InvoiceDuplicateResolution._meta.db_table)
            cursor.execute(f'UPDATE {table} SET id = id WHERE 1 = 0')


def _decode_selection(selection, actor):
    if not isinstance(selection, dict):
        raise DuplicateReviewError('Each duplicate review must select a record to retain.')
    group = _decode(selection.get('group_token'), 'group', actor)
    keep = _decode(selection.get('keep_token'), 'record', actor)
    if (keep.get('invoice_id') != group.get('invoice_id')
            or keep.get('fingerprint') != group.get('fingerprint')):
        raise DuplicateReviewError('Select a record from this duplicate review to retain.')
    return {'invoice_id': _integer(group.get('invoice_id'), 'invoice_id', 1, 9223372036854775807),
            'fingerprint': group.get('fingerprint'), 'record': keep.get('record')}


def resolve_duplicate_invoice_groups(user, selections):
    """Resolve reviewed groups together; any invalid or failed group rolls back all."""
    actor = _actor(user)
    _check_backend()
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_BULK_GROUPS:
        raise DuplicateReviewError(f'Select between 1 and {MAX_BULK_GROUPS} duplicate invoice groups.')
    decoded = [_decode_selection(selection, actor) for selection in selections]
    identities = [selection['invoice_id'] for selection in decoded]
    if len(set(identities)) != len(identities):
        raise DuplicateReviewError('Each invoice ID can appear only once in a bulk review.')
    with transaction.atomic():
        _lock_invoice_writes()
        # A lock wait must not extend a signed review's lifetime. Decode every
        # pair again before reading rows, while keeping all writes behind the
        # full batch's token and physical-membership validation.
        decoded = [_decode_selection(selection, actor) for selection in selections]
        prepared = {}
        stale = []
        for selection in sorted(decoded, key=lambda item: item['invoice_id']):
            invoice_id = selection['invoice_id']
            records = _read_records(invoice_id, lock=True)
            if len(records) < 2 or _group_digest(invoice_id, records) != selection['fingerprint']:
                stale.append(invoice_id)
                continue
            selected = [record for record in records if record['fingerprint'] == selection['record']]
            if len(selected) != 1:
                raise DuplicateReviewError('Select exactly one reviewed invoice record to retain.')
            retained = selected[0]
            prepared[invoice_id] = {'retained': retained,
                                    'removed': [record for record in records if record is not retained]}
        if stale:
            raise DuplicateReviewError(
                'These invoice records changed. Refresh the duplicate review before deleting.', 409,
                stale_invoice_ids=stale,
            )
        # Archive every group before deleting any extras. Both phases share one
        # transaction, so an archive or verification failure leaves no partial
        # resolutions or audit entries behind.
        for invoice_id in identities:
            group = prepared[invoice_id]
            archive = InvoiceDuplicateResolution.objects.create(
                invoice_id=invoice_id, actor_id=actor, retained_record=group['retained']['data'],
                removed_records=[record['data'] for record in group['removed']],
            )
            group['archive_id'] = str(archive.pk)
        table = connection.ops.quote_name(CustomerInvoice._meta.db_table)
        with connection.cursor() as cursor:
            for invoice_id in identities:
                for record in prepared[invoice_id]['removed']:
                    if connection.vendor == 'postgresql':
                        cursor.execute(f'DELETE FROM {table} WHERE id = %s AND ctid = %s::tid AND tableoid = %s::oid',
                                       [invoice_id, record['locator'], record['table_oid']])
                    else:
                        cursor.execute(f'DELETE FROM {table} WHERE id = %s AND rowid = %s',
                                       [invoice_id, int(record['locator'])])
                    if cursor.rowcount != 1:
                        raise DuplicateReviewError(
                            'The invoice records changed during resolution. Nothing was deleted.', 409,
                            stale_invoice_ids=[invoice_id],
                        )
        for invoice_id in identities:
            remaining = _read_records(invoice_id)
            if len(remaining) != 1 or remaining[0]['fingerprint'] != prepared[invoice_id]['retained']['fingerprint']:
                raise DuplicateReviewError(
                    'The retained invoice could not be verified. Nothing was deleted.', 409,
                    stale_invoice_ids=[invoice_id],
                )
    results = [
        {'invoice_id': invoice_id, 'removed_count': len(prepared[invoice_id]['removed']),
         'kept_invoice_number': prepared[invoice_id]['retained']['data'].get('invoice_number'),
         'archive_id': prepared[invoice_id]['archive_id']}
        for invoice_id in identities
    ]
    return {'resolved_count': len(results), 'removed_count': sum(item['removed_count'] for item in results),
            'results': results}


def resolve_duplicate_invoices(user, group_token, keep_token):
    """Keep the single-review contract on the same atomic resolution path."""
    return resolve_duplicate_invoice_groups(
        user, [{'group_token': group_token, 'keep_token': keep_token}],
    )['results'][0]
