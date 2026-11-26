# Generated compatibility state: no physical table is created because managed=False.
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('hr_core', '0025_copy_payroll_employee_attributes'),
        ('payroll_engine', '0023_switch_employee_master_foreign_keys'),
    ]
    operations = [
        migrations.CreateModel(
            name='PayrollEmployee',
            fields=[
                ('employee_no', models.CharField(db_column='employee_number', db_index=True, max_length=50, unique=True)),
                ('employee', models.OneToOneField(db_column='id', on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, related_name='+', serialize=False, to='hr_core.employeemaster')),
                ('full_name', models.CharField(db_column='payroll_full_name', db_index=True, max_length=255)),
                ('emirates_id', models.CharField(blank=True, db_column='payroll_emirates_id', default='', max_length=32)),
                ('mol_no', models.CharField(blank=True, db_column='payroll_mol_no', default='', max_length=32)),
                ('iban', models.CharField(blank=True, default='', max_length=64)),
                ('bank_name', models.CharField(blank=True, default='', max_length=128)),
                ('routing_code', models.CharField(blank=True, db_column='payroll_routing_code', default='', max_length=64)),
                ('department', models.CharField(blank=True, db_index=True, default='', max_length=128)),
                ('discipline', models.CharField(blank=True, db_column='payroll_discipline', default='', max_length=128)),
                ('designation', models.CharField(blank=True, default='', max_length=128)),
                ('grade', models.CharField(blank=True, db_column='payroll_grade', default='', max_length=128)),
                ('nationality_group', models.CharField(blank=True, db_column='payroll_nationality_group', default='', max_length=64)),
                ('joining_date', models.DateField(blank=True, db_column='join_date', null=True)),
                ('leaving_date', models.DateField(blank=True, db_column='exit_date', null=True)),
                ('hours', models.DecimalField(db_column='payroll_hours', decimal_places=2, default=Decimal('176'), max_digits=8)),
                ('basic', models.DecimalField(db_column='payroll_basic', decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('housing', models.DecimalField(db_column='payroll_housing', decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('transport', models.DecimalField(db_column='payroll_transport', decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('home_leave', models.DecimalField(db_column='payroll_home_leave', decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('default_payment_mode', models.CharField(db_column='payroll_payment_mode', default='WPS', max_length=32)),
                ('is_active', models.BooleanField(db_column='payroll_enabled', db_index=True, default=True)),
                ('effective_from', models.DateField(blank=True, db_column='payroll_effective_from', null=True)),
                ('effective_to', models.DateField(blank=True, db_column='payroll_effective_to', null=True)),
                ('notes', models.TextField(blank=True, db_column='payroll_notes', default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Payroll Employee', 'db_table': 'hr_employee_master',
                     'ordering': ('full_name',), 'managed': False},
        ),
    ]
