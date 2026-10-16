"""Passive recipient extraction for the authorized sales-letter-contact brief."""
import logging
import re
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import PurePosixPath

from django.db.models import F, Q
from apps.rbac.action_policy import module_action_allowed

from .letter_context import normalize_body_text

logger = logging.getLogger(__name__)
LIMIT = 20_000
EMAIL = r'[A-Z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}'


def _label(text, labels):
    match = re.search(r'^(?:' + labels + r')\s*:\s*([^\n]+)', text, re.I | re.M)
    return match.group(1).strip()[:500] if match else ''


def extract_letter_fields(text, *, sender_name='', sender_email='', date=None, message_id=''):
    """Use explicit labels or a closing signature, never the To/Dear recipient."""
    text = text[:LIMIT].replace('\r\n', '\n').replace('\r', '\n')
    if re.search(r'<(?:html|body|div|p|br)(?:\s|/?>)', text, re.I):
        text = normalize_body_text(text)
    header_match = re.match(r'\A(?:(?:From|To|Cc|Subject|Date|Sent|Message[ -]?ID|Sender):[^\n]*(?:\n|$))+', text, re.I)
    headers = header_match.group(0) if header_match else ''
    sender = _label(headers, r'From')
    if sender:
        name, address = parseaddr(sender)
        sender_name = name or (sender if '@' not in sender else '')
        sender_email = address if '@' in address else sender_email
    sender_email = sender_email or _label(headers, r'Sender')
    ariba = bool(re.search(r'\bariba\b|\bsap\s+ariba\b', text + ' ' + sender_name + ' ' + sender_email, re.I))
    # Quoted older correspondence must not supply a second person's signature.
    # Drop the initial header block first, then stop at any forwarded message.
    body = text[len(headers):]
    body = re.split(r'(?im)^\s*(?:From:|[- ]*Original Message[- ]*)', body, maxsplit=1)[0]
    signature = re.search(r'(?im)^\s*(?:kind regards|best regards|regards|sincerely|thanks and regards)[,! .]*\n', body)
    tail = body[signature.end():].strip() if signature else ''
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    name = _label(body, r'Contact(?: Person| Name)?|Client Contact|Name')
    if not name and lines and re.fullmatch(r'[\w .\x27-]{3,100}', lines[0]) and '@' not in lines[0]:
        name = lines[0]
    title = _label(body, r'Position|Job Title|Title|Designation')
    company = _label(body, r'Company(?: Name)?|Organization|Organisation')
    invitation = re.search(r'^\s*([^\n]{2,200}?)\s+has invited you to participate\b', body, re.I | re.M)
    if ariba and not company and invitation:
        company = invitation.group(1).strip()
    if signature and len(lines) > 1 and not title and re.search(r'\b(?:manager|engineer|director|officer|specialist|buyer|head|lead|coordinator)\b', lines[1], re.I):
        title = lines[1]
    if signature and len(lines) > 2 and not company and title == lines[1] and not re.search(r'[:@]|https?://', lines[2], re.I):
        company = lines[2][:500]
    email = _label(body, r'(?:Contact )?E-?mail(?: Address)?')
    email_match = re.search(EMAIL, email or tail, re.I)
    body_emails = list(dict.fromkeys(re.findall(EMAIL, body, re.I)))
    body_email = body_emails[0] if len(body_emails) == 1 else ''
    # Procurement notifications address the supplier in the greeting and may
    # contain the supplier's login email. The event-contact sentence identifies
    # the buyer contact; neither the greeting nor login is a recipient source.
    event_contact = re.search(
        r'\bcontact\s+([^\n:]{2,120}?)\s+(?:via|by)\s+(?:telephone|phone|e-?mail)\b'
        r'[^\n]{0,240}?\b(?:at|:)\s*(' + EMAIL + r')', body, re.I)
    if event_contact:
        if not name:
            name = event_contact.group(1).strip()
        if not email_match:
            body_email = event_contact.group(2).rstrip('.')
    address = _label(body, r'(?:Postal |Office |Company )?Address')
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
            pass
    message_id = _label(headers, r'Message[ -]?ID') or message_id
    if ariba:
        portal_id = re.search(r'\bsourcing site,[^\n]{0,1500}?\bMessage ID\s*:\s*(MSG[A-Z0-9_-]+)\b', body, re.I)
        if portal_id:
            message_id = portal_id.group(1)
    if date:
        label = 'SAP Ariba email notifications' if ariba else (sender_name or 'Email notification')
        result['your_ref'] = f'{label} dated: {date:%d,%m,%Y}'
        if message_id:
            result['your_ref'] += f' ,Message ID :{message_id}'
    elif message_id:
        result['your_ref'] = f'Message ID :{message_id}'
    return {key: value for key, value in result.items() if value}


def _eml_fields(stream):
    data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        return {}
    message = BytesParser(policy=policy.default).parsebytes(data)
    body = message.get_body(preferencelist=('plain', 'html'))
    text = body.get_content() if body else ''
    if body and body.get_content_type() == 'text/html':
        text = normalize_body_text(text)
    name, address = parseaddr(str(message.get('From', '')))
    headers = '\n'.join(f'{key}: {message[key]}' for key in ('Date', 'Message-ID', 'Subject') if message[key])
    return extract_letter_fields(headers + '\n\n' + str(text)[:LIMIT], sender_name=name, sender_email=address)


def extract_opportunity_letter_fields(deal, actor):
    """Read a bounded set of current files through existing export authorization."""
    from .document_classification_content import extract_document_text
    from .private_attachments import download_private_file
    from .workspace_models import OpportunityWorkspaceUpload

    uploads = OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=deal, provider='radai', status='ready',
        folder_key__in=('tender', 'correspondence'), size__lte=4 * 1024 * 1024,
    ).filter(Q(document__isnull=True) | Q(document__head_upload_id=F('pk'))).order_by('created_at', 'pk')
    candidates = list(uploads[:20])
    candidates.sort(key=lambda upload: PurePosixPath(upload.name.lower()).suffix not in {'.eml', '.msg'})
    for upload in candidates:
        suffix = PurePosixPath(upload.name.lower()).suffix
        if suffix not in {'.eml', '.msg', '.txt', '.pdf', '.docx'}:
            continue
        try:
            stream, _ = download_private_file(deal, actor, upload.folder_key, 'radai-' + str(upload.pk))
            try:
                if suffix == '.eml':
                    fields = _eml_fields(stream)
                else:
                    text, _ = extract_document_text(stream, upload.name, upload.size)
                    fields = extract_letter_fields(text)
            finally:
                stream.close()
            if fields.get('recipient_name') or fields.get('recipient_email'):
                # An unrelated intake cannot provide this file's missing date.
                fields.setdefault('your_ref', '')
                fields['_source'] = {'upload_id': str(upload.pk), 'sha256': upload.sha256}
                return fields
        except Exception:
            # Permission, corrupt content and unavailable storage are optional
            # extraction failures. Never log source contents or provider errors.
            logger.info('Letter source unavailable for upload %s', upload.pk)
    from .email_permissions import visible_email_intakes
    if not module_action_allowed(actor, 'sales_email_intake', 'read'):
        return {}
    intake = visible_email_intakes(actor).filter(opportunity=deal).order_by('received_at', 'pk').first()
    if intake:
        fields = extract_letter_fields(
            intake.body_preview, sender_name=intake.sender_name, sender_email=intake.sender_email,
            date=intake.sent_at or intake.received_at,
            message_id=intake.internet_message_id or intake.source_message_id,
        )
        fields['_source'] = {'intake_id': str(intake.pk)}
        return fields
    return {}
