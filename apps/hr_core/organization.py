"""HR-ENH-01 organization options and payroll eligibility."""
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Lower, Trim

BUSINESS_UNITS = ('Rejlers Abu Dhabi', 'Rejlers India', 'PMC', 'EDC')
BUSINESS_AREAS = (
    'Management', 'Engineering', 'Project Delivery', 'Procurement',
    'HR & Administration', 'Finance & IT', 'Business Development & Sales', 'AI', 'QHSE',
)


def edc_employee_codes():
    from .models import EmployeeMaster
    employees = EmployeeMaster.objects.alias(_hr_unit=Lower(Trim('business_unit'))).filter(_hr_unit='edc')
    return {code.strip().lower() for row in employees.values_list('employee_code', 'emp_code', 'employee_number')
            for code in row if code}


def payroll_eligible(queryset):
    """Exclude EDC even when a legacy payroll row has not been canonically linked."""
    return queryset.alias(_hr_unit=Coalesce(Lower(Trim('employee__business_unit')), Value('')), _hr_code=Lower(Trim('employee_no'))).exclude(
        Q(_hr_unit='edc') | Q(_hr_code__in=edc_employee_codes())
    )


def require_payroll_eligible_run(run):
    """Do not process an older draft containing an employee later assigned to EDC."""
    from rest_framework.exceptions import ValidationError
    if run.payslips.alias(_hr_unit=Lower(Trim('employee__employee__business_unit')),
                         _hr_code=Lower(Trim('employee__employee_no'))).filter(
        Q(_hr_unit='edc') | Q(_hr_code__in=edc_employee_codes())
    ).exists():
        raise ValidationError({'payroll': 'This run contains EDC profile-only employees. Regenerate the draft without them before payroll processing.'})
