"""Conservative, local email suggestions. Source text never grants authority."""

import re
from datetime import date, datetime


MAX_CONTENT = 250_000
DATE_TOKEN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?[\s-]+[A-Za-z]{3,9}[\s,-]+\d{4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})"
)
UNCERTAIN_STATEMENT = re.compile(
    r'\b(?:if|unless|could|might|hypothetical|proposed|propose|proposing|'
    r'not|never|cancelled|canceled|withdrawn)\b|'
    r'\b(?:for\s+example|sample\s+(?:letter|request|deadline)|example\s+(?:only|request|deadline))\b|'
    r'\bno\b(?!\s+(?:later\s+than|\d))|'
    r'\bmay\b(?!\s+\d{1,2}(?:st|nd|rd|th)?\b)', re.I,
)
ORGANIZATION_TERM = re.compile(
    r'\b(?:ltd|limited|llc|plc|inc|incorporated|corp|corporation|company|holdings|'
    r'industries|utilities|engineering|infrastructure|energy|services|marine|institute|university)\b', re.I,
)


def _organization_name_allowed(value):
    """Reject sentence clauses captured before an invitation verb.

    Free-form invitation rules are deliberately broader than labelled fields so
    they can recognize short names and acronyms.  They must still reject prose
    such as "We are pleased ... and would like to invite you", which describes
    the sender but is not its organization name.
    """
    value = re.sub(r'\s+', ' ', str(value or '')).strip(' \t,.:')
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&'\u2019.-]*", value)
    if not value or len(words) > 12:
        return False
    if re.match(r'^(?:we|i|you|they|our|your|this|please)\b', value, re.I):
        return False
    if re.search(r'\b(?:we|you|they)\b|\b(?:is|are|was|were|has|have|had|would|will|shall|'
                 r'could|should|might)\b', value, re.I):
        return False
    return True


def email_text_units(text):
    """Literal sentence/paragraph spans, retaining soft line wraps as evidence."""
    for paragraph in re.split(r'\r?\n[ \t]*\r?\n', text):
        for match in re.finditer(r'[^.!?]+(?:[.!?]|$)', paragraph):
            unit = match.group(0).strip()
            if unit:
                yield unit


def _leading_sender_entities(body):
    """One leading bounded From block, never a header in later reply text."""
    match = re.match(
        r'\A[ \t\n]*From:[ \t]*(?P<name>[^\n<>()]{0,255}?)[ \t]*(?:\n[ \t]*)?'
        r'(?:\((?P<organization>[^\n<>()]{2,300})\)[ \t]*(?:\n[ \t]*)?)?'
        r'<(?P<email>[^<>\s@]{1,100}@[^<>\s@]{1,150})>', body[:1024], re.I,
    )
    if not match:
        return {}
    result = {key: (match.group(group).strip(), match.group(0).strip())
              for key, group in (('customer_name', 'organization'), ('contact_name', 'name'), ('contact_email', 'email'))
              if match.group(group) and match.group(group).strip()}
    if 'customer_name' in result and not ORGANIZATION_TERM.search(result['customer_name'][0]):
        result.pop('customer_name')
    return result


def _date_context_allowed(content, start, end):
    before = content[max(0, start - 240):start]
    # A blank paragraph or sentence terminator bounds qualifications. A soft
    # wrap may still carry "if"/"not" into the following deadline line.
    before = re.split(r'[.!?]|\n[ \t]*\n', before)[-1]
    after = re.split(r'[.!?]|\n[ \t]*\n', content[end:end + 100])[0]
    context = before + content[start:end] + after
    if UNCERTAIN_STATEMENT.search(context) or '?' in content[end:end + 2]:
        return False
    if re.search(r'\b(?:payment|invoice|registration\s+fee|tender\s+fee|bid\s+bond)\b', before, re.I):
        return bool(re.search(r'\b(?:proposal|quotation|tender|bid)\s+(?:submission\s+)?(?:deadline|due)', content[start:end], re.I))
    return True


def _date_value(value):
    """Normalize date-only values; do not choose a locale for ambiguous dates."""
    value = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", value, flags=re.I)
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", value):
        first, second, year = map(int, re.split(r"[/-]", value))
        if first <= 12 and second <= 12 and first != second:
            return ""
        day, month = (second, first) if second > 12 else (first, second)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    for pattern in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            normalized = value if pattern == "%Y-%m-%d" else re.sub(r"[\s,-]+", " ", value).strip()
            return datetime.strptime(normalized, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def _extract_email_fields(*, subject="", body_text="", sender_email="", sender_name=""):
    """Return editable suggestions and short evidence, without external AI calls.

    Explicit labels and bounded organization/request phrases provide evidence.
    Multiple different candidates remain unresolved. Stated dates here remain
    distinct from the public submission date derived from the source timestamp.
    Legacy extraction keys remain available to existing imported-intake clients.
    """
    # Imported at call time because the semantic helper shares these date
    # primitives. Filter before resolving conflicts so a separate agreement
    # deadline cannot erase a genuine proposal deadline in the same source.
    from .email_agreement_actions import is_agreement_deadline_evidence
    subject = str(subject or "")
    raw_content = f"{subject}\n\n{body_text or ''}"
    content = raw_content[:MAX_CONTENT].replace("\r\n", "\n").replace("\r", "\n")
    content = content.replace("\u00a0", " ")
    evidence, warnings = {}, []
    if len(raw_content) > MAX_CONTENT:
        warnings.append("Only the first part of this long email was checked. Review the full email.")
    result = {key: "" for key in (
        "title", "customer_name", "company_name", "submission_date", "submission_date_text",
        "due_date", "due_date_text", "request_type_code", "request_type", "tender_reference",
        "deadline_text", "deadline_date", "expected_award_date", "estimated_value", "currency",
        "declared_client_domain", "contact_name", "contact_email", "contact_phone", "location",
        "industry", "scope_summary", "scope_type", "project_name", "request_type_basis", "request_match_strength",
    )}
    result["title"] = subject
    evidence["title"] = subject[:300]
    result["client_domain"] = str(sender_email or "").rsplit("@", 1)[-1].lower() if "@" in str(sender_email or "") else ""
    result["industry_type"] = "other"

    def unique_value(key, matches):
        candidates = [(value.strip(), source.strip()) for value, source in matches if value.strip()]
        distinct = {value.casefold() for value, _ in candidates}
        if len(distinct) == 1:
            result[key] = candidates[0][0]
            evidence[key] = candidates[0][1][:300]
        elif distinct:
            evidence[key] = " | ".join(source for _, source in candidates)[:600]
            warnings.append(f"Conflicting {key.replace('_', ' ')} values need review.")

    sender_entities = _leading_sender_entities(str(body_text or '').replace('\r\n', '\n').replace('\r', '\n'))
    metadata_entities = {}
    if (isinstance(sender_name, str) and len(sender_name) <= 500 and '(' in sender_name
            and not re.search(r'[\r\n<>]', sender_name)
            and isinstance(sender_email, str) and re.fullmatch(r'[^<>\s@]{1,100}@[^<>\s@]{1,150}', sender_email)):
        metadata_entities = _leading_sender_entities(f'From: {sender_name} <{sender_email}>')
    labels = {
        "customer_name": r"(?:Company|Client|Customer|Organisation|Organization)(?:\s+name)?",
        "declared_client_domain": r"(?:Client|Customer)\s+(?:domain|website)",
        "contact_name": r"Contact\s+(?:person|name)",
        "contact_email": r"Contact\s+email",
        "contact_phone": r"Contact\s+phone",
        "location": r"Project\s+location",
        "industry": r"Industry",
        "scope_summary": r"(?:Scope\s+summary|Project\s+scope|Scope\s+of\s+work)",
        "project_name": r"Project(?:\s+name)?",
    }
    for key, label in labels.items():
        pattern = rf"^[ \t]*(?:{label})[ \t]*(?::[ \t]*|\t+)([^\n]+)"
        candidates = [(match.group(1), match.group(0)) for match in re.finditer(pattern, content, re.I | re.M)]
        if key in sender_entities:
            candidates.append(sender_entities[key])
        if key in metadata_entities:
            candidates.append(metadata_entities[key])
        unique_value(key, candidates)
    result["company_name"] = result["customer_name"]

    # Portal notifications and invitations often name an organization in prose
    # rather than in a Customer: field. A domain/display name alone is not proof.
    if not result["customer_name"] and "customer_name" not in evidence:
        organization_patterns = (
            r"\byour\s+customer\s*,\s*(?P<name>[^\n]{2,180}?)\s*,\s*(?:has|have|is|invites|requests)\b",
            r"\bon\s+behalf\s+of\s+(?P<name>[^\n:;]{2,180}?)(?:,|\s+(?:we|I)\s|\s+(?:invites|requests)\b)",
            r"^[ \t]*(?P<name>(?-i:[A-Z])[^\n:;]{1,160}?)\s+(?:cordially\s+)?(?:invites?\s+you|requests?\s+(?:your|a)\s+(?:proposal|quotation|tender))\b",
            r"^[ \t]*(?:Visit\s+(?:the\s+)?|Welcome\s+to\s+(?:the\s+)?)?(?P<name>[A-Z][^\n:;]{1,160}?)['’]s?\s+sourcing\s+site\b",
            r"^[ \t]*(?:Visit\s+(?:the\s+)?|Welcome\s+to\s+(?:the\s+)?)?(?P<name>[A-Z][^\n:;]{1,160}?)\s+sourcing\s+site\s*,",
            r"^[ \t]*(?:Issued\s+by|Tendering\s+authority|Contracting\s+authority|Employer)[ \t]*:[ \t]*(?P<name>[^\n]+)",
            r"^[ \t]*(?P<name>[A-Z][^\n:;]{1,160}?)\s+(?:is|are)\s+(?:currently\s+)?(?:conducting|undertaking|carrying\s+out)\s+(?:an?\s+)?(?:market\s+(?:benchmarking|survey)|procurement|tendering|supplier\s+selection)\b",
        )
        organizations = []
        for pattern in organization_patterns:
            for match in re.finditer(pattern, content, re.I | re.M):
                name = match.group('name').strip(' \t,.:')
                name = re.sub(r'^(?:Dear|Hello|Hi)\s+(?:supplier|vendor|bidder|tenderer|all|team)s?\s*,\s*', '', name, flags=re.I)
                name = re.sub(r'^(?:please\s+)?(?:visit|access|welcome\s+to|log\s+in\s+to)\s+(?:the\s+)?', '', name, flags=re.I)
                if re.match(r'^(?:Dear|Hello|Hi)\b', name, re.I):
                    continue
                if (_organization_name_allowed(name)
                        and name.casefold() not in {'our team', 'the team', 'the company', 'this company', 'the project', 'this project'}
                        and not re.search(r'https?://|@', name)):
                    organizations.append((name, match.group(0)))
        unique_value('customer_name', organizations)
        result['company_name'] = result['customer_name']
    if not result['project_name'] and 'project_name' not in evidence:
        project_matches = re.finditer(
            r'\b(?:quotation|proposal|tender)\s*[-\u2013\u2014:]\s*(?P<name>[^|;]{2,280}?\bproject)\b',
            subject[:1000], re.I,
        )
        unique_value('project_name', [(' '.join(match.group('name').split()), match.group(0))
                                      for match in project_matches])
    if not result['scope_summary'] and 'scope_summary' not in evidence:
        scope_matches = list(re.finditer(
            r'^[ \t]*(?:The\s+)?(?:scope|work|services?)\s+(?:includes?|covers?|comprises?|consists?\s+of)\s*:?[ \t]*([^\n]+)',
            content, re.I | re.M,
        ))
        unique_value('scope_summary', [(match.group(1), match.group(0)) for match in scope_matches])

    date_labels = {
        "submission_date": r"(?<!required )(?<!required\s)submission\s+date|date\s+of\s+submission",
        "due_date": r"proposal\s+deadline|submission\s+deadline|submission\s+due\s+date|tender\s+closing\s+date|closing\s+date|proposal\s+required\s+by|required\s+submission\s+date|required\s+by|due\s+date|deadline|(?:tenders?|proposals?|bids?|quotations?)\s+(?:are\s+)?(?:due|close[sd]?)(?:\s+(?:on|by|at))?|(?:submit|send|return|provide)\s+(?:(?:your|the|a)\s+)?(?:(?:technical|commercial|budgetary)\s+)?(?:proposal|tender|bid|quotation)(?:\s+(?:by|before|on\s+or\s+before|no\s+later\s+than))",
        "expected_award_date": r"expected\s+award\s+date|anticipated\s+award\s+date|contract\s+award\s+date|award\s+expected\s+by",
    }
    for key, label in date_labels.items():
        weekday = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
        bridge = r"[ \t]*[:\-]?[ \t]*"
        if key == 'due_date':
            bridge = (
                r'\s*(?:for\s+(?:the\s+)?(?:submission|receipt|return)'
                r'(?:\s+of\s+(?:the\s+|your\s+)?(?:(?:budgetary|technical|commercial)\s+)?'
                r'(?:quotations?|proposals?|tenders?|bids?))?)?'
                r'\s*(?:(?:is|will\s+be|shall\s+be|falls\s+on)\s*)?(?:on\s+)?[:\-]?\s*'
            )
        matches = [match for match in re.finditer(
            rf"\b(?:{label}){bridge}{weekday}(?P<date>{DATE_TOKEN})(?!\d)", content, re.I,
        ) if len(match.group(0)) <= 500 and match.group(0).count('\n') <= 4
            and not re.search(r'\n[ \t]*\n', match.group(0))
            and _date_context_allowed(content, match.start(), match.end())
            and (key != 'due_date' or not is_agreement_deadline_evidence(
                match.group(0), content, source_span=(match.start(), match.end())))]
        normalized = [(_date_value(match.group("date")), match) for match in matches]
        distinct = {value for value, _ in normalized}
        if matches:
            evidence[key] = " | ".join(match.group(0).strip() for match in matches)[:600]
            if key in {"submission_date", "due_date"}:
                result[f"{key}_text"] = " / ".join(dict.fromkeys(match.group("date") for match in matches))[:300]
            if len(distinct) == 1 and "" not in distinct:
                result[key] = normalized[0][0]
            else:
                warnings.append(f"The {key.replace('_', ' ')} is ambiguous, invalid or conflicting. Enter it after review.")
    result["deadline_date"] = result["due_date"]
    result["deadline_text"] = result["due_date_text"]

    # Search actual tokens: 'submitted' must not match the acronym ITT.
    request_patterns = {
        "EOI": r"\bEOI\b|\bexpression\s+of\s+interest\b",
        "RFT": r"\bRFT\b|\brequest\s+for\s+tender\b",
        "EIO": r"\bEIO\b",
        "RFQ": r"\bRFQ\b|\brequest\s+for\s+(?:budgetary\s+)?quotations?\b|\bbudgetary\s+quotations?\b",
        "RFP": r"\bRFP\b|\brequest\s+for\s+proposals?\b",
        "ITT": r"\bITT\b|\binvitation\s+to\s+tender\b",
    }
    units = list(email_text_units(content))
    request_matches = [(code, match.group(0)) for code, pattern in request_patterns.items()
                       for unit in units if not UNCERTAIN_STATEMENT.search(unit)
                       if (match := re.search(pattern, unit, re.I))]
    result['request_type_basis'] = 'explicit' if request_matches else ''
    if not request_matches:
        weak_patterns = {'RFQ': r'\bquotation\b', 'RFP': r'\bproposal\b', 'RFT': r'\btender\b'}
        request_matches = [(code, match.group(0)) for code, pattern in weak_patterns.items()
                           for unit in units if not UNCERTAIN_STATEMENT.search(unit)
                           and not re.search(r'\b(?:fee|invoice|payment|penalty|signature|support\s+team)\b', unit, re.I)
                           if (match := re.search(pattern, unit, re.I))]
        if request_matches:
            result['request_type_basis'] = 'keyword'
    unique_value("request_type_code", request_matches)
    result['request_match_strength'] = (
        result['request_type_basis'] if result['request_type_code'] else
        'conflicting' if request_matches else 'none'
    )
    if result["request_type_code"]:
        result["request_type"] = {
            'EOI': 'Expression of interest', 'EIO': 'EIO', 'RFT': 'Request for tender',
            'RFQ': 'Request for quotation', 'RFP': 'Request for proposal', 'ITT': 'Invitation to tender',
        }[result["request_type_code"]]
        evidence["request_type"] = evidence["request_type_code"]
    else:
        legacy_types = (
            (r"\bRFQ\b|\brequest for quotation\b", "Request for quotation"),
            (r"\bRFP\b|\brequest for proposal\b", "Request for proposal"),
            (r"\bITT\b|\binvitation to tender\b", "Invitation to tender"),
            (r"\btender\b", "Tender"), (r"\bclarification\b", "Clarification"),
            (r"\bpurchase order\b", "Purchase order"), (r"\bcomplaint\b", "Complaint"),
            (r"\binvoice\b", "Invoice"), (r"\bmeeting\b", "Meeting request"),
        )
        if not request_matches:
            result["request_type"] = next(
                (label for pattern, label in legacy_types
                 if any(re.search(pattern, unit, re.I) for unit in units
                        if not UNCERTAIN_STATEMENT.search(unit))), "General client email",
            )

    references = list(re.finditer(r"\b((?:EOI|EIO|RFT|RFQ|RFP|ITT)[-_/][A-Z0-9][A-Z0-9._/-]{2,})\b", content, re.I))
    labelled_refs = list(re.finditer(r"\b(?:EOI|EIO|RFT|RFQ|RFP|ITT|Tender|Client)\s*(?:No\.?|Number|Ref(?:erence)?|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", content, re.I))
    labelled_refs += list(re.finditer(r"\bEvent\s*(?:(?:ID|Reference|Number|No\.?)\s*)?[:#-]?\s*((?=[A-Z0-9._/-]*\d)[A-Z][A-Z0-9._/-]{2,})\b", content, re.I))
    unique_value("tender_reference", [(match.group(1), match.group(0)) for match in references + labelled_refs])

    money_pattern = r"^[ \t]*(?:Estimated\s+contract\s+value|Estimated\s+value|Contract\s+value|Budget)[ \t]*:[ \t]*(?:([A-Z]{3})[ \t]+)?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?:[ \t]+([A-Z]{3}))?[ \t]*$"
    money_matches = list(re.finditer(money_pattern, content, re.I | re.M))
    unique_value("estimated_value", [(match.group(2).replace(",", ""), match.group(0)) for match in money_matches])
    currencies = [(match.group(1) or match.group(3) or "", match.group(0)) for match in money_matches]
    currencies += [(match.group(1), match.group(0)) for match in re.finditer(r"^[ \t]*Currency[ \t]*:[ \t]*([A-Z]{3})[ \t]*$", content, re.I | re.M)]
    unique_value("currency", [(value.upper(), source) for value, source in currencies])

    scope_types = (
        (r"\bpre[ -]feed\b", "pre_feed"), (r"(?<!pre-)(?<!pre )\bfeed\b", "feed"),
        (r"\bdetailed\s+engineering\b", "detailed_engineering"),
        (r"\bbasic\s+engineering\b", "basic_engineering"), (r"\bconceptual\b", "conceptual"),
        (r"\bEPCM\b", "epcm"), (r"\bEPC\b", "epc"), (r"\bPMC\b", "pmc"),
        (r"\bowner['’]?s?\s+engineer\b", "owner_engineer"), (r"\bfeasibility\b", "feasibility"),
    )
    unique_value("scope_type", [(value, match.group(0)) for pattern, value in scope_types if (match := re.search(pattern, content, re.I))])
    industry = result["industry"].lower()
    if any(term in industry for term in ("energy", "power", "utilities")):
        result["industry_type"] = "power_generation"
    elif any(term in industry for term in ("oil", "gas")):
        result["industry_type"] = "oil_gas"
    elif "water" in industry:
        result["industry_type"] = "water_treatment"
    result["evidence"] = evidence
    result["warnings"] = warnings
    return result


def extract_email_information(
    *, subject='', body_text='', sender_email='', sender_name='', received_at='',
    sent_at='', has_attachments=False, mailbox_address='', coverage=None,
):
    """Analyze one available body, including its quoted/forwarded email chain."""
    from .email_analysis import analyze_email_conversation

    result = analyze_email_conversation([{
        'id': 'selected-message', 'subject': subject, 'body_text': body_text,
        'sender_email': sender_email, 'sender_name': sender_name,
        'received_at': received_at, 'sent_at': sent_at, 'has_attachments': has_attachments,
    }], selected_message_id='selected-message', mailbox_address=mailbox_address, coverage=coverage)
    return {**result['extracted_information'], 'analysis': result['analysis']}
