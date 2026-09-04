from django.db import migrations
from django.db.models import Q


def reconcile_employee_identity(apps, schema_editor):
    """Repair links and shared identity fields without recreating employees."""
    EmployeeMaster = apps.get_model('hr_core', 'EmployeeMaster')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    User = apps.get_model('users', 'User')

    # Backfill the canonical FK for historical RBAC profiles. Prefer the
    # one-to-one login-account link; use a business identifier only when it
    # resolves to exactly one employee and is not owned by another profile.
    for profile in UserProfile.objects.filter(
        canonical_employee_id__isnull=True,
        is_deleted=False,
    ).iterator():
        matches = EmployeeMaster.objects.filter(user_id=profile.user_id)
        if not matches.exists() and profile.employee_id:
            matches = EmployeeMaster.objects.filter(
                Q(employee_number__iexact=profile.employee_id)
                | Q(employee_code__iexact=profile.employee_id)
                | Q(emp_code__iexact=profile.employee_id)
            )
        match_ids = list(matches.values_list('pk', flat=True)[:2])
        if (
            len(match_ids) == 1
            and not UserProfile.objects.filter(canonical_employee_id=match_ids[0]).exists()
        ):
            UserProfile.objects.filter(pk=profile.pk).update(
                canonical_employee_id=match_ids[0]
            )

    for profile in UserProfile.objects.filter(
        canonical_employee_id__isnull=False,
        is_deleted=False,
    ).iterator():
        employee = EmployeeMaster.objects.filter(pk=profile.canonical_employee_id).first()
        if employee is None:
            continue

        employee_updates = {}
        employee_number = str(profile.employee_id or '').strip()
        if employee_number and not EmployeeMaster.objects.filter(
            employee_number__iexact=employee_number,
        ).exclude(pk=employee.pk).exists():
            employee_updates.update({
                'employee_number': employee_number,
                'employee_code': employee_number,
                'emp_code': employee_number[:20],
            })
        if employee_updates:
            EmployeeMaster.objects.filter(pk=employee.pk).update(**employee_updates)
            for field, value in employee_updates.items():
                setattr(employee, field, value)

        profile_updates = {
            'employee_id': employee.employee_number,
            'department': employee.department or '',
            'job_title': (
                employee.designation
                or employee.job_title_uae
                or employee.job_title_finland
                or ''
            ),
        }
        UserProfile.objects.filter(pk=profile.pk).update(**profile_updates)
        if employee.user_id:
            User.objects.filter(pk=employee.user_id).update(
                email=employee.email,
                first_name=employee.first_name or '',
                last_name=employee.last_name or '',
            )

    # Link historical payroll rows using the account first, then an
    # unambiguous employee number/code. No records are created or deleted.
    for row in PayrollEmployee.objects.filter(employee_id__isnull=True).iterator():
        matches = EmployeeMaster.objects.none()
        if row.user_id:
            matches = EmployeeMaster.objects.filter(user_id=row.user_id)
        if not matches.exists() and row.employee_no:
            matches = EmployeeMaster.objects.filter(
                Q(employee_number__iexact=row.employee_no)
                | Q(employee_code__iexact=row.employee_no)
                | Q(emp_code__iexact=row.employee_no)
            )
        match_ids = list(matches.values_list('pk', flat=True)[:2])
        if len(match_ids) == 1:
            PayrollEmployee.objects.filter(pk=row.pk).update(employee_id=match_ids[0])

    for row in PayrollEmployee.objects.filter(employee_id__isnull=False).iterator():
        employee = EmployeeMaster.objects.filter(pk=row.employee_id).first()
        if employee is None:
            continue
        updates = {
            'user_id': employee.user_id,
            'full_name': f'{employee.first_name} {employee.last_name}'.strip(),
            'department': employee.department or '',
            'designation': employee.designation or '',
            'joining_date': employee.join_date,
        }
        if employee.employee_number and not PayrollEmployee.objects.filter(
            employee_no__iexact=employee.employee_number,
        ).exclude(pk=row.pk).exists():
            updates['employee_no'] = employee.employee_number
        PayrollEmployee.objects.filter(pk=row.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [
        ('hr_core', '0010_link_duplicate_onboarding_history'),
        ('rbac', '0049_userprofile_employee'),
        ('payroll_engine', '0018_alter_payrollemployee_employee'),
    ]

    operations = [
        migrations.RunPython(reconcile_employee_identity, migrations.RunPython.noop),
    ]
