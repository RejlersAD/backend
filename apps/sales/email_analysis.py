"""Local, source-backed analysis of available email conversation text."""

import hashlib
import re
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime

from .email_extraction import DATE_TOKEN, MAX_CONTENT, UNCERTAIN_STATEMENT, _date_value, _extract_email_fields, email_text_units
from .email_classification import COMPILED_RULES, classify_email_segment
from .email_intelligence import add_email_intelligence


MAX_MESSAGES = 100
MAX_TEXT = 2_000_000
MAX_SEGMENTS = 300
REPLY_PREFIX = re.compile(r'^(?:(?:re|fw|fwd)\s*:\s*)+', re.I)
QUOTE_MARKER = re.compile(
    r'^\s*(?:-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}|'
    r'Begin forwarded message:|On .{5,400} wrote:)\s*$', re.I,
)
HEADERS = re.compile(r'^\s*(From|Sent|Date|To|Cc|Subject):\s*(.*)$', re.I)
REQUEST_BODY = re.compile(
    r'\b(?:invites?\s+you|invitation\s+to|request\s+for\s+(?:tender|(?:budgetary\s+)?quotation|proposal)|'
    r'please\s+submit|you\s+are\s+(?:invited|requested)\s+to|expression\s+of\s+interest)\b', re.I,
)
REQUEST_CODE = r'(?:RFQ|RFP|RFT|EOI|EIO|ITT)'
REQUEST_RECLASSIFICATION = (
    re.compile(rf'\b(?P<old>{REQUEST_CODE})\b\s+(?:is|has\s+been|was)\s+'
               rf'(?:replaced|superseded|changed|converted)\s+(?:by|with|to)\s+(?:an?\s+)?\b(?P<new>{REQUEST_CODE})\b', re.I),
    re.compile(rf'\brequest\s+type\b[^.!?]{{0,40}}\b(?:changed|converted|revised)\b'
               rf'[^.!?]{{0,30}}\bfrom\s+(?P<old>{REQUEST_CODE})\b[^.!?]{{0,20}}\bto\s+(?P<new>{REQUEST_CODE})\b', re.I),
    re.compile(rf'\b(?:please\s+)?treat\s+this\s+as\s+(?:an?\s+)?(?P<new>{REQUEST_CODE})\b'
               rf'[^.!?]{{0,35}}\b(?:instead\s+of|rather\s+than)\s+(?P<old>{REQUEST_CODE})\b', re.I),
)
ATTACHMENT_TEXT = re.compile(
    r'\b(?:attach(?:ment|ments|ed)|enclosed|download\s+(?:the\s+)?(?:documents?|bulletin|files?)|'
    r'(?:documents?|bulletin|files?)\s+(?:are\s+)?available\s+(?:on|in|via|at)|'
    r'(?:sourcing|supplier|tender|procurement)\s+(?:site|portal))\b', re.I,
)
ACTION = re.compile(
    r'^(?:(?:please|kindly)\s+|(?:we\s+(?:ask|request|invite)\s+you\s+to\s+)|'
    r'(?:you\s+are\s+(?:requested|required|invited)\s+to\s+))?'
    r'(?:submit|provide|confirm|acknowledge|review|return|complete|respond|clarify|send|register|download)\b', re.I,
)


def _plain(value):
    return value if isinstance(value, str) else ''


def _subject(value):
    return REPLY_PREFIX.sub('', _plain(value)).strip()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(_plain(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def email_thread_role(message, *, quote_kind=''):
    """Transport role is independent of business purpose and mailbox direction."""
    def role(value, basis, reason):
        return {'thread_role': value, 'thread_role_basis': basis, 'thread_role_reason': reason}

    if message.get('is_draft') is True:
        return role('draft', 'draft_flag', 'This is unsent draft text.')
    prefix = re.match(r'^\s*(re|fw|fwd)\s*:', _plain(message.get('subject')), re.I)
    if prefix and prefix.group(1).lower() in {'fw', 'fwd'}:
        return role('forward', 'subject_prefix', 'The selected subject has a forward prefix; the forwarded original may be separate.')
    metadata = message.get('_thread_metadata')
    if isinstance(metadata, dict) and (metadata.get('in_reply_to') or metadata.get('references')):
        return role('reply', 'reply_headers', 'The email contains a reply reference to an earlier message.')
    if prefix:
        return role('reply', 'subject_prefix', 'The selected subject has a reply prefix; the original may be unavailable.')
    if quote_kind in {'reply', 'forward'}:
        return role(quote_kind, 'quoted_header', 'The body contains an earlier email with a recognized reply or forward header.')
    if isinstance(metadata, dict) and metadata.get('headers_available') is True and not quote_kind:
        return role('new_message', 'no_reply_reference', 'The available email headers contain no reply reference; this does not prove it is the first email.')
    return role('unknown', 'insufficient_evidence', 'The available source does not establish whether this is a new message, reply or forward.')


def _address(value):
    value = _plain(value).strip().casefold()
    return value if re.fullmatch(r'[^@\s]+@[^@\s]+', value) else ''


def _segment_direction(segment, mailbox_address):
    if segment['is_draft']:
        return 'draft'
    supplied = segment.get('direction')
    if supplied in {'incoming', 'outgoing', 'unknown'}:
        return supplied if _address(segment['sender_email']) else 'unknown'
    sender, own = _address(segment['sender_email']), _address(mailbox_address)
    if not sender or not own:
        return 'unknown'
    # Quoted source direction uses the known sender relative to this mailbox;
    # it never asserts that a quoted segment is an independently captured item.
    return 'outgoing' if sender == own else 'incoming'


def _kind(subject, body):
    text = f'{subject}\n{body[:4000]}'
    if re.search(r'\b(?:bulletin|addendum|corrigendum)\b', text, re.I):
        return 'tender_bulletin'
    if re.search(r'\b(?:deadline|closing\s+date).{0,35}(?:extend|revis|reschedul|postpon)|\brevised\s+deadline\b', text, re.I):
        return 'deadline_update'
    if COMPILED_RULES['subject']['clarification'].search(subject) or any(
        COMPILED_RULES['body']['clarification'].search(unit) and not UNCERTAIN_STATEMENT.search(unit)
        for unit in email_text_units(body[:4000])
    ):
        return 'clarification'
    if re.match(r'^\s*(?:fw|fwd)\s*:', subject, re.I):
        return 'forward'
    if re.match(r'^\s*re\s*:', subject, re.I):
        return 'reply'
    if REQUEST_BODY.search(text) or re.search(r'\b(?:RFT|RFQ|RFP|EOI|EIO|ITT)\b', subject, re.I):
        return 'request'
    if re.search(r'\b(?:received\s+with\s+thanks|thank\s+you|acknowledg)', text, re.I):
        return 'acknowledgement'
    return 'message'


def _header_block(lines, start):
    if not re.match(r'^\s*From:', lines[start], re.I):
        return False
    names = {match.group(1).lower() for line in lines[start:start + 12]
             if (match := HEADERS.match(line))}
    return 'from' in names and bool(names & {'sent', 'date'}) and bool(names & {'to', 'subject'})


def _quoted_metadata(lines, inherited_subject):
    metadata, body_start, current_key = {}, 0, None
    for index, line in enumerate(lines[:18]):
        if not line.strip():
            body_start = index + 1
            current_key = None
            continue
        match = HEADERS.match(line)
        if not match:
            if current_key and line[:1].isspace() and line.strip():
                metadata[current_key] += ' ' + line.strip()
                body_start = index + 1
                continue
            break
        current_key = match.group(1).lower()
        metadata[current_key] = match.group(2).strip()
        body_start = index + 1
    name, address = parseaddr(metadata.get('from', ''))
    timestamp = metadata.get('sent') or metadata.get('date') or ''
    # Exact header text can identify repeated quoted copies without assigning
    # a timezone or interpreting an ambiguous numeric date. Never truncate into
    # a deduplication key, or treat an undated label as a timestamp.
    raw_timestamp = timestamp if len(timestamp) <= 512 and re.search(DATE_TOKEN, timestamp, re.I) else ''
    try:
        parsed = parsedate_to_datetime(timestamp)
        timestamp = parsed.isoformat() if parsed and parsed.tzinfo else ''
    except (ValueError, TypeError, OverflowError):
        timestamp = ''
    return {
        'subject': metadata.get('subject') or _subject(inherited_subject),
        'sender_name': name, 'sender_email': address, 'sent_at': timestamp,
        '_subject_explicit': bool(metadata.get('subject')),
        '_raw_sent_at': raw_timestamp,
    }, '\n'.join(lines[body_start:]).strip()


def _split_message(message, position):
    body = _plain(message.get('body_text')).replace('\r\n', '\n').replace('\r', '\n')
    raw_lines = body.split('\n')
    lines = [re.sub(r'^\s*(?:>\s*)+', '', line) for line in raw_lines]
    starts = []
    for index, line in enumerate(lines):
        if starts and index <= starts[-1][0] + starts[-1][1] - 1:
            continue
        if QUOTE_MARKER.match(line):
            starts.append((index, 1, line.strip()))
        elif re.match(r'^\s*On\s+\S', line, re.I):
            # Gmail may wrap the date, display name and final "wrote:" marker.
            for width in range(2, min(5, len(lines) - index) + 1):
                marker = ' '.join(part.strip() for part in lines[index:index + width])
                if len(marker) > 600:
                    break
                if re.fullmatch(r'On .{5,590} wrote:', marker, re.I):
                    starts.append((index, width, marker))
                    break
        elif _header_block(lines, index) and not (starts and index - starts[-1][0] <= 2):
            starts.append((index, 0, ''))
        elif (re.match(r'^\s*>', raw_lines[index])
              and (index == 0 or not re.match(r'^\s*>', raw_lines[index - 1]))
              and not (starts and index - starts[-1][0] <= 3)):
            starts.append((index, 0, 'bare_quote'))
    subject = _plain(message.get('subject'))
    current = {
        'id': f'm{position}-current', 'message_id': _plain(message.get('id')),
        'subject': subject, 'sender_name': _plain(message.get('sender_name')),
        'sender_email': _plain(message.get('sender_email')),
        'sent_at': _plain(message.get('sent_at') or message.get('received_at')),
        'actual_sent_at': _plain(message.get('sent_at')),
        '_raw_sent_at': _plain(message.get('sent_header')) if len(_plain(message.get('sent_header'))) <= 512 else '',
        'origin': 'message', 'body': '\n'.join(lines[:starts[0][0] if starts else len(lines)]).strip(),
        'classification_body': '\n'.join(raw_lines[:starts[0][0] if starts else len(lines)]).strip(),
        'has_attachments': bool(message.get('has_attachments')),
        'is_draft': bool(message.get('is_draft')),
        '_thread_metadata': message.get('_thread_metadata'),
        'direction': message.get('direction'),
        'chronology_basis': 'sent_at' if _timestamp(message.get('sent_at')) is not None
                            else 'received_at' if _timestamp(message.get('received_at')) is not None else 'unknown',
    }
    first_marker = starts[0][2] if starts else ''
    quote_kind = ('reply' if re.match(r'^On\s', first_marker, re.I) else
                  'forward' if re.search(r'forwarded', first_marker, re.I) else 'quoted' if starts else '')
    current.update(email_thread_role(current, quote_kind=quote_kind))
    quoted = []
    for index, (start, width, marker) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        metadata, quote_body = _quoted_metadata(lines[start + width:end], subject)
        if marker and re.match(r'^\s*On .+ wrote:', marker, re.I):
            email = re.search(r'<?([^<>\s]+@[^<>\s]+)>?', marker)
            if email:
                metadata['sender_email'] = email.group(1)
            # Parse only a timezone-aware marker date; never use its parent date.
            date_text = marker[3:marker.lower().rfind(' wrote:')]
            if email:
                date_text = date_text[:email.start() - 3].rstrip(' <,')
            if len(date_text) <= 512 and re.search(DATE_TOKEN, date_text, re.I):
                metadata['_raw_sent_at'] = date_text
            try:
                stamp = parsedate_to_datetime(date_text)
                if stamp is not None and stamp.tzinfo:
                    metadata['sent_at'] = stamp.isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        if quote_body:
            segment = {
                **metadata, 'id': f'm{position}-quoted-{index + 1}',
                'actual_sent_at': metadata['sent_at'],
                'message_id': current['message_id'], 'origin': 'quoted',
                'body': quote_body, 'has_attachments': False, 'is_draft': current['is_draft'],
                'chronology_basis': 'quoted_header' if _timestamp(metadata['sent_at']) is not None else 'quoted_order',
            }
            segment.update(email_thread_role(segment))
            quoted.append(segment)
    known_quoted_times = [_timestamp(segment['sent_at']) for segment in quoted]
    parent_time = _timestamp(current['sent_at'])
    current['_order_time'] = parent_time if parent_time is not None else max(
        [value for value in known_quoted_times if value is not None] or [float(position)]
    ) + 0.001
    for index, segment in enumerate(quoted, 1):
        quoted_time = _timestamp(segment['sent_at'])
        if parent_time is not None and quoted_time is not None and quoted_time > parent_time:
            quoted_time = None
        segment['_quoted_time_known'] = quoted_time is not None
        segment['_parent_time'] = current['_order_time']
        segment['_order_time'] = quoted_time if quoted_time is not None else current['_order_time'] - (index * 0.001)
    # Standard quoted chains place the oldest text below more recent replies.
    return [*reversed(quoted), current]


def _revision(body):
    """Only an explicit deadline revision may supersede earlier evidence."""
    candidates = []
    weekday = r'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?'
    patterns = (
        rf'\b(?:deadline|closing\s+date|submission\s+(?:due\s+)?date)\s+(?:(?:has\s+been|is|was)\s+)?(?:extended|revised|rescheduled|postponed|changed)(?:\s+from\s+{DATE_TOKEN})?\s+(?:to|until)\s+{weekday}(?P<date>{DATE_TOKEN})',
        rf'\b(?:new|revised|extended|updated)\s+(?:deadline|closing\s+date|submission\s+(?:due\s+)?date)\s*(?:is|:|-)?\s*{weekday}(?P<date>{DATE_TOKEN})',
    )
    for line in email_text_units(body):
        if UNCERTAIN_STATEMENT.search(line) or re.search(r'\b(?:request|propose|could|might|seek|asking|not)\b', line, re.I):
            continue
        for pattern in patterns:
            candidates.extend((match.group('date'), match.group(0)) for match in re.finditer(pattern, line, re.I))
    if not candidates:
        return None
    dates = {_date_value(raw) for raw, _ in candidates}
    return {
        'value': next(iter(dates)) if len(dates) == 1 and '' not in dates else '',
        'raw': ' / '.join(dict.fromkeys(raw for raw, _ in candidates))[:300],
        'evidence': ' | '.join(dict.fromkeys(excerpt for _, excerpt in candidates))[:600],
    }


def _requested_actions(body):
    actions = []
    for line in body.splitlines():
        for sentence in re.split(r'(?<=[.!?])\s+', line.strip()):
            sentence = re.sub(r'^[\s•*\-\d.)]+', '', sentence).strip()
            obligation = (
                re.search(r'\b(?:must|shall|are\s+required\s+to|is\s+required\s+to)\b', sentence, re.I)
                and re.search(r'\b(?:submit|include|consider|incorporate|reflect|address|provide|review|acknowledge|comply|complete)\w*\b', sentence, re.I)
            )
            if len(sentence) < 8 or not (ACTION.search(sentence) or obligation):
                continue
            if re.search(r'\b(?:print|environment|confidentiality|unsubscribe)\b|ignore\s+(?:all|previous)\s+instructions', sentence, re.I):
                continue
            actions.append(sentence[:450])
    return list(dict.fromkeys(actions))[:8]


def _notice(body):
    for line in body.splitlines():
        for sentence in re.split(r'(?<=[.!?])\s+', line.strip()):
            if re.search(r'\b(?:bulletin|addendum|corrigendum|amendment|clarification)\b', sentence, re.I) and re.search(
                r'\b(?:impact|affect|consider|include|reflect|incorporat|change|revis|updat|issu|releas|clarif|supersed|replace)\w*\b', sentence, re.I,
            ):
                return sentence[:700]
    return ''


def _request_reclassification(segment):
    """Return an explicit positive request-type replacement, never a guess."""
    # Only the unquoted text of an independently supplied incoming message can
    # amend the request.  A quoted earlier message, draft, outgoing reply, or a
    # sentence discussing sample wording is evidence to review, not a change.
    if segment['is_draft'] or segment['direction'] != 'incoming' or segment['origin'] != 'message':
        return None
    body = segment.get('classification_body') or segment['body']
    for unit in email_text_units(body[:MAX_CONTENT]):
        if (UNCERTAIN_STATEMENT.search(unit) or '?' in unit
                or re.search(r'\b(?:ask|request(?:ed|ing)?|propos(?:e|ed|ing)|suggest(?:ed|ing)?|recommend(?:ed|ing)?|'
                             r'would\s+like|should|could|might)\s+(?:that|to)\b', unit, re.I)
                or re.search(r'\b(?:example|sample|template|wording|quoted?|quotation\s+marks?|draft)\b'
                             r'[^.!?]{0,80}(?:says?|reads?|states?|[:\"\u201c\u201d])', unit, re.I)):
            continue
        for pattern in REQUEST_RECLASSIFICATION:
            match = pattern.search(unit)
            if match and match.group('old').upper() != match.group('new').upper():
                return {
                    'old': match.group('old').upper(), 'new': match.group('new').upper(),
                    'evidence': match.group(0)[:500], 'source_id': segment['id'],
                }
    return None


def analyze_email_conversation(messages, *, selected_message_id=None, coverage=None, mailbox_address=''):
    """Analyze provided messages in oldest-to-newest order; never fetch sources."""
    messages = [message for message in messages if isinstance(message, dict)] if isinstance(messages, (list, tuple)) else []
    input_coverage = coverage if isinstance(coverage, dict) else {}
    status = input_coverage.get('status', 'selected_only' if len(messages) <= 1 else 'complete')
    if status not in {'complete', 'partial', 'selected_only', 'saved_content'}:
        status = 'partial'
    limitations = []
    if input_coverage.get('reason'):
        limitations.append(_plain(input_coverage['reason'])[:600])
    if len(messages) > MAX_MESSAGES:
        limitations.append('Only the first 100 available conversation messages were analyzed.')
        status = 'partial'
    segments, used_characters, reviewed_messages = [], 0, 0
    collected_quotes = set()
    selected_segment = None
    for position, message in enumerate(messages[:MAX_MESSAGES], 1):
        if len(segments) >= MAX_SEGMENTS:
            status = 'partial'
            limitations.append('The quoted chain exceeded the segment limit; some messages were not analyzed.')
            break
        body = _plain(message.get('body_text'))
        remaining = MAX_TEXT - used_characters
        if remaining <= 0:
            status = 'partial'
            limitations.append('The conversation exceeded the available text limit; some messages were not analyzed.')
            break
        if len(body) > remaining:
            body = body[:remaining]
            status = 'partial'
            limitations.append('The conversation exceeded the available text limit; some text was not analyzed.')
        used_characters += len(body)
        reviewed_messages += 1
        for segment in _split_message({**message, 'body_text': body}, position):
            body_hash = hashlib.sha256(re.sub(r'\s+', ' ', segment['body']).strip().encode('utf-8')).hexdigest()
            # Only deduplicate with matching sender AND known timestamp. Same
            # words sent by different people or at different times are evidence.
            stamp = _timestamp(segment['sent_at'])
            segment['_fingerprint'] = (
                segment['is_draft'], _subject(segment['subject']).casefold(), body_hash,
                _address(segment['sender_email']), stamp,
            ) if _address(segment['sender_email']) and stamp is not None else None
            if segment['origin'] == 'quoted':
                quote_key = ('aware', segment['_fingerprint']) if segment['_fingerprint'] is not None else None
                if quote_key is None and _address(segment['sender_email']) and segment.get('_raw_sent_at'):
                    quote_key = (
                        'raw_date', segment['is_draft'], _subject(segment['subject']).casefold(),
                        body_hash, _address(segment['sender_email']), segment['_raw_sent_at'],
                    )
                if quote_key is not None:
                    if quote_key in collected_quotes:
                        continue
                    collected_quotes.add(quote_key)
            # Repeated quoted copies must not consume the bounded source slots
            # before later actual messages (including the selection) are read.
            if len(segments) >= MAX_SEGMENTS:
                status = 'partial'
                limitations.append('The quoted chain exceeded the segment limit; some text was not analyzed.')
                break
            segment['direction'] = _segment_direction(segment, mailbox_address)
            segment['kind'] = 'draft' if segment['is_draft'] else _kind(segment['subject'], segment['body'])
            segment['fields'] = _extract_email_fields(
                subject=segment['subject'], body_text=segment['body'], sender_email=segment['sender_email'],
                sender_name=segment['sender_name'],
            )
            segment['revision'] = _revision(segment['body'][:MAX_CONTENT])
            segment['actions'] = _requested_actions(segment['body'][:MAX_CONTENT])
            segment['attachments'] = segment['has_attachments'] or bool(ATTACHMENT_TEXT.search(segment['body']))
            segments.append(segment)
            if segment['origin'] == 'message' and segment['message_id'] == selected_message_id:
                selected_segment = segment
    actual_fingerprints = {segment['_fingerprint'] for segment in segments
                           if segment['origin'] == 'message' and segment['_fingerprint'] is not None}
    quoted_seen, retained = set(), []
    for segment in segments:
        fingerprint = segment['_fingerprint']
        if segment['origin'] == 'quoted' and fingerprint is not None:
            if fingerprint in actual_fingerprints or fingerprint in quoted_seen:
                continue
            quoted_seen.add(fingerprint)
        retained.append(segment)
    segments = sorted(retained, key=lambda segment: segment['_order_time'])
    selected_available = selected_segment is not None or not selected_message_id
    selected_segment = selected_segment or (segments[-1] if segments else None)
    factual_segments = [segment for segment in segments if not segment['is_draft']]
    if len(factual_segments) < len(segments):
        limitations.append('Unsent draft text was excluded from detected facts and requested actions.')
    own_address = _address(mailbox_address)

    def incoming(segment):
        if segment['direction'] in {'outgoing', 'draft'}:
            return False
        address = _address(segment['sender_email'])
        # A shared domain does not establish that a different sender is internal.
        return not own_address or not address or address != own_address

    request_segments = [segment for segment in factual_segments if segment['direction'] == 'incoming' and (
        segment['thread_role'] not in {'reply', 'forward'} and
        not (isinstance(segment.get('_thread_metadata'), dict) and segment['_thread_metadata'].get('invalid') is True) and
        (segment['origin'] != 'quoted' or segment.get('_subject_explicit') or REQUEST_BODY.search(segment['body'])) and
        segment['kind'] not in {'tender_bulletin', 'deadline_update', 'clarification', 'reply', 'forward'} and
        (segment['kind'] == 'request' or REQUEST_BODY.search(segment['body']))
    )]
    # Anchor the conversation to the earliest formal request evidence. A later
    # reply may mention proposal/tender/quotation words, but it does not redefine
    # the original request. Internally conflicting formal evidence is still the
    # original source and must remain unresolved rather than being replaced by a
    # cleaner later reply.
    original = next((segment for segment in request_segments
                     if segment['fields'].get('request_type_basis') == 'explicit'), None)
    original = original or next((segment for segment in request_segments if
                                 segment['fields']['request_type_code'] or REQUEST_BODY.search(segment['body'])), None)
    original_identified = original is not None
    original_request = original
    original = original or next((segment for segment in factual_segments if incoming(segment) and segment['fields']['request_type_code']), None)
    original = original or (factual_segments[0] if factual_segments else None)
    incoming_segments = [segment for segment in factual_segments
                         if segment['direction'] == 'incoming' and segment['origin'] == 'message']
    known_incoming = [segment for segment in incoming_segments if _timestamp(segment['sent_at']) is not None]
    first_incoming = None
    if known_incoming:
        earliest = min(_timestamp(segment['sent_at']) for segment in known_incoming)
        tied = [segment for segment in known_incoming if _timestamp(segment['sent_at']) == earliest]
        if len(tied) == 1 and len(known_incoming) == len(incoming_segments):
            first_incoming = tied[0]
        else:
            limitations.append('The earliest incoming source is uncertain because dates are missing or tied.')
    elif incoming_segments:
        limitations.append('Incoming source dates are unavailable; their first-message order is unconfirmed.')
    if any(segment['origin'] == 'quoted' and segment['chronology_basis'] == 'quoted_order' for segment in segments):
        limitations.append('Some quoted emails have no usable timezone-aware date. Their nesting shows quoted order, not a confirmed timestamp or ordering against separate messages.')
    result = _extract_email_fields()
    result['evidence'], result['warnings'], result['field_sources'] = {}, [], {}
    for segment in factual_segments:
        result['warnings'].extend(segment['fields']['warnings'])
        if len(segment['body']) > MAX_CONTENT:
            limitations.append('A long email segment exceeded the field-extraction limit; review the full source.')
            status = 'partial'
    if original:
        result['title'] = original['subject']
        result['evidence']['title'] = original['subject'][:300]
        result['field_sources']['title'] = [original['id']]
        result['client_domain'] = original['fields']['client_domain']

    def resolve(key, candidates):
        sourced = [(segment, segment['fields'].get(key, '')) for segment in candidates
                   if segment['fields'].get(key) or segment['fields']['evidence'].get(key)]
        if not sourced:
            return
        values = {value.casefold() for _, value in sourced}
        result['field_sources'][key] = [segment['id'] for segment, _ in sourced]
        result['evidence'][key] = ' | '.join(dict.fromkeys(
            segment['fields']['evidence'].get(key, value) for segment, value in sourced
        ))[:900]
        if len(values) == 1 and '' not in values:
            result[key] = sourced[0][1]
        elif len(sourced) > 1:
            result['warnings'].append(f'Conflicting {key.replace("_", " ")} evidence in the available chain needs review.')

    customer_segments = [segment for segment in factual_segments if incoming(segment)]
    for key in (
        'customer_name', 'declared_client_domain', 'contact_name', 'contact_email', 'contact_phone',
        'location', 'industry', 'scope_summary', 'scope_type', 'project_name', 'tender_reference', 'estimated_value',
        'currency', 'submission_date', 'expected_award_date',
    ):
        # Customer identity belongs to the original request.  Names mentioned
        # in later replies, internal coordination, or bulletin text must not
        # replace or conflict with the organization that sent the solicitation.
        candidates = ([original_request] if original_request else customer_segments) if key == 'customer_name' else factual_segments
        resolve(key, candidates)

    request_candidates = customer_segments
    if original_request and original_request['fields'].get('request_type_basis') == 'explicit':
        request_candidates = [original_request]
    else:
        explicit = [segment for segment in request_candidates
                    if segment['fields'].get('request_type_basis') == 'explicit']
        request_candidates = explicit or request_candidates
    resolve('request_type_code', request_candidates)

    amendments = []
    if original_request:
        original_index = factual_segments.index(original_request)
        amendments = [change for segment in factual_segments[original_index + 1:]
                      if (change := _request_reclassification(segment))]
    if amendments:
        original_code = original_request['fields'].get('request_type_code', '')
        material = [change for change in amendments
                    if not original_code or change['old'] == original_code]
        if material:
            result['request_type_code'] = ''
            result['field_sources']['request_type_code'] = list(dict.fromkeys([
                original_request['id'], *(change['source_id'] for change in material),
            ]))
            result['evidence']['request_type_code'] = ' | '.join(dict.fromkeys([
                original_request['fields']['evidence'].get('request_type_code', ''),
                *(change['evidence'] for change in material),
            ]))[:900]
            result['warnings'].append(
                'An explicit later email changes the original request type. Confirm the current request type before use.'
            )
    revisions = [(index, segment) for index, segment in enumerate(factual_segments) if segment['revision'] is not None]
    if revisions:
        latest_index, latest = revisions[-1]
        revision = latest['revision']
        result['due_date'] = revision['value']
        result['due_date_text'] = revision['raw']
        result['evidence']['due_date'] = revision['evidence']
        result['field_sources']['due_date'] = [latest['id']]
        later_values = [(segment, segment['fields']['due_date']) for segment in factual_segments[latest_index + 1:]
                        if segment['fields'].get('due_date') or segment['fields']['evidence'].get('due_date')]
        uncertain_conflicts = []
        if latest['origin'] == 'quoted' and not latest.get('_quoted_time_known'):
            # The enclosing reply's date cannot establish when a quoted update
            # happened. Ordinary dated deadline evidence matters here too.
            uncertain_conflicts = [segment for segment in reversed(factual_segments[:latest_index])
                                   if (segment['revision'] or segment['fields'].get('due_date')
                                       or segment['fields']['evidence'].get('due_date'))
                                   and (segment['revision']['value'] if segment['revision']
                                        else segment['fields']['due_date']) != revision['value']]
        if uncertain_conflicts or not revision['value'] or any(value != revision['value'] for _, value in later_values):
            result['due_date'] = ''
            result['warnings'].append('The revised deadline has uncertain chronology or conflicting evidence. Confirm it before submission.')
            competing = uncertain_conflicts + [segment for segment, _ in later_values]
            result['field_sources']['due_date'].extend(segment['id'] for segment in competing)
            result['evidence']['due_date'] = ' | '.join(dict.fromkeys(
                [revision['evidence']] + [segment['revision']['evidence'] if segment['revision']
                                         else segment['fields']['evidence'].get('due_date', '')
                                         for segment in competing]
            ))[:900]
            result['due_date_text'] = ' / '.join(dict.fromkeys(
                [revision['raw']] + [segment['revision']['raw'] if segment['revision']
                                    else segment['fields']['due_date_text'] for segment in competing]
            ))[:300]
        else:
            result['warnings'] = [warning for warning in result['warnings'] if 'due date' not in warning]
    else:
        resolve('due_date', factual_segments)
        result['due_date_text'] = ' / '.join(dict.fromkeys(
            segment['fields']['due_date_text'] for segment in factual_segments if segment['fields']['due_date_text']
        ))[:300]
    result['submission_date_text'] = ' / '.join(dict.fromkeys(
        segment['fields']['submission_date_text'] for segment in factual_segments if segment['fields']['submission_date_text']
    ))[:300]
    result['company_name'] = result['customer_name']
    result['deadline_date'], result['deadline_text'] = result['due_date'], result['due_date_text']
    for alias, key in (('company_name', 'customer_name'), ('deadline_date', 'due_date')):
        if key in result['field_sources']:
            result['field_sources'][alias] = result['field_sources'][key]
    code = result['request_type_code']
    result['request_type'] = {
        'EOI': 'Expression of interest', 'EIO': 'EIO', 'RFT': 'Request for tender',
        'RFQ': 'Request for quotation', 'RFP': 'Request for proposal', 'ITT': 'Invitation to tender',
    }.get(code, original['fields']['request_type'] if original else 'General client email')
    if not code and result['field_sources'].get('request_type_code'):
        result['request_type'] = ''
    if code:
        result['evidence']['request_type'] = result['evidence'].get('request_type_code', code)
        result['field_sources']['request_type'] = result['field_sources'].get('request_type_code', [])
    elif original and result['request_type'] not in {'', 'General client email'}:
        result['field_sources']['request_type'] = [original['id']]
    if original:
        result['industry_type'] = original['fields']['industry_type']
    result['warnings'] = list(dict.fromkeys(result['warnings']))

    key_points = []
    for key, label in (
        ('customer_name', 'Customer'), ('request_type', 'Underlying request'), ('tender_reference', 'Reference'),
        ('scope_summary', 'Scope'), ('submission_date', 'Stated submission date'), ('due_date', 'Current due date'),
        ('expected_award_date', 'Expected award date'), ('estimated_value', 'Estimated value'),
    ):
        if result.get(key) and (key != 'request_type' or result['request_type'] != 'General client email'):
            value = result[key]
            if key == 'estimated_value' and result['currency']:
                value = f"{result['currency']} {value}"
            key_points.append({'label': label, 'value': value, 'source_ids': result['field_sources'].get(key, [])})
    notice = _notice(selected_segment['body'][:MAX_CONTENT]) if selected_segment and not selected_segment['is_draft'] else ''
    if notice:
        key_points.insert(0, {'label': 'Notice', 'value': notice, 'source_ids': [selected_segment['id']]})
    requested, action_seen = [], set()
    for segment in reversed(factual_segments):
        if segment['direction'] == 'outgoing':
            continue
        for text in segment['actions']:
            if revisions and segment['_order_time'] <= revisions[-1][1]['_order_time'] and re.search(
                r'\b(?:submit|submission|deadline|closing)\w*\b|\b(?:send|return|provide)\b[^.]{0,80}\b(?:proposal|tender|bid|quotation)\b', text, re.I,
            ):
                dates = [_date_value(match.group(0)) for match in re.finditer(DATE_TOKEN, text, re.I)]
                if any(not date or date != result['due_date'] for date in dates):
                    # Keep the old wording in source evidence, not as a current
                    # action after its deadline was explicitly superseded.
                    continue
            if text.casefold() not in action_seen and len(requested) < 8:
                action_seen.add(text.casefold())
                requested.append({'text': text, 'source_ids': [segment['id']]})
    attachment_segments = [segment for segment in factual_segments if segment['attachments']]
    suggestions = []
    if attachment_segments:
        limitations.append('Attachment and linked-portal document contents were not read. Dates or requirements stated only there remain unconfirmed.')
        suggestions.append({
            'text': 'Review the referenced attachments or portal documents.',
            'reason': 'The available email text refers to documents whose contents are outside this analysis.',
            'source_ids': [segment['id'] for segment in attachment_segments][:8],
        })
    if (code or original_identified) and not result['due_date']:
        suggestions.append({
            'text': 'Confirm the current submission deadline before planning a response.',
            'reason': 'No single unambiguous current due date is established in the available email text.',
            'source_ids': result['field_sources'].get('due_date', [original['id']] if original else []),
        })
    if result['warnings']:
        suggestions.append({
            'text': 'Resolve the flagged source evidence before using detected values.',
            'reason': ' '.join(result['warnings'])[:600],
            'source_ids': list(dict.fromkeys(source for sources in result['field_sources'].values() for source in sources))[:8],
        })
    if not original_identified:
        limitations.append('The original incoming request could not be confirmed from the available text.')
    if status == 'selected_only':
        limitations.append('Analysis covers the selected email and its visible quoted chain; other conversation messages were not supplied.')
    elif status == 'saved_content':
        limitations.append('Analysis covers saved messages and their visible quoted chains only.')
    elif status == 'partial':
        limitations.append('Only part of the conversation was available; later updates or the original request may be missing.')
    message_kind = selected_segment['kind'] if selected_segment else 'message'
    title = result['title'] or 'the available email'
    purpose = message_kind.replace('_', ' ')
    summary = f'The selected {purpose} concerns {title}.'
    if notice:
        summary += f' The notice states: {notice}'
    if result['customer_name']:
        summary += f" The customer identified in the source is {result['customer_name']}."
    if code:
        summary += f" The underlying request is {result['request_type']} ({code})."
    if result['scope_summary']:
        summary += f" Stated scope: {result['scope_summary']}."
    if result['due_date']:
        summary += f" {'The explicitly revised' if revisions else 'The stated'} due date is {result['due_date']}."
    elif code or original_identified:
        summary += ' A current due date is not confirmed in the available email text.'
    if requested:
        if not notice or requested[0]['text'] != notice:
            summary += f" The available email requests: {requested[0]['text']}"
    if attachment_segments:
        summary += ' Referenced document contents still require review.'
    sources = []
    for index, segment in enumerate(segments, 1):
        excerpts = list(segment['fields']['evidence'].values()) + segment['actions']
        if segment['revision']:
            excerpts.insert(0, segment['revision']['evidence'])
        if selected_segment is segment and notice:
            excerpts.insert(0, notice)
        excerpt = '\n'.join(dict.fromkeys(excerpts))[:1400] or segment['body'][:1400]
        sources.append({
            'id': segment['id'], 'label': f"{'Draft text' if segment['is_draft'] else 'Quoted email' if segment['origin'] == 'quoted' else 'Email'} {index}",
            'subject': segment['subject'], 'sender_name': segment['sender_name'],
            'sender_email': segment['sender_email'], 'sent_at': segment['sent_at'],
            'origin': segment['origin'], 'excerpt': excerpt,
            'thread_role': segment['thread_role'],
            'thread_role_basis': segment['thread_role_basis'],
            'thread_role_reason': segment['thread_role_reason'],
            'direction': segment['direction'], 'chronology_basis': segment['chronology_basis'],
            'is_selected': segment is selected_segment,
            'is_first_incoming': segment is first_incoming,
            'is_original_request': segment is original_request,
        })
    analysis = {
        'version': 1, 'message_kind': message_kind, 'summary': summary,
        'selected_source_id': selected_segment['id'] if selected_segment and selected_available else None,
        'first_incoming_source_id': first_incoming['id'] if first_incoming else None,
        'original_request_source_id': original_request['id'] if original_request else None,
        'key_points': key_points, 'requested_actions': requested, 'suggested_actions': suggestions,
        'limitations': list(dict.fromkeys(value for value in limitations if value)), 'sources': sources,
        'coverage': {
            'status': status, 'messages_reviewed': reviewed_messages, 'segments_reviewed': len(segments),
            'original_identified': original_identified,
        },
    }
    result['classification'] = classify_email_segment(
        selected_segment if selected_available else None, truncated=status == 'partial',
    )
    result = add_email_intelligence(result, analysis, segments, original_request=original_request,
                                    selected=selected_segment if selected_available else None,
                                    mailbox_address=mailbox_address)
    return {'extracted_information': result, 'analysis': analysis}
