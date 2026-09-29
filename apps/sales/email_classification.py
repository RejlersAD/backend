"""Bounded, deterministic proposals for the selected email's current purpose.

Rule confidence describes evidence strength, not a calibrated probability.
Source content is never executed and cannot grant authority or approve a record.
"""

import re

from .email_extraction import email_text_units


LABELS = {
    'tender_opportunity': 'Tender Opportunity', 'rfp': 'RFP', 'rfq': 'RFQ',
    'rft': 'RFT', 'eoi': 'EOI', 'itt': 'ITT', 'proposal_request': 'Proposal Request',
    'clarification': 'Clarification', 'tender_bulletin': 'Tender Bulletin',
    'tender_addendum': 'Tender Addendum', 'award_notification': 'Award Notification',
    'regret_notification': 'Regret Notification', 'contract_award': 'Contract Award',
    'framework_agreement': 'Framework Agreement', 'purchase_order': 'Purchase Order',
    'variation_request': 'Variation Request', 'vendor_request': 'Vendor Request',
    'invoice_related': 'Invoice Related', 'general_communication': 'General Communication',
}
MAX_BODY = 50_000
MAX_SUBJECT = 1_000
MAX_UNITS = 600
MAX_EVIDENCE = 4
REPLY_SUBJECT = re.compile(r'^\s*(?:(?:re|fw|fwd)\s*:\s*)+', re.I)
SIGNATURE = re.compile(
    r'^\s*(?:--\s*|(?:kind|best|warm)?\s*regards[,!.]?|'
    r'yours\s+(?:sincerely|faithfully)[,!]?|sincerely[,!]?|'
    r'thanks\s+(?:and|&)\s+regards[,!]?|sent\s+from\s+my\b.*|'
    r'thanks[,!]?|thank\s+you[,!]?|'
    r'(?:confidentiality\s+notice|disclaimer)\s*:?.*)\s*$', re.I,
)
TENDER_CONTEXT = re.compile(r'\b(?:tender|bid(?:der)?|RFQ|RFP|RFT|EOI|ITT|solicitation|sourcing)\b', re.I)
NEGATIVE = re.compile(r"\b(?:not|never|without|cancelled|canceled|withdrawn)\b|\bno\s+(?!\d|later\s+than\b)|\b(?:isn|wasn|hasn|haven|don|doesn|didn)'t\b", re.I)
CONDITIONAL = re.compile(
    r'\b(?:if|unless|whenever|hypothetical|template|might|could)\b|'
    r'\b(?:an?\s+(?:example|sample)|for\s+example|(?:example|sample)\s+(?:only|award|letter|request|deadline))\b|'
    r'\bmay\b(?!\s+\d{1,2}(?:st|nd|rd|th)?\b)|'
    r'\b(?:before|after|upon|subject\s+to|conditional\s+on|pending)\b|'
    r'\b(?:will|would|shall)\s+(?:be\s+)?(?:issu\w*|send|award\w*|receiv\w*|invoic\w*|notif\w*)\b', re.I,
)
QUALIFIED = re.compile(
    r'\bshould\s+(?:your|the|you|we)\b|\bunder\s+(?:consideration|review)\b|'
    r'\b(?:whether|pending|proposed|planned|awaiting)\b|'
    r'\b(?:can|could|would)\s+you\s+(?:please\s+)?confirm\b', re.I,
)
HISTORICAL = re.compile(r'\b(?:previous|previously|prior|historical|last\s+year|for\s+reference\s+only)\b', re.I)
REQUEST_REFERENCE = re.compile(
    r'\b(?:acknowledge|acknowledgement|receipt|regarding|in\s+reply\s+to|in\s+response\s+to|respond|reviewing|received)\b', re.I,
)
COMMERCIAL_OUTCOME = re.compile(r'\b(?:contract|tender|bid|bidder|proposal|offer|quotation|procurement)\b', re.I)
REQUEST_CODES = frozenset({'tender_opportunity', 'rfp', 'rfq', 'rft', 'eoi', 'itt', 'proposal_request'})
EVENT_CODES = frozenset(LABELS) - REQUEST_CODES - {'general_communication'}
FORMAL_REQUESTS = {
    'rfp': r'request\s+for\s+proposals?',
    'rfq': r'request\s+for\s+(?:budgetary\s+)?quotations?|budgetary\s+quotations?',
    'rft': r'request\s+for\s+tenders?',
    'eoi': r'expression\s+of\s+interest',
    'itt': r'invitation\s+to\s+tender',
}

# Subject labels are evidence only on a fresh subject. Replies/forwards must
# establish the current purpose in their own unquoted body instead.
SUBJECT_RULES = {
    **{code: rf'\b(?:{code}|{phrase})\b' for code, phrase in FORMAL_REQUESTS.items()},
    'tender_opportunity': r'\b(?:tender\s+(?:opportunity|invitation|notice)|invitation\s+to\s+bid|bid\s+invitation)\b',
    'proposal_request': r'\b(?:proposal\s+request|request\s+(?:a|your)\s+proposal)\b',
    'clarification': r'\b(?:clarification(?:s|\s+request)?|technical\s+quer(?:y|ies))\b',
    'tender_bulletin': r'\bbulletin\b',
    'tender_addendum': r'\b(?:addendum|addenda|corrigendum)\b',
    'award_notification': r'\b(?:award\s+notification|notice\s+of\s+award|successful\s+bidder\s+notification)\b',
    'regret_notification': r'\b(?:regret\s+(?:notification|letter)|unsuccessful\s+(?:bid|tender|proposal)|bid\s+rejection)\b',
    'contract_award': r'\b(?:contract\s+award|letter\s+of\s+award)\b',
    'framework_agreement': r'\bframework\s+agreement\b',
    'purchase_order': r'\bpurchase\s+order\b',
    'variation_request': r'\b(?:variation\s+(?:request|order)|change\s+order\s+request)\b',
    'vendor_request': r'\b(?:vendor\s+request|(?:vendor|supplier)\s+(?:registration|onboarding|prequalification)\s+request)\b',
    'invoice_related': r'\b(?:invoice|invoicing|credit\s+note|payment\s+reminder)\b',
    'general_communication': r'\b(?:meeting\s+(?:invitation|minutes|agenda)|minutes\s+of\s+(?:the\s+)?meeting|weekly\s+update)\b',
}
BODY_RULES = {
    **{code: rf'\b(?:{phrase})\b|\b(?:invited|invite|invites|invitation|issued|issuing|submit|participate)\b[^.!?]{{0,90}}\b{code}\b'
       for code, phrase in FORMAL_REQUESTS.items()},
    'tender_opportunity': r'\b(?:invitation\s+to\s+bid|invited\s+to\s+(?:bid|tender)|tender\s+opportunity)\b|\b(?:invite|invites|inviting)\s+you\s+to\s+(?:bid|tender|participate\s+in\s+(?:this|the|a)\s+tender)\b',
    'proposal_request': r'\bproposal\s+request\b|\b(?:please|kindly)\s+(?:prepare|provide|submit|send)\s+(?:us\s+)?(?:(?:your|a|the)\s+)?(?:technical\s+|commercial\s+)?proposal\b',
    'clarification': r'\bclarification\s+(?:request|response|notice|question)\b|\b(?:please|kindly)\s+(?:provide|submit|review|respond\s+to)\s+(?:(?:the|your|our|attached)\s+)?(?:technical\s+)?clarifications?\b|\bplease\s+clarify\b',
    'tender_bulletin': r'\b(?:tender\s+)?bulletin\b',
    'tender_addendum': r'\b(?:addendum|addenda|corrigendum)\b',
    'award_notification': r'\b(?:award\s+notification|notice\s+of\s+award)\b|\b(?:you|your\s+(?:company|bid|proposal|offer))\s+(?:(?:have|has)\s+been|(?:are|is))\s+(?:selected|successful)\b|\bselected\s+(?:your\s+(?:company|bid|proposal)|you)\s+(?:as|for)\b',
    'regret_notification': r'\bregret\s+(?:to\s+inform|notification|letter)\b|\b(?:your\s+(?:bid|tender|proposal|offer)|you)\s+(?:(?:has|have)\s+)?(?:not\s+been\s+(?:selected|successful)|(?:was|were|is|are)\s+(?:unsuccessful|not\s+(?:selected|successful)))\b',
    'contract_award': r'\b(?:contract\s+award|letter\s+of\s+award)\b|\b(?:contract|agreement)\s+(?:has\s+been|is|was)\s+(?:hereby\s+)?awarded\b|\bhereby\s+award\s+(?:you\s+)?(?:the\s+)?contract\b',
    'framework_agreement': r'\bframework\s+agreement\b',
    'purchase_order': r'\bpurchase\s+order\b',
    'variation_request': r'\b(?:variation\s+(?:request|order)|change\s+order\s+request)\b|\b(?:request|requesting)\s+(?:a\s+)?variation\b',
    'vendor_request': r'\bvendor\s+request\b|\b(?:vendor|supplier)\s+(?:registration|onboarding|prequalification)\s+(?:request|form|questionnaire)\b|\bplease\s+(?:register|onboard)\s+(?:as\s+)?(?:a\s+)?(?:vendor|supplier)\b',
    'invoice_related': r'\b(?:invoice|invoicing|credit\s+note|payment\s+reminder)\b',
    'general_communication': r'\b(?:thank\s+you|received\s+with\s+thanks|thanks\s+for|acknowledge\s+receipt|meeting\s+(?:invitation|agenda)|minutes\s+of\s+(?:the\s+)?meeting)\b|\bplease\s+join\s+(?:the|our|a)\s+meeting\b',
}
COMPILED_RULES = {
    'subject': {code: re.compile(pattern, re.I) for code, pattern in SUBJECT_RULES.items()},
    'body': {code: re.compile(pattern, re.I) for code, pattern in BODY_RULES.items()},
}


def _text(value):
    return value if isinstance(value, str) else ''


def _body_without_signature(body):
    lines = body.splitlines(keepends=True)
    usable = []
    for line in lines:
        if SIGNATURE.fullmatch(line.strip('\r\n')):
            break
        # Bare quoted lines may lack Outlook/Gmail header boundaries.
        if re.match(r'^\s*>', line):
            # A hard boundary prevents unrelated surviving lines from becoming
            # a fabricated request phrase or a non-literal evidence excerpt.
            usable.append('\n\n')
            continue
        usable.append(line)
    return ''.join(usable)


def _units(text):
    # Each excerpt stays a literal substring; splitting never fabricates quotes.
    return list(email_text_units(text))


def _eligible(code, unit, location, *, commercial_context=False):
    if CONDITIONAL.search(unit) or QUALIFIED.search(unit) or HISTORICAL.search(unit) or '?' in unit:
        return False
    negative_text = unit
    if code == 'regret_notification':
        negative_text = re.sub(r'\bnot\s+(?:been\s+)?(?:selected|successful|awarded)\b', '', negative_text, flags=re.I)
    if NEGATIVE.search(negative_text):
        return False
    if code in {'award_notification', 'contract_award'} and re.search(
        r'\b(?:another|other|different)\s+(?:bidder|supplier|vendor|company)\b|\baward\s+(?:ceremony|criteria|process|committee)\b', unit, re.I,
    ):
        return False
    if code in {'award_notification', 'regret_notification'} and location == 'body' and not commercial_context:
        return False
    if code == 'regret_notification' and location == 'subject' and not commercial_context:
        return False
    if code in REQUEST_CODES and location == 'body' and REQUEST_REFERENCE.search(unit):
        return False
    if code in {'framework_agreement', 'purchase_order', 'invoice_related'}:
        if re.search(r'\b(?:invoice|invoicing|purchase\s+order|framework\s+agreement)\s+(?:processing|tracking|workflow|management|software|platform|system|module|automation|support|team|department)\b', unit, re.I):
            return False
    if location == 'body' and code in {'framework_agreement', 'purchase_order', 'invoice_related'}:
        # Mere lists of required documents or background references are not
        # evidence that this email issues/discusses that commercial document.
        if re.search(r'\b(?:terms\s+and\s+conditions|requirements?|checklist|such\s+as|including|for\s+example)\b', unit, re.I):
            return False
        # Require an actual document reference or action, rather than incidental
        # commercial nouns in technical scope or standard payment terms.
        document = {'framework_agreement': r'framework\s+agreement',
                    'purchase_order': r'purchase\s+order',
                    'invoice_related': r'(?:invoice|credit\s+note|payment\s+reminder)'}[code]
        if not re.search(
            rf'\b(?:please|kindly)\s+(?:acknowledge|review|pay|settle|check|process|find|provide|send|submit|confirm)\b.{{0,70}}\b{document}\b|'
            rf'\b(?:attached|enclosed|issued|issuing)\b.{{0,60}}\b{document}\b|'
            rf'\b{document}\b.{{0,60}}\b(?:attached|enclosed|issued|overdue|outstanding|paid|incorrect)\b|'
            rf'\b{document}\s+(?:number|no\.?|ref(?:erence)?|#)\s*[:#-]?\s*[A-Z0-9/-]*\d',
            unit, re.I,
        ):
            return False
    return True


def _topic(code, unit):
    if code in {'contract_award', 'award_notification'}:
        return re.search(r'\b(?:award(?:ed)?|selected|successful)\b', unit, re.I)
    return COMPILED_RULES['body'][code].search(unit)


def _evidence(source_id, location, unit, match, code):
    start = max(0, match.start() - 100)
    end = min(len(unit), max(match.end() + 100, start + 220))
    return {'source_id': source_id, 'location': location, 'excerpt': unit[start:end],
            'rule_id': f'{code}.{location}.v1'}


def classify_email_segment(segment, *, truncated=False):
    """Classify only one supplied current segment; never retrieve other evidence."""
    result = {
        'version': 1, 'status': 'unclassified', 'code': '', 'label': 'Needs review',
        'confidence': {'level': 'unresolved', 'method': 'rule_evidence_v1', 'reason': ''},
        'needs_review': True, 'evidence': [], 'alternatives': [],
    }
    if not isinstance(segment, dict) or not _text(segment.get('id')):
        result['confidence']['reason'] = 'The selected current email text was not available for classification.'
        return result
    if segment.get('is_draft'):
        result.update(status='draft', label='Draft')
        result['confidence']['reason'] = 'Unsent draft text is not a confirmed notice or request.'
        return result
    source_id = segment['id']
    raw_subject = _text(segment.get('subject'))
    raw_body = _text(segment.get('classification_body', segment.get('body')))
    limited = truncated or len(raw_subject) > MAX_SUBJECT or len(raw_body) > MAX_BODY
    subject = raw_subject[:MAX_SUBJECT]
    body = _body_without_signature(raw_body[:MAX_BODY])
    body_units = _units(body)
    limited = limited or len(body_units) > MAX_UNITS
    body_units = body_units[:MAX_UNITS]
    tender_context = bool(TENDER_CONTEXT.search(f'{subject}\n{body}'))
    commercial_context = bool(COMMERCIAL_OUTCOME.search(f'{subject}\n{body}'))
    candidates = {}
    candidate_units = {}
    qualifications = {}
    body_has_eio = bool(re.search(r'\bEIO\b', body, re.I))
    eio = body_has_eio or (not REPLY_SUBJECT.match(subject) and bool(re.search(r'\bEIO\b', subject, re.I)))
    for location, units in (('subject', [] if REPLY_SUBJECT.match(subject) else [subject]), ('body', body_units)):
        for unit_index, unit in enumerate(units):
            for code, rule in COMPILED_RULES[location].items():
                if code in {'tender_bulletin', 'tender_addendum'} and not tender_context:
                    continue
                match = rule.search(unit)
                eligible = _eligible(code, unit, location, commercial_context=commercial_context)
                if location == 'body' and not eligible and code != 'general_communication':
                    topic = _topic(code, unit)
                    # Preserve explicit contradictory/qualified current wording
                    # so a positive-looking old subject cannot override it.
                    if topic and (NEGATIVE.search(unit) or CONDITIONAL.search(unit) or QUALIFIED.search(unit)
                                  or '?' in unit or (code in REQUEST_CODES and REQUEST_REFERENCE.search(unit))):
                        evidence = _evidence(source_id, location, unit, topic, code)
                        evidence['rule_id'] = f'{code}.qualified_body.v1'
                        qualifications.setdefault(code, []).append(evidence)
                if match is None or not eligible:
                    continue
                if code not in candidates:
                    candidates[code], candidate_units[code] = [], set()
                evidence = _evidence(source_id, location, unit, match, code)
                if evidence not in candidates[code] and len(candidates[code]) < MAX_EVIDENCE:
                    candidates[code].append(evidence)
                candidate_units[code].add((location, unit_index))

    # A specific selected notice supersedes an acronym in its subject or a
    # request reference within that same notice. Independent request statements
    # remain competing evidence rather than disappearing behind global priority.
    event_units = set().union(*(candidate_units[code] for code in candidates if code in EVENT_CODES))
    if event_units:
        for code in list(candidates):
            if code in REQUEST_CODES and all(location == 'subject' or (location, index) in event_units
                                             for location, index in candidate_units[code]):
                del candidates[code]
    if any(code in candidates for code in FORMAL_REQUESTS):
        candidates.pop('proposal_request', None)
        candidates.pop('tender_opportunity', None)
    if 'contract_award' in candidates:
        candidates.pop('award_notification', None)
    if len(candidates) > 1:
        candidates.pop('general_communication', None)

    conflicts = {code: [*evidence, *qualifications[code]][:MAX_EVIDENCE]
                 for code, evidence in candidates.items() if code in qualifications}

    if conflicts:
        result['status'] = 'ambiguous'
        result['confidence']['reason'] = 'The current body qualifies or contradicts category wording. Review the source before choosing a category.'
        result['alternatives'] = [{'code': code, 'label': LABELS[code], 'evidence': evidence}
                                  for code, evidence in conflicts.items()]
        result['evidence'] = [item for evidence in conflicts.values() for item in evidence][:MAX_EVIDENCE]
    elif eio and not candidates:
        result['confidence']['reason'] = 'The source says EIO. Its meaning is unconfirmed; it was not interpreted as EOI.'
    elif len(candidates) > 1:
        result['status'] = 'ambiguous'
        result['confidence']['reason'] = 'The selected text supports multiple business categories. Review the alternatives.'
        result['alternatives'] = [{'code': code, 'label': LABELS[code], 'evidence': evidence}
                                  for code, evidence in candidates.items()]
        result['evidence'] = [item for evidence in candidates.values() for item in evidence][:MAX_EVIDENCE]
    elif candidates:
        code, evidence = next(iter(candidates.items()))
        result.update(status='classified', code=code, label=LABELS[code], evidence=evidence)
        if code == 'general_communication':
            level, reason = 'low', 'Acknowledgement or meeting wording was found; this does not establish that the email has no commercial significance.'
        elif any(item['location'] == 'subject' for item in evidence) and any(item['location'] == 'body' for item in evidence):
            level, reason = 'high', 'The fresh subject and current body both contain explicit category evidence.'
        else:
            level, reason = 'medium', 'Explicit category wording was found in the selected current text; review the cited evidence.'
        result['confidence'].update(level=level, reason=reason)
    else:
        result['confidence']['reason'] = 'No clear supported business category was established in the selected current text. Review is required.'
    if eio and candidates:
        result['confidence']['reason'] += ' The separate EIO wording was not interpreted as EOI.'
    if limited:
        result['confidence']['reason'] += ' Only bounded available text was checked; omitted content may change this proposal.'
        if result['confidence']['level'] == 'high':
            result['confidence']['level'] = 'medium'
    return result
