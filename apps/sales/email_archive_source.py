"""Bounded MIME validation shared by the local test agent and file handoff."""
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
import hashlib

from .email_content import project_email_body

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_BODY_CHARS = 1_000_000


class ArchiveError(Exception):
    """Static, non-sensitive diagnostic codes only."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def payload_from_eml(data, mailbox):
    if len(data) > MAX_FILE_BYTES:
        raise ArchiveError('email_too_large')
    message = BytesParser(policy=policy.default).parsebytes(data)
    if message.get('Subject') is None or message.get('From') is None:
        raise ArchiveError('not_original_eml')
    subject = str(message.get('Subject', ''))
    if 'RADAI-TEST' not in subject.upper():
        return None
    recipients = {address.casefold() for _, address in getaddresses(
        [str(value) for field in ('To', 'Cc') for value in message.get_all(field, [])])}
    if mailbox.casefold() not in recipients:
        raise ArchiveError('target_mailbox_not_in_recipients')
    name, sender = parseaddr(str(message.get('From', '')))
    if not sender or '@' not in sender or len(subject) > 500 or len(name) > 255:
        raise ArchiveError('unsupported_email_headers')
    # The message Date is a sent timestamp, not an authoritative received time.
    # Take the most recent Received header with an explicit timezone instead.
    received = None
    for header in message.get_all('Received', []):
        try:
            candidate = parsedate_to_datetime(str(header).rsplit(';', 1)[1].strip())
            if candidate.tzinfo is not None:
                received = candidate.astimezone(timezone.utc)
                break
        except (IndexError, ValueError, TypeError, OverflowError):
            continue
    if received is None:
        raise ArchiveError('received_timestamp_missing')
    part = message.get_body(preferencelist=('plain', 'html'))
    text = part.get_content() if part else ''
    if not str(text).strip():
        part = message.get_body(preferencelist=('html',))
        text = part.get_content() if part else ''
    if not isinstance(text, str) or len(text) > MAX_BODY_CHARS:
        raise ArchiveError('unsupported_or_oversized_body')
    if part and part.get_content_type() == 'text/html':
        text = project_email_body(text, 'html')['body_text']
    # Content + target mailbox gives the same source identity after renaming,
    # copying or an uncertain HTTP response. Changed bytes remain a new source.
    identity = digest(mailbox.casefold().encode() + b'\0' + data)
    message_id = str(message.get('Message-ID', ''))
    if len(message_id) > 512:
        raise ArchiveError('unsupported_message_id')
    return {
        'source_message_id': 'onedrive-test:' + identity,
        'internet_message_id': message_id,
        'subject': subject, 'sender_name': name, 'sender_email': sender,
        'received_at': received.isoformat(), 'body_preview': text,
        'has_attachments': any(item.get_content_disposition() == 'attachment'
                               and not item.is_multipart() for item in message.walk()),
    }
