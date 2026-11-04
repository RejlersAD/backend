"""Source-first tender facts; no internal capability or commercial assumptions."""
import re

from ..email_extraction import DATE_TOKEN, _date_value

LABELS = {
    'project_name': r'project(?:\s+(?:name|title))?|name\s+of\s+(?:the\s+)?project',
    'client': r'client(?:\s+(?:name|company))?|customer(?:\s+name)?|employer',
    'scope': r'scope(?:\s+(?:of\s+work|summary))?|general\s+scope\s+of\s+services(?:\s+and\s+timeline)?|statement\s+of\s+work|project\s+(?:overview|description)',
    'disciplines': r'(?:required\s+)?disciplines?|required\s+(?:roles|specialists)',
    'technologies': r'(?:required\s+)?technolog(?:y|ies)|technology\s+stack|systems?\s+landscape',
    'deliverables': r'(?:key|required\s+)?deliverables?',
    'timeline': r'timeline|project\s+(?:schedule|duration)|implementation\s+schedule|milestones?',
    'locations': r'locations?|delivery\s+locations?|geographic\s+scope',
    'key_risks': r'(?:key|project|technical|delivery|commercial)?\s*risks?',
    'submission_details': r'(?:bid\s+)?submission\s+(?:deadline|date)|tender\s+closing\s+date',
    'contract_type': r'contract\s+type',
    'commercial_model': r'(?:commercial|pricing)\s+model',
    'payment_terms': r'payment\s+terms',
    'clarification_deadline': r'clarification\s+(?:deadline|closing\s+date)',
}
CONTENT = {
    'scope': r'\b(?:design|implement(?:ation)?|rollout|scope|workstream|services|read.only)\b',
    'disciplines': r'\b(?:discipline|SAP\s+(?:functional|technical|basis)|engineer(?:ing)?|'
                   r'architect|specialist|data\s+migration|cybersecurity|change\s+management)\b',
    'technologies': r'\b(?:SAP(?:\s+S/?4HANA)?|S/?4H(?:ANA)?|OneERP|Oracle|Microsoft|Azure|AWS|IMOS|AMOS|'
                    r'EyeShare|BOS|DigitALL\s+Ocean|HiBob|SmartPorts|ILMS|SAC|SuccessFactors|Ariba|FieldGlass|'
                    r'Tricentis|Tosca|satellite|'
                    r'integration\s+middleware)\b',
    'deliverables': r'\b(?:deliverables?|migration\s+plan|cutover\s+plan|test\s+scripts?|'
                    r'training\s+materials?|architecture\s+design)\b',
    'timeline': r'\b(?:Q[1-4]\s+20\d{2}|[1-4](?:st|nd|rd|th)\s+quarter\s+of\s+20\d{2}|'
                r'end\s+of\s+20\d{2}|go[ -]?live|milestones?|hypercare|'
                r'within\s+\d+\s+(?:days|weeks|months)|\d+\s+(?:days|weeks|months))\b',
    'locations': r'\b(?:Abu\s+Dhabi|Dubai|United\s+Arab\s+Emirates|UAE|Saudi\s+Arabia|'
                 r'KSA|Qatar|Kuwait|Bahrain|Oman|Singapore|London|on[ -]?site)\b',
    'key_risks': r'\b(?:risks?|mitigation|dependency|dependencies|constraints?|freeze|downtime)\b',
}
HEADINGS = [(key, re.compile(r'^(?:\d+(?:\.\d+)*[.)]?\s+)?(?:'
                            + (r'project\s+(?:name|title)|name\s+of\s+(?:the\s+)?project'
                               if key == 'project_name' else label) + r')\s*[:=-]?\s*$', re.I))
            for key, label in LABELS.items()]
INLINE = [(key, re.compile(r'^(?:' + label + r')\s*[:=]\s*(.+)$', re.I))
          for key, label in LABELS.items()]


def source_facts(diagnostics):
    facts = {key: [] for key in LABELS}
    seen = set()

    def add(key, value, quote, source, location, *, direct=True):
        value = value.strip()
        if key in {'project_name', 'client'}:
            value = re.sub(r'^[*•\-\s]+', '', value).strip()
        if not value or re.fullmatch(r'unknown|not available|not provided|TBC|TBD', value, re.I):
            return
        identity = (key, value.casefold(), source.get('source_id'), location)
        if identity in seen:
            return
        seen.add(identity)
        facts[key].append({
            'value': value, 'finding': value, 'source_text': quote, 'excerpt': quote,
            'source_id': source.get('source_id'), 'source_file': source.get('source_name', ''),
            'source_page': location, 'source_section': key.replace('_', ' '),
            'confidence': 86, 'confidence_method': 'deterministic_source_rule',
            'matched_rule': 'section_or_label' if direct else 'content_pattern',
            'status': 'likely', 'verification_status': 'Source Confirmed',
        })

    for source in diagnostics:
        section = None
        for chunk in source.get('chunks', []):
            location, text = ((chunk.get('location', ''), chunk.get('text', '')) if isinstance(chunk, dict)
                              else chunk if isinstance(chunk, (list, tuple)) and len(chunk) == 2 else ('', ''))
            lines = str(text or '').splitlines()
            pending = []

            def flush():
                if section and pending:
                    quote = '\n'.join(pending).strip()
                    # Do not truncate a source claim into a different meaning.
                    if len(quote) <= 6000:
                        add(section, quote, quote, source, location)

            for line_index, raw in enumerate(lines):
                line = raw.strip(' \t•▪-*')
                if not line:
                    continue
                # PDF table cells are separate lines. Retain adjacent cells so
                # a quantity remains tied to its object in the cited excerpt.
                if re.fullmatch(r'Materials? Master', line, re.I):
                    following = [part.strip() for part in lines[line_index + 1:line_index + 3]]
                    if following and re.fullmatch(r'\d[\d,]*', following[0]):
                        quote = '\n'.join(lines[line_index:line_index + 3]).strip()
                        add('scope', quote, quote, source, location, direct=False)
                if re.search(r'\.{3,}\s*\d+\s*$|^ADNOC Classification|^Page\s+\d+', line, re.I):
                    continue
                heading = next((key for key, pattern in HEADINGS if pattern.fullmatch(line)), None)
                inline = next(((key, pattern.match(line)) for key, pattern in INLINE if pattern.match(line)), None)
                if heading or inline:
                    flush()
                    section, pending = heading, []
                    if inline:
                        key, match = inline
                        if key in {'scope', 'disciplines', 'technologies', 'deliverables', 'timeline',
                                   'locations', 'key_risks'}:
                            section, pending = key, [match[1]]
                        else:
                            add(key, match[1], raw.strip(), source, location)
                    continue
                if re.search(r'\b(?:T\s*&\s*M|time\s+and\s+materials?)\s+model\b', line, re.I):
                    add('commercial_model', 'T&M', raw.strip(), source, location)
                if re.search(r'\b(?:purely\s+)?milestone[ -]based\s+payments?\b|\b(?:purely\s+)?milestone\s+payments?\b', line, re.I):
                    add('payment_terms', 'Milestone payments', raw.strip(), source, location)
                if section:
                    if re.match(r'^\d+(?:\.\d+)*[.)]?\s+[A-Z]', line) or re.fullmatch(r'[A-Z][A-Za-z /&-]{2,70}:', line):
                        flush()
                        section, pending = None, []
                    else:
                        pending.append(raw.strip())
                        continue
                for key, pattern in CONTENT.items():
                    if re.search(pattern, line, re.I) and len(line) >= 12:
                        add(key, line, raw.strip(), source, location, direct=False)
                client = re.match(
                    r"^([A-Z][A-Za-z0-9 &'’.-]{2,150}(?:Services|Company|Corporation|Group|LLC|Ltd))"
                    r"\s*(?:\[[^\]\n]{1,30}\]|\([^)\n]{1,30}\))?\s+is\b"
                    r".{0,220}\b(?:company|corporation|organization|organisation|client)\b", line, re.I)
                if client:
                    add('client', client[1], raw.strip(), source, location)
                if (len(line) <= 160 and not re.search(r'[.!?;:]', line)
                        and re.fullmatch(r'[A-Z][A-Za-z0-9 &/()+-]+(?:ERP|Digital|System|Platform)'
                                         r'[A-Za-z0-9 &/()+-]*(?:Transformation|Program|Project)', line)):
                    add('project_name', line, raw.strip(), source, location)
            flush()
    for rows in facts.values():
        rows.sort(key=lambda item: item['matched_rule'] != 'section_or_label')
    return facts


def fact_diagnostics(facts, diagnostics):
    incomplete = any(source.get('parse_status') in {'partial', 'failed', 'blocked'}
                     for source in diagnostics)
    scalar = {'project_name', 'client', 'contract_type', 'commercial_model', 'payment_terms',
              'submission_details', 'clarification_deadline'}
    rows = []
    for key, values in facts.items():
        distinct = {re.sub(r'\s+', ' ', item['value']).casefold() for item in values}
        if key == 'client' and len(distinct) == 2:
            short, long = sorted(distinct, key=len)
            if long.startswith(short + ' (') and long.endswith(')'):
                distinct = {short}
        if key in {'submission_details', 'clarification_deadline'}:
            distinct = {_date_value(match[0]) or match[0] for item in values
                        for match in re.finditer(DATE_TOKEN, item['value'], re.I)} or distinct
        contradictory = key in scalar and len(distinct) > 1
        if contradictory:
            for item in values:
                item['verification_status'] = 'Conflicting Evidence'
                item['status'] = 'manual_review_required'
        reason = ('Contradictory source values require review.' if contradictory else
                  'Source evidence located in the tender document.' if values else
                  'Source extraction is incomplete; retry or review the document before asking the client.' if incomplete else
                  'No supported source evidence found for this field.')
        rows.append({'field': key.replace('_', ' ').title(), 'key': key,
                     'status': 'Clarification Required' if contradictory or not values else 'Extracted',
                     'evidence_found': bool(values), 'reason': reason,
                     'value': '; '.join(item['value'] for item in values),
                     'source': '; '.join(dict.fromkeys(item['source_file'] for item in values)),
                     'confidence': values[0]['confidence'] if values else 0,
                     'verification_status': 'Conflicting Evidence' if contradictory else
                     'Source Confirmed' if values else 'Pending Verification',
                     'contradictory': contradictory, 'incomplete': incomplete and not values})
    return rows


# Source-text coverage is a review aid. A keyword hit establishes a location to
# inspect, not that the surrounding statement proves a requirement or a risk.
ERP_COVERAGE_TOPICS = (
    ('IMOS voyage accounting', r'\bIMOS\b'),
    ('EyeShare invoice flow', r'\bEyeShare\b'),
    ('AMOS vessel management', r'\bAMOS\b'),
    ('DigitALL Ocean commercial flow', r'\bDigitALL\s+Ocean\b'),
    ('BOS bunker trading', r'\bBOS\b'),
    ('Ariba and procurement governance', r'\b(?:Ariba|TBMS)\b'),
    ('ILMS and SmartPorts', r'\b(?:ILMS|SmartPorts)\b'),
    ('SAC and SuccessFactors', r'\b(?:SAC|SuccessFactors|FieldGlass|HiBob)\b'),
    ('External payroll journals', r'\bpayroll\b'),
    ('Material master volume', r'\b(?:500,000|60,000\s+active)\b'),
    ('Vendor and customer volumes', r'\b(?:Vendors?\s+Master|Customers?\s+Master|2,875|1,600)\b'),
    ('Legal entities and company codes', r'\b(?:legal entities|company codes?)\b'),
    ('Finance transaction volumes', r'\b(?:purchase orders|AP entries|AR entries|journal entries|jornal entries)\b'),
    ('Cleansing cycles and ownership', r'\b(?:two\s*\(2\)\s+iterative cleansing|cleansing|data ownership|data steward)\b'),
    ('Nonproduction data masking', r'\b(?:data masking|data scrambling|scrambl|masking)\b'),
    ('S/4HANA 2509 upgrade', r'\b2509\b'),
    ('Release approval and regression', r'\b(?:CAB|Tosca|regression test|release management)\b'),
    ('Cybersecurity go-live gate', r'\bgo.live shall be dependent\b'),
    ('Identity and privileged access', r'\b(?:Entra|MFA|PAM|identity|privileged access)\b'),
    ('Remote access', r'\b(?:VDI|VPN|IPSec|remote access|virtual desktop)\b'),
    ('Cloud and data residency', r'\b(?:data residency|UAE.based|cloud subscription|tenant)\b'),
    ('Security logs and monitoring', r'\b(?:SIEM|SOC|logging|monitoring)\b'),
    ('Vessel connectivity', r'\b(?:vessel connectivity|satellite|ship.to.shore)\b'),
    ('Navig8 finance model', r'\bMumbai\b'),
    ('ZMI vessel teams', r'\b(?:integrated operating team|IOT)\b'),
    ('L&S shared services transition', r'\bshared services\b'),
    ('Finance and VAT redesign', r'\b(?:VAT|finance process|tax|vendor master)\b'),
    ('Procurement and HCM redesign', r'\b(?:procurement|HCM|payroll management|supplier management)\b'),
    ('Legacy ERP read-only cutover', r'\b(?:read.only|legacy ERP|Dynamics)\b'),
    ('Training and hypercare', r'\b(?:training|hypercare)\b'),
    ('Phased entity rollout', r'\b(?:phased|rollout|phase.1|phase.2)\b'),
)

PREFERRED_COVERAGE_TEXT = {
    'EyeShare invoice flow': r'EyeShare\s*->\s*SAP|AP invoices',
    'AMOS vessel management': r'AMOS\s*->\s*SAP|SAP\s*->\s*AMOS',
    'BOS bunker trading': r'BOS\s*->\s*SAP|BOS is the front office',
    'Cleansing cycles and ownership': r'two\s*\(2\)\s+iterative cleansing|data ownership',
    'Navig8 finance model': r'centralized Finance organization based in Mumbai',
}


def source_coverage(diagnostics):
    """Locate ERP review topics in retained source text with exact page excerpts."""
    sow_sources = [source for source in diagnostics if re.search(
        r'\bSOW\b|statement of work|scope of work', source.get('source_name', ''), re.I)]
    if sow_sources:
        diagnostics = sow_sources
    chunks = []
    for source in diagnostics:
        for chunk in source.get('chunks', []):
            if isinstance(chunk, dict):
                location, body = chunk.get('location', ''), chunk.get('text', '')
            elif isinstance(chunk, (list, tuple)) and len(chunk) == 2:
                location, body = chunk
            else:
                continue
            if body:
                chunks.append((source, location, body))
    if not any(re.search(r'\b(?:ERP|SAP|OneERP)\b', body, re.I) for _, _, body in chunks):
        return []
    result = []
    for title, expression in ERP_COVERAGE_TOPICS:
        pattern = re.compile(expression, re.I)
        preferred = re.compile(PREFERRED_COVERAGE_TEXT.get(title, r'(?!)'), re.I)
        count, candidates, seen = 0, [], set()
        for source, location, body in chunks:
            lines = body.splitlines()
            for index, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                quote = '\n'.join(lines[max(0, index - 1):min(len(lines), index + 2)]).strip()
                key = (source.get('source_id'), location, quote.casefold())
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                score = int(bool(preferred.search(quote))) - int('....' in quote)
                if title == 'Cleansing cycles and ownership' and re.search(r'two\s*\(2\)\s+iterative', quote, re.I):
                    score += 2
                if title == 'BOS bunker trading' and re.search(r'BOS\s*->\s*SAP', quote, re.I):
                    score += 2
                candidates.append((score, count, {'text': quote, 'source_file': source.get('source_name', ''),
                                                   'source_page': location, 'source_id': source.get('source_id')}))
        examples = [item for _, _, item in sorted(candidates, key=lambda row: (-row[0], row[1]))[:5]]
        result.append({'title': title, 'mentions': count, 'evidence': examples,
                       'status': 'source_mention' if count else 'not_found_in_extracted_text'})
    return result


WRITTEN_NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
                   'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12}


def written_number_conflicts(diagnostics):
    """Flag contradictory written and parenthesized quantities in source text."""
    pattern = re.compile(r'\b(' + '|'.join(WRITTEN_NUMBERS) + r')\s*\((\d{1,2})\)', re.I)
    conflicts, seen = [], set()
    for source in diagnostics:
        for chunk in source.get('chunks', []):
            if isinstance(chunk, dict):
                location, body = chunk.get('location', ''), chunk.get('text', '')
            elif isinstance(chunk, (list, tuple)) and len(chunk) == 2:
                location, body = chunk
            else:
                continue
            lines = str(body or '').splitlines()
            for index, line in enumerate(lines):
                for match in pattern.finditer(line):
                    if WRITTEN_NUMBERS[match[1].casefold()] == int(match[2]):
                        continue
                    key = (source.get('source_id'), location, match[0].casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    quote = '\n'.join(lines[max(0, index - 1):min(len(lines), index + 2)]).strip()
                    conflicts.append({'field': 'source_quantity_conflict',
                                      'question': f'Please confirm the quantity stated as {match[0]} in the source.',
                                      'why_material': 'The written and numeric quantities disagree.',
                                      'evidence': [{'source_file': source.get('source_name', ''),
                                                    'source_page': location, 'source_text': quote,
                                                    'excerpt': quote, 'source_id': source.get('source_id')}]})
                    if len(conflicts) >= 12:
                        return conflicts
    return conflicts
