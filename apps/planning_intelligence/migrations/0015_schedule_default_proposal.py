from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('planning_intelligence', '0014_seed_engineering_workflows'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScheduleDefaultProposal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('title', models.CharField(max_length=180)),
                ('rationale', models.TextField(blank=True)),
                ('base_configuration_version', models.PositiveIntegerField()),
                ('proposed_values', models.JSONField(default=dict)),
                ('test_results', models.JSONField(default=list)),
                ('status', models.CharField(choices=[('proposed', 'Proposed'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('superseded', 'Superseded')], db_index=True, default='proposed', max_length=16)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_comment', models.TextField(blank=True)),
                ('configuration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='default_proposals', to='planning_intelligence.projectscheduleconfiguration')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_schedule_defaults_decided', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schedule_default_proposals', to='planning_intelligence.planningproject')),
                ('proposed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planning_schedule_defaults_proposed', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='scheduledefaultproposal',
            index=models.Index(fields=['project', 'status', '-created_at'], name='planning_in_project_9f568d_idx'),
        ),
    ]
