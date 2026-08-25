import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0006b_verify_planningfile_constraints'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(name='DocumentIntelligenceRun', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('status', models.CharField(choices=[('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='running', max_length=16)),
            ('engine_version', models.CharField(default='2.0', max_length=32)), ('source_file_ids', models.JSONField(blank=True, default=list)),
            ('summary', models.JSONField(blank=True, default=dict)), ('fact_count', models.PositiveIntegerField(default=0)),
            ('conflict_count', models.PositiveIntegerField(default=0)), ('started_at', models.DateTimeField()),
            ('finished_at', models.DateTimeField(blank=True, null=True)), ('error_message', models.TextField(blank=True)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='intelligence_runs', to='planning_intelligence.planningproject')),
            ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='document_intelligence_runs_requested', to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ['-created_at']}),
        migrations.CreateModel(name='DocumentProfile', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('declared_category', models.CharField(blank=True, max_length=40)), ('detected_category', models.CharField(blank=True, max_length=40)),
            ('classification_confidence', models.FloatField(default=0)), ('extension', models.CharField(blank=True, max_length=16)),
            ('mime_type', models.CharField(blank=True, max_length=128)), ('language', models.CharField(default='en', max_length=16)),
            ('page_count', models.PositiveIntegerField(default=0)), ('word_count', models.PositiveIntegerField(default=0)),
            ('checksum_sha256', models.CharField(blank=True, db_index=True, max_length=64)), ('extraction_method', models.CharField(blank=True, max_length=64)),
            ('quality_flags', models.JSONField(blank=True, default=list)), ('classified_at', models.DateTimeField(blank=True, null=True)),
            ('file', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='document_profile', to='planning_intelligence.planningfile')),
        ], options={'ordering': ['-updated_at']}),
        migrations.CreateModel(name='IntelligenceConflict', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('key', models.CharField(db_index=True, max_length=160)), ('conflict_type', models.CharField(default='value_mismatch', max_length=40)),
            ('fact_ids', models.JSONField(blank=True, default=list)), ('description', models.CharField(max_length=500)),
            ('status', models.CharField(choices=[('open', 'Open'), ('resolved', 'Resolved'), ('ignored', 'Ignored')], db_index=True, default='open', max_length=16)),
            ('resolution', models.JSONField(blank=True, default=dict)), ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='intelligence_conflicts_resolved', to=settings.AUTH_USER_MODEL)),
            ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conflicts', to='planning_intelligence.documentintelligencerun')),
        ], options={'ordering': ['status', 'key']}),
        migrations.CreateModel(name='IntelligenceFact', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('fact_type', models.CharField(choices=[('project_name', 'Project Name'), ('effective_date', 'Effective Date'), ('duration_months', 'Duration Months'), ('client', 'Client'), ('location', 'Location'), ('discipline', 'Discipline'), ('deliverable', 'Deliverable'), ('hse_study', 'HSE Study'), ('milestone', 'Milestone'), ('calendar', 'Calendar'), ('review_cycle', 'Review Cycle'), ('requirement', 'Requirement'), ('exclusion', 'Exclusion')], db_index=True, max_length=32)),
            ('key', models.CharField(db_index=True, max_length=160)), ('value', models.JSONField()),
            ('normalized_value', models.CharField(blank=True, max_length=500)), ('confidence', models.FloatField(default=0)),
            ('extraction_method', models.CharField(choices=[('deterministic', 'Deterministic'), ('ai', 'AI'), ('manual', 'Manual')], default='deterministic', max_length=20)),
            ('source_excerpt', models.CharField(blank=True, max_length=1000)), ('source_locator', models.JSONField(blank=True, default=dict)),
            ('status', models.CharField(choices=[('detected', 'Detected'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected'), ('conflicted', 'Conflicted'), ('superseded', 'Superseded')], db_index=True, default='detected', max_length=16)),
            ('reviewed_at', models.DateTimeField(blank=True, null=True)),
            ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='intelligence_facts_reviewed', to=settings.AUTH_USER_MODEL)),
            ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='facts', to='planning_intelligence.documentintelligencerun')),
            ('source_file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='intelligence_facts', to='planning_intelligence.planningfile')),
        ], options={'ordering': ['fact_type', '-confidence', 'id']}),
        migrations.AddIndex(model_name='documentintelligencerun', index=models.Index(fields=['project', '-created_at'], name='planning_in_project_ac1727_idx')),
        migrations.AddIndex(model_name='intelligenceconflict', index=models.Index(fields=['run', 'status'], name='planning_in_run_id_0fb8c2_idx')),
        migrations.AddIndex(model_name='intelligencefact', index=models.Index(fields=['run', 'fact_type', 'status'], name='planning_in_run_id_f31087_idx')),
    ]
