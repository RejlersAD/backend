from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


LEGACY_ID_INDEXES = (
    ('planning_intelligence_planningproject', 'planning_project_id_uniq'),
    ('planning_intelligence_planninggeneration', 'planning_generation_id_uniq'),
)


def restore_legacy_id_uniqueness(apps, schema_editor):
    """Repair legacy PostgreSQL tables that were synced without PK indexes."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    quote_name = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table_name, index_name in LEGACY_ID_INDEXES:
            constraints = connection.introspection.get_constraints(cursor, table_name)
            id_is_unique = any(
                constraint['unique'] and constraint['columns'] == ['id']
                for constraint in constraints.values()
            )
            if not id_is_unique:
                schema_editor.execute(
                    f'CREATE UNIQUE INDEX {quote_name(index_name)} '
                    f'ON {quote_name(table_name)} ({quote_name("id")})'
                )


def drop_legacy_id_uniqueness(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    quote_name = schema_editor.connection.ops.quote_name
    for _, index_name in reversed(LEGACY_ID_INDEXES):
        schema_editor.execute(f'DROP INDEX IF EXISTS {quote_name(index_name)}')


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0008_document_documentaccesslog'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('planning_intelligence', '0004_alter_planningfile_file'),
    ]

    operations = [
        # Some production databases contain these legacy tables without the
        # primary-key indexes declared by 0001. PostgreSQL cannot create the
        # new foreign keys below unless their referenced id columns are unique.
        # Avoid adding redundant indexes on databases where the primary keys
        # already exist (including every normally-created fresh database).
        migrations.RunPython(
            code=restore_legacy_id_uniqueness,
            reverse_code=drop_legacy_id_uniqueness,
        ),
        migrations.AddField(
            model_name='planningproject', name='enterprise_project',
            field=models.OneToOneField(
                blank=True,
                db_constraint=False,
                help_text=(
                    'Enterprise project that owns this planning workspace. The legacy '
                    'production core_project table does not consistently expose a '
                    'database-level unique constraint on id, so referential integrity '
                    'is enforced by the API and ORM instead of a PostgreSQL foreign key.'
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='planning_workspace',
                to='core.project',
            ),
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
