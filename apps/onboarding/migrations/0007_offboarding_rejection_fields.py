from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('onboarding', '0006_expand_checklist_stage_choices'),
    ]

    operations = [
        migrations.AddField(
            model_name='offboardingrecord',
            name='rejected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='offboardingrecord',
            name='rejected_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='offboarding_rejected',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='offboardingrecord',
            name='rejection_reason',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='offboardingrecord',
            name='status',
            field=models.CharField(
                choices=[
                    ('initiated', 'Initiated'),
                    ('access_revocation', 'Access Revocation'),
                    ('equipment_return', 'Equipment Return'),
                    ('exit_interview', 'Exit Interview'),
                    ('final_settlement', 'Final Settlement'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                    ('rejected', 'Rejected'),
                ],
                default='initiated',
                max_length=50,
            ),
        ),
    ]
