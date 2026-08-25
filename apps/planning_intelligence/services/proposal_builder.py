"""Build frozen, planner-editable proposal content from controlled planning data."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from django.utils import timezone


def _text(value, fallback='Not specified in the available project references.'):
    if value is None or value == '' or value == [] or value == {}:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(f'• {_text(item, "")}' for item in value if item not in (None, '', {}, [])) or fallback
    if isinstance(value, dict):
        return '\n'.join(f'{str(key).replace("_", " ").title()}: {_text(item, "")}' for key, item in value.items())
    return str(value)


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _plain_text(value):
    """Remove presentation Markdown from generated proposal copy."""
    text = _text(value)
    lines = []
    for line in text.splitlines():
        line = re.sub(r'^\s{0,3}#{1,6}\s*', '', line)
        line = re.sub(r'^\s*[-*+]\s+', '', line)
        line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
        line = re.sub(r'__([^_]+)__', r'\1', line)
        lines.append(line.rstrip())
    return '\n'.join(lines).strip()


def _human_date(value, fallback):
    if not value:
        return fallback
    try:
        parsed = date.fromisoformat(str(value))
        return f'{parsed.day} {parsed.strftime("%B %Y")}'
    except ValueError:
        return str(value)


def _executive_summary(project, snapshot):
    schedule = snapshot['schedule']
    client = project.client or 'the Client'
    phase = project.phase or 'FEED/DEFINE'
    location = f' in {project.location}' if project.location else ''
    subject = re.sub(r'\s+Schedule\s*$', '', project.name, flags=re.IGNORECASE)
    deliverable_count = len(snapshot['deliverables'])
    first_paragraph = (
        f'This proposal defines the execution approach for the {phase} scope of '
        f'{subject}{location} for {client}. The work is structured into '
        f'{schedule["wbs_count"]} WBS elements and an integrated programme of '
        f'{schedule["activity_count"]} activities linked by '
        f'{schedule["relationship_count"]} logic relationships.'
    )
    dates = (
        f'The programme is planned from '
        f'{_human_date(schedule["planned_start"], "a date to be agreed")} to '
        f'{_human_date(schedule["calculated_finish"], "a finish date to be confirmed")}'
    )
    second_paragraph = (
        f'{dates}, with {schedule["critical_count"]} activities currently on the critical path. '
        f'The {deliverable_count} engineering deliverables will pass through defined discipline '
        'reviews and approval stages. Progress, decisions and changes will be reported against '
        'the approved schedule revision throughout execution.'
    )
    return f'{first_paragraph}\n\n{second_paragraph}'


def _list_value(value):
    """Keep document-intelligence evidence tabular without inventing missing facts."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _monthly_progress(activities):
    """Create a transparent activity-count plan when no weighted curve is supplied."""
    monthly = defaultdict(int)
    for activity in activities:
        finish = activity.planned_finish or activity.planned_start
        if finish:
            monthly[finish.strftime('%Y-%m')] += 1
    total = sum(monthly.values())
    cumulative = 0
    rows = []
    for month, count in sorted(monthly.items()):
        cumulative += count
        rows.append({
            'month': month,
            'activities_due': count,
            'monthly_activity_pct': round(count * 100 / total, 2) if total else 0,
            'cumulative_activity_pct': round(cumulative * 100 / total, 2) if total else 0,
            'basis': 'Activity-count proxy; replace with approved weighted progress',
        })
    return rows


def build_proposal_snapshot(project, version, generation=None):
    activities = list(version.activities.filter(is_deleted=False).order_by('sort_order', 'external_id'))
    wbs = list(version.wbs_nodes.filter(is_deleted=False).order_by('sort_order', 'code'))
    relationships = version.relationships.filter(is_deleted=False)
    resources = list(project.schedule_resources.filter(is_deleted=False).order_by('code'))
    milestones = [row for row in activities if row.is_milestone]
    critical = [row for row in activities if row.is_critical]
    source_documents = list(project.files.filter(is_deleted=False).order_by('category', 'original_filename'))
    discipline_counts = defaultdict(int)
    for activity in activities:
        discipline_counts[activity.discipline or 'Unassigned'] += 1
    intelligence = (generation.intelligence or {}) if generation else {}
    return {
        'captured_at': timezone.now().isoformat(),
        'project': {
            'id': project.id, 'name': project.name, 'client': project.client,
            'location': project.location, 'phase': project.phase,
            'effective_date': _iso(project.effective_date),
            'planned_end_date': _iso(project.planned_end_date),
            'duration_months': float(project.duration_months),
        },
        'generation': {'id': generation.id, 'version': generation.version} if generation else None,
        'schedule': {
            'id': version.schedule_id, 'version_id': version.id, 'version': version.version,
            'status': version.status, 'planned_start': _iso(version.schedule.planned_start),
            'calculated_finish': _iso(version.calculated_finish), 'activity_count': len(activities),
            'critical_count': len(critical), 'milestone_count': len(milestones),
            'relationship_count': relationships.count(), 'wbs_count': len(wbs),
        },
        'disciplines': [{'name': key, 'activity_count': value} for key, value in sorted(discipline_counts.items())],
        'wbs': [{'code': row.code, 'name': row.name, 'level': row.level, 'discipline': row.discipline} for row in wbs],
        'milestones': [{
            'id': row.external_id, 'name': row.name,
            'start': _iso(row.planned_start), 'finish': _iso(row.planned_finish),
        } for row in milestones],
        'resources': [{'code': row.code, 'name': row.name, 'role': row.role, 'type': row.resource_type} for row in resources],
        'source_documents': [{
            'name': row.original_filename, 'category': row.category,
            'parse_status': row.parse_status, 'confidence': row.confidence_score,
        } for row in source_documents],
        'deliverables': list((generation.eddr or [])) if generation else [],
        'validation': generation.validation if generation else [],
        'manhours': generation.manhours if generation else {},
        'monthly_progress': _monthly_progress(activities),
        'project_references': _list_value(intelligence.get('previous_experience') or intelligence.get('project_references')),
        'key_personnel': _list_value(intelligence.get('key_personnel') or intelligence.get('personnel')),
        'subcontractors': _list_value(intelligence.get('subcontractors') or intelligence.get('technology_partners')),
        'hse_statistics': _list_value(intelligence.get('hse_statistics') or intelligence.get('safety_statistics')),
        'certifications': _list_value(intelligence.get('certifications') or intelligence.get('iso_certificates')),
    }


def _build_legacy_default_sections(project, version, generation=None):
    snapshot = build_proposal_snapshot(project, version, generation)
    intelligence = (generation.intelligence or {}) if generation else {}
    scope = intelligence.get('scope') or intelligence.get('scope_summary') or intelligence.get('project_scope')
    disciplines = ', '.join(row['name'].title() for row in snapshot['disciplines'])
    schedule = snapshot['schedule']

    def section(
        key, number, group, title, content, data=None, *, included=True,
        section_type='narrative', source='planner', required=False, readiness='draft',
    ):
        return {
            'key': key, 'number': number, 'group': group, 'title': title,
            'content': _plain_text(content), 'included': included, 'data': data or [],
            'section_type': section_type, 'source': source, 'required': required,
            'readiness': readiness,
        }

    compliance = [
        {'item': '1', 'requirement': 'Executive summary and value proposition', 'response_section': '2.1 Executive Summary', 'status': 'Covered'},
        {'item': '2', 'requirement': 'Understanding and interpretation of requirements', 'response_section': '2.2 Understanding of Client Requirements', 'status': 'Covered'},
        {'item': '3', 'requirement': 'Scope, deliverables and execution methodology', 'response_section': '2.3–2.7 Technical Execution', 'status': 'Covered'},
        {'item': '4', 'requirement': 'Work schedule, milestones and resource allocation', 'response_section': '3.1–3.4 Work Programme', 'status': 'Covered'},
        {'item': '5', 'requirement': 'Project controls, reporting and change management', 'response_section': '4.1–4.4 Project Management', 'status': 'Covered'},
        {'item': '6', 'requirement': 'Quality and HSE arrangements', 'response_section': '5.1–5.2 Assurance', 'status': 'Covered'},
        {'item': '7', 'requirement': 'Corporate experience, personnel and certifications', 'response_section': '6.1–6.4 Corporate Evidence', 'status': 'Evidence required'},
        {'item': '8', 'requirement': 'Qualifications, exclusions and subcontractors', 'response_section': '1.4, 5.3 and 5.4', 'status': 'Planner review'},
    ]

    sections = [
        section('cover', '', 'Front Matter', 'Cover Page', f"Technical Proposal\n{project.name}\nPrepared for {project.client or 'Client'}", section_type='cover', source='project', required=True, readiness='ready'),
        section('transmittal', '1.1', 'Front Matter', 'Letter of Transmittal', f"Dear Sir/Madam,\n\nWe are pleased to submit our technical proposal for {project.name}. This submission describes our understanding of the requirement, proposed execution approach, work programme, resources and assurance arrangements.\n\nWe look forward to working with {project.client or 'the Client'} and remain available to clarify any aspect of this proposal.", required=True),
        section('document_control', '1.2', 'Front Matter', 'Document Control', 'This proposal is a controlled document. Its proposal number, revision, submission status, schedule source and approval record are maintained by RADAI.', section_type='control', source='system', required=True, readiness='ready'),
        section('compliance_matrix', '1.3', 'Front Matter', 'Technical Bid Compliance Matrix', 'This matrix maps the principal tender requirements to the corresponding proposal response. The bid team shall confirm each status before issue.', compliance, section_type='matrix', source='hybrid', required=True),
        section('qualification_statement', '1.4', 'Front Matter', 'Qualification Statement', 'The proposed services comply with the stated technical requirements except where a qualification or clarification is expressly recorded below. Commercial qualifications shall be maintained separately from the technical response.', section_type='matrix', source='planner', required=True),
        section('contents', '1.5', 'Front Matter', 'Table of Contents', 'The final PDF and Word documents generate the controlled section sequence from the included proposal chapters.', section_type='contents', source='system', required=True, readiness='ready'),

        section('executive_summary', '2.1', 'Technical Bid', 'Executive Summary', _executive_summary(project, snapshot), source='schedule', required=True, readiness='ready'),
        section('requirements', '2.2', 'Technical Bid', 'Understanding of Client Requirements', scope, source='documents', required=True),
        section('scope', '2.3', 'Technical Bid', 'Scope of Services', f'The proposed services cover the following planning and engineering disciplines: {disciplines}.', source='documents', required=True),
        section('objectives', '2.4', 'Technical Bid', 'Project Objectives and Success Criteria', 'The objectives are to complete the defined scope safely, satisfy the specified technical and quality requirements, maintain schedule visibility and provide traceable deliverables for Client review and acceptance.', required=True),
        section('methodology', '2.5', 'Technical Bid', 'Project Execution Methodology', 'The services will be executed through mobilization, source-document verification, discipline production, interdisciplinary review, Client review, comment incorporation, approval and final issue. Each deliverable will remain linked to the WBS and controlled programme.', required=True),
        section('wbs', '2.6', 'Technical Bid', 'Work Breakdown Structure', 'The WBS establishes the controlled hierarchy for execution, reporting and accountability.', snapshot['wbs'], section_type='register', source='schedule', required=True, readiness='ready'),
        section('deliverables', '2.7', 'Technical Bid', 'Deliverables and EDDR', f"The controlled register currently contains {len(snapshot['deliverables'])} identified engineering deliverables and their review cycles.", snapshot['deliverables'], section_type='register', source='generation', required=True, readiness='ready'),
        section('technical_interfaces', '2.8', 'Technical Bid', 'Technical Interfaces and Reviews', 'Interfaces between disciplines, Client stakeholders and third parties will be identified early, assigned to accountable owners and closed through documented review actions.'),

        section('schedule', '3.1', 'Work Programme', 'Detailed Work Schedule', f"The controlled programme contains {schedule['activity_count']} activities and {schedule['relationship_count']} logic relationships. It starts on {_human_date(schedule['planned_start'], 'a date to be agreed')} and currently finishes on {_human_date(schedule['calculated_finish'], 'a date to be confirmed')}.", snapshot['milestones'], section_type='schedule', source='schedule', required=True, readiness='ready'),
        section('milestones', '3.2', 'Work Programme', 'Milestones and Stage Gates', f"The programme contains {schedule['milestone_count']} controlled milestones.", snapshot['milestones'], section_type='register', source='schedule', required=True, readiness='ready'),
        section('resource_plan', '3.3', 'Work Programme', 'Resource and Manhour Plan', 'Resources will be mobilized against the approved programme and monitored by discipline, role and reporting period.', snapshot['resources'], section_type='resource', source='schedule', required=True),
        section('progress_curve', '3.4', 'Work Programme', 'Planned Progress and S-Curve', 'Planned monthly and cumulative progress will be calculated from the time-phased approved schedule. The issue version shall include the approved S-curve and manpower histogram.', included=False, section_type='chart', source='controls'),

        section('organization', '4.1', 'Project Management', 'Project Organization and Key Roles', 'The project organization will define the Project Manager, engineering leadership, discipline leads, project controls, quality, HSE and document-control responsibilities.', snapshot['resources'], section_type='organization', source='planner', required=True),
        section('controls', '4.2', 'Project Management', 'Project Controls and Reporting', 'Performance will be controlled through an approved baseline, defined data dates, actual and remaining durations, progress measurement, forecasts, variance analysis and periodic reporting.', required=True),
        section('change_risk', '4.3', 'Project Management', 'Change, Risk and Opportunity Management', 'Changes will be identified, assessed for scope, schedule and cost impact, approved through the agreed authority and incorporated only into a controlled revision. Risks and opportunities will be assigned, monitored and reported.'),
        section('communication', '4.4', 'Project Management', 'Communication, Meetings and Governance', 'Focal points, correspondence protocols, reporting cycles and meeting schedules will be agreed at kick-off. Decisions, comments and approvals will be recorded against the controlled project revision.'),
        section('document_control_plan', '4.5', 'Project Management', 'Document Control and Deliverable Workflow', 'Deliverables will be numbered, reviewed, transmitted and revised in accordance with the agreed document-control procedure and EDDR workflow.'),

        section('quality', '5.1', 'Assurance and Compliance', 'Quality Management Plan', 'Quality requirements will be implemented through approved procedures, competent resources, interdisciplinary checks, verification records, audits and traceable close-out of comments and non-conformities.'),
        section('hse', '5.2', 'Assurance and Compliance', 'Health, Safety and Environment Plan', intelligence.get('hse_requirements') or 'HSE requirements will be incorporated into planning and execution through risk assessment, competency, approved procedures, assurance checks and incident-prevention measures.'),
        section('subcontractors', '5.3', 'Assurance and Compliance', 'Proposed Subcontractors and Specialist Partners', 'No subcontractor or specialist partner is proposed unless identified and approved in this section.', section_type='register'),
        section('assumptions', '5.4', 'Assurance and Compliance', 'Assumptions, Exclusions and Qualifications', intelligence.get('assumptions') or 'Items not expressly identified in the approved scope remain subject to clarification and formal change control.', required=True),

        section('experience', '6.1', 'Corporate Evidence', 'Relevant Experience and Case Studies', 'Insert approved corporate project references relevant to the Client, project phase, disciplines and scope.', included=False, section_type='evidence', source='corporate_library'),
        section('key_personnel', '6.2', 'Corporate Evidence', 'Key Personnel and CV Index', 'Insert the approved key-personnel schedule and link controlled CV attachments.', included=False, section_type='evidence', source='corporate_library'),
        section('certifications', '6.3', 'Corporate Evidence', 'Management-System Certifications', 'Attach current applicable ISO and statutory certificates from the controlled corporate library.', included=False, section_type='evidence', source='corporate_library'),
        section('corporate_profile', '6.4', 'Corporate Evidence', 'Corporate Organization and Capability', 'Insert the approved corporate profile, organization charts and office capability statement.', included=False, section_type='evidence', source='corporate_library'),

        section('source_register', 'A', 'Appendices', 'Source Document Register', 'The following uploaded project references support the proposal and controlled planning data.', snapshot['source_documents'], section_type='register', source='documents', required=True, readiness='ready'),
        section('schedule_appendix', 'B', 'Appendices', 'Schedule and Milestone Extracts', 'Attach the approved schedule extract, milestone register, manpower histogram and S-curve applicable to this proposal revision.', section_type='appendix', source='schedule'),
        section('eddr_appendix', 'C', 'Appendices', 'Deliverable Register', 'Attach the controlled EDDR extract applicable to this proposal revision.', snapshot['deliverables'], section_type='appendix', source='generation'),
        section('supporting_evidence', 'D', 'Appendices', 'Supporting Plans, Certificates and CVs', 'Attach only the evidence referenced by the compliance matrix. Each attachment shall carry a title, revision, validity status and source-library reference.', included=False, section_type='appendix', source='corporate_library'),
    ]
    return snapshot, sections


def build_default_sections(project, version, generation=None):
    """Build the enterprise tender structure used by Proposal Studio."""
    snapshot = build_proposal_snapshot(project, version, generation)
    intelligence = (generation.intelligence or {}) if generation else {}
    scope = intelligence.get('scope') or intelligence.get('scope_summary') or intelligence.get('project_scope')
    disciplines = ', '.join(row['name'].title() for row in snapshot['disciplines'])
    schedule = snapshot['schedule']

    def section(
        key, number, group, title, content, data=None, *, section_type='narrative',
        source='planner', readiness='draft', required=True,
    ):
        return {
            'key': key, 'number': number, 'group': group, 'title': title,
            'content': _plain_text(content), 'included': True, 'data': data or [],
            'section_type': section_type, 'source': source, 'required': required,
            'readiness': readiness,
        }

    compliance_requirements = [
        ('10.1', 'Qualification statement and confirmation of RFT compliance', '1 Qualification Statement', 'Bid team review'),
        ('10.2', 'Detailed technical-bid compliance matrix', '2 Technical Bid Content', 'Covered'),
        ('10.3', 'Previous experience and comparable project case studies', '3.1 Previous Experience', 'Evidence required'),
        ('10.4.1', 'Corporate organization structure and regional office layout', '3.2.1 Corporate Organization and Regional Offices', 'Evidence required'),
        ('10.4.2', 'Project organization chart for management, engineering and support', '3.2.2 Project Organization Chart', 'Evidence required'),
        ('10.4.3', 'Key personnel schedule: age, nationality, qualifications, experience and HSE training', '3.2.3 Key Personnel Schedule', 'Evidence required'),
        ('10.4.4', 'Detailed curricula vitae for proposed key personnel', '3.2.4 Detailed CVs', 'Evidence required'),
        ('10.5.1', 'Executive summary and value proposition', '3.3.1 Executive Summary', 'Covered'),
        ('10.5.2', 'Project objectives and measurable success criteria', '3.3.2 Project Objectives', 'Covered'),
        ('10.5.3', 'Understanding and interpretation of Client requirements', '3.3.3 Understanding of Requirements', 'Document review'),
        ('10.5.4', 'Proposed solution architecture and architectural diagrams', '3.3.5 Proposed Solution Architecture', 'Bid team review'),
        ('10.5.5', 'Data flow, systems interfaces and integration points', '3.3.6 Data Flow and Integration Points', 'Bid team review'),
        ('10.5.6', 'AI model development and technical methodology', '3.3.7 AI Model Development Methodology', 'Bid team review'),
        ('10.5.7', 'Data preparation and pre-processing strategy', '3.3.8 Data Preparation and Pre-processing', 'Bid team review'),
        ('10.5.8', 'DevOps and MLOps lifecycle practices', '3.3.9 DevOps and MLOps', 'Bid team review'),
        ('10.5.9', 'Performance metrics, acceptance thresholds and evaluation', '3.3.10 Performance Metrics and Evaluation', 'Bid team review'),
        ('10.5.10', 'Security, data privacy, governance and compliance framework', '3.3.11 Security, Privacy and Governance', 'Bid team review'),
        ('10.5.11', 'Risk assessment and mitigation strategy', '3.3.12 Risk Assessment and Mitigation', 'Covered'),
        ('10.5.12', 'Tools, platforms, technology stack and engineering integrations', '3.3.13 Tools, Platforms and Technology Stack', 'Bid team review'),
        ('10.5.13', 'Project execution methodology', '3.3.14 Project Execution Methodology', 'Covered'),
        ('10.5.14', 'Communication and stakeholder-engagement plan', '3.3.15 Communication Plan', 'Covered'),
        ('10.5.15', 'Project controls, progress reporting and change control', '3.3.16 Project Controls and Reporting', 'Covered'),
        ('10.5.16', 'Quality management plan', '3.3.17 Quality Management Plan', 'Covered'),
        ('10.5.17', 'Project HSE plan', '3.3.18 HSE Plan', 'Document review'),
        ('10.5.18', 'Work breakdown structure', '3.3.19 Work Breakdown Structure', 'Covered'),
        ('10.5.19', 'List of deliverables and EDDR', '3.3.20 Deliverables and EDDR', 'Covered'),
        ('10.6.1', 'Detailed work schedule and milestones', '3.4.1 Detailed Work Schedule', 'Covered'),
        ('10.6.2', 'Manpower histogram and resource loading', '3.4.2 Manpower Histogram', 'Data required'),
        ('10.6.3', 'Planned progress S-curve', '3.4.3 Planned Progress S-Curve', 'Data required'),
        ('10.6.4', 'Monthly plan-progress breakdown', '3.4.4 Monthly Plan Progress', 'Activity-count proxy'),
        ('10.7', 'Proposed subcontractors and technology partners', '3.5 Proposed Subcontractors', 'Evidence required'),
        ('10.8', 'Project-specific HSE requirements, statistics and organization', '3.6 Specific HSE Requirements', 'Evidence required'),
        ('10.9', 'ISO certifications and quality-system documentation', '3.7 Quality and Certifications', 'Evidence required'),
        ('10.10', 'Business venture, investment and manufacturing entity plans', '4 Business Venture and Investment Plans', 'Confirm applicability'),
    ]
    compliance = [
        {
            'sow_item': item, 'requirement': requirement,
            'proposal_reference': reference, 'compliance_status': status,
            'bidder_comment': 'Confirm and finalize before technical-bid issue.',
        }
        for item, requirement, reference, status in compliance_requirements
    ]
    qualification_rows = [
        {'topic': 'Technical compliance', 'position': 'Comply', 'qualification_or_justification': 'Subject only to qualifications explicitly recorded in this register.'},
        {'topic': 'Commercial qualifications', 'position': 'Separate submission', 'qualification_or_justification': 'Maintain commercial departures outside the technical proposal where the RFT requires separate envelopes.'},
        {'topic': 'Intellectual property', 'position': 'To be agreed', 'qualification_or_justification': 'Pre-existing IP remains with its owner; project-specific use and deliverable rights shall follow the final contract.'},
        {'topic': 'Source code and escrow', 'position': 'To be agreed', 'qualification_or_justification': 'Define escrow triggers, deposited artefacts, third-party software exclusions and release rights before award.'},
    ]

    sections = [
        section('cover', '', 'Front Matter', 'Cover Page', f"Technical Proposal\n{project.name}\nPrepared for {project.client or 'Client'}", section_type='cover', source='project', readiness='ready'),
        section('transmittal', '', 'Front Matter', 'Letter of Transmittal', f"Dear Sir/Madam,\n\nWe are pleased to submit our technical proposal for {project.name}. This controlled submission presents our compliance position, technical solution, execution plan, programme, resources and supporting evidence for review by {project.client or 'the Client'}.", readiness='draft'),
        section('document_control', '', 'Front Matter', 'Document Control and Revision History', 'Proposal number, revision, status, prepared-by, checked-by, approved-by and issue date are controlled in the Studio and reproduced in the issued document.', section_type='control', source='system', readiness='ready'),
        section('contents', '', 'Front Matter', 'Table of Contents', 'The final PDF and Word deliverables generate their section sequence from the chapters included in this controlled proposal revision.', section_type='contents', source='system', readiness='ready'),

        section('qualification_statement', '1', 'Qualification Statement', 'Qualification Statement', 'We confirm compliance with the technical requirements of the RFT and Scope of Work, except for matters expressly identified in the qualification register. Each qualification requires an accountable owner, justification and formal disposition before issue.', qualification_rows, section_type='matrix', readiness='bid_review'),
        section('compliance_matrix', '2', 'Technical Bid Content', 'Compliance to SOW Section 10', 'The matrix cross-references every core SOW Section 10 technical-bid requirement to its controlled proposal location. Statuses marked evidence required, data required or bid-team review must be closed before submission.', compliance, section_type='matrix', source='hybrid', readiness='bid_review'),

        section('experience', '3.1', 'Previous Experience', 'Previous Experience and Comparable Case Studies', 'Provide verified reference projects demonstrating delivery of comparable AI, digital twin, industrial automation, engineering and project-control scopes. Each case study should identify the Client, scope, dates, value where permitted, technologies, outcomes and reference contact.', snapshot['project_references'], section_type='evidence', source='corporate_library', readiness='evidence_required'),
        section('corporate_organization', '3.2.1', 'Resources & Organizational Structure', 'Corporate Organization and Regional Offices', 'Present the legal entity, corporate reporting structure, regional office layout, office locations, delivery centres and the capabilities available to support this assignment.', section_type='organization', source='corporate_library', readiness='evidence_required'),
        section('project_organization', '3.2.2', 'Resources & Organizational Structure', 'Project Organization Chart', 'Define reporting lines for the Project Director, Project Manager, engineering disciplines, AI and data teams, project controls, quality, HSE, document control, commissioning and support.', snapshot['resources'], section_type='organization', source='planner', readiness='evidence_required'),
        section('key_personnel', '3.2.3', 'Resources & Organizational Structure', 'Key Personnel Schedule', 'Record each proposed person’s role, age, nationality, highest qualification, professional registrations, total and relevant years of experience, location, availability and HSE training status.', snapshot['key_personnel'], section_type='evidence', source='corporate_library', readiness='evidence_required'),
        section('personnel_cvs', '3.2.4', 'Resources & Organizational Structure', 'Detailed Curricula Vitae', 'Provide controlled CVs for all key personnel, aligned to the personnel schedule and limited to verified qualifications, employment history, relevant assignments, certifications and training.', snapshot['key_personnel'], section_type='evidence', source='corporate_library', readiness='evidence_required'),

        section('executive_summary', '3.3.1', 'Method Statement / Execution Plan', 'Executive Summary', _executive_summary(project, snapshot), source='schedule', readiness='ready'),
        section('objectives', '3.3.2', 'Method Statement / Execution Plan', 'Project Objectives and Success Criteria', 'The project will deliver the agreed scope safely and predictably, meet technical and quality requirements, preserve data traceability, maintain programme visibility and achieve Client acceptance against agreed performance measures.'),
        section('requirements', '3.3.3', 'Method Statement / Execution Plan', 'Understanding of Requirements', scope, source='documents', readiness='document_review'),
        section('scope', '3.3.4', 'Method Statement / Execution Plan', 'Scope of Services and Boundaries', f'The current controlled planning basis covers these disciplines: {disciplines}. Confirm interfaces, exclusions, battery limits, Client inputs and acceptance boundaries against the final RFT.', source='documents', readiness='document_review'),
        section('solution_architecture', '3.3.5', 'Method Statement / Execution Plan', 'Proposed Solution Architecture', 'Describe the proposed logical and deployment architecture, user experience, application services, data stores, AI services, integration layer, identity controls, hosting boundaries, resilience and operational support. Insert a reviewed high-level architecture diagram.', section_type='architecture', readiness='technical_review'),
        section('data_flow', '3.3.6', 'Method Statement / Execution Plan', 'Data Flow and Integration Points', 'Map source systems, data owners, ingestion methods, transformation rules, interfaces, validation, storage, model consumption, outputs and feedback loops. Identify real-time and batch boundaries, interface protocols and ownership at each hand-off.', section_type='architecture', readiness='technical_review'),
        section('ai_methodology', '3.3.7', 'Method Statement / Execution Plan', 'AI Model Development Methodology', 'Define use-case selection, baseline establishment, model selection, experimentation, traceability, validation, human oversight, explainability, release approval and monitoring. Do not claim model capability that has not been demonstrated and accepted.', readiness='technical_review'),
        section('data_preparation', '3.3.8', 'Method Statement / Execution Plan', 'Data Preparation and Pre-processing Strategy', 'Describe data discovery, classification, quality profiling, cleansing, normalization, labelling, feature engineering, train-validation-test separation, lineage, retention and handling of incomplete or sensitive records.', readiness='technical_review'),
        section('mlops', '3.3.9', 'Method Statement / Execution Plan', 'DevOps and MLOps', 'Define source control, automated testing, infrastructure configuration, model and data versioning, CI/CD promotion gates, registry controls, observability, drift monitoring, rollback, backup and release evidence.', readiness='technical_review'),
        section('performance_metrics', '3.3.10', 'Method Statement / Execution Plan', 'Performance Metrics and Evaluation Criteria', 'Agree business and technical acceptance measures for each use case, including accuracy or error measures, latency, availability, robustness, safety constraints, false-positive and false-negative tolerances, test datasets and approval thresholds.', section_type='matrix', readiness='technical_review'),
        section('security_governance', '3.3.11', 'Method Statement / Execution Plan', 'Security, Data Privacy, Governance and Compliance', 'Apply least privilege, segregation of duties, encryption, secure secrets management, logging, vulnerability management, data classification, residency and retention rules, privacy controls, incident response and Client approval gates.', readiness='technical_review'),
        section('risk_mitigation', '3.3.12', 'Method Statement / Execution Plan', 'Risk Assessment and Mitigation', 'Identify technical, data, integration, resource, schedule, HSE, cybersecurity, adoption and third-party risks. Assign probability, impact, owner, mitigation, contingency, due date and residual rating in the controlled risk register.'),
        section('technology_stack', '3.3.13', 'Method Statement / Execution Plan', 'Tools, Platforms and Technology Stack', 'List proposed software, versions, licensing responsibilities, environments and support boundaries. Address engineering integrations—including Aspen HYSYS and AutoCAD where required—along with AI frameworks, data platforms, APIs, DevOps/MLOps tooling and cybersecurity controls.', section_type='register', readiness='technical_review'),
        section('methodology', '3.3.14', 'Method Statement / Execution Plan', 'Project Execution Methodology', 'Execute through mobilization, requirements confirmation, source-data validation, design, build, verification, staged review, deployment, acceptance and close-out. Each output remains linked to the WBS, accountable owner and controlled programme.'),
        section('communication', '3.3.15', 'Method Statement / Execution Plan', 'Communication and Stakeholder Plan', 'Agree focal points, governance forums, meeting cadence, reporting cycle, escalation route, correspondence protocol, decision log, action register and Client review workflow at kick-off.'),
        section('controls', '3.3.16', 'Method Statement / Execution Plan', 'Project Controls and Reporting', 'Control performance through an approved baseline, data dates, physical-progress rules, actual and remaining durations, forecasts, variance analysis, change control, risk review and periodic reporting.'),
        section('quality', '3.3.17', 'Method Statement / Execution Plan', 'Quality Management Plan', 'Implement approved procedures, competent resources, checking and verification, interdisciplinary reviews, document controls, audits, non-conformance control, corrective actions and traceable close-out of comments.'),
        section('hse', '3.3.18', 'Method Statement / Execution Plan', 'Health, Safety and Environment Plan', intelligence.get('hse_requirements') or 'Integrate HSE leadership, competency, hazard identification, risk assessment, work controls, assurance, emergency response, reporting and continual improvement into every execution phase.', source='documents', readiness='document_review'),
        section('wbs', '3.3.19', 'Method Statement / Execution Plan', 'Work Breakdown Structure', 'The controlled WBS establishes the hierarchy for scope ownership, scheduling, resource planning, progress measurement and reporting.', snapshot['wbs'], section_type='register', source='schedule', readiness='ready'),
        section('deliverables', '3.3.20', 'Method Statement / Execution Plan', 'List of Deliverables and EDDR', f"The current controlled register contains {len(snapshot['deliverables'])} identified deliverables and associated review cycles.", snapshot['deliverables'], section_type='register', source='generation', readiness='ready'),

        section('schedule', '3.4.1', 'Work Schedule', 'Detailed Work Schedule and Milestones', f"The controlled programme contains {schedule['activity_count']} activities, {schedule['relationship_count']} logic relationships and {schedule['milestone_count']} milestones. It starts on {_human_date(schedule['planned_start'], 'a date to be agreed')} and currently finishes on {_human_date(schedule['calculated_finish'], 'a date to be confirmed')}.", snapshot['milestones'], section_type='schedule', source='schedule', readiness='ready'),
        section('manpower_histogram', '3.4.2', 'Work Schedule', 'Manpower Histogram and Resource Loading', 'Present time-phased manpower by month, discipline and role using approved effort and calendar assumptions. Reconcile total planned hours to the resource plan before issue.', snapshot['resources'], section_type='chart', source='controls', readiness='data_required'),
        section('progress_curve', '3.4.3', 'Work Schedule', 'Planned Progress S-Curve', 'Present monthly and cumulative planned progress derived from the approved weighted schedule. The curve must identify the baseline revision, cut-off date, weighting basis and stage-gate assumptions.', snapshot['monthly_progress'], section_type='chart', source='controls', readiness='data_required'),
        section('monthly_progress', '3.4.4', 'Work Schedule', 'Monthly Plan Progress Breakdown', 'The table below is an activity-count planning proxy generated from dated activities. Replace or approve it against the contractual weighted-progress method before issue.', snapshot['monthly_progress'], section_type='register', source='schedule', readiness='data_required'),
        section('subcontractors', '3.5', 'Proposed Subcontractors', 'Proposed Subcontractors and Technology Partners', 'Identify each specialist subcontractor or technology partner, including Pivotol AI where proposed. State legal name, location, scope, deliverables, responsibility boundary, prior relationship, approvals, cybersecurity access and contractual status.', snapshot['subcontractors'], section_type='register', source='corporate_library', readiness='evidence_required'),
        section('specific_hse', '3.6.1', 'Specific HSE Requirements', 'Project-Specific HSE Requirements', 'Translate Client and site HSE obligations into project controls, responsibilities, competence requirements, hazard registers, assurance activities and deliverable requirements.', section_type='register', source='documents', readiness='evidence_required'),
        section('hse_statistics', '3.6.2', 'Specific HSE Requirements', 'Safety Statistics', 'Provide verified corporate and relevant regional safety statistics for the tender-defined reporting period, with definitions, hours worked, recordable events, lost-time events and calculated rates.', snapshot['hse_statistics'], section_type='evidence', source='corporate_library', readiness='evidence_required'),
        section('hse_organization', '3.6.3', 'Specific HSE Requirements', 'HSE Organization Chart', 'Provide the project HSE reporting structure, named focal points, escalation path and interfaces with corporate, regional, Client and subcontractor HSE organizations.', section_type='organization', source='corporate_library', readiness='evidence_required'),
        section('certifications', '3.7.1', 'Quality & Certifications', 'ISO and Management-System Certificates', 'Attach current, legible certificates with certificate number, standard, scope, issuing body, legal entity, sites covered, issue date and expiry date.', snapshot['certifications'], section_type='evidence', source='corporate_library', readiness='evidence_required'),
        section('quality_documentation', '3.7.2', 'Quality & Certifications', 'Quality Management Documentation', 'Provide the relevant quality policy, management-system overview, project quality-plan basis, audit arrangements, document-control procedure and quality records requested by the RFT.', section_type='evidence', source='corporate_library', readiness='evidence_required'),

        section('business_venture', '4.1', 'Business Venture & Investment Plans', 'Business Venture Plan', 'Confirm applicability to the tender. Where required, describe the proposed venture model, partners, governance, commercial rationale, local value, capability transfer, milestones and commitments. Otherwise record a formally approved Not Applicable statement.', readiness='applicability_review'),
        section('investment_plan', '4.2', 'Business Venture & Investment Plans', 'Investment Plan', 'Confirm applicability and provide phased capital and operating investment, funding source, approvals, schedule, employment impact, local-content outcomes, risks and performance commitments without introducing unapproved commercial terms.', readiness='applicability_review'),
        section('manufacturing_entity', '4.3', 'Business Venture & Investment Plans', 'Manufacturing Entity Proposal', 'Where requested, describe the proposed legal entity, ownership, location, facilities, products, capacity, technology transfer, supply chain, certifications, implementation programme and sustainability commitments. Otherwise record Not Applicable with justification.', readiness='applicability_review'),

        section('source_register', 'A', 'Appendices', 'Source Document Register', 'These uploaded project references support the proposal and its controlled planning data.', snapshot['source_documents'], section_type='register', source='documents', readiness='ready'),
        section('schedule_appendix', 'B', 'Appendices', 'Schedule, Histogram and S-Curve Extracts', 'Attach the approved programme, milestone register, manpower histogram, S-curve and monthly progress table applicable to this proposal revision.', snapshot['monthly_progress'], section_type='appendix', source='schedule', readiness='data_required'),
        section('eddr_appendix', 'C', 'Appendices', 'Deliverable Register', 'Attach the controlled EDDR extract applicable to this proposal revision.', snapshot['deliverables'], section_type='appendix', source='generation', readiness='ready'),
        section('supporting_evidence', 'D', 'Appendices', 'CV, Certificate and Corporate Evidence Index', 'Attach only approved evidence cited by the proposal and compliance matrix. Each attachment must identify its title, revision, validity, owner and controlled-library reference.', section_type='appendix', source='corporate_library', readiness='evidence_required'),
    ]
    return snapshot, sections
