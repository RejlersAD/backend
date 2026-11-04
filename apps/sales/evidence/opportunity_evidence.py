"""Deterministic, source-cited extraction of opportunity requirements."""

import re


_OBLIGATION = re.compile(
    r'\b(?:shall|must|required\s+to|is\s+required\s+to|are\s+required\s+to|'
    r'mandatory|need\s+to|needs\s+to|will\s+(?:provide|prepare|submit|deliver|complete|execute|perform))\b', re.I,
)
_ACTION = re.compile(
    r'\b(?:provide|prepare|submit|deliver|develop|define|establish|execute|perform|'
    r'create|implement|review|validate|complete|obtain|maintain|produce|conduct|'
    r'coordinate|support|ensure|propose|document|resolve|monitor|assess|identify|'
    r'plan|test|train|approve|sign[ -]?off|migrate|reconcile|onboard)\w*\b', re.I,
)
_BIDDER = re.compile(r'\b(?:bidder|supplier|vendor|consultant|contractor)\b', re.I)
_RESPONSIBILITY = re.compile(r'\b(?:is|are)\s+responsible\s+for\b', re.I)
_ARTIFACT = re.compile(
    r'\b(?:proposal|response|document|deliverable|report|plan|matrix|schedule|'
    r'scenario|script|training|material|assessment|architecture|design|template|'
    r'process|framework|reconciliation|data|test|cutover|migration|support|'
    r'certificate|CVs?|risk|mitigation|approval|requirement|submission|bid|'
    r'technical|commercial)\w*\b', re.I,
)
_DATE = re.compile(
    r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|'
    r'\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|'
    r'Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|'
    r'Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}|Q[1-4]\s+\d{4})\b', re.I,
)
_DEADLINE = re.compile(r'\b(?:deadline|due\s+date|closing\s+date|submit(?:ted|s|ting)?\s+by|no\s+later\s+than)\b', re.I)
_CLARIFICATION = re.compile(r'\b(?:clarification|clarifications|RFI|questions?\s+to\s+(?:the\s+)?client)\b', re.I)
_RISK = re.compile(r'\b(?:risk|risks|risk\s+mitigation|risk\s+acceptance|critical\s+finding|high\s+finding|block(?:ing|er))\b', re.I)
_COMPLIANCE = re.compile(r'\b(?:comply|compliance|mandatory|in\s+compliance|standard|policy|security\s+control|approval\s+required|must\s+not)\b', re.I)
_MILESTONE = re.compile(r'\b(?:milestone|feasibility\s+study|requirements?\s+collection|system\s+design|implementation|acceptance\s+test|go[ -]?live|cutover|rollout|hypercare|project\s+plan|design\s+phase|deployment\s+phase)\b', re.I)
_DATE_RANGE = re.compile(r'\b(?:within|no later than|by)\s+(\d{1,3})\s+(?:calendar\s+|working\s+|business\s+)?days?\b', re.I)
_SECTION = re.compile(
    r'\b(?:scope\s+of\s+work|expected\s+content\s+of\s+(?:the\s+)?response|'
    r'technical\s+proposal|commercial\s+proposal|deliverables?|requirements?|'
    r'clarifications?|project\s+plan|milestones?|risks?|compliance|'
    r'submission\s+requirements?|testing\s+governance|security\s+baseline)\b', re.I,
)
_TOC_DOTS = re.compile(r'\.{3,}|…{2,}')
_TOC_SUFFIX = re.compile(r'\s+(?:\d+|\d+\.\d+)\s*$')
_BOILERPLATE = re.compile(
    r'^(?:page\s+\d+|ADNOC\s+Classification|contents|table\s+of\s+contents|'
    r'figure\s+\d+|section\s+\d+(?:\.\d+)*|\d+(?:\.\d+)*\s*)$', re.I,
)


def _normal_lines(text):
    lines = [re.sub(r'\s+', ' ', line).strip(' \t•▪◦*-') for line in (text or '').splitlines()]
    return [line for line in lines if line]


def _passages(text):
    """Build bounded sentence or list-item excerpts without joining unrelated text."""
    lines = _normal_lines(text)
    passages = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _BOILERPLATE.fullmatch(line) or _TOC_DOTS.search(line):
            index += 1
            continue
        # Headings and numbered contents entries are section labels, not claims.
        if _SECTION.search(line) and (_TOC_SUFFIX.search(line) or len(line) < 90):
            index += 1
            continue
        current = line
        while index + 1 < len(lines) and len(current) < 1200:
            following = lines[index + 1]
            if (_BOILERPLATE.fullmatch(following) or _TOC_DOTS.search(following)
                    or _SECTION.search(following) and (_TOC_SUFFIX.search(following) or len(following) < 90)
                    or re.match(r'^(?:\d+[.)]|[A-Z][.)]|[o•▪◦])\s+', following)
                    or re.search(r'[.!?]\s*$', current)):
                break
            index += 1
            current += ' ' + following
        # PDF extraction can place several sentences on one line. Split them,
        # retaining the exact words and avoiding a page-sized evidence claim.
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', current):
            sentence = sentence.strip()
            if sentence:
                passages.append(sentence[:1200])
        index += 1
    return passages


def _classifications(text):
    obligation = bool(_OBLIGATION.search(text))
    action = bool(_ACTION.search(text))
    artifact = bool(_ARTIFACT.search(text))
    date = _DATE.search(text)
    clarification = bool(_CLARIFICATION.search(text))
    classifications = []
    if date and (_DEADLINE.search(text) or clarification):
        classifications.append(('deadline', 'clarification_deadline' if clarification else 'submission_deadline'))
    elif _DATE_RANGE.search(text) and (_DEADLINE.search(text) or clarification):
        classifications.append(('deadline', 'clarification_deadline' if clarification else 'submission_deadline'))
    if _RISK.search(text):
        classifications.append(('risk', 'risk'))
    if _COMPLIANCE.search(text):
        classifications.append(('submission', 'compliance_requirement'))
    assigned = bool(_BIDDER.search(text) and (obligation or _RESPONSIBILITY.search(text)
                    or re.search(r'\bbidder\s+to\b', text, re.I)))
    if action and artifact and (assigned or obligation and artifact):
        if re.search(r'\b(?:submit|submission|proposal|response|bidder|bid)\b', text, re.I):
            classifications.append(('submission', 'required_submission'))
        if re.search(r'\b(?:deliverable|deliver|produce|prepare|provide|report|plan|matrix|template|document|certificate|CV)\b', text, re.I):
            classifications.append(('submission', 'deliverable'))
        if assigned or obligation:
            classifications.append(('action', 'action_item'))
    if _MILESTONE.search(text):
        classifications.append(('milestone', 'milestone'))
    if clarification and not date and (obligation or action):
        classifications.append(('clarification', 'clarification_requirement'))
    return list(dict.fromkeys(classifications))


def extract_opportunity_evidence(text, *, source_id, source_name, source_location):
    """Return exact-source claims and deterministic parse diagnostics."""
    lines = _normal_lines(text)
    sections = list(dict.fromkeys(line[:180] for line in lines if len(line) <= 180 and _SECTION.search(line)
                                   and not re.search(r'\.{3,}|\s\d+(?:\.\d+)?\s*$', line)))
    extracted, rejected = [], []
    seen = set()
    for passage in _passages(text):
        if len(passage) < 24 or _BOILERPLATE.fullmatch(passage):
            continue
        matches = _classifications(passage)
        indicators = bool(_OBLIGATION.search(passage) or _RISK.search(passage) or _COMPLIANCE.search(passage)
                          or _MILESTONE.search(passage) or _CLARIFICATION.search(passage) or _DEADLINE.search(passage))
        if not matches:
            if indicators:
                rejected.append({'text': passage, 'excerpt': passage, 'source_name': source_name,
                                 'source_location': source_location, 'status': 'rejected',
                                 'reason': 'A review keyword matched, but the deterministic rule could not identify a supported evidence category.'})
            continue
        for kind, evidence_type in matches:
            identity = (kind, re.sub(r'\W+', ' ', passage.casefold()).strip())
            if identity in seen:
                continue
            seen.add(identity)
            date = _DATE.search(passage)
            extracted.append({
                'kind': kind, 'evidence_type': evidence_type, 'text': passage,
                'excerpt': passage, 'date': (date.group(0) if date else _DATE_RANGE.search(passage).group(0)
                         if kind == 'deadline' and _DATE_RANGE.search(passage) else '') if kind == 'deadline' else '',
                'source_id': source_id, 'name': source_name, 'location': source_location,
                'source_name': source_name, 'source_location': source_location,
                'status': 'likely', 'confidence': 86.0,
                'reason': f'Deterministic {evidence_type.replace("_", " ")} rule matched the source text; reviewer confirmation is required.',
            })
    return {'sections_identified': sections, 'extracted': extracted, 'rejected': rejected,
            'parsed_characters': len(text or '')}
