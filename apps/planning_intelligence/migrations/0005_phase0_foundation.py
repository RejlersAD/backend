from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0008_document_documentaccesslog'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('planning_intelligence', '0004_alter_planningfile_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='planningproject', name='enterprise_project',
            field=models.OneToOneField(blank=True, help_text='Enterprise project that owns this planning workspace.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='planning_workspace', to='core.project'),
        ),
        migrations.AddField(model_name='planninggeneration', name='change_summary', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(
            model_name='planninggeneration', name='parent_generation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revisions', to='planning_intelligence.planninggeneration'),
        ),
        migrations.CreateModel(
            name='PlanningJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('job_type', models.CharField(choices=[('analyze', 'Analyze Documents'), ('generate', 'Generate Schedule')], max_length=16)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=16)),
                ('progress', models.PositiveSmallIntegerField(default=0)), ('message', models.CharField(blank=True, max_length=255)),
                ('request_data', models.JSONField(blank=True, default=dict)), ('result_data', models.JSONField(blank=True, default=dict)),
                ('task_id', models.CharField(blank=True, max_length=255)), ('error_code', models.CharField(blank=True, max_length=64)),
                ('error_message', models.TextField(blank=True)), ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='jobs', to='planning_intelligence.planningproject')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_jobs_requested', to=settings.AUTH_USER_MODEL)),
                ('result_generation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='jobs', to='planning_intelligence.planninggeneration')),
            ], options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='PlanningAuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(db_index=True, max_length=64)), ('entity_type', models.CharField(max_length=64)),
                ('entity_id', models.CharField(blank=True, max_length=64)), ('before', models.JSONField(blank=True, default=dict)),
                ('after', models.JSONField(blank=True, default=dict)), ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_audit_events', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_events', to='planning_intelligence.planningproject')),
            ], options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='planningjob', index=models.Index(fields=['project', '-created_at'], name='planning_in_project_7313f8_idx')),
        migrations.AddIndex(model_name='planningjob', index=models.Index(fields=['status', '-created_at'], name='planning_in_status_1d4be1_idx')),
        migrations.AddIndex(model_name='planningauditevent', index=models.Index(fields=['project', '-created_at'], name='planning_in_project_b49813_idx')),
    ]
