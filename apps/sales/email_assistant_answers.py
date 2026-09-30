"""Concise presentations of verified email evidence, without provider calls.

This does not extract opportunity fields or establish a current deadline. The
caller retains the original, expanded citations and all source/access checks.
Unsupported wording returns None rather than dropping material qualifications.
"""
import re
from datetime import date, datetime

from .email_extraction import DATE_TOKEN, _date_value


_MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_DATE = re.compile(r'(?<!\d)(?:' + DATE_TOKEN + r'|\d{1,2}' + _MONTH + r'\d{4})(?!\d)', re.I)
_SHORT_DATE = re.compile(r'\b\d{1,2}(?:st|nd|rd|th)?\s+' + _MONTH + r'\b(?![\s,]+\d{4})', re.I)
_RELATIVE = re.compile(r'\b(?:today|tomorrow|tonight|next\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|week)|this\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b', re.I)
_CLOCK = re.compile(r'(?<![\d:])(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?(?![A-Za-z\d:])', re.I)
_ZONE = re.compile(r'\b(?:Gulf Standard Time|UAE\s+(?:local\s+)?time|Oman\s+(?:local\s+)?time|(?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?|GST|EST|EDT|CST|CDT|MST|MDT|PST|PDT|BST|CET|CEST|EET|EEST|IST|AST|SGT|HKT)\b', re.I)
_CONNECTOR = re.compile(r'\b(?:by|until|before|no\s+later\s+than|not\s+later\s+than)\b', re.I)
_DEADLINE = re.compile(r'\b(?:deadline|due(?:\s+date)?|closing(?:\s+date)?|close[sd]?\s+(?:on|at|by))\b', re.I)
_ACTION = re.compile(r'\b(?:confirm|confirmation|send|submit|submission|return|provide|respond|reply|express|acknowledge|share|complete|sign|stamp|upload|required)\b', re.I)
_CORRECTION = re.compile(r'\b(?:correction|cancelled|canceled|withdrawn|superseded|revised|extended|rescheduled|postponed|replaced|obsolete)\b|\bno\s+longer\s+(?:valid|applicable)\b', re.I)
_QUALIFIED = re.compile(r'\b(?:if|unless|could|might|hypothetical|proposed|unconfirmed|tentative|subject\s+to|for\s+example|sample\s+deadline)\b', re.I)
_INSTRUCTION = re.compile(r'\b(?:ignore|override|reveal|disclose)\b[^.!?]{0,100}\b(?:instructions?|prompts?|keys?|secrets?)\b|(?:^|\n)\s*(?:system|assistant)\s*:', re.I)
_BRIDGE = re.compile(r'^[\s,;:()\-–]*(?:(?:on|at|by|before|date|time|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b[\s,;:()\-–]*)*$', re.I)
_PARTICIPATION = re.compile(r'\bif\s+(?:(?:you\s+are\s+)?interested(?:\s+in\s+participating)?|you\s+(?:wish|want)\s+to\s+participate)\s*,', re.I)


def _plain(text):
    return re.sub(r'\s+', ' ', text).strip()


def _anchored(text):
    return bool(_DEADLINE.search(text) or (_ACTION.search(text) and _CONNECTOR.search(text)))


def _date_label(raw):
    value = _date_value(raw)
    if not value:
        compact = re.fullmatch(r'(\d{1,2})(' + _MONTH + r')(\d{4})', raw, re.I)
        if compact:
            value = _date_value(' '.join(compact.groups()))
    if value:
        parsed = date.fromisoformat(value)
        return f'{parsed.day} {parsed:%b %Y}', ''
    if re.fullmatch(r'\d{1,2}[/-]\d{1,2}[/-]\d{4}', raw):
        first, second, _ = map(int, re.split(r'[/-]', raw))
        if 1 <= first <= 12 and 1 <= second <= 12 and first != second:
            return raw, 'Day/month order is ambiguous.'
    return None


def _purpose(owner):
    # A reply acknowledging a proposal is not its submission deadline.
    acknowledgement = bool(re.search(r'\b(?:confirm|acknowledge)\b[^.!?]{0,50}\b(?:receipt|received)\b', owner, re.I))
    if acknowledgement:
        return _literal_purpose(owner)
    if re.search(r'\bEOI\b|expression\s+of\s+interest|\bconfirm(?:ation)?\b[^.!?]{0,80}\binterest\b', owner, re.I):
        if _submission_purpose(owner, r'(?:proposal|quotation|tender|bid)'):
            return _literal_purpose(owner)
        return 'Expression of interest response deadline'
    if re.search(r'\bconfirm(?:ation)?\b[^.!?]{0,80}\bparticipat', owner, re.I):
        return 'Participation confirmation deadline'
    if re.search(r'\b(?:agreement|work\s+order)\b', owner, re.I) and re.search(r'\b(?:return|sign(?:ed|ing)?|stamp(?:ed|ing)?)\b', owner, re.I):
        return 'Agreement return deadline'
    for expression, label in (
        ('proposal', 'Proposal submission deadline'),
        ('quotation', 'Quotation deadline'),
        (r'(?:tender|bid)', 'Tender submission deadline'),
    ):
        if _submission_purpose(owner, expression):
            return label
    return _literal_purpose(owner)


def _submission_purpose(owner, noun):
    patterns = (
        r'\b' + noun + r'\s+(?:submission\s+)?(?:deadline|due|closing|required)\b',
        r'\b(?:submit|send|return|provide)\s+(?:(?:your|our|the|a|technical|commercial|budgetary|revised|final)\s+)*' + noun + r'\b',
        r'\b(?:submission|submitting)\b[^.!?]{0,100}\b' + noun + r'\b',
        r'\b(?:deadline|closing)\s+(?:(?:for|of|the)\s+)*' + noun + r'\b',
    )
    return any(re.search(pattern, owner, re.I) for pattern in patterns)


def _literal_purpose(owner):
    # Keep an unfamiliar action's literal words instead of guessing its type.
    clause = _CONNECTOR.split(owner, maxsplit=1)[0].strip(' \t:,-–')
    clause = re.sub(r'\s+(?:has\s+been|is|was)\s+set\s+to$', '', clause, flags=re.I)
    if not clause or len(clause) > 150 or '\n' in clause:
        return None
    return clause


def _clock_and_zone(clause, dated):
    clocks = []
    zones = list(_ZONE.finditer(clause))
    literal_clocks = [clock for clock in _CLOCK.finditer(clause)
                      if (clock['minute'] or clock['meridiem'])
                      and not any(zone.start() <= clock.start() < zone.end() for zone in zones)]
    if len(literal_clocks) > 1:
        return None  # Do not choose between two clocks in the same clause.
    for clock in literal_clocks:
        zone = None
        if clock.end() <= dated.start():
            bridge = clause[clock.end():dated.start()]
            zone = _ZONE.match(bridge.lstrip(' \t\r\n,;()[]'))
            if zone:
                bridge = bridge.lstrip(' \t\r\n,;()[]')[zone.end():]
        elif clock.start() >= dated.end():
            bridge = clause[dated.end():clock.start()]
        else:
            continue
        if not _BRIDGE.fullmatch(bridge):
            continue
        hour, minute = int(clock['hour']), int(clock['minute'] or 0)
        meridiem = re.sub(r'\.', '', clock['meridiem'] or '').upper()
        if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
            return None
        value = f'{hour}:{minute:02d} {meridiem}' if meridiem else f'{hour:02d}:{minute:02d}'
        tail = clause[clock.end():]
        zone = zone or _ZONE.match(tail.lstrip(' \t\r\n,;()[]'))
        if not zone and clock.end() <= dated.start():
            zone = _ZONE.match(clause[dated.end():].lstrip(' \t\r\n,;()[]'))
        clocks.append((value, _plain(zone.group()) if zone else ''))
    clocks = list(dict.fromkeys(clocks))
    if len(clocks) > 1:
        return None
    if clocks:
        return clocks[0]
    zone = _ZONE.match(clause[dated.end():].lstrip(' \t\r\n,;()[]'))
    return '', _plain(zone.group()) if zone else ''


def _candidate(clause, previous, following):
    matches = list(_DATE.finditer(clause))
    qualification = ''
    if not matches:
        matches = list(_RELATIVE.finditer(clause))
        qualification = 'Relative date; confirm the calendar date.'
    if not matches:
        matches = list(_SHORT_DATE.finditer(clause))
        qualification = 'Year not stated.'
    if not matches:
        return []
    result = []
    for dated in matches:
        prefix = clause[:dated.start()]
        if _anchored(prefix):
            owner = prefix
        elif _BRIDGE.fullmatch(prefix) or re.fullmatch(r'\s*(?:Date\s*:\s*)?', prefix, re.I):
            # Only a clearly continued owning request may label a date-only
            # block. A subject mentioning deadlines cannot label a mail header.
            if not previous.endswith(':') or not _anchored(previous):
                continue
            owner = previous
        else:
            continue
        purpose = _purpose(_plain(owner))
        if not purpose:
            return None
        parsed = (dated.group(), qualification) if qualification else _date_label(dated.group())
        if parsed is None:
            return None
        weekday = re.search(r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*$', prefix, re.I)
        if weekday and not qualification and not parsed[1]:
            actual_weekday = datetime.strptime(parsed[0], '%d %b %Y').strftime('%A')
            if weekday.group(1).casefold() != actual_weekday.casefold():
                return None
        # A dedicated Time block can continue a Date block; a briefing/event
        # sentence after it cannot supply the submission time.
        clock_clause = clause
        if re.match(r'^Time\s*:', following, re.I) and not _DATE.search(following):
            clock_clause += ' ' + following
        clock = _clock_and_zone(clock_clause, dated)
        if clock is None:
            return None
        participation = bool(_PARTICIPATION.search(owner))
        details_action = bool(
            re.search(r'\bconfirm\b[^.!?]{0,60}\binterest\b', owner, re.I)
            and re.search(r'\b(?:provide|send|share)\s+(?:(?:us|the|your|following|requested)\s+)*(?:following|details|information)\b', owner, re.I)
        )
        result.append({'purpose': purpose, 'date': parsed[0], 'qualification': parsed[1],
                       'time': clock[0], 'zone': clock[1], 'owner': _plain(owner),
                       'context': (previous, clause, following), 'participation': participation,
                       'details_action': details_action})
    return result


def _qualification_text(clauses):
    values = []
    for clause in clauses:
        if (re.search(r'\bnot\s+a\s+commitment\b[^.!?]{0,120}\baward\b[^.!?]{0,40}\bcontract\b', clause, re.I)
                and not _DEADLINE.search(clause) and not _DATE.search(clause)):
            continue  # This narrowly identified award disclaimer does not negate a response deadline.
        values.append(_PARTICIPATION.sub('', clause))
    return ' '.join(values)


def deadline_answer(citations):
    """Return a short qualified deadline answer, or None for unsafe wording.

    Input must already be source-verified, context-restored citations. No dates
    are inferred from receipt times, current time, sender domain or location.
    """
    if not isinstance(citations, list) or not 1 <= len(citations) <= 8:
        return None
    candidates, texts, qualifying_clauses = [], [], []
    for citation in citations:
        if not isinstance(citation, dict) or not isinstance(citation.get('excerpt'), str):
            return None
        text = citation['excerpt'].strip()
        if not text or len(text) > 1800 or _INSTRUCTION.search(text):
            return None
        texts.append(text)
        # Splitting leaves time abbreviations such as p.m. attached to a date.
        clauses = [_plain(clause) for block in re.split(r'\n\s*\n', text)
                   for clause in re.split(r'(?<=[.!?])\s+(?=[A-Z])', block) if clause.strip()]
        qualifying_clauses.extend(clause for clause in clauses if _DEADLINE.search(clause) or _DATE.search(clause))
        for index, clause in enumerate(clauses):
            found = _candidate(clause, clauses[index - 1] if index else '',
                               clauses[index + 1] if index + 1 < len(clauses) else '')
            if found is None:
                return None
            for candidate in found:
                qualifying_clauses.extend(candidate['context'])
                signature = tuple(candidate[name] for name in ('purpose', 'date', 'time', 'zone', 'qualification'))
                if all(signature != row[0] for row in candidates):
                    candidates.append((signature, candidate))
    if not candidates or len(candidates) > 3:
        return None
    rows = [row for _, row in candidates]
    context = ' '.join(texts)
    correction = bool(_CORRECTION.search(context) or (len(rows) > 1 and re.search(
        r'\b(?:new|final|updated)\s+(?:deadline|due\s+date|closing\s+date)\b', context, re.I)))
    qualification_text = _qualification_text(qualifying_clauses)
    qualified = bool(_QUALIFIED.search(qualification_text))
    # Never promote a negative statement into an affirmative deadline. Standard
    # "not later than" and "no later than" clauses remain affirmative requests.
    negative_context = re.sub(r'\b(?:not|no)\s+later\s+than\b', '', qualification_text, flags=re.I)
    negated = bool(re.search(r"\b(?:not|never|no\s+(?:deadline|submission)|(?:is|are|was|were|do|does|did|wo|ca)n['’]t)\b",
                            negative_context, re.I))
    several_same_purpose = any(sum(row['purpose'] == other['purpose'] for other in rows) > 1 for row in rows)
    lines = []
    for row in rows:
        value = row['date'] + (', ' + row['time'] if row['time'] else '')
        if row['zone']:
            value += ' ' + row['zone']
        line = f"{row['purpose']}: {value}."
        if row['qualification']:
            line += ' ' + row['qualification']
        if not row['zone']:
            line += ' Timezone not stated.' if row['time'] else ' Time and timezone not stated.'
        elif not row['time']:
            line += ' Time not stated.'
        if row['participation'] and not row['details_action']:
            line += ' Applies if participating.'
        lines.append(line)
        if row['details_action']:
            prefix = 'If participating: ' if row['participation'] else 'Requested action: '
            lines.append(prefix + 'Confirm interest and provide the requested details.')
    if correction or qualified or negated or several_same_purpose:
        reasons = []
        if correction:
            reasons.append('correction or cancellation wording')
        if qualified or negated:
            reasons.append('conditional or negative wording')
        if several_same_purpose:
            reasons.append('multiple stated dates for the same request')
        lines.insert(0, 'Deadline needs review (' + '; '.join(reasons) + '). Dates mentioned below are not confirmed current:')
    answer = '\n'.join(lines)
    return answer if len(answer) <= 600 else None
