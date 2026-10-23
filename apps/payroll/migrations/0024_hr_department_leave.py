from django.db import migrations, models
import django.db.models.deletion


def seed(apps, schema_editor):
    LeaveType = apps.get_model('payroll', 'LeaveType')
    for order, (code, name, category) in enumerate([
        ('CL', 'Compassionate Leave', 'compassionate'),
        ('NSL', 'National Service Leave', 'national_service'),
        ('WFH', 'Work From Home', 'work_from_home'),
        ('FU', 'Flex Utilization', 'flex_utilization'),
    ], 20):
        LeaveType.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={
            'name': name, 'category': category, 'display_order': order, 'requires_approval': True,
        })


class Migration(migrations.Migration):
    dependencies = [('payroll', '0023_leaverequest_half_day'), ('hr_core', '0015_employee_nationality')]
    operations = [
        migrations.AddField(model_name='leaverequest', name='selected_manager',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='manually_routed_leave_requests', to='hr_core.employeemaster',
                help_text='Request-specific manager for Sales/Finance employees without an assigned manager.')),
        migrations.AlterField(model_name='leavetype', name='category', field=models.CharField(
            blank=True, db_index=True, default='other', max_length=20,
            choices=[('annual', 'Annual Leave'), ('sick', 'Sick Leave'), ('emergency', 'Emergency Leave'),
                ('unpaid', 'Unpaid Leave'), ('maternity', 'Maternity Leave'), ('paternity', 'Paternity Leave'),
                ('compensatory', 'Compensatory Leave'), ('public_holiday', 'Public Holiday'),
                ('work_off', 'Work Off'), ('other', 'Other'), ('compassionate', 'Compassionate Leave'),
                ('national_service', 'National Service Leave'), ('work_from_home', 'Work From Home'),
                ('flex_utilization', 'Flex Utilization')],
            help_text='Canonical category key — must match ESS_LEAVE_TYPE_CONFIG key in hrLeave.config.js')),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
