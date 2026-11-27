"""Merge migration: joins the local recruitment-opening branch (0024_recruitment_opening_requisition)
with the upstream payroll-attributes branch (0024_employeemaster_payroll_basic_and_more -> 0025).

Schema-neutral: no operations. Both branches only depend on 0023 and touch different tables/fields.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hr_core', '0024_recruitment_opening_requisition'),
        ('hr_core', '0025_copy_payroll_employee_attributes'),
    ]

    operations = []
