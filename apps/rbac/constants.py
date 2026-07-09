"""
RBAC Constants
Soft-coded choices and constants for the RBAC module
"""

# Soft-coded: Department choices for Oil & Gas engineering organization
# Used across Profile, Onboarding, and User Management modules
DEPARTMENTS = [
    ('process', 'Process Engineering'),
    ('piping', 'Piping Engineering'),
    ('instrument', 'Instrument & Control'),
    ('electrical', 'Electrical Engineering'),
    ('mechanical', 'Mechanical Engineering'),
    ('civil', 'Civil & Structural Engineering'),
    ('safety', 'Safety & HSE'),
    ('project_controls', 'Project Controls'),
    ('commissioning', 'Commissioning'),
    ('materials', 'Materials & Corrosion'),
    ('environmental', 'Environmental Engineering'),
    ('procurement', 'Procurement'),
    ('operations', 'Operations'),
    ('maintenance', 'Maintenance'),
    ('quality', 'Quality Assurance'),
    ('finance', 'Finance'),
    ('hr', 'Human Resources'),
    ('it', 'Information Technology'),
    ('admin', 'Administration'),
    ('management', 'Management'),
]

# Helper function to get department choices as dict for API responses
def get_department_choices():
    """Return department choices as list of dicts for API consumption"""
    return [{'value': value, 'label': label} for value, label in DEPARTMENTS]

# Helper function to get department label from value
def get_department_label(value):
    """Get department display label from value"""
    for dept_value, dept_label in DEPARTMENTS:
        if dept_value == value:
            return dept_label
    return value  # Return original value if not found
