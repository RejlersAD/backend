import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr_core', '0009_link_legacy_employee_extensions'),
        ('onboarding', '0013_offboardingrecord_employee_onboardingrecord_employee_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='onboardingrecord',
            name='canonical_employee',
            field=models.ForeignKey(
                blank=True,
                help_text='Canonical employee identity; name/email/code fields are immutable workflow snapshots.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='onboarding_records',
                to='hr_core.employeemaster',
            ),
        ),
    ]
