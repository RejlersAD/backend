"""Deterministic, scope-bound work packages for engineering project schedules.

This module never creates project scope. A caller supplies an actual deliverable
and receives an ordered work breakdown for that deliverable. Durations are
working-day planning allowances, not contractual dates, quantity-based estimates,
or values prescribed by PMI, GAO or Primavera. The allowance and complexity
selection remain attached to each task for planner review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


LIBRARY_VERSION = 'enterprise-workflows-v1'
SUPPORTED_PROJECT_TYPES = ('building', 'industrial', 'infrastructure', 'oil_gas')
_PROJECT_TYPE_ALIASES = {
    'engineering': '', 'engineering project': '',
    'building': 'building', 'building project': 'building', 'buildings': 'building',
    'commercial': 'building', 'residential': 'building',
    'industrial': 'industrial', 'industrial project': 'industrial', 'epc': 'industrial',
    'infrastructure': 'infrastructure', 'infrastructure project': 'infrastructure',
    'oil gas': 'oil_gas', 'oil and gas': 'oil_gas', 'oil gas project': 'oil_gas',
    'oil and gas project': 'oil_gas', 'oilgas': 'oil_gas',
}
_DISCIPLINES = {
    'architectural': 'Architectural', 'architecture': 'Architectural', 'arch': 'Architectural',
    'structural': 'Structural', 'structure': 'Structural', 'str': 'Structural',
    'civil': 'Civil', 'civil structural': 'Civil / Structural',
    'civil and structural': 'Civil / Structural', 'c s': 'Civil / Structural',
    'mechanical': 'Mechanical', 'mech': 'Mechanical', 'hvac': 'Mechanical',
    'electrical': 'Electrical', 'elec': 'Electrical', 'el': 'Electrical',
    'mep': 'MEP', 'plumbing': 'MEP', 'building services': 'MEP',
    'process': 'Process', 'proc': 'Process',
    'piping': 'Piping', 'pipeline': 'Piping',
    'instrumentation': 'Instrumentation', 'instrument': 'Instrumentation',
    'instrumentation and control': 'Instrumentation', 'i c': 'Instrumentation',
    'control systems': 'Instrumentation', 'ic': 'Instrumentation',
    'survey': 'Survey', 'surveying': 'Survey', 'utilities': 'Utilities',
    'engineering': 'Engineering', 'multidiscipline': 'Engineering',
    'multidisciplinary': 'Engineering', 'multi discipline': 'Engineering',
    'procurement': 'Procurement', 'construction': 'Construction',
    'commissioning': 'Commissioning', 'project controls': 'Project Controls',
    'planning': 'Project Controls', 'project management': 'Project Controls',
    'quality': 'Quality', 'qa qc': 'Quality', 'safety': 'Technical Safety',
    'technical safety': 'Technical Safety', 'hse': 'Technical Safety',
    'telecom': 'Telecommunications', 'telecommunications': 'Telecommunications',
}


def _normalized(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


def normalize_project_type(project_type=''):
    """Return an industry key, or blank for an unspecified/engineering project."""
    value = _normalized(project_type)
    if not value:
        return ''
    if value not in _PROJECT_TYPE_ALIASES:
        raise ValueError(f'Unsupported project type: {project_type}.')
    return _PROJECT_TYPE_ALIASES[value]


@dataclass(frozen=True)
class _Stage:
    code: str
    action: str
    low: int
    high: int
    output: str
    role: str
    weight: int


@dataclass(frozen=True)
class _Workflow:
    name: str
    discipline: str
    phase: str
    stages: tuple[_Stage, ...]


def _s(code, action, days, output, role='Discipline Engineer', weight=15):
    return _Stage(code, action, *days, output, role, weight)


_WORKFLOWS = {
    'foundation_design': _Workflow('Foundation engineering', 'Civil / Structural', 'engineering', (
        _s('INPUTS', 'Collect loading and geotechnical input data', (2, 5), 'Design input register'),
        _s('BASIS', 'Review existing drawings and foundation design criteria', (3, 7), 'Checked design basis'),
        _s('CALCULATE', 'Develop foundation sizing and reinforcement calculations', (7, 15), 'Foundation calculations', weight=30),
        _s('DRAWINGS', 'Develop foundation drawings and quantities', (5, 12), 'Foundation drawings and quantities', 'Structural Designer', 25),
        _s('CHECK', 'Complete internal technical and interface review', (3, 7), 'Technical review record', 'Lead Structural Engineer'),
        _s('ISSUE', 'Resolve review comments and issue the design package', (2, 4), 'Issued foundation design package', 'Lead Structural Engineer', 10),
    )),
    'architectural_design': _Workflow('Architectural design development', 'Architectural', 'engineering', (
        _s('INPUTS', 'Collect client brief and site planning inputs', (2, 5), 'Architectural input register', 'Architect'),
        _s('BASIS', 'Review existing drawings and applicable planning requirements', (3, 7), 'Design criteria review', 'Architect'),
        _s('LAYOUT', 'Develop architectural layouts and details', (7, 15), 'Architectural design drawings', 'Architect', 30),
        _s('COORDINATE', 'Coordinate structural and building services interfaces', (3, 7), 'Coordinated interface record', 'Lead Architect', 20),
        _s('CHECK', 'Complete architectural technical review', (3, 7), 'Checked architectural design', 'Lead Architect'),
        _s('ISSUE', 'Resolve comments and issue architectural design documents', (2, 4), 'Issued architectural package', 'Lead Architect', 10),
    )),
    'structural_design': _Workflow('Structural design development', 'Structural', 'engineering', (
        _s('INPUTS', 'Collect geometry, loading and material inputs', (2, 5), 'Structural input register'),
        _s('BASIS', 'Review structural design basis and existing drawings', (3, 7), 'Checked structural design basis'),
        _s('ANALYZE', 'Develop structural analysis and member design', (7, 15), 'Structural analysis and calculations', weight=30),
        _s('DETAIL', 'Develop structural details and coordinate interfaces', (5, 12), 'Coordinated structural drawings', 'Structural Designer', 25),
        _s('CHECK', 'Complete independent structural design check', (3, 7), 'Structural check record', 'Lead Structural Engineer'),
        _s('ISSUE', 'Resolve check comments and issue structural documents', (2, 4), 'Issued structural design package', 'Lead Structural Engineer', 10),
    )),
    'mep_design': _Workflow('Building services design development', 'MEP', 'engineering', (
        _s('INPUTS', 'Collect building services loads and spatial inputs', (2, 5), 'Building services input register'),
        _s('BASIS', 'Review services design criteria and existing provisions', (3, 7), 'Checked services design basis'),
        _s('SIZE', 'Develop building services sizing and equipment selections', (5, 12), 'Services calculations and selections', weight=30),
        _s('COORDINATE', 'Develop coordinated building services layouts', (5, 12), 'Coordinated services layouts', 'MEP Designer', 25),
        _s('CHECK', 'Review services interfaces and maintenance access', (3, 7), 'Services technical review record', 'Lead MEP Engineer'),
        _s('ISSUE', 'Resolve review comments and issue services documents', (2, 4), 'Issued building services package', 'Lead MEP Engineer', 10),
    )),
    'electrical_design': _Workflow('Electrical engineering development', 'Electrical', 'engineering', (
        _s('INPUTS', 'Collect electrical loads and supply interface data', (2, 5), 'Electrical design inputs'),
        _s('BASIS', 'Review electrical design criteria and existing documents', (3, 7), 'Checked electrical design basis'),
        _s('DEVELOP', 'Develop electrical calculations and design documents', (5, 12), 'Electrical calculations and draft documents', weight=30),
        _s('COORDINATE', 'Coordinate electrical equipment and discipline interfaces', (3, 7), 'Electrical interface review record', weight=20),
        _s('CHECK', 'Complete independent electrical technical review', (3, 7), 'Electrical checking record', 'Lead Electrical Engineer'),
        _s('ISSUE', 'Resolve comments and issue electrical design documents', (2, 4), 'Issued electrical design package', 'Lead Electrical Engineer', 10),
    )),
    'process_design': _Workflow('Process engineering development', 'Process', 'engineering', (
        _s('INPUTS', 'Collect process operating conditions and design inputs', (2, 5), 'Process design input register'),
        _s('BASIS', 'Review process design basis and existing process documents', (3, 7), 'Checked process design basis'),
        _s('DEVELOP', 'Develop process calculations and engineering documents', (7, 15), 'Draft process design documents', weight=30),
        _s('INTERFACES', 'Review operating requirements and discipline interfaces', (3, 7), 'Process interface review record', weight=20),
        _s('CHECK', 'Complete process technical and safety interface review', (3, 7), 'Process checking record', 'Lead Process Engineer'),
        _s('ISSUE', 'Resolve review comments and issue process documents', (2, 4), 'Issued process design package', 'Lead Process Engineer', 10),
    )),
    'piping_design': _Workflow('Piping engineering development', 'Piping', 'engineering', (
        _s('INPUTS', 'Collect line conditions and equipment interface data', (2, 5), 'Piping design input register'),
        _s('BASIS', 'Review piping design criteria and existing documents', (3, 7), 'Checked piping design basis'),
        _s('DEVELOP', 'Develop piping engineering documents and design details', (7, 15), 'Draft piping design documents', weight=30),
        _s('COORDINATE', 'Coordinate piping layout, supports and equipment interfaces', (3, 7), 'Piping interface review record', weight=20),
        _s('CHECK', 'Complete piping technical review', (3, 7), 'Piping checking record', 'Lead Piping Engineer'),
        _s('ISSUE', 'Resolve review comments and issue piping documents', (2, 4), 'Issued piping design package', 'Lead Piping Engineer', 10),
    )),
    'instrumentation_design': _Workflow('Instrumentation engineering development', 'Instrumentation', 'engineering', (
        _s('INPUTS', 'Collect control requirements and process interface data', (2, 5), 'Instrumentation design inputs'),
        _s('BASIS', 'Review instrumentation design criteria and existing documents', (3, 7), 'Checked instrumentation design basis'),
        _s('DEVELOP', 'Develop instrumentation and control design documents', (5, 12), 'Draft instrumentation design documents', weight=30),
        _s('COORDINATE', 'Coordinate control system and equipment interfaces', (3, 7), 'Control interface review record', weight=20),
        _s('CHECK', 'Complete instrumentation technical review', (3, 7), 'Instrumentation checking record', 'Lead Instrumentation Engineer'),
        _s('ISSUE', 'Resolve comments and issue instrumentation documents', (2, 4), 'Issued instrumentation package', 'Lead Instrumentation Engineer', 10),
    )),
    'engineering': _Workflow('Engineering deliverable development', 'Engineering', 'engineering', (
        _s('INPUTS', 'Collect engineering input data', (2, 5), 'Engineering input register'),
        _s('BASIS', 'Review existing documents and design criteria', (3, 7), 'Checked engineering design basis'),
        _s('DEVELOP', 'Develop engineering calculations and draft documents', (5, 15), 'Draft engineering deliverable', weight=30),
        _s('COORDINATE', 'Coordinate discipline interfaces', (3, 7), 'Discipline interface review record', weight=20),
        _s('CHECK', 'Complete internal technical review', (3, 7), 'Technical review record', 'Lead Discipline Engineer'),
        _s('ISSUE', 'Resolve review comments and issue engineering documents', (2, 4), 'Issued engineering deliverable', 'Lead Discipline Engineer', 10),
    )),
    'technical_review': _Workflow('Technical review and comment resolution', 'Engineering', 'engineering', (
        _s('INPUTS', 'Collect review documents and supporting design inputs', (2, 5), 'Complete technical review input set'),
        _s('CRITERIA', 'Confirm review criteria and discipline interfaces', (3, 5), 'Agreed technical review criteria'),
        _s('REVIEW', 'Perform technical and interface review', (3, 7), 'Technical review comments', weight=30),
        _s('RESOLVE', 'Coordinate responses and resolve technical review comments', (3, 7), 'Resolved technical comment register', weight=25),
        _s('CLOSE', 'Verify comment closure and issue review record', (2, 4), 'Closed technical review record', 'Lead Discipline Engineer', 15),
    )),
    'technical_document': _Workflow('Technical document preparation and issue', 'Engineering', 'engineering', (
        _s('INPUTS', 'Collect technical requirements and reference documents', (2, 5), 'Technical document input register'),
        _s('CRITERIA', 'Review document requirements and acceptance criteria', (3, 5), 'Checked technical document criteria'),
        _s('DRAFT', 'Prepare technical document and supporting records', (5, 10), 'Draft technical document', weight=30),
        _s('REVIEW', 'Complete technical and discipline interface review', (3, 7), 'Technical document review record', 'Lead Discipline Engineer', 25),
        _s('ISSUE', 'Resolve comments and issue approved technical document', (2, 4), 'Issued technical document', 'Lead Discipline Engineer', 15),
    )),
    'technical_safety': _Workflow('Technical safety assessment and action closure', 'Technical Safety', 'engineering', (
        _s('INPUTS', 'Collect design documents and safety assessment inputs', (2, 5), 'Safety assessment input register', 'Technical Safety Engineer'),
        _s('PLAN', 'Confirm assessment boundaries, method and review team', (3, 5), 'Safety assessment terms of reference', 'Technical Safety Engineer'),
        _s('ASSESS', 'Conduct technical safety assessment and record findings', (5, 10), 'Safety assessment findings', 'Technical Safety Lead', 30),
        _s('RESOLVE', 'Evaluate recommendations and assign closure actions', (3, 7), 'Safety recommendation and action register', 'Technical Safety Engineer', 25),
        _s('ISSUE', 'Review findings and issue safety assessment report', (3, 5), 'Issued technical safety assessment', 'Technical Safety Lead', 15),
    )),
    'feed': _Workflow('Front end engineering definition', 'Engineering', 'feed', (
        _s('INPUTS', 'Collect operating requirements and existing facility inputs', (2, 5), 'FEED input register'),
        _s('BASIS', 'Validate design basis and battery limits', (3, 7), 'Agreed FEED design basis'),
        _s('OPTIONS', 'Develop and evaluate engineering concepts', (7, 15), 'Engineering concept evaluation', weight=25),
        _s('DEFINE', 'Develop selected concept and discipline design definition', (7, 15), 'FEED design definition', weight=30),
        _s('REVIEW', 'Complete multidisciplinary design and operability review', (3, 7), 'FEED review and action register', 'Engineering Manager'),
        _s('ISSUE', 'Close review actions and issue FEED package', (3, 5), 'Issued FEED package', 'Engineering Manager', 10),
    )),
    'survey': _Workflow('Survey investigation and reporting', 'Survey', 'survey', (
        _s('INPUTS', 'Collect existing survey records and site access information', (2, 5), 'Survey input register', 'Survey Engineer'),
        _s('PLAN', 'Review survey requirements and establish control plan', (3, 5), 'Approved survey execution plan', 'Survey Engineer'),
        _s('FIELD', 'Establish controls and execute field measurements', (5, 12), 'Field survey observations', 'Survey Team', 35),
        _s('PROCESS', 'Process observations and develop survey drawings', (3, 7), 'Processed survey data and drawings', 'Survey Engineer', 25),
        _s('CHECK', 'Verify survey accuracy and resolve discrepancies', (3, 5), 'Survey quality verification', 'Lead Surveyor'),
        _s('ISSUE', 'Issue verified survey report and data', (2, 3), 'Issued survey report and data', 'Lead Surveyor', 10),
    )),
    'utilities_design': _Workflow('Utilities coordination and design', 'Utilities', 'engineering', (
        _s('INPUTS', 'Collect existing utility records and demand inputs', (2, 5), 'Utility input register'),
        _s('VERIFY', 'Review utility conflicts and authority requirements', (3, 7), 'Utility conflict and requirements register'),
        _s('DESIGN', 'Develop utility routing and design calculations', (5, 12), 'Utility design documents', weight=30),
        _s('COORDINATE', 'Coordinate utility crossings and connection interfaces', (3, 7), 'Coordinated utility interfaces', weight=20),
        _s('CHECK', 'Complete utility technical and authority interface review', (3, 7), 'Utility technical review record', 'Lead Utilities Engineer'),
        _s('ISSUE', 'Resolve review comments and issue utility design package', (2, 4), 'Issued utility design package', 'Lead Utilities Engineer', 10),
    )),
    'infrastructure_design': _Workflow('Infrastructure engineering development', 'Civil', 'engineering', (
        _s('INPUTS', 'Collect survey, site and existing asset inputs', (2, 5), 'Infrastructure design input register'),
        _s('BASIS', 'Review design criteria and site constraints', (3, 7), 'Checked infrastructure design basis'),
        _s('DESIGN', 'Develop civil engineering calculations and design drawings', (7, 15), 'Civil design calculations and drawings', weight=30),
        _s('INTERFACES', 'Coordinate utility and existing asset interfaces', (3, 7), 'Infrastructure interface review record', weight=20),
        _s('CHECK', 'Complete independent civil technical review', (3, 7), 'Civil design review record', 'Lead Civil Engineer'),
        _s('ISSUE', 'Resolve comments and issue infrastructure design documents', (2, 4), 'Issued infrastructure design package', 'Lead Civil Engineer', 10),
    )),
    'procurement': _Workflow('Technical procurement and purchase award', 'Procurement', 'procurement', (
        _s('REQUISITION', 'Prepare technical requisition and purchase requirements', (3, 7), 'Technical purchase requisition', 'Package Engineer', 20),
        _s('ENQUIRY', 'Issue enquiry and obtain compliant vendor quotations', (5, 10), 'Vendor quotation register', 'Buyer', 15),
        _s('EVALUATE', 'Evaluate technical and commercial quotations', (5, 10), 'Technical and commercial bid evaluations', 'Procurement Engineer', 25),
        _s('CLARIFY', 'Resolve vendor clarifications and finalize purchase recommendation', (3, 7), 'Resolved clarification register and recommendation', 'Procurement Engineer', 20),
        _s('AWARD', 'Obtain purchase approval and place order', (3, 5), 'Approved purchase order', 'Procurement Manager', 20),
    )),
    'procurement_evaluation': _Workflow('Procurement bid evaluation and recommendation', 'Procurement', 'procurement', (
        _s('INPUTS', 'Collect vendor bids and approved enquiry requirements', (2, 5), 'Complete bid evaluation input set', 'Procurement Engineer'),
        _s('CRITERIA', 'Confirm evaluation criteria and bid compliance', (3, 5), 'Bid compliance and evaluation criteria', 'Procurement Engineer'),
        _s('EVALUATE', 'Evaluate technical and commercial bid submissions', (5, 10), 'Draft bid evaluation', 'Procurement Engineer', 30),
        _s('CLARIFY', 'Resolve bid clarifications and document evaluation findings', (3, 7), 'Resolved bid clarification register', 'Procurement Engineer', 25),
        _s('RECOMMEND', 'Review evaluation and issue purchase recommendation', (3, 5), 'Approved bid evaluation and recommendation', 'Procurement Manager', 15),
    )),
    'vendor_documents': _Workflow('Vendor documentation review and acceptance', 'Engineering', 'procurement', (
        _s('REQUIREMENTS', 'Confirm vendor document requirements and review interfaces', (2, 5), 'Vendor document requirements register'),
        _s('RECEIVE', 'Receive and check completeness of vendor documentation', (2, 3), 'Complete vendor document submission'),
        _s('REVIEW', 'Perform technical review of vendor documents', (3, 5), 'Vendor technical review comments', weight=30),
        _s('RESOLVE', 'Coordinate and resolve vendor document comments', (3, 5), 'Resolved vendor comment register', weight=25),
        _s('ACCEPT', 'Verify revisions and release accepted vendor documents', (3, 5), 'Accepted vendor documents', 'Lead Discipline Engineer', 15),
    )),
    'construction': _Workflow('Construction work package execution', 'Construction', 'construction', (
        _s('READINESS', 'Review issued drawings and construction workfront readiness', (3, 7), 'Verified workfront readiness record', 'Construction Engineer'),
        _s('METHOD', 'Prepare method statement and inspection plan', (3, 7), 'Approved method statement and inspection plan', 'Construction Engineer'),
        _s('RESOURCES', 'Confirm materials, access and execution resources', (2, 5), 'Material and resource readiness record', 'Construction Supervisor'),
        _s('EXECUTE', 'Execute construction work to approved drawings', (10, 30), 'Completed construction work package', 'Construction Supervisor', 40),
        _s('INSPECT', 'Inspect completed work and close construction defects', (3, 7), 'Accepted inspection and defect records', 'Quality Inspector', 20),
        _s('HANDOVER', 'Compile as-built records and release completed work', (2, 5), 'Construction completion dossier', 'Construction Engineer', 10),
    )),
    'precommissioning': _Workflow('System pre-commissioning and readiness', 'Commissioning', 'precommissioning', (
        _s('BOUNDARY', 'Confirm system boundaries and mechanical completion records', (3, 5), 'Verified system completion boundary', 'Commissioning Engineer'),
        _s('PROCEDURE', 'Prepare pre-commissioning procedures and acceptance criteria', (3, 7), 'Approved pre-commissioning procedures', 'Commissioning Engineer'),
        _s('READINESS', 'Verify system isolation and pre-commissioning readiness', (3, 5), 'Pre-commissioning readiness record', 'Commissioning Supervisor'),
        _s('EXECUTE', 'Execute pre-commissioning checks and system tests', (5, 15), 'Completed pre-commissioning test records', 'Commissioning Supervisor', 40),
        _s('RECTIFY', 'Resolve test deficiencies and verify acceptance', (3, 7), 'Closed pre-commissioning punch items', 'Commissioning Engineer', 20),
        _s('RELEASE', 'Compile test dossier and release system for commissioning', (2, 5), 'Ready for commissioning certificate', 'Commissioning Manager', 10),
    )),
    'commissioning': _Workflow('Commissioning and operational acceptance', 'Commissioning', 'commissioning', (
        _s('READINESS', 'Review system completion and commissioning readiness', (3, 5), 'Verified commissioning readiness', 'Commissioning Engineer'),
        _s('PROCEDURE', 'Develop commissioning procedures and acceptance criteria', (3, 7), 'Approved commissioning procedures', 'Commissioning Engineer'),
        _s('PLAN', 'Confirm operating interfaces and test resources', (2, 5), 'Commissioning execution readiness record', 'Commissioning Supervisor'),
        _s('EXECUTE', 'Execute functional and performance commissioning tests', (5, 15), 'Commissioning test records', 'Commissioning Supervisor', 40),
        _s('RECTIFY', 'Resolve commissioning deficiencies and verify performance', (3, 7), 'Verified commissioning acceptance results', 'Commissioning Engineer', 20),
        _s('HANDOVER', 'Issue commissioning dossier and operational handover records', (2, 5), 'Operational handover dossier', 'Commissioning Manager', 10),
    )),
    'testing': _Workflow('Verification testing and acceptance', 'Quality', 'testing', (
        _s('INPUTS', 'Collect design acceptance criteria and inspection records', (2, 5), 'Test input and acceptance criteria register', 'Test Engineer'),
        _s('PROCEDURE', 'Prepare test procedures and acceptance check sheets', (3, 7), 'Approved test procedure', 'Test Engineer'),
        _s('READINESS', 'Verify test equipment, calibration and workfront readiness', (2, 5), 'Test readiness record', 'Quality Inspector'),
        _s('EXECUTE', 'Execute specified verification and acceptance tests', (5, 15), 'Completed acceptance test records', 'Test Engineer', 40),
        _s('RESOLVE', 'Resolve test nonconformances and verify corrective actions', (3, 7), 'Closed test nonconformance records', 'Quality Engineer', 20),
        _s('ACCEPT', 'Issue accepted test dossier and release records', (2, 5), 'Accepted test dossier', 'Quality Manager', 10),
    )),
    'inspection': _Workflow('Inspection and conformity verification', 'Quality', 'testing', (
        _s('INPUTS', 'Collect approved drawings and inspection acceptance criteria', (2, 5), 'Inspection input and criteria register', 'Quality Engineer'),
        _s('PLAN', 'Confirm inspection hold points and verification method', (3, 5), 'Approved inspection plan', 'Quality Engineer'),
        _s('INSPECT', 'Inspect completed work and record conformity findings', (3, 7), 'Inspection and conformity records', 'Quality Inspector', 30),
        _s('RESOLVE', 'Verify corrective actions and close inspection nonconformances', (3, 7), 'Closed inspection nonconformance records', 'Quality Inspector', 25),
        _s('RELEASE', 'Review inspection evidence and issue acceptance records', (2, 4), 'Accepted inspection dossier', 'Quality Manager', 15),
    )),
    'schedule_controls': _Workflow('Schedule development and baseline control', 'Project Controls', 'project_controls', (
        _s('INPUTS', 'Collect scope, execution strategy and schedule inputs', (2, 5), 'Schedule input and assumptions register', 'Planning Engineer'),
        _s('DECOMPOSE', 'Validate deliverable breakdown and work package coverage', (3, 7), 'Validated WBS and activity register', 'Planning Engineer'),
        _s('DEVELOP', 'Develop activity durations and execution relationships', (5, 10), 'Linked schedule and duration basis', 'Planning Engineer', 30),
        _s('ANALYZE', 'Calculate critical path and review resources and schedule risk', (3, 7), 'Schedule analysis and risk review', 'Senior Planning Engineer', 25),
        _s('BASELINE', 'Resolve review comments and submit schedule for approval', (3, 5), 'Reviewed baseline schedule and basis', 'Project Controls Manager', 15),
    )),
    'progress_controls': _Workflow('Progress measurement and reporting', 'Project Controls', 'project_controls', (
        _s('BASIS', 'Confirm reporting period and progress measurement rules', (2, 3), 'Agreed reporting and measurement basis', 'Planning Engineer'),
        _s('COLLECT', 'Collect verified quantities and deliverable status evidence', (2, 5), 'Verified progress evidence register', 'Planning Engineer'),
        _s('VALIDATE', 'Validate actual dates, remaining work and earned progress', (3, 5), 'Validated progress update', 'Planning Engineer', 25),
        _s('ANALYZE', 'Analyze schedule and cost variances and forecast completion', (3, 7), 'Variance analysis and completion forecast', 'Project Controls Engineer', 30),
        _s('ISSUE', 'Review corrective actions and issue the progress report', (2, 4), 'Approved progress report and action register', 'Project Controls Manager', 15),
    )),
    'risk_controls': _Workflow('Project risk assessment and response planning', 'Project Controls', 'project_controls', (
        _s('INPUTS', 'Collect project risk inputs and execution assumptions', (2, 5), 'Risk assessment input register', 'Risk Engineer'),
        _s('IDENTIFY', 'Identify risks and confirm causes and potential impacts', (3, 5), 'Project risk register', 'Risk Engineer', 20),
        _s('ASSESS', 'Assess risk probability and schedule and cost consequences', (3, 7), 'Assessed project risk register', 'Risk Engineer', 30),
        _s('RESPOND', 'Develop mitigation actions and assign risk owners', (3, 5), 'Risk response and ownership register', 'Risk Engineer', 20),
        _s('ISSUE', 'Review residual risk and issue risk assessment', (2, 4), 'Reviewed risk assessment and response plan', 'Project Controls Manager', 15),
    )),
    'change_controls': _Workflow('Project change evaluation and control', 'Project Controls', 'project_controls', (
        _s('REGISTER', 'Collect change request and supporting scope evidence', (2, 5), 'Registered change request', 'Change Control Engineer'),
        _s('SCOPE', 'Validate change scope and affected deliverables', (3, 5), 'Validated change scope assessment', 'Change Control Engineer', 20),
        _s('ASSESS', 'Assess schedule, cost and resource impacts of the change', (5, 10), 'Change impact assessment', 'Project Controls Engineer', 30),
        _s('REVIEW', 'Review change options and submit recommendation for decision', (3, 7), 'Reviewed change recommendation', 'Project Controls Manager', 20),
        _s('RECORD', 'Record change decision and authorized control updates', (2, 4), 'Change decision and control update record', 'Change Control Engineer', 15),
    )),
    'estimate_controls': _Workflow('Cost estimate development and review', 'Project Controls', 'project_controls', (
        _s('INPUTS', 'Collect scope quantities, rates and estimating inputs', (2, 5), 'Cost estimate input register', 'Cost Engineer'),
        _s('BASIS', 'Validate estimate scope, exclusions and estimating basis', (3, 5), 'Agreed basis of estimate', 'Cost Engineer'),
        _s('DEVELOP', 'Develop cost build-ups and estimate calculations', (5, 10), 'Draft cost estimate', 'Cost Engineer', 30),
        _s('REVIEW', 'Review cost allowances, risks and estimate completeness', (3, 7), 'Cost estimate review record', 'Lead Cost Engineer', 25),
        _s('ISSUE', 'Resolve estimate comments and issue cost estimate', (2, 4), 'Issued cost estimate and basis', 'Project Controls Manager', 15),
    )),
}


def _has(value, expression):
    return bool(re.search(r'\b(?:' + expression + r')\b', value))


def _validate_title(title):
    if not isinstance(title, str):
        raise ValueError('A source deliverable title is required to select a workflow.')
    title = re.sub(r'\s+', ' ', title).strip()
    value = _normalized(title)
    if not value or not re.search(r'[a-z]', value):
        raise ValueError('A descriptive source deliverable title is required.')
    if (re.fullmatch(r'(?:task|activity|deliverable|wbs|work package|package|phase|item|project|stage|node)'
                     r'(?: [a-z]| [0-9]+(?: [0-9]+)*)?', value)
            or value in {'general', 'miscellaneous', 'other', 'unknown', 'tbd', 'tbc', 'scope', 'overview', 'untitled'}
            or _has(value, r'demo|placeholder|lorem ipsum|sample task|sample activity|example task|test task')):
        raise ValueError(f'Provide a real project deliverable instead of the placeholder "{title}".')
    return title, value


def _infer_discipline(value):
    for pattern, discipline in (
        (r'foundation|foundations|geotechnical|civil structural', 'Civil / Structural'),
        (r'architectural|architecture|facade|façade|floor plan|floor plans|door schedule|window schedule', 'Architectural'),
        (r'structural|steelwork|reinforcement|concrete frame', 'Structural'),
        (r'mep|plumbing|building services|hvac|fire protection', 'MEP'),
        (r'electrical|power distribution|single line|sld|slds|cable|cables|earthing|lighting|load list|load schedule', 'Electrical'),
        (r'piping|pipeline|pipelines|isometric|isometrics|pipe stress|valve schedule', 'Piping'),
        (r'instrumentation|instrument|instruments|control system|control systems|loop diagram|loop diagrams', 'Instrumentation'),
        (r'process|p id|p ids|pfd|pfds|heat balance|mass balance', 'Process'),
        (r'mechanical|pump|pumps|compressor|compressors|equipment|vessel|vessels', 'Mechanical'),
        (r'telecom|telecommunications|fiber optic', 'Telecommunications'),
        (r'survey|surveying|topographic|topographical', 'Survey'),
        (r'utility|utilities|sewer|water supply|stormwater|drainage', 'Utilities'),
        (r'civil|road|roads|highway|bridge|bridges|earthworks|pavement', 'Civil'),
        (r'hazop|hazid|hazard|hazards|technical safety|safety study|safety studies|fire and gas|fire gas|hse', 'Technical Safety'),
        (r'feed|design|engineering|drawing|drawings|specification|specifications|calculation|calculations|datasheet|datasheets|data sheet|data sheets|mto|bill of materials', 'Engineering'),
    ):
        if _has(value, pattern):
            return discipline
    return ''


def _select_code(value, discipline, project_type):
    # Execution intent wins over the names of the engineered components.
    if _has(value, r'vendor (?:document|documents|documentation|drawing|drawings)'):
        return 'vendor_documents'
    if _has(value, r'(?:bid|tender|quotation) evaluation'):
        return 'procurement_evaluation'
    if _has(value, r'change control|change request|change management|variation|variations'):
        return 'change_controls'
    if _has(value, r'risk register|risk assessment|risk management|risk analysis'):
        return 'risk_controls'
    if _has(value, r'cost estimate|cost estimating|cost estimation|capital estimate|capex estimate|opex estimate'):
        return 'estimate_controls'
    if _has(value, r'progress|reporting|progress report|cost report|earned value|performance report'):
        return 'progress_controls'
    design_schedule = _has(value, r'(?:cable|load|lighting|equipment|valve|door|window|instrument|panel|distribution board|foundation) schedules?')
    if (not design_schedule and _has(value, r'schedule|scheduling|baseline|planning|project controls|execution plan')) or discipline == 'Project Controls':
        return 'schedule_controls'
    # A construction drawing or commissioning procedure is a document, not
    # authorization to construct or commission the referenced physical system.
    document_scope = _has(value, r'design|drawing|drawings|procedure|procedures|philosophy|specification|specifications|method statement|method statements|requisition|requisitions')
    if document_scope and _has(value, r'construction|installation|erection|fabrication|test|testing|inspection|commissioning|energization|energisation|procurement|purchase|requisition|requisitions'):
        return 'technical_document'
    if _has(value, r'(?:design|technical|document|engineering) review'):
        return 'technical_review'
    if _has(value, r'pre commissioning|precommissioning|pre commission'):
        return 'precommissioning'
    if _has(value, r'commissioning|commission|startup|start up|energize|energise|energization|energisation') or discipline == 'Commissioning':
        return 'commissioning'
    if _has(value, r'test|tests|testing|acceptance test|acceptance tests|hydrotest|hydrostatic|fat|sat'):
        return 'testing'
    if _has(value, r'inspect|inspection|inspections'):
        return 'inspection'
    if _has(value, r'procure|procurement|purchase|purchasing|bid evaluation|tender evaluation|requisition|rfq') or discipline == 'Procurement':
        return 'procurement'
    if _has(value, r'construction|construct|installation|install|erection|erect|fabrication|fabricate|pouring|excavation|backfilling') or discipline == 'Construction':
        return 'construction'
    if _has(value, r'feed|front end engineering'):
        return 'feed'
    if discipline == 'Survey':
        return 'survey'
    if _has(value, r'foundation|foundations'):
        return 'foundation_design'
    if discipline == 'Architectural':
        return 'architectural_design'
    if discipline == 'Structural':
        return 'structural_design'
    if discipline == 'MEP' or (discipline == 'Mechanical' and project_type == 'building'):
        return 'mep_design'
    if discipline == 'Electrical':
        return 'electrical_design'
    if discipline == 'Process':
        return 'process_design'
    if discipline == 'Piping':
        return 'piping_design'
    if discipline == 'Instrumentation':
        return 'instrumentation_design'
    if discipline == 'Technical Safety':
        return 'technical_safety'
    if discipline == 'Utilities':
        return 'utilities_design'
    if discipline in {'Civil', 'Civil / Structural'}:
        return 'infrastructure_design' if project_type == 'infrastructure' or discipline == 'Civil' else 'structural_design'
    if discipline:
        return 'engineering'
    raise ValueError('The deliverable has no recognized execution workflow. Specify its engineering discipline or a concrete work package title.')


def select_workflow(title, discipline='', project_type='', complexity='standard'):
    """Return a fresh workflow with ordered, scope-specific executable tasks.

    ``simple``, ``standard`` and ``complex`` select the lower bound, rounded
    midpoint and upper bound of each stage's allowance. Industry controls the
    workflow context without adding unsolicited architectural, process or other
    scope. A supplied discipline is retained as ownership for technical work;
    procurement, construction and controls keep their execution-team ownership.
    """
    title, value = _validate_title(title)
    project_type = normalize_project_type(project_type)
    complexity = _normalized(complexity)
    if complexity not in {'simple', 'standard', 'complex'}:
        raise ValueError('Complexity must be simple, standard or complex.')
    supplied_discipline = _DISCIPLINES.get(_normalized(discipline), '')
    inferred_discipline = _infer_discipline(value)
    resolved_discipline = supplied_discipline or inferred_discipline
    code = _select_code(value, resolved_discipline, project_type)
    workflow = _WORKFLOWS[code]
    discipline = workflow.discipline
    if workflow.discipline == 'Engineering' or workflow.phase in {'engineering', 'feed'}:
        discipline = resolved_discipline or workflow.discipline
    if workflow.phase == 'engineering' and project_type == 'oil_gas':
        phase = 'detailed_design'
    else:
        phase = workflow.phase
    weight_total = sum(stage.weight for stage in workflow.stages)
    stages = []
    for index, stage in enumerate(workflow.stages):
        days = {'simple': stage.low, 'standard': (stage.low + stage.high + 1) // 2, 'complex': stage.high}[complexity]
        weight = round(stage.weight * 100 / weight_total, 4)
        if index == len(workflow.stages) - 1:
            weight = round(100 - sum(item['progress_weight'] for item in stages), 4)
        role = stage.role.replace('Discipline', discipline)
        stages.append({
            'code': stage.code, 'sequence': index + 1,
            'name': f'{stage.action}: {title}', 'duration_days': days,
            'deliverable': f'{stage.output}: {title}', 'discipline': discipline,
            'responsible_role': role, 'progress_weight': weight,
            'progress_measurement': 'weighted_deliverable_acceptance',
            'acceptance_criteria': f'{stage.output} completed, checked and recorded for {title}.',
            'duration_basis': {
                'source': 'planning_allowance', 'library_version': LIBRARY_VERSION,
                'range_days': [stage.low, stage.high], 'unit': 'working_days',
                'complexity': complexity,
                'selection': {'simple': 'lower_bound', 'standard': 'rounded_midpoint', 'complex': 'upper_bound'}[complexity],
                'basis': f'{workflow.name}: {stage.action.lower()}. Allowance assumes one defined work package and normal resource availability.',
                'requires_planner_review': True,
            },
        })
    return {
        'code': code, 'name': workflow.name, 'discipline': discipline, 'phase': phase,
        'project_type': project_type, 'scope_title': title, 'complexity': complexity,
        'library_version': LIBRARY_VERSION, 'stages': stages,
    }
