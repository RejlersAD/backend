"""Private, immutable attachment retention for imported email evidence."""

import hashlib
import re
from io import BytesIO
from uuid import uuid4
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from django.core.files.base import ContentFile
from rest_framework.exceptions import ValidationError

from ..attachment_storage import attachment_storage
from ..email_archive_source import MAX_FILE_BYTES, payload_from_eml
from ..models import SalesEmailIntakeAttachment
from ..analysis_content import extract_analysis_chunks
from ..email_content import project_email_body

MAX_ATTACHMENTS = 20
MAX_TOTAL_TEXT = 1_000_000


def safe_filename(value, index):
    name = str(value or f'Attachment_{index}.bin').replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f"*:<>?/\\|]', '_', name).strip(' .')
    if not name or len(name) > 255:
        raise ValidationError({'original_eml': 'An attachment filename needs review.'})
    return name


def ingest_original_eml_attachments(intake, mailbox, raw):
    """Validate source identity, retain direct MIME parts privately and index text."""
    if len(raw) > MAX_FILE_BYTES:
        raise ValidationError({'original_eml': 'The original email exceeds the 32 MiB limit.'})
    if intake.source_message_id.startswith('onedrive-test:'):
        try:
            payload = payload_from_eml(raw, mailbox)
        except Exception:
            raise ValidationError({'original_eml': 'A valid original test email is required.'}) from None
        if not payload or payload['source_message_id'] != intake.source_message_id or any(
            str(payload[key]).strip() != str(getattr(intake, key)).strip()
            for key in ('subject', 'sender_name', 'sender_email', 'internet_message_id', 'body_preview')
        ):
            raise ValidationError({'original_eml': 'The original email does not match this intake.'})
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not intake.source_message_id.startswith('onedrive-test:'):
        name, sender = parseaddr(str(message.get('From', '')))
        recipients = {address.casefold() for _, address in getaddresses(
            [str(value) for key in ('To', 'Cc') for value in message.get_all(key, [])])}
        body = message.get_body(preferencelist=('plain', 'html'))
        body_text = str(body.get_content()) if body else ''
        if body and body.get_content_type() == 'text/html':
            body_text = project_email_body(body_text, 'html')['body_text']
        normalize = lambda value: re.sub(r'\s+', ' ', str(value or '')).strip()
        if (not mailbox or mailbox.casefold() not in recipients or sender.casefold() != intake.sender_email.casefold()
                or normalize(name) != normalize(intake.sender_name)
                or normalize(message.get('Subject')) != normalize(intake.subject)
                or normalize(message.get('Message-ID')) != normalize(intake.internet_message_id)
                or normalize(body_text) != normalize(intake.body_preview)):
            raise ValidationError({'original_eml': 'The original email does not match this intake.'})
    parts = [(index, part) for index, part in enumerate(message.iter_attachments(), 1)
             if part.get_content_disposition() == 'attachment' and not part.is_multipart()]
    if len(parts) > MAX_ATTACHMENTS:
        raise ValidationError({'original_eml': 'At most 20 direct attachments are supported.'})
    storage, fingerprint = attachment_storage()
    saved = []
    text_remaining = MAX_TOTAL_TEXT
    for index, part in parts:
        content = part.get_payload(decode=True)
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValidationError({'original_eml': 'An attachment is empty or exceeds the 32 MiB file limit.'})
        name = safe_filename(part.get_filename(), index)
        digest = hashlib.sha256(content).hexdigest()
        previous = SalesEmailIntakeAttachment.objects.filter(intake=intake, source_part_index=index).first()
        if previous:
            if previous.sha256 != digest:
                raise ValidationError({'original_eml': 'An attachment changed for an already imported email.'})
            saved.append(previous)
            continue
        row_id = uuid4()
        key = f'sales-email-intake-attachments/{intake.pk}/{row_id}/original'
        try:
            chunks, diagnostic = extract_analysis_chunks(BytesIO(content), name, len(content))
        except Exception:
            chunks, diagnostic = [], 'invalid_document'
        bounded_chunks = []
        for location, text in chunks:
            if text_remaining <= 0:
                diagnostic = diagnostic or 'attachment_text_budget'
                break
            piece = text[:text_remaining]
            bounded_chunks.append({'location': location, 'text': piece})
            text_remaining -= len(piece)
            if len(piece) < len(text):
                diagnostic = 'attachment_text_budget'
                break
        stored_name = storage.save(key, ContentFile(content))
        try:
            row = SalesEmailIntakeAttachment.objects.create(
                id=row_id, intake=intake, source_part_index=index, name=name,
                mime_type=str(part.get_content_type() or '')[:255], size=len(content), sha256=digest,
                storage_name=stored_name, storage_fingerprint=fingerprint,
                text_chunks=bounded_chunks,
                extraction_status='read' if bounded_chunks and not diagnostic else 'partial' if bounded_chunks else 'unread',
                extraction_diagnostic=diagnostic,
            )
        except Exception:
            storage.delete(stored_name)
            raise
        saved.append(row)
    return saved


def read_attachment(row):
    storage, fingerprint = attachment_storage()
    expected = f'sales-email-intake-attachments/{row.intake_id}/{row.pk}/original'
    if fingerprint != row.storage_fingerprint or row.storage_name != expected:
        raise OSError('email_attachment_integrity_failed')
    with storage.open(row.storage_name, 'rb') as source:
        content = source.read(MAX_FILE_BYTES + 1)
    if len(content) != row.size or hashlib.sha256(content).hexdigest() != row.sha256:
        raise OSError('email_attachment_integrity_failed')
    return BytesIO(content)


def attachment_projection(row):
    return {
        'id': str(row.pk), 'name': row.name, 'size': row.size,
        'mime_type': row.mime_type, 'sha256': row.sha256,
        'extraction_status': row.extraction_status,
        'extraction_diagnostic': row.extraction_diagnostic,
        'download_url': f'/api/v1/sales/email-intakes/{row.intake_id}/attachments/{row.pk}/',
    }
