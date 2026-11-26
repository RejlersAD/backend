from django.db import migrations

from . import _payroll_consolidation


class Migration(migrations.Migration):
    dependencies = [('payroll_engine', '0022_employee_master_foreign_keys')]
    operations = [
        migrations.RunPython(
            _payroll_consolidation.copy_employee_relationships,
            migrations.RunPython.noop,
        ),
    ]
