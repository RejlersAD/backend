"""Original PO uploads, scoped to their saved order rather than public URLs."""

from pathlib import PurePosixPath


def is_safe_source_storage_key(key):
    """Accept only normalized relative storage keys, never URLs or local paths."""
    return (isinstance(key, str) and bool(key) and key == key.strip()
            and not any(value in key for value in ('\\', ':', '%')) and not key.startswith('/')
            and not any(part in ('', '.', '..') for part in key.split('/'))
            and str(PurePosixPath(key)) == key)


def uploaded_purchase_order_sources(order):
    """Return source metadata and private storage keys without regenerating PDFs.

    Confirmed document links are authoritative. Older, explicitly signed PO
    attachments may also carry a storage key under this order's upload folder.
    Do not treat supplier quotations, PR PDFs or generated exports as originals.
    """
    sources = []
    document_ids = set()
    storage_keys = set()
    for document in order.source_documents.filter(
        document_type__in=('purchase_order', 'unknown'),
    ).only('id', 'original_filename', 's3_key', 'created_at').order_by('-created_at', '-id'):
        document_ids.add(str(document.pk))
        if document.s3_key and document.s3_key in storage_keys:
            continue
        storage_keys.add(document.s3_key)
        sources.append({
            'id': str(document.pk),
            'filename': document.original_filename,
            'uploaded_at': document.created_at.isoformat(),
            'storage_key': document.s3_key,
        })

    order_prefix = f'procurement/orders/{order.po_number}/'
    for index, attachment in enumerate(order.attachments or []):
        if not isinstance(attachment, dict) or attachment.get('type') != 'signed_purchase_order_pdf':
            continue
        # A document UUID must resolve through the confirmed order relation.
        # Never let an editable attachment point at another order's document.
        if attachment.get('document_id'):
            if str(attachment['document_id']) in document_ids:
                continue
            # Preserve a missing legacy source as an unavailable entry so the
            # UI can report it, without exposing another document's bytes.
            key = ''
        else:
            key = str(attachment.get('s3_key') or '').strip()
            if not key.startswith(order_prefix) or '\\' in key or '..' in PurePosixPath(key).parts:
                key = ''
        if key and key in storage_keys:
            continue
        storage_keys.add(key)
        sources.append({
            'id': f'attachment-{index}',
            'filename': str(attachment.get('filename') or attachment.get('name') or 'Uploaded purchase order.pdf'),
            'uploaded_at': attachment.get('uploaded_at') or None,
            'storage_key': key,
        })
    return sources
