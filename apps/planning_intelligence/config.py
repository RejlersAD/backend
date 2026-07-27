"""
RADAI Project Planning Application — Soft-coded Planning Knowledge Base.

Every discipline, HSE study, workflow template, review-cycle duration and
milestone name lives here. Add / adjust planning standards by editing this
file only — never hardcode planning logic inline in services or views.

This is the deterministic "Planning Knowledge Base" (MODULE 3 of the spec).
The generation services (services/*.py) read from this file and never
duplicate these lists.
"""
import re

from decouple import config

# ─────────────────────────────────────────────────────────────────────────────
# File categories accepted by the Upload Manager (MODULE 1)
# ─────────────────────────────────────────────────────────────────────────────
FILE_CATEGORIES = [
    ('sow', 'Scope of Work'),
    ('wbs', 'WBS Structure'),
    ('mdr', 'Master Deliverable Register'),
    ('eddr', 'Engineering Document Deliverable Register'),
    ('schedule_requirements', 'Schedule Requirements'),
    ('project_control_procedure', 'Project Control Procedure'),
    ('reference_schedule', 'Reference Schedule'),
    ('output_schedule_sample', 'Output Schedule Sample'),
    ('timeline', 'Timeline / Milestone File'),
    ('other', 'Other Attachment'),
]

PARSE_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('processing', 'Processing'),
    ('done', 'Parsed'),
    ('failed', 'Failed'),
]

# Maximum upload size (bytes) — defaults to 100 MB, overridable via env.
MAX_FILE_BYTES = int(config('PLANNING_MAX_FILE_BYTES', default=str(100 * 1024 * 1024)))

# NOTE: S3 storage location/prefix for uploaded planning files is now owned by
# apps.core.storage_backends.PlanningIntelligenceStorage (location=
# 'media/planning_intelligence'), set directly on PlanningFile.file's
# `storage=` argument — not duplicated here.

# ─────────────────────────────────────────────────────────────────────────────
# Default calendar / review-cycle rules (used when Schedule Requirements file
# does not specify project-specific values) — mirrors ADNOC RFT Appendix-4.
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CALENDAR = {
    'working_days_per_week': 5,       # Monday–Friday
    'hours_per_day': 8,
    'man_days_per_month': 22,
}

# Default review-cycle durations in *working days* — MODULE 7.
DEFAULT_REVIEW_CYCLE_DAYS = {
    'prepare_ifr': 5,          # Prepare & Issue for Review
    'company_review': 10,      # Company review of first issue
    'incorporate_ifa': 5,      # Incorporate comments & Issue for Approval
    'company_approval': 5,     # Company approval
    'issue_ifd': 2,            # Final Issue for Design / Tender
}

MAX_ALLOWED_LAG_DAYS = 14  # lags beyond this must become their own activity
MAX_TOTAL_FLOAT_DAYS = 21  # any activity exceeding this is flagged by the validator
LEVEL4_MAX_ACTIVITY_DURATION_DAYS = 15  # ~3 working weeks — Level-4 activity ceiling

# ─────────────────────────────────────────────────────────────────────────────
# Disciplines (MODULE 3 / MODULE 4 — Level-2 WBS) with Activity-ID prefixes
# ─────────────────────────────────────────────────────────────────────────────
DISCIPLINES = [
    {'code': 'pm',   'name': 'Project Management',            'prefix': 'PM'},
    {'code': 'pc',   'name': 'Project Controls',               'prefix': 'PC'},
    {'code': 'survey', 'name': 'Data Collection and Surveys',  'prefix': 'SS'},
    {'code': 'hse',  'name': 'HSE Studies',                    'prefix': 'HS'},
    {'code': 'process', 'name': 'Process Engineering',         'prefix': 'PR'},
    {'code': 'mechanical', 'name': 'Mechanical Engineering',   'prefix': 'ME'},
    {'code': 'piping', 'name': 'Piping Engineering',           'prefix': 'PI'},
    {'code': 'civil', 'name': 'Civil / Structural Engineering', 'prefix': 'CV'},
    {'code': 'electrical', 'name': 'Electrical Engineering',   'prefix': 'EL'},
    {'code': 'instrumentation', 'name': 'Instrumentation & Control', 'prefix': 'IN'},
    {'code': 'telecom', 'name': 'Telecom',                     'prefix': 'TL'},
    {'code': 'procurement', 'name': 'Procurement Support',     'prefix': 'PU'},
    {'code': '3d_model', 'name': '3D Model / CAD',             'prefix': 'MD'},
    {'code': 'constructability', 'name': 'Constructability and Reviews', 'prefix': 'CR'},
    {'code': 'tiein', 'name': 'Tie-in and Shutdown Planning',  'prefix': 'TI'},
    {'code': 'epc', 'name': 'EPC Tender Package',              'prefix': 'EP'},
    {'code': 'pdr', 'name': 'Project Definition Report',       'prefix': 'PD'},
    {'code': 'closeout', 'name': 'Closeout',                   'prefix': 'CL'},
]

DISCIPLINE_PREFIX_BY_CODE = {d['code']: d['prefix'] for d in DISCIPLINES}
DISCIPLINE_NAME_BY_CODE = {d['code']: d['name'] for d in DISCIPLINES}

# Engineering discipline execution order (drives default scheduling sequence).
ENGINEERING_DISCIPLINE_ORDER = [
    'process', 'piping', 'mechanical', 'civil', 'electrical', 'instrumentation', 'telecom',
]

# ─────────────────────────────────────────────────────────────────────────────
# Deliverable keyword catalogue — used by the Document Intelligence Engine to
# detect which deliverables a discipline needs, and by the Activity Generator
# to fall back to defaults when the source documents don't explicitly list them.
# ─────────────────────────────────────────────────────────────────────────────
DISCIPLINE_DEFAULT_DELIVERABLES = {
    'process': [
        'Process Design Basis', 'Block Flow Diagram', 'Heat & Material Balance',
        'Process Flow Diagram (PFD)', 'Piping & Instrumentation Diagram (P&ID) - Process',
        'Hydraulic Analysis Report', 'Equipment List', 'Line List',
        'Process Data Sheets for Equipment', 'Cause & Effect Diagram',
        'Operation and Control Philosophy',
    ],
    'piping': [
        'Piping Design Basis', 'Piping Material Specification', 'Plot Plan',
        'Piping General Arrangement Drawings', 'Tie-in List',
        'Piping Stress Analysis Report', 'MTO / Bill of Materials',
    ],
    'mechanical': [
        'Mechanical Design Basis', 'Mechanical Data Sheets for Equipment',
        'Material Requisition for Equipment', 'Technical Bid Evaluation',
    ],
    'civil': [
        'Civil & Structural Design Basis', 'Plot Plan / Civil Layout',
        'Foundation Design Calculations', 'Piperack Layout Drawings',
        'Structural Drawings',
    ],
    'electrical': [
        'Electrical Design Basis', 'Single Line Diagram', 'Electrical Load List',
        'Cable Schedule', 'Electrical Equipment Layout', 'Cable Routing Layout',
    ],
    'instrumentation': [
        'Instrument Design Basis', 'Instrument Index', 'Input/Output List',
        'Instrument Data Sheets', 'Cause & Effect Diagram',
        'Instrument Cable Routing Drawings',
    ],
    'telecom': [
        'Telecom Design Basis', 'Telecom System Philosophy', 'Telecom Equipment List',
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Default HSE studies (MODULE 3)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_HSE_STUDIES = [
    'HAZID', 'HAZOP', 'ENVID', 'OHID', 'SIL', 'QRA', 'RAM', 'SIMOPS',
    'Inherently Safer Design (ISD)', 'Fire & Gas Mapping Study',
    'Constructability Review', 'Value Engineering Review',
]

# ─────────────────────────────────────────────────────────────────────────────
# Workflow templates — a deliverable is never one activity, it is a workflow.
# Each step: (suffix, is_review_activity, review_cycle_key or None duration_days)
# ─────────────────────────────────────────────────────────────────────────────
DELIVERABLE_WORKFLOW_STEPS = [
    {'suffix': 'Prepare / Develop {name}',                    'cycle_key': 'prepare_ifr'},
    {'suffix': 'Issue {name} for Company Review',             'cycle_key': None, 'duration': 1},
    {'suffix': 'Company Review of {name}',                    'cycle_key': 'company_review'},
    {'suffix': 'Incorporate Comments & Issue {name} for Approval', 'cycle_key': 'incorporate_ifa'},
    {'suffix': 'Company Approval of {name}',                  'cycle_key': 'company_approval'},
    {'suffix': 'Issue {name} for Design / Tender',            'cycle_key': 'issue_ifd'},
]

HSE_STUDY_WORKFLOW_STEPS = [
    {'suffix': 'Prepare Scope of Work / TOR for {name}',          'cycle_key': 'prepare_ifr'},
    {'suffix': 'Issue {name} TOR for Company Review',             'cycle_key': None, 'duration': 1},
    {'suffix': 'Company Review of {name} TOR',                    'cycle_key': 'company_review'},
    {'suffix': 'Issue {name} TOR for Approval',                    'cycle_key': 'incorporate_ifa'},
    {'suffix': 'Company Approval of {name} TOR',                  'cycle_key': 'company_approval'},
    {'suffix': 'Conduct {name} Workshop',                          'cycle_key': None, 'duration': 1, 'milestone': True},
    {'suffix': 'Prepare {name} Report',                            'cycle_key': None, 'duration': 5},
    {'suffix': 'Issue {name} Report for Company Review',           'cycle_key': None, 'duration': 1},
    {'suffix': 'Company Comments on {name} Report',                'cycle_key': 'company_review'},
    {'suffix': 'Issue {name} Report for Approval',                 'cycle_key': 'incorporate_ifa'},
    {'suffix': 'Company Approval of {name} Report',                'cycle_key': 'company_approval'},
    {'suffix': 'Issue Final {name} Report',                        'cycle_key': 'issue_ifd'},
    {'suffix': 'Close Out {name} Action Items',                    'cycle_key': None, 'duration': 5},
]

SURVEY_WORKFLOW_STEPS = [
    {'suffix': 'Prepare Site Survey Plan',                     'cycle_key': None, 'duration': 3},
    {'suffix': 'Issue Site Survey Plan for Company Review',    'cycle_key': None, 'duration': 1},
    {'suffix': 'Company Approval of Site Survey Plan',         'cycle_key': 'company_approval'},
    {'suffix': 'Conduct Site Visit / Survey',                  'cycle_key': None, 'duration': 5},
    {'suffix': 'Prepare Site Survey Report',                   'cycle_key': None, 'duration': 5},
    {'suffix': 'Issue Site Survey Report for Company Review',  'cycle_key': None, 'duration': 1},
    {'suffix': 'Company Comments on Site Survey Report',       'cycle_key': 'company_review'},
    {'suffix': 'Issue Site Survey Report for Approval',        'cycle_key': 'incorporate_ifa'},
    {'suffix': 'Company Approval of Site Survey Report',       'cycle_key': 'company_approval'},
    {'suffix': 'Issue Site Survey Report for Design',          'cycle_key': 'issue_ifd'},
]

# ─────────────────────────────────────────────────────────────────────────────
# Milestone workflow (MODULE 3) — chronological order, linked FS by default.
# ─────────────────────────────────────────────────────────────────────────────
MILESTONE_TEMPLATE = [
    'Contract Award - Effective Date',
    'Kick-Off Meeting',
    'Mobilization',
    'Data Collection Complete',
    'Site Survey Complete',
    'Basis of Design Complete',
    'Major Engineering Design Review',
    'HSE Workshops Complete',
    '30% Model Review',
    '60% Model Review',
    '90% Model Review',
    'Project Definition Report (PDR) Issue',
    'EPC Tender Package Issue',
    'Final FEED / DEFINE Dossier Issue',
    'Project Closeout',
]

# ─────────────────────────────────────────────────────────────────────────────
# Resource roles (MODULE 10)
# ─────────────────────────────────────────────────────────────────────────────
RESOURCE_ROLES = [
    'Project Manager', 'Engineering Manager', 'Project Controls Manager',
    'Planning Engineer', 'Document Controller', 'Lead Process Engineer',
    'Process Engineer', 'Lead Piping Engineer', 'Piping Engineer',
    'Mechanical Engineer', 'Civil Engineer', 'Electrical Engineer',
    'Instrumentation Engineer', 'HSE Engineer', 'Procurement Engineer',
    '3D Model Coordinator', 'Cost Estimator',
]

# Discipline -> default responsible role (used when generating activities).
DISCIPLINE_RESPONSIBLE_ROLE = {
    'pm': 'Project Manager',
    'pc': 'Planning Engineer',
    'survey': 'Planning Engineer',
    'hse': 'HSE Engineer',
    'process': 'Lead Process Engineer',
    'mechanical': 'Mechanical Engineer',
    'piping': 'Lead Piping Engineer',
    'civil': 'Civil Engineer',
    'electrical': 'Electrical Engineer',
    'instrumentation': 'Instrumentation Engineer',
    'telecom': 'Instrumentation Engineer',
    'procurement': 'Procurement Engineer',
    '3d_model': '3D Model Coordinator',
    'constructability': 'Engineering Manager',
    'tiein': 'Lead Piping Engineer',
    'epc': 'Project Controls Manager',
    'pdr': 'Project Controls Manager',
    'closeout': 'Project Manager',
}

# ─────────────────────────────────────────────────────────────────────────────
# Validation rule catalogue (MODULE 11) — each rule maps to a check function
# name implemented in services/validation_engine.py
# ─────────────────────────────────────────────────────────────────────────────
VALIDATION_SEVERITY = ('pass', 'warning', 'critical')

# ─────────────────────────────────────────────────────────────────────────────
# BYOK (Bring Your Own Key) — Claude/Anthropic augmentation, per-project.
#
# This is OPTIONAL. When a project has no key configured (the default), every
# service in this app behaves exactly as before — pure deterministic
# analysis. When a user supplies their own Anthropic API key for a project,
# document intelligence + narrative generation are AUGMENTED (never
# replaced/blocked) by a real Claude call. See services/claude_client.py.
#
# Model catalogue is soft-coded here so new Claude releases only require
# adding a row — no code changes elsewhere. Verify current model IDs against
# Anthropic's model list before changing DEFAULT_CLAUDE_MODEL in production.
# ─────────────────────────────────────────────────────────────────────────────
AI_PROVIDERS = [
    {'value': 'anthropic', 'label': 'Anthropic Claude'},
]

CLAUDE_MODEL_CHOICES = [
    {
        'value': 'claude-opus-4-1-20250805',
        'label': 'Claude Opus 4.1 (most capable — recommended for complex, multi-format documents)',
        'tier': 'opus',
        'recommended': True,
    },
    {
        'value': 'claude-opus-4-20250514',
        'label': 'Claude Opus 4',
        'tier': 'opus',
        'recommended': False,
    },
    {
        'value': 'claude-sonnet-4-20250514',
        'label': 'Claude Sonnet 4 (balanced cost/quality)',
        'tier': 'sonnet',
        'recommended': False,
    },
    {
        'value': 'claude-3-5-haiku-20241022',
        'label': 'Claude Haiku 3.5 (fastest / cheapest)',
        'tier': 'haiku',
        'recommended': False,
    },
]
DEFAULT_CLAUDE_MODEL = 'claude-opus-4-1-20250805'
CLAUDE_MODEL_VALUES = {m['value'] for m in CLAUDE_MODEL_CHOICES}

# Anthropic API keys always start with this prefix.
CLAUDE_API_KEY_PATTERN = re.compile(r'^sk-ant-[A-Za-z0-9\-_]{20,}$')

# Ops kill-switch — disables the entire BYOK/Claude augmentation path
# platform-wide without a code change (e.g. during an incident).
CLAUDE_BYOK_ENABLED = config('PLANNING_CLAUDE_BYOK_ENABLED', default=True, cast=bool)

# Cost/latency guardrails — soft-coded so they can be tuned without touching
# services/claude_client.py or services/intelligence.py.
CLAUDE_MAX_INPUT_CHARS = int(config('PLANNING_CLAUDE_MAX_INPUT_CHARS', default='60000'))
CLAUDE_INTELLIGENCE_MAX_TOKENS = int(config('PLANNING_CLAUDE_INTELLIGENCE_MAX_TOKENS', default='1500'))
CLAUDE_NARRATIVE_MAX_TOKENS = int(config('PLANNING_CLAUDE_NARRATIVE_MAX_TOKENS', default='800'))
CLAUDE_REQUEST_TIMEOUT_SECONDS = int(config('PLANNING_CLAUDE_TIMEOUT_SECONDS', default='45'))
