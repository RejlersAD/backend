"""Validation and normalization for Purchase Requisition input."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
import zipfile

from rest_framework.exceptions import ValidationError


MAX_LINE_ITEMS = 100
MAX_ATTACHMENT_COUNT_PER_REQUEST = 10
MAX_ATTACHMENT_COUNT_PER_REQUISITION = 20
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_SIZE = 50 * 1024 * 1024

ALLOWED_ATTACHMENT_TYPES = {
    '.pdf': {'application/pdf'},
    '.png': {'image/png'},
    '.jpg': {'image/jpeg'},
    '.jpeg': {'image/jpeg'},
    '.doc': {'application/msword'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    '.xls': {'application/vnd.ms-excel'},
    '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
}
GENERIC_MIME_TYPES = {'', 'application/octet-stream'}
MONEY_QUANTUM = Decimal('0.01')


def _decimal(value, label, *, minimum=None, maximum=None, decimal_places=4):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f'{label} must be a valid number.')
    if not number.is_finite():
        raise ValidationError(f'{label} must be finite.')
    if minimum is not None and number < minimum:
        raise ValidationError(f'{label} must be at least {minimum}.')
    if maximum is not None and number > maximum:
        raise ValidationError(f'{label} cannot exceed {maximum}.')
    quantum = Decimal(1).scaleb(-decimal_places)
    try:
        return number.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValidationError(f'{label} is outside the supported numeric range.')


def normalize_line_items(value):
    """Validate a bounded JSON line-item list and return one canonical schema."""
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValidationError('Line items must be a list.')
    if len(value) > MAX_LINE_ITEMS:
        raise ValidationError(f'No more than {MAX_LINE_ITEMS} line items are allowed.')

    normalized = []
    for index, raw_item in enumerate(value, start=1):
        if not isinstance(raw_item, dict):
            raise ValidationError(f'Line item {index} must be an object.')

        description = str(
            raw_item.get('description') or raw_item.get('item') or raw_item.get('name') or ''
        ).strip()
        raw_quantity = raw_item.get('quantity', raw_item.get('qty', 1))
        raw_unit_price = raw_item.get('unit_price', raw_item.get('price', 0))

        # A completely blank row from the UI is not persisted as a line item.
        if not description and raw_quantity in ('', None, 1, '1') and raw_unit_price in ('', None, 0, '0'):
            continue
        if not description:
            raise ValidationError(f'Line item {index} requires a description.')
        if len(description) > 500:
            raise ValidationError(f'Line item {index} description cannot exceed 500 characters.')

        quantity = _decimal(
            raw_quantity,
            f'Line item {index} quantity',
            minimum=Decimal('0.0001'),
            maximum=Decimal('1000000000'),
        )
        unit_price = _decimal(
            raw_unit_price,
            f'Line item {index} unit price',
            minimum=Decimal('0'),
            maximum=Decimal('9999999999999.99'),
            decimal_places=2,
        )
        calculated_total = (quantity * unit_price).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if calculated_total > Decimal('9999999999999.99'):
            raise ValidationError(f'Line item {index} total exceeds the supported monetary range.')

        supplied_total = raw_item.get('total', raw_item.get('line_total'))
        if supplied_total not in (None, ''):
            supplied_total = _decimal(
                supplied_total,
                f'Line item {index} total',
                minimum=Decimal('0'),
                decimal_places=2,
            )
            if supplied_total != calculated_total:
                raise ValidationError(
                    f'Line item {index} total must equal quantity multiplied by unit price.'
                )

        unit = str(raw_item.get('unit') or raw_item.get('uom') or 'EA').strip() or 'EA'
        if len(unit) > 30:
            raise ValidationError(f'Line item {index} unit cannot exceed 30 characters.')

        normalized_item = {
            'description': description,
            'quantity': format(quantity.normalize(), 'f'),
            'unit': unit,
            'unit_price': format(unit_price, '.2f'),
            'total': format(calculated_total, '.2f'),
        }
        code = str(raw_item.get('code') or raw_item.get('sku') or '').strip()
        if code:
            if len(code) > 100:
                raise ValidationError(f'Line item {index} code cannot exceed 100 characters.')
            normalized_item['code'] = code
        normalized.append(normalized_item)

    return normalized


def line_items_total(items):
    return sum((Decimal(item['total']) for item in items), Decimal('0.00'))


def sanitize_attachment_name(name):
    """Remove path traversal and unsafe object-key characters."""
    basename = Path(str(name or '').replace('\\', '/')).name
    stem = re.sub(r'[^A-Za-z0-9._-]+', '_', basename).strip('._')
    return stem[:180] or 'attachment'


def _validate_magic(upload, extension):
    upload.seek(0)
    header = upload.read(16)
    upload.seek(0)

    if extension == '.pdf' and not header.startswith(b'%PDF-'):
        return False
    if extension == '.png' and not header.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    if extension in {'.jpg', '.jpeg'} and not header.startswith(b'\xff\xd8\xff'):
        return False
    if extension in {'.doc', '.xls'} and not header.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return False
    if extension in {'.docx', '.xlsx'}:
        if not header.startswith(b'PK\x03\x04'):
            return False
        try:
            with zipfile.ZipFile(upload) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                    return False
                names = {entry.filename for entry in entries}
                expected_prefix = 'word/' if extension == '.docx' else 'xl/'
                if '[Content_Types].xml' not in names or not any(
                    name.startswith(expected_prefix) for name in names
                ):
                    return False
        except (OSError, zipfile.BadZipFile):
            return False
        finally:
            upload.seek(0)
    return True


def validate_attachments(files, existing_attachments=None):
    """Validate size, extension, MIME declaration, and file signature."""
    files = list(files or [])
    existing_attachments = list(existing_attachments or [])
    if len(files) > MAX_ATTACHMENT_COUNT_PER_REQUEST:
        raise ValidationError(
            f'No more than {MAX_ATTACHMENT_COUNT_PER_REQUEST} files may be uploaded at once.'
        )
    if len(existing_attachments) + len(files) > MAX_ATTACHMENT_COUNT_PER_REQUISITION:
        raise ValidationError(
            f'A requisition may contain at most {MAX_ATTACHMENT_COUNT_PER_REQUISITION} attachments.'
        )

    existing_size = sum(int(item.get('file_size') or 0) for item in existing_attachments if isinstance(item, dict))
    new_size = 0
    validated = []
    for upload in files:
        safe_name = sanitize_attachment_name(getattr(upload, 'name', ''))
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_ATTACHMENT_TYPES:
            raise ValidationError(f'{safe_name}: file type is not allowed.')
        size = int(getattr(upload, 'size', 0) or 0)
        if size <= 0:
            raise ValidationError(f'{safe_name}: empty files are not allowed.')
        if size > MAX_ATTACHMENT_SIZE:
            raise ValidationError(f'{safe_name}: file exceeds the 10 MB limit.')

        declared_mime = str(getattr(upload, 'content_type', '') or '').split(';', 1)[0].strip().lower()
        if declared_mime not in ALLOWED_ATTACHMENT_TYPES[extension] | GENERIC_MIME_TYPES:
            raise ValidationError(f'{safe_name}: content type does not match its extension.')
        if not _validate_magic(upload, extension):
            raise ValidationError(f'{safe_name}: file contents do not match its extension.')

        upload.safe_name = safe_name
        upload.verified_content_type = next(iter(ALLOWED_ATTACHMENT_TYPES[extension]))
        new_size += size
        validated.append(upload)

    if existing_size + new_size > MAX_ATTACHMENT_TOTAL_SIZE:
        raise ValidationError('Attachment storage for one requisition cannot exceed 50 MB.')
    return validated
