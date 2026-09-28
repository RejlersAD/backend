"""Conservative, local email suggestions. Source text never grants authority."""

import re
from datetime import date, datetime


MAX_CONTENT = 250_000
DATE_TOKEN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?[\s-]+[A-Za-z]{3,9}[\s,-]+\d{4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})"
)


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


def extract_email_information(*, subject="", body_text="", sender_email=""):
    """Return editable suggestions and short evidence, without external AI calls.

    Only explicit labels establish a customer/date/value. Multiple different
    candidates remain unresolved. Dates are distinct from email timestamps.
    Legacy extraction keys remain available to existing imported-intake clients.
    """
    subject = str(subject or "")
    raw_content = f"{subject}\n{body_text or ''}"
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
        "industry", "scope_summary", "scope_type",
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

    labels = {
        "customer_name": r"(?:Company|Client|Customer|Organisation|Organization)(?:\s+name)?",
        "declared_client_domain": r"Client\s+domain",
        "contact_name": r"Contact\s+(?:person|name)",
        "contact_email": r"Contact\s+email",
        "contact_phone": r"Contact\s+phone",
        "location": r"Project\s+location",
        "industry": r"Industry",
        "scope_summary": r"(?:Scope\s+summary|Project\s+scope|Scope\s+of\s+work)",
    }
    for key, label in labels.items():
        pattern = rf"^[ \t]*(?:{label})[ \t]*(?::[ \t]*|\t+)([^\n]+)"
        unique_value(key, [(match.group(1), match.group(0)) for match in re.finditer(pattern, content, re.I | re.M)])
    result["company_name"] = result["customer_name"]

    date_labels = {
        "submission_date": r"(?<!required )(?<!required\s)submission\s+date|date\s+of\s+submission",
        "due_date": r"proposal\s+deadline|submission\s+deadline|submission\s+due\s+date|tender\s+closing\s+date|closing\s+date|proposal\s+required\s+by|required\s+submission\s+date|required\s+by|due\s+date|deadline",
        "expected_award_date": r"expected\s+award\s+date|anticipated\s+award\s+date|contract\s+award\s+date|award\s+expected\s+by",
    }
    for key, label in date_labels.items():
        matches = list(re.finditer(rf"\b(?:{label})[ \t]*[:\-]?[ \t]*(?P<date>{DATE_TOKEN})(?!\d)", content, re.I))
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
    }
    request_matches = [(code, match.group(0)) for code, pattern in request_patterns.items()
                       if (match := re.search(pattern, content, re.I))]
    unique_value("request_type_code", request_matches)
    if result["request_type_code"]:
        result["request_type"] = result["request_type_code"]
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
        result["request_type"] = next((label for pattern, label in legacy_types if re.search(pattern, content, re.I)), "General client email")

    references = list(re.finditer(r"\b((?:EOI|EIO|RFT|RFQ|RFP|ITT)[-_/][A-Z0-9][A-Z0-9._/-]{2,})\b", content, re.I))
    labelled_refs = list(re.finditer(r"\b(?:EOI|EIO|RFT|RFQ|RFP|ITT|Tender|Client)\s*(?:No\.?|Number|Ref(?:erence)?|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", content, re.I))
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
