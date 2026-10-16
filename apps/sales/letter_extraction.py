"""Passive recipient extraction for the authorized sales-letter-contact brief."""
import logging
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import PurePosixPath
from uuid import UUID
from itertools import chain

from django.db.models import F, Q
from apps.rbac.action_policy import module_action_allowed

from .email_content import project_email_body

logger = logging.getLogger(__name__)
LIMIT = 2_000_000
SOURCE_BYTES = 32 * 1024 * 1024
EMAIL = r'[A-Z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}'
FIELD_LABELS = (
    r'Contact(?: Person| Name)?|Client Contact|Contact person name|Recipient(?: Name)?|Attention|Attn\.?|Name|'
    r'Position|Job Title|Title|Designation|Company(?: Name)?|Organization|Organisation|'
    r'(?:Contact |Recipient )?E-?mail(?: Address)?|E|(?:Postal |Office |Company )?Address|'
    r'Telephone|Phone|Mobile|Fax|Website|Date|Sent|Subject|From|To|Cc|Message[ -]?ID|Sender'
)
EMAIL_HEADERS = r'(?:(?:From|To|Cc|Subject|Date|Sent|Message[ -]?ID|Sender):[^\n]*(?:\n|$))+'


def recipient_present(fields):
    """A company, mailbox or automated notification is not a named recipient."""
    name = str(fields.get('recipient_name') or '').strip()
    return bool(name and '@' not in name and re.search(r'[^\W\d_]', name)
                and not re.search(r'\b(?:notifications?|no[ -]?reply|do[ -]?not[ -]?reply|automated|system|sap ariba)\b', name, re.I))


def require_recipient(fields):
    from rest_framework.exceptions import ValidationError
    if not recipient_present(fields):
        raise ValidationError({'detail': 'Recipient is empty. No letter was generated. '
                               'Upload readable client correspondence containing a contact name, '
                               'or enter the recipient name before preparing the letter.',
                               'code': 'recipient_empty'})


def _label(text, labels):
    match = re.search(
        r'^[ \t]*(?:' + labels + r')[ \t]*(?:(?::|：|\t)[ \t]*(?:\n[ \t]*){0,3}|(?:\n[ \t]*){1,3})([^\n]+)',
        text, re.I | re.M)
    if not match:
        return ''
    value = match.group(1).strip()
    if re.match(r'^(?:' + FIELD_LABELS + r')[ \t]*(?::|：|\t|$)', value, re.I):
        return ''
    return value[:500]


def _address(text):
    labels = r'(?:Postal |Office |Company )?Address'
    value = _label(text, labels)
    if not value:
        return ''
    match = re.search(r'^[ \t]*(?:' + labels + r')[ \t]*(?::|：|\t|\n)', text, re.I | re.M)
    lines = text[match.end():].lstrip(' \t\n').splitlines()
    # HTML paragraphs project to doubled newlines, including between address lines.
    if '\n\n' in text and not re.search(r'(?<!\n)\n(?!\n)', text):
        lines = [line for line in lines if line.strip()]
    parts = []
    for line in lines[:5]:
        line = line.strip()
        if (not line or len(line) > 150
                or re.match(r'^(?:' + FIELD_LABELS + r')[ \t]*(?::|：|\t|$)', line, re.I)
                or re.search(r'@|https?://', line, re.I)):
            break
        parts.append(line)
    return ', '.join(parts)[:1000] or value


def extract_letter_fields(text, *, sender_name='', sender_email='', date=None, message_id=''):
    """Use explicit labels or a closing signature, never the To/Dear recipient."""
    text = text[:LIMIT].replace('\r\n', '\n').replace('\r', '\n').strip()
    if re.search(r'<(?:html|body|div|p|br|table)(?:\s|/?>)', text, re.I):
        text = project_email_body(text, 'html')['body_text']
    header_match = re.match(r'\A' + EMAIL_HEADERS, text, re.I)
    headers = header_match.group(0) if header_match else ''
    sender = _label(headers, r'From')
    if sender:
        name, address = parseaddr(sender)
        sender_name = name or (sender if '@' not in sender else '')
        sender_email = address if '@' in address else sender_email
    sender_email = sender_email or _label(headers, r'Sender')
    from .letter_defaults import get_letterhead_defaults
    internal_domain = parseaddr(get_letterhead_defaults()['email'])[1].rpartition('@')[2].casefold()
    sender_domain = sender_email.rpartition('@')[2].casefold()
    if internal_domain and sender_domain == internal_domain:
        for forwarded in re.finditer(r'(?im)^[ \t]*From:', text[len(headers):]):
            candidate = text[len(headers) + forwarded.start():].lstrip()
            block = re.match(EMAIL_HEADERS, candidate, re.I)
            if not block:
                continue
            original = block.group(0)
            _, original_email = parseaddr(_label(original, 'From'))
            original_domain = original_email.rpartition('@')[2].casefold()
            if (original_domain and original_domain != internal_domain
                    and _label(original, 'Date|Sent') and _label(original, 'Subject')):
                return extract_letter_fields(candidate)
    ariba = bool(re.search(r'\bariba\b|\bsap\s+ariba\b', text + ' ' + sender_name + ' ' + sender_email, re.I))
    # Quoted older correspondence must not supply a second person's signature.
    # Drop the initial header block first, then stop at any forwarded message.
    body = text[len(headers):]
    body = re.split(r'(?im)^\s*(?:From:|[- ]*Original Message[- ]*)', body, maxsplit=1)[0]
    signature = re.search(r'(?im)^\s*(?:kind regards|best regards|warm regards|regards|sincerely|yours sincerely|thanks and regards|best wishes|many thanks|thank you|thanks|best)[,! .]*\n', body)
    tail = body[signature.end():].strip() if signature else ''
    # Outlook signatures often have no closing. Only use a nearby sender name
    # as an anchor, never a greeting or an arbitrary name elsewhere in the body.
    if not tail and sender_name:
        anchor = re.search(r'(?im)^\s*' + re.escape(sender_name) + r'\s*$', body)
        if anchor:
            tail = body[anchor.start():].strip()
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    lines = [part.strip() for line in lines for part in re.split(r'\s+[|]\s+|\t+', line) if part.strip()]
    name = _label(body, r'Contact(?: Person| Name)?|Client Contact|Contact person name|Recipient(?: Name)?|Attention|Attn\.?|Name')
    if not name and lines and re.fullmatch(r'[\w .\x27-]{3,100}', lines[0]) and '@' not in lines[0]:
        name = lines[0]
    title = _label(body, r'Position|Job Title|Title|Designation')
    company = _label(body, r'Company(?: Name)?|Organization|Organisation')
    invitation = re.search(r'^\s*([^\n]{2,200}?)\s+has invited you to participate\b', body, re.I | re.M)
    if ariba and not company and invitation:
        company = invitation.group(1).strip()
    if len(lines) > 1 and not title and re.search(r'\b(?:manager|engineer|director|officer|specialist|buyer|head|lead|coordinator|procurement|purchasing|administrator|consultant)\b', lines[1], re.I):
        title = lines[1]
    if len(lines) > 2 and not company and title == lines[1] and not re.search(r'[:@]|https?://', lines[2], re.I):
        company = lines[2][:500]
    email = _label(body, r'(?:Contact |Recipient )?E-?mail(?: Address)?|E')
    email_match = re.search(EMAIL, email or tail, re.I)
    body_emails = list(dict.fromkeys(re.findall(EMAIL, body, re.I)))
    body_email = body_emails[0] if len(body_emails) == 1 else ''
    # Procurement notifications address the supplier in the greeting and may
    # contain the supplier's login email. The event-contact sentence identifies
    # the buyer contact; neither the greeting nor login is a recipient source.
    event_contact = re.search(
        r'\bcontact\s+([^\n:]{2,120}?)\s+(?:via|by)\s+(?:telephone|phone|e-?mail)\b'
        r'[\s\S]{0,240}?\b(?:at|:)\s*(' + EMAIL + r')', body, re.I)
    if event_contact:
        name = event_contact.group(1).strip()
        if not email_match:
            body_email = event_contact.group(2).rstrip('.')
    # Common procurement wording outside portal-specific templates.
    contact = re.search(r'\b(?:please\s+)?contact\s+([\w .\x27-]{2,100}?)\s*(?:at|on|:|\()\s*(' + EMAIL + r')', body, re.I)
    if contact and not name:
        name = contact.group(1).strip()
        if not email_match:
            body_email = contact.group(2)
    address = _address(body)
    if not address and tail:
        address_lines = [line for line in lines if re.search(r'\b(?:P\.?\s*O\.?\s*Box|street|road|floor|building)\b', line, re.I)]
        address = ', '.join(address_lines)[:1000]
    result = {
        'recipient_name': name or sender_name,
        'recipient_title': title,
        'recipient_company': company,
        'recipient_email': email_match.group(0) if email_match else (body_email or sender_email),
        'recipient_address': address,
    }
    raw_date = _label(headers, r'Date|Sent')
    if raw_date:
        try:
            date = parsedate_to_datetime(raw_date)
        except (ValueError, TypeError, OverflowError):
            for pattern in ('%A, %B %d, %Y %I:%M %p', '%A, %d %B %Y %I:%M %p',
                            '%d %B %Y %H:%M', '%A, %B %d, %Y %H:%M'):
                try:
                    date = datetime.strptime(raw_date, pattern)
                    break
                except ValueError:
                    continue
    message_id = _label(headers, r'Message[ -]?ID') or message_id
    if ariba:
        portal_id = re.search(r'\bsourcing site,[\s\S]{0,1500}?\bMessage ID\s*:\s*(MSG[A-Z0-9_-]+)\b', body, re.I)
        if portal_id:
            message_id = portal_id.group(1)
    if date:
        label = 'SAP Ariba email notifications' if ariba else (sender_name or 'Email notification')
        result['your_ref'] = f'{label} dated: {date:%d,%m,%Y}'
        if message_id:
            result['your_ref'] += f' ,Message ID :{message_id}'
    elif message_id:
        result['your_ref'] = f'Message ID :{message_id}'
    if not recipient_present(result):
        result.pop('recipient_name', None)
    return {key: value for key, value in result.items() if value}


def _eml_fields(stream):
    data = stream.read(SOURCE_BYTES + 1)
    if len(data) > SOURCE_BYTES:
        return {}
    message = BytesParser(policy=policy.default).parsebytes(data)
    body = message.get_body(preferencelist=('plain', 'html'))
    text = body.get_content() if body else ''
    if not str(text).strip():
        body = message.get_body(preferencelist=('html',))
        text = body.get_content() if body else ''
    if body and body.get_content_type() == 'text/html':
        text = project_email_body(text, 'html')['body_text']
    name, address = parseaddr(str(message.get('From', '')))
    headers = '\n'.join(f'{key}: {message[key]}' for key in ('Date', 'Message-ID', 'Subject') if message[key])
    return extract_letter_fields(headers + '\n\n' + str(text)[:LIMIT], sender_name=name, sender_email=address)


def extract_opportunity_letter_fields(deal, actor):
    """Read a bounded set of current files through existing export authorization."""
    from .analysis_content import extract_analysis_chunks
    from .private_attachments import download_private_file
    from .opportunity_workspace import download_workspace_file
    from .workspace_models import OpportunityWorkspaceUpload

    # Outgoing letters live beside incoming client correspondence. They must
    # never become recipient evidence for their own next preparation (or for
    # another letter type). Use saved upload identities, not filename guesses.
    generated_uploads = set()
    for data in deal.letters.values_list('custom_data', flat=True):
        if not isinstance(data, dict):
            continue
        attachments = (data or {}).get('attachments') or {}
        if not isinstance(attachments, dict) or not isinstance(attachments.get('files', []), list):
            continue
        for item in attachments.get('files', []):
            if not isinstance(item, dict) or not isinstance(item.get('file'), dict):
                continue
            file_id = item['file'].get('id', '')
            if isinstance(file_id, str) and file_id.startswith('radai-'):
                try:
                    generated_uploads.add(UUID(file_id[6:]))
                except ValueError:
                    continue
    uploads = OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=deal, status='ready',
        folder_key__in=('tender', 'correspondence'), size__lte=SOURCE_BYTES,
    ).exclude(Q(pk__in=generated_uploads) | Q(document__root_upload_id__in=generated_uploads)).filter(
        Q(document__isnull=True) | Q(document__head_upload_id=F('pk')),
    ).order_by('-created_at', '-pk')
    email_files = Q(name__iendswith='.eml') | Q(name__iendswith='.msg')
    # Prioritize incoming email across the complete inventory, rather than
    # cutting off the listing before a later uploaded email is considered.
    candidates = chain(uploads.filter(email_files).iterator(chunk_size=100),
                       uploads.exclude(email_files).iterator(chunk_size=100))
    for upload in candidates:
        suffix = PurePosixPath(upload.name.lower()).suffix
        if suffix not in {'.eml', '.msg', '.txt', '.pdf', '.docx', '.html', '.htm', '.md'}:
            continue
        try:
            if upload.provider == 'radai':
                stream, _ = download_private_file(deal, actor, upload.folder_key, 'radai-' + str(upload.pk))
            else:
                file_id = (upload.result or {}).get('id')
                if not file_id:
                    continue
                stream, _ = download_workspace_file(deal, actor, upload.folder_key, file_id)
            try:
                if suffix == '.eml':
                    fields = _eml_fields(stream)
                else:
                    chunks, _ = extract_analysis_chunks(stream, upload.name, upload.size)
                    fields = extract_letter_fields('\n'.join(text for _, text in chunks))
            finally:
                stream.close()
            if recipient_present(fields):
                # An unrelated intake cannot provide this file's missing date.
                fields.setdefault('your_ref', '')
                fields['_source'] = {
                    'upload_id': str(upload.pk), 'sha256': upload.sha256,
                    'name': upload.name, 'version': upload.version_number,
                    'document_id': str(upload.document_id) if upload.document_id else None,
                    'folder': upload.folder_key,
                }
                return fields
        except Exception:
            # Permission, corrupt content and unavailable storage are optional
            # extraction failures. Never log source contents or provider errors.
            logger.info('Letter source unavailable for upload %s', upload.pk)
    from .email_permissions import visible_email_intakes
    if not module_action_allowed(actor, 'sales_email_intake', 'read'):
        return {}
    intakes = visible_email_intakes(actor).filter(opportunity=deal).order_by('-received_at', '-pk')
    for intake in intakes.iterator(chunk_size=100):
        fields = extract_letter_fields(
            intake.body_preview, sender_name=intake.sender_name, sender_email=intake.sender_email,
            date=intake.sent_at or intake.received_at,
            message_id=intake.internet_message_id or intake.source_message_id,
        )
        if recipient_present(fields):
            fields['_source'] = {'intake_id': str(intake.pk)}
            return fields
    return {}
