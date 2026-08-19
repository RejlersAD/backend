# Generated manually on 2026-07-23
# Clean migration for PIDVAICheckRun model without index renaming

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pid_verification', '0009_pidvreferencedata'),
    ]

    operations = [
        migrations.CreateModel(
            name='PIDVAICheckRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('run_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('extracting', 'Extracting P&ID Elements'), ('checking', 'Running Checks'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('analysis_mode', models.CharField(default='hybrid', max_length=30)),
                ('extracted_data', models.JSONField(blank=True, help_text='All extracted P&ID elements from vision APIs', null=True)),
                ('check_results', models.JSONField(blank=True, default=list, help_text='Results of all executed checks')),
                ('summary_stats', models.JSONField(blank=True, default=dict, help_text='Summary statistics of check run')),
                ('processing_metadata', models.JSONField(blank=True, default=dict, help_text='Processing metrics and costs')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_check_runs', to='pid_verification.pidvproject')),
                ('triggered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pid_ai_check_runs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'pidv_ai_check_runs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='pidvaicheckrun',
            index=models.Index(fields=['run_id'], name='pidv_ai_check_runs_run_id_idx'),
        ),
        migrations.AddIndex(
            model_name='pidvaicheckrun',
            index=models.Index(fields=['project', 'status'], name='pidv_ai_check_runs_project_status_idx'),
        ),
    ]
