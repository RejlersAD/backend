from django.db import migrations


def link_payroll_employees(apps, schema_editor):
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    UserProfile = apps.get_model('rbac', 'UserProfile')

    profiles_by_employee_no = {
        str(employee_no).strip(): user_id
        for employee_no, user_id in UserProfile.objects.filter(
            is_deleted=False,
        ).exclude(employee_id='').values_list('employee_id', 'user_id')
        if str(employee_no).strip()
    }

    for employee in PayrollEmployee.objects.filter(user_id__isnull=True).iterator():
        user_id = profiles_by_employee_no.get(str(employee.employee_no).strip())
        if user_id:
            employee.user_id = user_id
            employee.save(update_fields=['user'])


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0047_rename_purchase_recommendations_module'),
        ('payroll_engine', '0014_add_payslip_line_item_change_log'),
    ]

    operations = [
        migrations.RunPython(link_payroll_employees, migrations.RunPython.noop),
    ]
