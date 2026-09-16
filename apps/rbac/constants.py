"""
RBAC Constants
Soft-coded choices and constants for the RBAC module
"""

from .organization_catalog import get_department_choices


# Keep the tuple contract for existing callers while the organization catalog
# supplies the choices shared by Profile, Onboarding, and User Management.
DEPARTMENTS = [(item['value'], item['label']) for item in get_department_choices()]

# Helper function to get department label from value
def get_department_label(value):
    """Get department display label from value"""
    for dept_value, dept_label in DEPARTMENTS:
        if dept_value == value:
            return dept_label
    return value  # Return original value if not found
