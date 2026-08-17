from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('onboarding', '0007_offboarding_rejection_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='offboardingrecord',
            name='project_manager_approval_status',
            field=models.CharField(
                choices=[
                    ('not_required', 'Not Required'),
                    ('pending', 'Pending'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='not_required',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='offboardingrecord',
            name='project_manager_decided_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='offboardingrecord',
            name='project_manager_decided_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='offboarding_project_manager_decisions',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='offboardingrecord',
            name='project_manager_decision_note',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE offboarding_record "
                "ALTER COLUMN project_manager_approval_status SET DEFAULT 'not_required'"
            ),
            reverse_sql=(
                "ALTER TABLE offboarding_record "
                "ALTER COLUMN project_manager_approval_status DROP DEFAULT"
            ),
        ),
    ]
