"""Source-cited Rejlers qualification inputs; no unsupported internal scoring."""

import re


WEIGHTS = {
    'engineering_capability': 30,
    'qualification_compliance': 20,
    'commercial_attractiveness': 15,
    'resource_availability': 15,
    'win_probability': 10,
    'strategic_value': 10,
}

# These are search cues for a human review, not proof of a requirement or fit.
# A missing cue means it was not found in retained text, not absent from the bid.
DIMENSIONS = (
    ('strategic_fit', 'Strategic fit', (
        ('client', 'Client or end user', r'\b(?:client|end[ -]?user|employer)\b'),
        ('country', 'Country', r'\b(?:UAE|United Arab Emirates|Saudi Arabia|KSA|Sweden|India|Qatar|Oman|Bahrain)\b'),
        ('sector', 'Target sector', r'\b(?:oil\s*(?:&|and)\s*gas|energy|power|utilities|industrial|industries|infrastructure)\b'),
        ('project_type', 'Project type', r'\b(?:greenfield|brownfield|expansion|upgrade|digital transformation)\b'),
        ('agreement', 'Agreement type', r'\b(?:framework agreement|project[ -]specific)\b'),
    )),
    ('engineering_capability', 'Engineering capability match', (
        ('process', 'Process engineering', r'\bprocess engineering\b'),
        ('mechanical', 'Mechanical', r'\bmechanical\b'),
        ('piping', 'Piping', r'\bpiping\b'),
        ('electrical', 'Electrical', r'\belectrical\b'),
        ('instrumentation', 'Instrumentation and control', r'\b(?:instrumentation|control systems?|I&C)\b'),
        ('civil', 'Civil', r'\bcivil\b'),
        ('structural', 'Structural', r'\bstructural\b'),
        ('telecom', 'Telecom', r'\b(?:telecom|telecommunications)\b'),
        ('digital', 'Digital solutions', r'\b(?:digital solutions?|digital transformation)\b'),
        ('automation', 'Automation', r'\bautomation\b'),
        ('bim', 'BIM', r'\bBIM\b'),
        ('concept', 'Concept study', r'\bconcept stud(?:y|ies)\b'),
        ('feasibility', 'Feasibility study', r'\bfeasibility stud(?:y|ies)\b'),
        ('feed', 'FEED', r'\bFEED\b'),
        ('detailed', 'Detailed engineering', r'\bdetailed engineering\b'),
        ('epcm', 'EPCM support', r'\bEPCM\b'),
        ('owner', 'Owner engineer', r'\bowner[’\x27s ]*engineer\b'),
        ('site', 'Site engineering', r'\bsite engineering\b'),
        ('construction', 'Construction support', r'\bconstruction support\b'),
        ('integrity', 'Asset integrity', r'\basset integrity\b'),
        ('pmc', 'PMC', r'\bPMC\b'),
    )),
    ('resource_availability', 'Resource availability', (
        ('manpower', 'Required manpower', r'\b(?:manpower|headcount|\d+\s*FTE)\b'),
        ('personnel', 'Key personnel', r'\bkey personnel\b'),
        ('heads', 'Discipline heads', r'\bdiscipline heads?\b'),
        ('resident', 'Resident engineers', r'\bresident engineers?\b'),
        ('site_team', 'Site-based team', r'\bsite[ -]based\b'),
        ('mobilization', 'Mobilization', r'\bmobili[sz]ation\b'),
        ('locations', 'Delivery locations', r'\b(?:Abu Dhabi|UAE|Saudi Arabia|KSA|India|Sweden)\b'),
    )),
    ('qualification_compliance', 'Qualification compliance', (
        ('adnoc', 'ADNOC vendor registration or approval',
         r'\bADNOC(?:\s+Group(?:\s+company)?)?\s+(?:(?:vendor|supplier)\s+)?(?:registration|prequalification)\b|'
         r'\bADNOC(?:\s+Group(?:\s+company)?)?\s+(?:vendor|supplier)\s+approv(?:al|ed)\b|'
         r'\b(?:vendor|supplier|bidder)\b.{0,50}\b(?:registered|approved|prequalified)\s+(?:with|by)\s+ADNOC\b'),
        ('aramco', 'Aramco approval', r'\bAramco\b.{0,60}\b(?:approval|approved|registration)\b'),
        ('taqa', 'TAQA registration', r'\bTAQA\b.{0,60}\bregistration\b'),
        ('dewa', 'DEWA registration', r'\bDEWA\b.{0,60}\bregistration\b'),
        ('iso9001', 'ISO 9001', r'\bISO\s*9001\b'),
        ('iso14001', 'ISO 14001', r'\bISO\s*14001\b'),
        ('iso45001', 'ISO 45001', r'\bISO\s*45001\b'),
        ('cyber', 'Cybersecurity standard', r'\bcybersecurity standards?\b'),
        ('experience', 'Similar project experience', r'\b(?:similar projects?|relevant experience|reference projects?)\b'),
        ('project_value', 'Required project value', r'\b(?:minimum project value|project value)\b'),
    )),
    ('commercial_attractiveness', 'Commercial attractiveness', (
        ('lump_sum', 'Lump sum', r'\blump[ -]sum\b'),
        ('tm', 'Time and material', r'\b(?:T\s*&\s*M|time\s+and\s+material)\b'),
        ('framework', 'Framework or call-off', r'\b(?:framework|call[ -]?off)\b'),
        ('unit_rate', 'Unit rate', r'\bunit rates?\b'),
        ('payment', 'Payment terms', r'\b(?:payment terms?|milestone payments?|payment period)\b'),
        ('retention', 'Retention', r'\bretention\b'),
        ('advance', 'Advance payment', r'\badvance payment\b'),
        ('bond', 'Performance bond', r'\bperformance bond\b'),
        ('guarantee', 'Parent company guarantee', r'\bparent company guarantee\b'),
    )),
    ('contract_risk', 'Contract risk', (
        ('unlimited', 'Unlimited liability', r'\bunlimited liability\b'),
        ('pi', 'Professional indemnity', r'\bprofessional indemnity\b'),
        ('consequential', 'Consequential damages', r'\bconsequential damages?\b'),
        ('indemnity', 'Indemnification', r'\bindemnif(?:y|ication)\b'),
        ('ld', 'Liquidated damages', r'\bliquidated damages?\b'),
        ('delay', 'Delay penalties', r'\bdelay penalt(?:y|ies)\b'),
        ('credits', 'Service credits', r'\bservice credits?\b'),
    )),
    ('delivery_feasibility', 'Delivery feasibility', (
        ('issue', 'Tender issue date', r'\btender issue date\b'),
        ('clarification', 'Clarification deadline', r'\bclarification deadline\b'),
        ('submission', 'Submission deadline', r'\bsubmission deadline\b'),
        ('award', 'Contract award', r'\b(?:contract award|award date)\b'),
        ('mobilization', 'Mobilization date', r'\bmobili[sz]ation\b'),
        ('completion', 'Completion date', r'\bcompletion date\b'),
    )),
    ('win_probability', 'Win probability', (
        ('existing', 'Existing client or framework', r'\b(?:existing client|framework holder|current framework)\b'),
        ('incumbent', 'Previous contractor or incumbent', r'\b(?:previous contractor|incumbent)\b'),
        ('competition', 'Competitive or single-source procurement',
         r'\bcompetitive\s+(?:tender|bid|procurement)\b|'
         r'\b(?:single|sole)[ -]source(?:d)?\s+(?:procurement|tender|award|contract|selection)\b|'
         r'\b(?:procurement|sourcing|tender)\s*(?:method|model|basis)?\s*[:=-]\s*(?:single|sole)[ -]source\b'),
        ('preferred', 'Preferred supplier', r'\bpreferred suppl(?:ier|iers)\b'),
        ('competitor', 'Known competitor', r'\bcompetitors?\b'),
    )),
    ('strategic_value', 'Strategic value', (
        ('adnoc', 'ADNOC Group client', r'\bADNOC\b'),
        ('country', 'New country entry', r'\bnew country\b'),
        ('framework', 'Long-term framework', r'\b(?:long[ -]term framework|framework agreement)\b'),
        ('recurring', 'Recurring work', r'\brecurring\b'),
        ('follow_on', 'Follow-on projects', r'\bfollow[ -]on\b'),
        ('reference', 'Reference project', r'\breference project\b'),
    )),
)


def build_rejlers_review(diagnostics):
    chunks = []
    for source in diagnostics:
        for chunk in source.get('chunks', []):
            if isinstance(chunk, dict):
                location, body = chunk.get('location', ''), chunk.get('text', '')
            elif isinstance(chunk, (tuple, list)) and len(chunk) == 2:
                location, body = chunk
            else:
                continue
            if body:
                chunks.append((source, location, str(body).splitlines()))
    dimensions = []
    for key, title, checks in DIMENSIONS:
        items = []
        for item_key, label, expression in checks:
            pattern = re.compile(expression, re.I)
            examples, seen = [], set()
            for source, location, lines in chunks:
                for index, line in enumerate(lines):
                    if not pattern.search(line) or re.search(r'\.{4,}\s*\d+\s*$', line):
                        continue
                    excerpt = '\n'.join(lines[max(0, index - 1):min(len(lines), index + 2)]).strip()
                    identity = (source.get('source_id'), location, excerpt.casefold())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    if len(examples) < 2:
                        examples.append({'text': excerpt, 'source_id': source.get('source_id'),
                                         'source_file': source.get('source_name', ''), 'source_page': location})
            items.append({'key': item_key, 'label': label, 'evidence': examples,
                          'status': 'source_mention' if examples else 'not_found_in_extracted_text'})
        dimensions.append({'key': key, 'title': title, 'weight': WEIGHTS.get(key),
                           'assessment': 'unknown', 'score': None,
                           'evidence_found': sum(bool(item['evidence']) for item in items),
                           'evidence_total': len(items), 'items': items})
    return {'dimensions': dimensions, 'weights': WEIGHTS, 'weighted_score': None,
            'recommendation': 'insufficient_evidence',
            'missing_internal': [
                'Approved rating scale and Go / No-Go thresholds',
                'Approved Rejlers target sectors, growth priorities and existing-client status',
                'Approved Rejlers capability and project-reference inventory',
                'Named capacity and mobilization plan for Abu Dhabi, Saudi Arabia, India Delivery Center and Sweden',
                'Vendor registrations, certifications and qualification evidence',
                'Approved pricing, margin and contract-risk guardrails',
                'Client relationship and competitive-position evidence',
            ]}
