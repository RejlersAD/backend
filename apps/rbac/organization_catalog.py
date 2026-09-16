"""Organizational departments and positions from RAD-HM-CHT-0001 Rev. 6.

This reference catalog does not assign employees, reporting managers, access
roles, modules, or permissions. Aliases are for display/filtering only and must
not be used to rewrite employee departments or derive authorization grants.
"""

from copy import deepcopy
import re


ORG_CHART_SOURCE = {
    'document_id': 'RAD-HM-CHT-0001',
    'revision': 6,
    'date': '2025-12-12',
    'title': 'Rejlers Abu Dhabi Corporate Organization Chart',
    'filename': 'RAD Organization Chart (With Photo) Rev 6.pdf',
}


def _department(code, label, parent_code, head_role_code, functions=(), aliases=()):
    return {
        'code': code,
        'label': label,
        'parent_code': parent_code,
        'head_role_code': head_role_code,
        'functions': list(functions),
        'aliases': list(aliases),
    }


# Management and Engineering are grouping labels for the chart's CEO and
# Manager of Engineering branches. Functional bullets stay within their branch
# instead of being invented as separate departments or manager positions.
DEPARTMENTS = [
    _department('management', 'Management', None, 'ceo'),
    _department('sales', 'Sales & Business Development', 'management', 'head_sales_business_development', (
        'Business Development', 'Client Relationship', 'Sales & Proposals', 'Proposal Engineers',
    ), ('Sales', 'Business Development')),
    _department('operations', 'Operations & Project Delivery', 'management', 'head_operations_project_delivery',
                aliases=('Operations', 'Operation Management')),
    _department('hr', 'HR & Administration', 'management', 'head_hr_administration', (
        'Recruitment', 'PRO', 'Logistics', 'Office Admins',
    ), ('Human Resources', 'Human Resource', 'HR', 'Administration', 'admin')),
    _department('finance', 'Finance & ICT', 'management', 'cfo', (
        'Financial Planning, Forecasting & Control', 'Accounting', 'ICT',
    ), ('Finance', 'Information Technology', 'IT', 'ICT')),
    _department('project_management', 'Project Management', 'operations', 'manager_projects', (
        'Project Director', 'Project Management', 'PMC Contracts', 'Document Control',
        'Digitization, Design & Drafting Contracts',
    )),
    _department('engineering', 'Engineering', 'operations', 'manager_engineering', (
        'Rejlers India (RIN) Manager', 'Engineering Systems Administration',
    ), ('Engineering Management',)),
    _department('qhse', 'QHSE', 'operations', 'qhse_manager', (
        'ISO Certifications', 'IMS System', 'Project Quality', 'QHSE Team',
    )),
    _department('procurement', 'Procurement', 'operations', 'procurement_manager', (
        'Procurement Services', 'Internal Procurement', 'Project Procurement',
    )),
    _department('radai', 'Artificial Intelligence (AI)', 'operations', None, (
        'Artificial Intelligence (AI) Team', 'RIN Growth',
    ), ('RadAI', 'AI')),
    _department('project_controls', 'Project Controls', 'project_management', 'senior_manager_project_controls'),
    _department('process', 'Process Engineering', 'engineering', 'hod_process'),
    _department('civil', 'Civil & Structural', 'engineering', 'hod_civil_structural',
                aliases=('Civil & Structural Engineering', 'Civil and Structural Engineering')),
    _department('instrument', 'Instrumentation & Control', 'engineering', 'hod_instrumentation_control',
                aliases=('Instrument & Control', 'Instruments & Control')),
    _department('electrical', 'Electrical', 'engineering', 'hod_electrical',
                aliases=('Electrical Engineering',)),
    _department('piping', 'Piping / Mechanical / Pipeline', 'engineering', 'hod_piping_mechanical_pipeline',
                aliases=('Piping Engineering', 'Mechanical Engineering', 'mechanical', 'Pipeline')),
]


def _role(code, label, department_code, reports_to_role_code=None, holder_name='', *, acting=False,
          additional_titles=()):
    return {
        'code': code,
        'label': label,
        'department_code': department_code,
        'reports_to_role_code': reports_to_role_code,
        'holder_name': holder_name,
        'acting': acting,
        'additional_titles': list(additional_titles),
    }


ORGANIZATIONAL_ROLES = [
    _role('ceo', 'CEO, Rejlers Abu Dhabi', 'management', holder_name='Jarmo Suominen',
          additional_titles=('Senior VP, Middle East Region',)),
    _role('head_sales_business_development', 'Head of Sales & Business Development', 'sales', 'ceo',
          'Anam Abbas', additional_titles=('VP, Rejlers Abu Dhabi',)),
    _role('head_operations_project_delivery', 'Head of Operations & Project Delivery', 'operations', 'ceo',
          'Mohamad El-Ghawanmeh', additional_titles=('VP, Rejlers Abu Dhabi',)),
    _role('head_hr_administration', 'Head of HR & Administration (Acting)', 'hr', 'ceo',
          'Sanglin Samuel', acting=True, additional_titles=('Manager, Rejlers Abu Dhabi',)),
    _role('cfo', 'CFO, Rejlers Abu Dhabi', 'finance', 'ceo', 'Aleksi Murtomaki',
          additional_titles=('VP & Head of Finance & ICT',)),
    _role('manager_projects', 'Manager of Projects', 'project_management', 'head_operations_project_delivery',
          'Jamal Ayoub'),
    _role('manager_engineering', 'Manager of Engineering', 'engineering', 'head_operations_project_delivery',
          'Rafat Saqer'),
    _role('qhse_manager', 'QHSE Manager', 'qhse', 'head_operations_project_delivery', 'Shaju Chacko'),
    _role('procurement_manager', 'Procurement Manager', 'procurement', 'head_operations_project_delivery',
          'Richa Thomas'),
    _role('senior_manager_project_controls', 'Sr. Manager Project Controls', 'project_controls', 'manager_projects',
          'Timothy Dolan'),
    _role('engineering_manager', 'Engineering Manager', 'engineering', 'manager_engineering'),
    _role('hod_process', 'HOD Process Engineering', 'process', 'manager_engineering', 'Debasis Sana'),
    _role('hod_civil_structural', 'HOD Civil & Structural', 'civil', 'manager_engineering', 'Sherwin Mapaye'),
    _role('hod_instrumentation_control', 'HOD Instrumentation & Control', 'instrument', 'manager_engineering',
          'Sanu Jacob'),
    _role('hod_electrical', 'HOD Electrical', 'electrical', 'manager_engineering', 'Swapnil Linge'),
    _role('hod_piping_mechanical_pipeline', 'HOD Piping / Mechanical / Pipeline (Acting)', 'piping',
          'manager_engineering', 'Amit Thakur', acting=True),
    _role('project_director', 'Project Director', 'project_management', 'manager_projects'),
    _role('proposal_engineer', 'Proposal Engineer', 'sales', 'head_sales_business_development'),
    _role('rin_manager', 'Rejlers India (RIN) Manager', 'engineering', 'manager_engineering'),
    # The chart shares this group across engineering disciplines; no individual
    # line manager is specified for either generic position.
    _role('engineer', 'Engineer', 'engineering'),
    _role('designer', 'Designer', 'engineering'),
]


def _key(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value or '').casefold()).strip('_')


def resolve_department_code(value):
    """Resolve catalog/legacy labels for presentation, never for access control."""
    key = _key(value)
    for department in DEPARTMENTS:
        if key in {_key(item) for item in [department['code'], department['label'], *department['aliases']]}:
            return department['code']
    return None


def get_department_choices():
    return [{'value': item['code'], 'label': item['label']} for item in DEPARTMENTS]


def get_organizational_role_choices(department=None):
    code = resolve_department_code(department) if department else None
    if department and code is None:
        return []
    return [
        {'value': role['code'], 'label': role['label'], 'department_code': role['department_code']}
        for role in ORGANIZATIONAL_ROLES
        if code is None or role['department_code'] == code
    ]


def get_organizational_job_titles():
    """Include executive dual titles in all job-title suggestion surfaces."""
    return sorted({
        title
        for role in ORGANIZATIONAL_ROLES
        for title in [role['label'], *role['additional_titles']]
    }, key=str.casefold)


def get_organization_catalog():
    """Return fresh reference data so request consumers cannot mutate globals."""
    return deepcopy({
        'source': ORG_CHART_SOURCE,
        'departments': DEPARTMENTS,
        'organizational_roles': ORGANIZATIONAL_ROLES,
    })
