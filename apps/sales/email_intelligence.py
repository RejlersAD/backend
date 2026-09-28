"""Deterministic, reviewable email intelligence; no I/O or business mutations."""

import ipaddress
import re
from datetime import datetime
from urllib.parse import urlsplit

from .email_extraction import DATE_TOKEN, _date_value


PORTAL_DOMAINS = ('ariba.com', 'sap.com', 'sapbusinessnetwork.com', 'coupa.com', 'jaggaer.com')
PUBLIC_DOMAINS = ('gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'icloud.com', 'aol.com', 'proton.me', 'protonmail.com')
PORTAL_TEXT = re.compile(r'\b(?:SAP\s+Ariba|Ariba|sourcing\s+(?:site|portal)|supplier\s+portal|tender\s+portal)\b', re.I)
COMMON_COUNTRY_SECOND_LEVELS = {'ac', 'co', 'com', 'edu', 'gov', 'net', 'org'}
DOMAIN_WORDS = {'eng': 'Engineering', 'intl': 'International', 'tech': 'Technology'}


def domain(value):
    """Exact DNS host only: no guessed parent domain, URL fetch, or company name."""
    if not isinstance(value, str) or len(value) > 1024:
        return ''
    value = value.strip().rstrip('.').casefold()
    if '://' in value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {'https', 'http'} or parsed.username or parsed.password:
                return ''
            value = parsed.hostname or ''
        except ValueError:
            return ''
    elif '@' in value:
        if value.count('@') != 1 or re.search(r'\s', value):
            return ''
        value = value.rsplit('@', 1)[1]
    try:
        value = value.encode('idna').decode('ascii')
        ipaddress.ip_address(value)
        return ''
    except ValueError:
        pass
    except UnicodeError:
        return ''
    labels = value.split('.')
    return value if len(value) <= 253 and len(labels) >= 2 and all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels
    ) else ''


def _belongs(value, domains):
    return bool(value and any(value == base or value.endswith('.' + base) for base in domains))


def customer_name_from_domain(value):
    """Create a reviewable display name from an evidenced external domain.

    This is not a legal-entity match. It removes DNS suffixes and separators,
    handles common country-code second-level suffixes, and expands only a small
    set of unambiguous business abbreviations. Canonical matching continues to
    require separately evidenced organization identity.
    """
    host = domain(value)
    if not host:
        return ''
    labels = host.split('.')
    position = -3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in COMMON_COUNTRY_SECOND_LEVELS else -2
    label = labels[position]
    words = [part for part in re.split(r'[-_]+', label) if re.search(r'[a-z]', part)]
    if not words:
        return ''
    return ' '.join(DOMAIN_WORDS.get(word, word.capitalize()) for word in words)[:300]


def sent_day(segment):
    """Use genuine sent evidence, never the timeline's received-time fallback.

    Calendar date follows the source timestamp/header. A timezone-naive quoted
    header can establish an unambiguous calendar day, not an absolute instant.
    """
    raw = segment.get('actual_sent_at', '')
    if raw:
        try:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                return stamp.date().isoformat(), raw, 'sent_timestamp'
        except (ValueError, TypeError, OverflowError):
            pass
    raw = segment.get('_raw_sent_at', '')
    if isinstance(raw, str) and 0 < len(raw) <= 512:
        values = {_date_value(match.group(0)) for match in re.finditer(DATE_TOKEN, raw, re.I)}
        if len(values) == 1 and '' not in values:
            return next(iter(values)), raw, 'sent_header_calendar_date'
    return '', '', 'missing_sent_evidence'


def original_incoming(segments, original_request, coverage):
    if original_request:
        return original_request, 'original_request'
    # A non-request notice can also start a conversation. A reply/forward or
    # omitted/invalid parent headers cannot establish that original transport.
    incoming = [item for item in segments if item['direction'] == 'incoming' and not item['is_draft']]
    candidates = [item for item in incoming if item['origin'] == 'message' and item['thread_role'] == 'new_message'
                  and not (isinstance(item.get('_thread_metadata'), dict) and item['_thread_metadata'].get('invalid'))]
    # No parent reference alone does not establish a conversation start. For a
    # non-request notice require complete supplied history, a unique start and
    # usable chronology. Saved/selected/partial history stays unconfirmed.
    if coverage == 'complete' and len(candidates) == 1 and incoming:
        try:
            stamps = [datetime.fromisoformat(item['actual_sent_at'].replace('Z', '+00:00')) for item in incoming]
            if all(stamp.tzinfo is not None for stamp in stamps):
                earliest = min(stamps)
                if stamps.count(earliest) == 1 and incoming[stamps.index(earliest)] is candidates[0]:
                    return candidates[0], 'original_incoming_headers'
        except (ValueError, TypeError, KeyError, OverflowError):
            pass
    return None, 'unconfirmed_original'


def _equivalent_quote_dates(original, segments):
    """Corroborate a missing saved sent date without rewriting its snapshot."""
    def normalize(value):
        return re.sub(r'\s+', ' ', value or '').strip()
    candidates = [item for item in segments if item['origin'] == 'quoted' and not item['is_draft']
                  and item['sender_email'].strip().casefold() == original['sender_email'].strip().casefold()
                  and normalize(item['subject']).casefold() == normalize(original['subject']).casefold()
                  and normalize(item['body']) == normalize(original['body'])]
    # Ambiguous competing Sent headers are evidence too, not absent data.
    if any((item.get('actual_sent_at') or item.get('_raw_sent_at')) and not sent_day(item)[0] for item in candidates):
        return ('', '', 'conflicting_sent_evidence'), [item['id'] for item in candidates]
    dates = [(sent_day(item), item['id']) for item in candidates if sent_day(item)[0]]
    if dates and len({item[0][0] for item in dates}) == 1:
        return dates[0][0], list(dict.fromkeys(item[1] for item in dates))
    if dates:
        return ('', '', 'conflicting_sent_evidence'), [item['id'] for item in candidates]
    return ('', '', 'missing_sent_evidence'), []


def add_email_intelligence(result, analysis, segments, *, original_request, selected, mailbox_address):
    """Apply public v2 semantics after thread resolution, before client matching."""
    evidence, refs = result['evidence'], result['field_sources']
    result['detection_version'] = 2
    result['warnings'] = [re.sub(r'customer[ _]name', 'organization name', warning, flags=re.I)
                          for warning in result['warnings']]
    for old, new in (('customer_name', 'organization_name'), ('submission_date', 'stated_submission_date')):
        result[new] = result.get(old, '')
        if old in evidence:
            evidence[new] = evidence.pop(old)
        if old in refs:
            refs[new] = refs.pop(old)
    result['company_name'] = result['organization_name']
    result['stated_submission_date_text'] = result.get('submission_date_text', '')
    result['customer_name'] = result['customer_domain'] = result['submission_date'] = result['submission_date_text'] = ''
    original, basis = original_incoming(segments, original_request, analysis['coverage']['status'])
    source_ids = [original['id']] if original else []
    info = {
        'version': 1, 'source': {'source_id': original['id'] if original else None, 'basis': basis, 'confirmed': bool(original)},
        'customer_name': {'status': 'requires_verification', 'reason': 'The original incoming source does not establish a customer name.', 'source_ids': source_ids},
        'customer_domain': {'status': 'requires_verification', 'reason': 'The original incoming source or customer domain is not confirmed.', 'source_ids': source_ids},
        'submission_date': {'status': 'requires_verification', 'reason': 'The original incoming email has no verified sent date in the available evidence.', 'source_ids': source_ids},
    }
    analysis['original_incoming_source_id'] = original['id'] if original else None
    if original:
        sender_domain, own_domain = domain(original['sender_email']), domain(mailbox_address)
        stated = original['fields'].get('declared_client_domain', '')
        declared = domain(stated)
        blocked = (*PORTAL_DOMAINS, *PUBLIC_DOMAINS, *((own_domain,) if own_domain else ()))
        candidates = []
        if sender_domain and not _belongs(sender_domain, blocked):
            candidates.append((sender_domain, f"From: {original['sender_email']}"))
        if declared and not _belongs(declared, blocked):
            candidates.append((declared, original['fields']['evidence'].get('declared_client_domain', stated)))
        values = {value for value, _ in candidates}
        declared_conflict = not stated and bool(original['fields']['evidence'].get('declared_client_domain'))
        if len(values) == 1 and not declared_conflict:
            result['customer_domain'] = next(iter(values))
            evidence['customer_domain'] = ' | '.join(text for _, text in candidates)[:900]
            refs['customer_domain'] = source_ids
            info['customer_domain'].update(status='detected', reason='The domain is supported by the original incoming sender or an explicit customer-domain statement; confirm customer identity before linking.')
        elif len(values) > 1 or declared_conflict:
            info['customer_domain'].update(status='conflicting', reason='Sender and stated customer-domain evidence is conflicting; verify the customer domain.')
        elif _belongs(sender_domain, PORTAL_DOMAINS):
            info['customer_domain']['reason'] = 'The sender uses a shared procurement platform. Verify the customer\'s own domain in the source documents; the delivery domain is not the customer.'
        elif _belongs(sender_domain, (*PUBLIC_DOMAINS, *((own_domain,) if own_domain else ()))):
            info['customer_domain']['reason'] = 'A public email provider or internal mailbox domain does not establish the customer domain.'

        if result['organization_name'] and evidence.get('organization_name') and refs.get('organization_name'):
            result['customer_name'] = result['organization_name']
            evidence['customer_name'] = evidence['organization_name']
            refs['customer_name'] = refs['organization_name']
            info['customer_name'].update(
                status='detected', basis='explicit_organization',
                reason='The customer name is explicitly supported by the original conversation evidence.',
                source_ids=refs['customer_name'],
            )
        elif result['customer_domain']:
            display_name = customer_name_from_domain(result['customer_domain'])
            if display_name:
                result['customer_name'] = display_name
                evidence['customer_name'] = evidence['customer_domain']
                refs['customer_name'] = refs['customer_domain']
                info['customer_name'].update(
                    status='detected', basis='domain_label',
                    reason='The display name is derived from the evidenced customer domain; verify the legal organization before linking.',
                    source_ids=refs['customer_name'],
                )
        date_info, date_refs = sent_day(original), source_ids
        if not date_info[0]:
            date_info, corroborating = _equivalent_quote_dates(original, segments)
            if corroborating:
                date_refs = corroborating
        day, raw_stamp, date_basis = date_info
        if day:
            result['submission_date'], result['submission_date_text'] = day, raw_stamp
            evidence['submission_date'] = f'Sent: {raw_stamp}'
            refs['submission_date'] = date_refs
            info['submission_date'].update(status='detected', reason='Calendar date from the original incoming email\'s sent evidence, separate from received time and proposal deadline.', source_ids=date_refs, basis=date_basis)
        elif date_basis == 'conflicting_sent_evidence':
            info['submission_date'].update(reason='Competing or ambiguous original sent headers require verification.', source_ids=date_refs)
        for key, value in (('contact_name', original['sender_name']), ('contact_email', original['sender_email'])):
            if value and not result.get(key) and not evidence.get(key):
                result[key], evidence[key], refs[key] = value, f'From: {original["sender_name"]} <{original["sender_email"]}>', source_ids

    portal_sources = [item for item in segments if not item['is_draft'] and item['direction'] != 'outgoing'
                      and (_belongs(domain(item['sender_email']), PORTAL_DOMAINS) or PORTAL_TEXT.search(item['body'][:250_000]))]
    deadline_refs = refs.get('due_date', [])
    deadline_status = 'detected' if result['due_date'] else 'ambiguous' if evidence.get('due_date') else 'not_detected'
    deadline_reason = 'The email states a due date. Review the cited deadline evidence.' if result['due_date'] else 'No unambiguous current deadline is established in the available email text.'
    if portal_sources:
        deadline_status = 'requires_verification'
        deadline_reason = 'Review the SAP Ariba or procurement-portal documents and verify the current deadline. Linked or attached document contents were not read.'
        deadline_refs = list(dict.fromkeys([*deadline_refs, *(item['id'] for item in portal_sources)]))[:8]
        suggestion = {'text': 'Verify the current deadline in the linked procurement documents.', 'reason': deadline_reason, 'source_ids': deadline_refs}
        analysis['suggested_actions'].insert(0, suggestion)
    info['deadline_review'] = {'status': deadline_status, 'reason': deadline_reason, 'source_ids': deadline_refs}

    classification = result.get('classification', {})
    follow_up = bool(selected and (selected['thread_role'] in {'reply', 'forward'} or selected['kind'] in {'tender_bulletin', 'deadline_update', 'clarification'}))
    original_code = original['fields'].get('request_type_code') if original else ''
    request_conflict = not result['request_type_code'] and bool(evidence.get('request_type_code'))
    if classification.get('status') == 'ambiguous' or request_conflict:
        state, reason = 'ambiguous', 'Competing or qualified request evidence needs review before deciding whether this is an opportunity.'
    elif follow_up:
        state, reason = 'follow_up', 'This reply, forward or notice relates to existing correspondence; review the original request and existing opportunities.'
    elif original_code and original_request and classification.get('status') == 'classified' and classification.get('code') in {'rfq', 'rfp', 'rft', 'eoi', 'itt', 'budgetary_quotation', 'proposal_request', 'tender_opportunity'}:
        state, reason = 'candidate', 'An original incoming request is evidenced. Review client, scope, deadline and commercial details before creating an opportunity.'
    else:
        state, reason = 'not_established', 'The available evidence does not establish a new opportunity; human review is required.'
    info['opportunity_detection'] = {'status': state, 'needs_review': True, 'reason': reason, 'source_ids': list(dict.fromkeys([*source_ids, *([selected['id']] if selected else [])]))}
    result['opportunity_detected'] = state == 'candidate'

    entities = []
    for key, entity_type in (('organization_name', 'organization'), ('project_name', 'project'), ('contact_name', 'contact'), ('contact_email', 'email'), ('customer_domain', 'domain')):
        if result.get(key) and evidence.get(key) and refs.get(key):
            entities.append({'entity_type': entity_type, 'value': result[key], 'evidence': evidence[key], 'source_ids': refs[key]})
    info['entities'] = entities
    confidence = {}
    partial = analysis['coverage']['status'] == 'partial'
    for key in ('title', 'customer_name', 'organization_name', 'submission_date', 'project_name', 'request_type_code', 'due_date', 'estimated_value'):
        level = 'medium' if result.get(key) and evidence.get(key) and refs.get(key) else 'unresolved'
        reason = 'Supported by cited source evidence; review the interpretation.' if level != 'unresolved' else 'Missing or conflicting source evidence requires verification.'
        if key == 'submission_date' and info['submission_date'].get('basis') == 'sent_timestamp':
            level, reason = 'high', 'A genuine sent timestamp establishes the source calendar date.'
        request_sources = [item for item in segments if item['id'] in refs.get('request_type_code', [])]
        if key == 'request_type_code' and request_sources and all(item['fields'].get('request_type_basis') == 'keyword' for item in request_sources):
            level, reason = ('low' if result.get(key) else 'unresolved'), 'A general keyword is a weaker signal than an explicit request phrase or code.'
        if key == 'customer_name' and info['customer_name'].get('basis') == 'domain_label':
            level, reason = 'low', 'The display name is derived from an evidenced domain and is not a verified legal organization.'
        if key == 'due_date' and portal_sources:
            level, reason = ('low' if result['due_date'] else 'unresolved'), deadline_reason
        if partial:
            level = 'medium' if level == 'high' else level
            reason += ' The available history is partial.'
        confidence[key] = {'level': level, 'method': 'rule_evidence_v1', 'reason': reason, 'source_ids': refs.get(key, [])}
    info['field_confidence'] = result['confidence'] = confidence
    result['intelligence'] = info
    for point in analysis['key_points']:
        if point['label'] == 'Customer':
            point['label'] = 'Organization'
        elif point['label'] == 'Stated submission date':
            point['label'] = 'Body-stated submission date'
    for key, label in (('customer_name', 'Customer name'), ('submission_date', 'Submission date (original email sent)')):
        if result[key]:
            analysis['key_points'].append({'label': label, 'value': result[key], 'source_ids': refs[key]})
    for source in analysis['sources']:
        source['is_original_incoming'] = bool(original and source['id'] == original['id'])
        segment = next(item for item in segments if item['id'] == source['id'])
        stamp = segment.get('actual_sent_at') or segment.get('_raw_sent_at')
        if stamp:
            source['excerpt'] = (f'Sent: {stamp}\n' + source['excerpt'])[:1800]
    return result
