"""Source-backed agreement actions, separate from tender submission facts."""

import re
from datetime import datetime

from .email_extraction import DATE_TOKEN, UNCERTAIN_STATEMENT, _date_value


AGREEMENT_REFERENCE = re.compile(
    r'\b(?:(?:WO|work\s+order)\s+agreement|work\s+order|agreement\s+(?:number|no\.?|reference|ref\.?))'
    r'\s*(?:(?:number|no\.?|reference|ref\.?)\s*)?[:#-]?\s*(?P<value>\d{5,20})\b', re.I,
)
CORRESPONDENCE_REFERENCE = re.compile(r'(?<![\w/-])Q-\d{3,20}(?![\w/-])', re.I)
AGREEMENT_WORD = re.compile(r'\b(?:agreement|contract|work\s+order|WO)\b', re.I)
RETURN_WORD = re.compile(r'\b(?:return|send|submit|provide|deliver|share)\b', re.I)
SOLICITATION_WORD = re.compile(r'\b(?:proposal|quotation|tender|bid)\b', re.I)
DATE_LINK = re.compile(
    r'\b(?:no\s+later\s+than|on\s+or\s+before|by|before|due(?:\s+date)?|deadline)\s*'
    r'(?:(?:is|was|will\s+be|shall\s+be)\s*)?[:=-]?\s*'
    r'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?'
    rf'(?P<date>{DATE_TOKEN})(?!\d)', re.I,
)
CLOCK = re.compile(r'\s*[,;]?\s*(?:(?:at|time\s*:)\s*)?(?P<time>\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)(?!\d)', re.I)
ZONE = re.compile(r'\s*\(?\s*(?P<zone>UAE\s+time|Gulf\s+Standard\s+Time|GST|UTC(?:\s*[+-]\s*\d{1,2}(?::?\d{2})?)?|GMT(?:\s*[+-]\s*\d{1,2}(?::?\d{2})?)?)(?![\w:+-])', re.I)


def _normalized(value):
    return ' '.join(str(value or '').split()).casefold()


def _agreement_date_clauses(text, *, agreement_context=False):
    """Identify date ownership even when its value is uncertain or invalid."""
    for match in DATE_LINK.finditer(text):
        # Keep the preceding request within one paragraph/sentence, with a
        # bounded allowance for ordinary email line wrapping.
        before = text[max(0, match.start() - 650):match.start()]
        boundaries = list(re.finditer(r'\n\s*\n|[.!?;](?:\s|$)', before))
        before = before[boundaries[-1].end():] if boundaries else before
        clause_start = match.start() - len(before)
        leading = before.strip()
        returns = list(RETURN_WORD.finditer(leading))
        labelled_return = bool(re.search(r'\bagreement\s+(?:return|signature|execution)\s*$', leading, re.I))
        if not returns and not labelled_return:
            continue
        action = leading[returns[-1].start():] if returns else leading
        if (SOLICITATION_WORD.search(action)
                or re.search(r'\b(?:meeting|briefing|expires?|expiry|validity|issued|sent|received|published)\b', action, re.I)):
            continue
        explicit_agreement = bool(AGREEMENT_WORD.search(action))
        signed_documents = bool(re.search(r'\b(?:signed|stamped|executed)\b', action, re.I)
                                and re.search(r'\b(?:copy|copies|documents?|originals?)\b', action, re.I))
        # "Share the above" can refer to the immediately preceding numbered
        # agreement-signing checklist. It cannot borrow context from elsewhere
        # in the conversation or from an unrelated intervening paragraph.
        preceding = text[max(0, clause_start - 1800):clause_start].rstrip()
        paragraphs = re.split(r'\n\s*\n', preceding)
        preceding = paragraphs[-1]
        numbered = re.match(r'^\s*(\d{1,2})[.)]\s+', preceding)
        if numbered:
            # HTML list items may become separate paragraphs. Walk only a
            # contiguous descending numbered list, never unrelated prose.
            checklist, expected = [preceding], int(numbered[1]) - 1
            for paragraph in reversed(paragraphs[:-1][-7:]):
                number = re.match(r'^\s*(?:To\s+enable\s+us\s+to\s+proceed,?\s+kindly:\s*)?(\d{1,2})[.)]\s+', paragraph, re.I)
                if not number or int(number[1]) != expected:
                    break
                checklist.insert(0, paragraph)
                expected -= 1
            preceding = '\n\n'.join(checklist)
        references_above = bool(re.fullmatch(
            r'(?:return|send|submit|provide|deliver|share)\s+(?:the\s+)?(?:above|(?:above|these|those)\s+(?:documents|copies|items))\s*', action, re.I))
        agreement_checklist = bool(AGREEMENT_WORD.search(preceding)
                                   and re.search(r'\b(?:initial|sign(?:ing|ed)?|stamp(?:ing|ed)?)\b', preceding, re.I)
                                   and not SOLICITATION_WORD.search(preceding)
                                   and not re.search(r'\b(?:meeting|briefing)\b', preceding, re.I)
                                   and not UNCERTAIN_STATEMENT.search(preceding))
        if not (explicit_agreement or labelled_return or (agreement_context and (signed_documents or (references_above and agreement_checklist)))):
            continue
        if returns and not labelled_return:
            clause_start += len(before) - len(before.lstrip()) + returns[-1].start()
        yield match, clause_start, before


def agreement_return_deadlines(text, *, agreement_context=False):
    """Find an agreement-return clause and its own date/clock, never a nearby date."""
    candidates = []
    text = str(text or '')[:250_000]
    for match, clause_start, before in _agreement_date_clauses(text, agreement_context=agreement_context):
        if (UNCERTAIN_STATEMENT.search(before)
                or re.search(r'\b(?:old|previous|previously|superseded)\b', before, re.I)):
            continue
        if re.match(r'\s*(?:or|and|/|to|[-\u2013])\s*(?:by\s+)?'
                    r'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?'
                    + DATE_TOKEN, text[match.end():match.end() + 180], re.I):
            continue
        if re.search(DATE_TOKEN + r'\s+(?:or|and)(?:\s+by)?\s*$', before, re.I):
            continue
        excerpt_end = match.end()
        clock = CLOCK.match(text[excerpt_end:])
        if clock and (len(clock.group()) > 80 or re.search(r'\n\s*\n', clock.group())):
            clock = None
        time, zone = '', ''
        if clock:
            raw_time = re.sub(r'\.', '', clock['time']).upper().strip()
            for pattern in ('%H:%M', '%I:%M %p', '%I:%M%p'):
                try:
                    time = datetime.strptime(raw_time, pattern).strftime('%H:%M')
                    break
                except ValueError:
                    pass
            excerpt_end += clock.end()
            zone_match = ZONE.match(text[excerpt_end:])
            if zone_match and re.search(r'\n\s*\n', zone_match.group()):
                zone_match = None
            if zone_match and time:
                zone = zone_match['zone'].strip()
                excerpt_end += zone_match.end()
                if text[excerpt_end:excerpt_end + 1] == ')':
                    excerpt_end += 1
        remainder = re.split(r'[.!?](?:\s|$)|\n\s*\n', text[excerpt_end:excerpt_end + 300], maxsplit=1)[0]
        if (UNCERTAIN_STATEMENT.search(remainder)
                or re.search(r'\b(?:subject\s+to\s+confirmation|tentative|provisional|unconfirmed|TBC)\b', remainder, re.I)):
            continue
        if re.match(r'\s*[)]?\s*[.!?]\s*(?:please\s+note\s+that\s+)?(?:that|this|the)\s+(?:deadline|date)\s+'
                    r'[^.!?\n]{0,100}\b(?:cancelled|canceled|withdrawn|superseded|no\s+longer\s+applies)\b',
                    text[excerpt_end:excerpt_end + 220], re.I):
            continue
        excerpt = text[clause_start:excerpt_end].strip()
        if len(excerpt) > 1000 or UNCERTAIN_STATEMENT.search(excerpt):
            continue
        day = _date_value(match['date'])
        if not day:
            continue
        candidates.append({'kind': 'agreement_return', 'date': day, 'time': time, 'timezone': zone,
                           'evidence': excerpt, 'status': 'requires_verification'})
    return candidates


def is_agreement_deadline_evidence(excerpt, source_text='', *, source_span=None):
    """Prevent an agreement date being re-labelled as a proposal due date."""
    if next(_agreement_date_clauses(str(excerpt or ''), agreement_context=True), None):
        return True
    normalized = _normalized(excerpt)
    if not normalized or not source_text:
        return False
    for match, start, _ in _agreement_date_clauses(source_text, agreement_context=bool(AGREEMENT_REFERENCE.search(source_text))):
        if source_span is not None:
            if start <= source_span[0] and source_span[1] <= match.end():
                return True
            continue
        # An AI excerpt may omit the action's object/qualifying suffix. Inspect
        # its owning source clause, including uncertain dates, before accepting
        # a proposal-deadline interpretation of that shortened excerpt.
        suffix = re.split(r'[.!?;](?:\s|$)|\n\s*\n', source_text[match.end():match.end() + 300], maxsplit=1)[0]
        full = _normalized(source_text[start:match.end()] + suffix)
        prefix = _normalized(source_text[start:match.end()])
        if normalized in full or prefix in normalized:
            return True
    return False


def reference_type_allowed(name, value, excerpt, source_text=''):
    """A literal WO/Q identifier cannot silently become another reference type."""
    agreement = any(match['value'].casefold() == value.casefold() for match in AGREEMENT_REFERENCE.finditer(excerpt))
    correspondence = bool(CORRESPONDENCE_REFERENCE.fullmatch(value))
    if name == 'agreement_reference':
        return agreement
    if name == 'correspondence_reference':
        return correspondence and bool(re.search(re.escape(value), excerpt, re.I))
    agreement = agreement or any(match['value'].casefold() == value.casefold() for match in AGREEMENT_REFERENCE.finditer(source_text))
    if not agreement and not correspondence:
        return True
    labels = {
        'tender_reference': r'(?:tender|RFQ|RFP|RFT|ITT|EOI|EIO)',
        'procurement_reference': r'(?:procurement|purchase\s+requisition)',
        'pr_reference': r'(?:PR|purchase\s+requisition)',
    }
    label = labels.get(name)
    return bool(label and re.search(r'\b' + label + r'\s*(?:(?:code|reference|ref|number|no)\.?\s*)?[:#-]?\s*'
                                   + re.escape(value) + r'(?![\w./-])', excerpt, re.I))


def add_agreement_actions(result, analysis, segments):
    """Enrich already-authorized segments; never turn a reply into a new request."""
    usable = [source for source in segments if not source['is_draft'] and source['direction'] != 'outgoing']
    claims = {'agreement_reference': [], 'correspondence_reference': [], 'scope_summary': []}
    deadlines = []
    for source in usable:
        subject, body = source['subject'], source['body']
        text = subject + '\n' + body
        for match in AGREEMENT_REFERENCE.finditer(text):
            claims['agreement_reference'].append((match['value'], match.group(), source['id']))
        for match in CORRESPONDENCE_REFERENCE.finditer(subject):
            claims['correspondence_reference'].append((match.group(), match.group(), source['id']))
        subject_reference = AGREEMENT_REFERENCE.search(subject)
        if subject_reference:
            tail = re.match(r'\s*[-:\u2013\u2014]\s*(?P<scope>[^\n]{10,1000})$', subject[subject_reference.end():])
            if tail:
                claims['scope_summary'].append((tail['scope'].strip(), subject, source['id']))
        for candidate in agreement_return_deadlines(body, agreement_context=bool(AGREEMENT_REFERENCE.search(text))):
            candidate['source_ids'] = [source['id']]
            if not any(all(item[key] == candidate[key] for key in ('date', 'time', 'timezone', 'evidence')) for item in deadlines):
                deadlines.append(candidate)
            else:
                same = next(item for item in deadlines if all(item[key] == candidate[key] for key in ('date', 'time', 'timezone', 'evidence')))
                same['source_ids'] = list(dict.fromkeys([*same['source_ids'], source['id']]))
    evidence, refs = result['evidence'], result['field_sources']
    for key, entries in claims.items():
        if not entries or result.get(key):
            continue
        # Existing conflicts must remain unresolved, not be overwritten by a
        # subject-tail convenience extraction.
        if evidence.get(key) and refs.get(key):
            continue
        refs[key] = list(dict.fromkeys(item[2] for item in entries))
        evidence[key] = ' | '.join(dict.fromkeys(item[1] for item in entries))[:2200]
        if len({_normalized(item[0]) for item in entries}) == 1:
            result[key] = entries[0][0]
        else:
            result[key] = ''
            result['warnings'].append(f'Conflicting {key.replace("_", " ")} values need review.')
    result.setdefault('agreement_reference', '')
    result.setdefault('correspondence_reference', '')
    result['action_deadlines'] = deadlines[:16]

    labels = {'agreement_reference': 'Agreement reference', 'correspondence_reference': 'Correspondence reference', 'scope_summary': 'Scope'}
    for key, label in labels.items():
        if result.get(key) and not any(point['label'] == label and point['value'] == result[key] for point in analysis['key_points']):
            analysis['key_points'].append({'label': label, 'value': result[key], 'source_ids': refs.get(key, [])})
    for deadline in result['action_deadlines']:
        value = ' '.join(part for part in (deadline['date'], deadline['time'], deadline['timezone']) if part)
        analysis['key_points'].append({'label': 'Agreement return deadline', 'value': value, 'source_ids': deadline['source_ids']})
    return result
