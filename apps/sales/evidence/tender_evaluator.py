"""Rules-only Go / No-Go evaluation projection from unified source evidence."""

import re
from .tender_facts import source_facts, fact_diagnostics, source_coverage, written_number_conflicts
from .rejlers_review import build_rejlers_review, WEIGHTS
from ..email_extraction import DATE_TOKEN, _date_value


CLARIFY = 'Further client clarification required'

def build_tender_evaluation(deal, compiled):
    diagnostics = compiled.get('evidence_debug') or []
    evidence = [item for source in diagnostics for item in source.get('extracted', [])]
    fields = (compiled.get('unified_evidence') or {}).get('selected') or {}

    def field_value(key, fallback='Unknown'):
        row = fields.get(key) or {}
        value = row.get('value') if row.get('status') in {'verified', 'likely'} else ''
        return value or fallback

    def rows_for(*kinds):
        return [item for item in evidence if item.get('kind') in kinds]

    def unique_text(rows):
        seen, result = set(), []
        for row in rows:
            value = (row.get('text') or '').strip()
            key = value.casefold()
            if value and key not in seen:
                result.append(row)
                seen.add(key)
        return result

    submission = unique_text(rows_for('submission'))
    actions = unique_text(rows_for('action'))
    milestones = unique_text(rows_for('milestone'))
    risks = unique_text(rows_for('risk'))
    scope = unique_text(submission + actions + milestones)
    role_pattern = re.compile(r'\b(?:engineer|consultant|manager|specialist|architect|analyst|developer|lead)\b', re.I)
    roles = unique_text([item for item in evidence if role_pattern.search(item.get('text', ''))])
    source_links = lambda rows: [{
        'finding': item.get('text', ''), 'value': item.get('text', ''),
        'source_text': item.get('excerpt', item.get('text', '')), 'confidence': item.get('confidence', 0),
        'status': item.get('status', 'likely'), 'source_file': item.get('source_name') or item.get('name', ''),
        'source_page': item.get('location', 'Unspecified location'), 'source_section': item.get('source_section', ''),
        'verification_status': 'Source Confirmed' if item.get('status') == 'verified' else 'Pending Verification',
        'excerpt': item.get('excerpt', ''),
    } for item in rows[:40]]

    project_facts = source_facts(diagnostics)
    for key, claims in (('submission_details', [item for item in evidence if item.get('kind') == 'deadline']),
                        ('key_risks', risks)):
        if not project_facts[key]:
            project_facts[key] = source_links(claims)
    field_diagnostics = fact_diagnostics(project_facts, diagnostics)
    def fact_value(key):
        values = project_facts[key]
        distinct = {}
        for row in values:
            value = re.sub(r'^[*•\-\s]+', '', row.get('value') or row.get('finding', '')).strip()
            if value:
                distinct.setdefault(value.casefold(), value)
        if key == 'client' and len(distinct) == 2:
            short, long = sorted(distinct.values(), key=len)
            if long.casefold().startswith(short.casefold() + ' (') and long.endswith(')'):
                return short
        return '; '.join(distinct.values()) or CLARIFY
    project = fact_value('project_name')
    client = fact_value('client')
    reference = field_value('tender_reference', field_value('rfq_reference'))
    deadline = field_value('due_date', '')
    if not deadline:
        dates = {_date_value(match[0]) for item in project_facts['submission_details']
                 for match in re.finditer(DATE_TOKEN, item.get('value', item.get('finding', '')), re.I)}
        deadline = next(iter(dates)) if len(dates) == 1 and '' not in dates else 'Unknown'
    executive_scope = '; '.join(item['finding'] for item in project_facts['scope'][:5]) or CLARIFY
    executive_tech = '; '.join(item['finding'] for item in project_facts['technologies'][:5])
    executive_summary = (f"{project} for {client}. Scope evidenced in the tender: {executive_scope}. "
                         + (f"Technology requirements include {executive_tech}. " if executive_tech else '')
                         + f"The source identifies {len(submission)} submission/compliance items, {len(milestones)} milestones and {len(risks)} risk statements. Submission date: {deadline}.")

    # Tender evidence cannot establish internal capability, capacity or margin.
    conditions = [
        'Confirm the required scope matches approved Rejlers Abu Dhabi capabilities and delivery references.',
        'Confirm named resource availability and specialist requirements against the proposed schedule.',
        'Review the pricing basis, contract terms, and commercial exposure before committing to a bid.',
    ]
    if deadline == 'Unknown':
        conditions.append('Confirm the submission deadline and any clarification cut-off with the client.')
    recommendation = 'insufficient_evidence'
    rationale = (
        f'The tender identifies {len(submission)} submission/compliance obligations, {len(milestones)} milestones, '
        f'and {len(risks)} explicit risks for {project}. An internal recommendation needs approved capability, '
        'resource capacity and commercial evidence. The tender facts alone do not establish those inputs.'
    )

    # Client clarification follows fact resolution, never an internal assessment
    # or absence of an estimated amount as a proxy for commercial terms.
    clarifications_out = [{
        'field': row['key'], 'question': f"Please clarify {row['key'].replace('_', ' ')}.",
        'why_material': row['reason'], 'evidence': project_facts[row['key']],
    } for row in field_diagnostics if row['status'] == 'Clarification Required' and not row['incomplete']]
    clarifications_out.extend(written_number_conflicts(diagnostics))
    if len(clarifications_out) > 12:
        clarifications_out = clarifications_out[:12]

    dimension_rows = {key: {'score': None, 'assessment': 'Unknown', 'evidence': []}
                      for key in WEIGHTS}
    letter_type = 'eoi' if recommendation == 'go' else 'regret_expertise' if recommendation == 'no_go' else 'none'
    letter_data = {
        'letter_type': letter_type, 'project_name': project, 'client_name': client,
        'client_reference': field_value('tender_reference'), 'rfq_reference': field_value('rfq_reference'),
        'submission_date': deadline, 'decision': recommendation.upper(),
        'template_fields': {},
    }
    if letter_type == 'eoi':
        letter_data['template_fields'] = {key: value for key, value in letter_data.items()
                                          if key in {'project_name', 'client_name', 'client_reference', 'rfq_reference', 'submission_date'}}
    elif letter_type == 'regret_expertise':
        letter_data['template_fields'] = {key: value for key, value in letter_data.items()
                                          if key in {'project_name', 'client_name', 'client_reference', 'rfq_reference', 'submission_date'}}

    return {
        'opportunity_summary': {
            'project_name': project, 'client_name': client, 'client_reference': field_value('tender_reference'),
            'rfq_reference': field_value('rfq_reference'), 'country': 'Further client clarification required',
            'location': '; '.join(item['finding'] for item in project_facts['locations']) or 'Further client clarification required',
            'industry': 'Further client clarification required',
            'project_type': field_value('scope_type'), 'contract_type': fact_value('contract_type'),
            'submission_date': deadline, 'project_start_date': 'Further client clarification required',
            'estimated_duration': 'Further client clarification required',
            'summary': executive_summary, 'supporting_evidence': source_links(scope[:5]),
            'project_facts': project_facts,
            'field_diagnostics': field_diagnostics,
        },
        'evidence_coverage': source_coverage(diagnostics),
        'rejlers_review': build_rejlers_review(diagnostics),
        'scope_analysis': {
            'required_services': [item.get('text', '') for item in submission[:30]],
            'required_disciplines': [item['finding'] for item in project_facts['disciplines']], 'project_phase': [item.get('text', '') for item in milestones[:30]],
            'key_deliverables': [item['finding'] for item in project_facts['deliverables']],
            'special_requirements': [item.get('text', '') for item in unique_text(rows_for('submission')) if item.get('evidence_type') == 'compliance_requirement'][:30],
            'evidence': source_links(scope),
        },
        'capability_assessment': {
            'required_capabilities': [], 'required_software': [], 'required_certifications': [],
            'required_local_presence': None, 'capability_gaps': [], 'assessment': 'Unknown', 'score': None,
            'reason': 'No approved internal capability inventory or reference cases were supplied.',
        },
        'strategic_fit': {'industry_alignment': 'Unknown', 'client_alignment': 'Unknown',
                          'service_alignment': 'Unknown', 'geographic_alignment': 'Unknown',
                          'assessment': 'Unknown', 'score': None},
        'resource_assessment': {'required_roles': [item.get('text', '') for item in roles[:30]],
                                'estimated_resource_demand': 'Unknown', 'specialist_requirements': [],
                                'resource_constraints': [], 'assessment': 'Unknown', 'score': None,
                                'evidence': source_links(roles)},
        'execution_risk': {'technical_risks': source_links([item for item in risks if re.search(r' technical | system | security | data ', f" {item.get('text','').casefold()} ")]),
                           'resource_risks': [], 'schedule_risks': [], 'commercial_risks': [],
                           'contractual_risks': source_links(risks), 'overall_assessment': 'Unknown', 'risk_score': None},
        'commercial_attractiveness': {'contract_model': fact_value('commercial_model'), 'fixed_price': None,
                                      'time_and_material': None, 'framework_agreement': None,
                                      'optional_scope': None, 'commercial_opportunities': [],
                                      'commercial_concerns': [], 'assessment': 'Unknown', 'score': None},
        'schedule_assessment': {'submission_date': deadline, 'project_start': 'Unknown',
                                'project_duration': 'Unknown', 'major_milestones': source_links(milestones),
                                'schedule_constraints': [], 'assessment': 'Unknown', 'score': None},
        'critical_clarifications': clarifications_out,
        'go_no_go_analysis': {'weights': WEIGHTS, 'scores': dimension_rows, 'weighted_total': None,
                              'reason': 'Weighted score is withheld because decision dimensions lack verified internal inputs.'},
        'decision': {'recommendation': recommendation.upper(), 'decision_rationale': rationale,
                     'key_reasons': [f"Tender identifies {len(submission)} submission/compliance obligations",
                                     f"Tender identifies {len(milestones)} milestones and {len(risks)} risk statements",
                                     'Management must validate capability, resource availability, and commercial exposure'],
                     'conditions': conditions},
        'letter_data': letter_data,
    }
