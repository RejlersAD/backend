"""Shared letter context builder.

Single source of truth for the data that fills the standard Rejlers letterhead.
Used both when rendering the letter subject/body templates (workflow) and when
rendering the PDF/DOCX output, so what the user edits is exactly what is
generated.
"""

import html
import re

from django.utils import timezone
from django.utils.html import strip_tags

from .letter_defaults import get_letterhead_defaults, get_logo_path
from .models import SERVICE_CATEGORIES


def ordinal_suffix(day):
    if 11 <= day % 100 <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')


def format_date_ordinal(value):
    """Format a date/datetime like the company template: '10th June 2026'."""
    if value is None:
        return ''
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    day = value.day
    return f"{day}{ordinal_suffix(day)} {value.strftime('%B %Y')}"


def normalize_body_text(body):
    """Reduce rich-text/editor HTML bodies to plain text with real newlines."""
    if not body:
        return ''
    text = re.sub(r'(?i)<\s*(br\s*/?|/p|/div|/li|/h[1-6])\s*>', '\n', str(body))
    text = strip_tags(text)
    text = html.unescape(text)
    return text.strip()


def split_body_paragraphs(body):
    """Split a letter body into paragraph strings (blank-line separated)."""
    text = normalize_body_text(body)
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    # Remove leading "Dear Sir" variants and trailing "Sincerely yours" variants
    # since these are now rendered in the template
    filtered = []
    for i, p in enumerate(paragraphs):
        # Skip leading salutation
        if i == 0 and re.match(r'^dear\s+sir', p, re.IGNORECASE):
            continue
        # Skip trailing complimentary close
        if i == len(paragraphs) - 1 and re.match(r'^sincerely\s+yours', p, re.IGNORECASE):
            continue
        filtered.append(p)
    return filtered


def _client_address(client):
    if not client:
        return ''
    parts = [p.strip() for p in (client.address, client.city, client.country) if p and p.strip()]
    return ', '.join(parts)


def build_letterhead(deal, custom_data=None):
    """Compute the full standard letterhead field set for an opportunity.

    Values already present in ``custom_data['letterhead']`` win, so edits made
    in the letter editor survive regeneration.
    """
    custom_data = custom_data or {}
    provided = custom_data.get('letterhead') or {}
    defaults = get_letterhead_defaults()

    contact = deal.client_contact
    client = deal.client

    earliest_intake = deal.email_intakes.order_by('received_at').first()
    if earliest_intake is not None:
        your_ref = f"{earliest_intake.sender_name or 'Email notification'} dated " \
                   f"{format_date_ordinal(earliest_intake.received_at)}"
        if earliest_intake.source_message_id:
            your_ref += f", Message ID: {earliest_intake.source_message_id}"
    else:
        your_ref = deal.client_reference or ''

    seq = deal.letters.count() + 1
    client_name = client.company_name if client else ''
    deal_code = deal.deal_code or ''
    if client_name:
        our_ref = f"LR-RAD-{client_name}-{seq:03d} / {deal_code}".strip()
    else:
        our_ref = f"LR-RAD-{seq:03d} / {deal_code}".strip()

    letterhead = {
        'response_label': defaults['response_label'],
        'response_code': deal_code,
        'confidential_date': format_date_ordinal(timezone.now()),
        'rev': 0,
        'sender_name': defaults['sender_name'],
        'sender_company': defaults['sender_company'],
        'focal_contact': defaults['focal_contact'],
        'contact_number': defaults['contact_number'],
        'fax': defaults['fax'],
        'email': defaults['email'],
        'adnoc_unified_code': defaults['adnoc_unified_code'],
        'recipient_name': contact.full_name if contact else '',
        'recipient_title': contact.job_title if contact else '',
        'recipient_company': client_name,
        'recipient_address': _client_address(client),
        'recipient_email': contact.email if contact else '',
        'your_ref': your_ref,
        'our_ref': our_ref,
        'signature_name': defaults['signature_name'],
        'signature_titles': list(defaults['signature_titles']),
        'footer_company': defaults['footer_company'],
        'footer_address': defaults['footer_address'],
        'footer_contact': defaults['footer_contact'],
    }
    for key, value in provided.items():
        if key in letterhead and value is not None:
            letterhead[key] = value
    if isinstance(letterhead.get('signature_titles'), str):
        letterhead['signature_titles'] = [line for line in letterhead['signature_titles'].split('\n') if line.strip()]
    letterhead['confidential'] = f"{letterhead['confidential_date']} / Rev {letterhead['rev']}"
    return letterhead


def build_letter_context(deal, letter=None, custom_data=None):
    """Build the rendering context for letter templates and PDF/DOCX output."""
    from apps.rbac.models import Organization

    custom_data = dict(custom_data or {})
    letterhead = build_letterhead(deal, custom_data)
    custom_data.pop('letterhead', None)

    org = Organization.objects.filter(is_active=True).first()
    company_name = letterhead['sender_company'] if letterhead['sender_company'] else (org.name if org else 'RADAI')

    client_contact = deal.client_contact
    service_choices = dict(deal._meta.get_field('service_categories').choices or {})
    service_line = ', '.join(
        service_choices.get(s, s) for s in (deal.service_categories or [])
    )

    context = {
        'deal_code': deal.deal_code or '',
        'deal_name': deal.deal_name or '',
        'deal_description': deal.description or '',
        'client_name': deal.client.company_name if deal.client else '',
        'client_reference': deal.client_reference or '',
        'client_contact_name': client_contact.full_name if client_contact else 'The Project Manager',
        'client_email': client_contact.email if client_contact else '',
        'opportunity_type': deal.get_opportunity_type_display() if deal.opportunity_type else '',
        'service_line': service_line,
        'core_services': ', '.join(service_choices.get(s, s) for s in (deal.service_categories or [])[:3]) or 'our core service lines',
        'industry_type': deal.client.get_industry_type_display() if deal.client else '',
        'owner_name': (deal.owner.get_full_name() or deal.owner.username) if deal.owner else '',
        'owner_email': deal.owner.email if deal.owner else '',
        'owner_title': getattr(deal.owner, 'rbac_profile', None) and deal.owner.rbac_profile.location or '',
        'company_name': company_name,
        'submission_due_date': deal.submission_due_date.strftime('%d %b %Y') if deal.submission_due_date else '',
        'expected_close_date': deal.expected_close_date.strftime('%d %b %Y') if deal.expected_close_date else '',
        'estimated_value': f"{deal.estimated_value:,.2f}" if deal.estimated_value else '',
        'currency': deal.currency or '',
        'bid_decision': deal.get_bid_decision_display() if deal.bid_decision else '',
        'bid_decision_reason': deal.bid_decision_reason or '',
        'current_date': timezone.now().strftime('%d %B %Y'),
        'today': timezone.now().strftime('%d %B %Y'),
        'letter_type': letter.letter_type if letter else '',
        'letter_type_display': letter.get_letter_type_display() if letter else '',
        'subject_suffix': 'EOI Response / Regret' if (letter and letter.letter_type in ('regret_expertise', 'regret_manpower')) else '',
        'letterhead': letterhead,
    }

    logo_path = get_logo_path()
    context['logo_path'] = logo_path
    context['logo_uri'] = logo_path.as_uri() if logo_path else ''
    # Use file path for WeasyPrint to load directly (avoids embedding large base64 in HTML)
    context['logo_data_uri'] = logo_path.as_uri() if logo_path else ''

    if letter is not None:
        context.update({
            'subject': letter.subject,
            'body_paragraphs': [
                {'field': f'body_p{i + 1}', 'content': para, 'is_first': i == 0}
                for i, para in enumerate(split_body_paragraphs(letter.body))
            ],
            'generated_at': letter.generated_at.strftime('%d %B %Y %H:%M') if letter.generated_at else '',
        })

    context.update(custom_data)
    return context
