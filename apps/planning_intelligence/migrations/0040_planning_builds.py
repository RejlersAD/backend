import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def protect_builds(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute("""
            CREATE FUNCTION radai_protect_planning_build() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Planning builds are immutable; create a new preview'; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER protect_planning_build BEFORE UPDATE OR DELETE ON planning_intelligence_planningbuild
                FOR EACH ROW EXECUTE FUNCTION radai_protect_planning_build();
        """)


def unprotect_builds(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS protect_planning_build ON planning_intelligence_planningbuild')
        schema_editor.execute('DROP FUNCTION IF EXISTS radai_protect_planning_build()')


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0039_master_schedule_selection'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='PlanningBuild', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('evidence_revision', models.PositiveIntegerField()),
            ('profile_selection_revision', models.PositiveIntegerField()),
            ('source_fingerprint', models.CharField(max_length=64)),
            ('profile_fingerprint', models.CharField(max_length=64)),
            ('fingerprint', models.CharField(max_length=64)),
            ('rule_version', models.CharField(max_length=64)),
            ('options', models.JSONField(default=dict)),
            ('evidence_snapshot', models.JSONField(default=dict)),
            ('profile_snapshot', models.JSONField(default=dict)),
            ('plan', models.JSONField(default=dict)),
            ('issues', models.JSONField(default=list)),
            ('reason', models.TextField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planning_builds_created', to=settings.AUTH_USER_MODEL)),
            ('evidence_graph', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planning_builds', to='planning_intelligence.evidencegraph')),
            ('profile', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planning_builds', to='planning_intelligence.planningprofile')),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='planning_builds', to='planning_intelligence.planningproject')),
        ], options={'ordering': ['-created_at', '-pk']}),
        migrations.AddField(model_name='scheduleversion', name='planning_build',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='schedule_versions', to='planning_intelligence.planningbuild')),
        migrations.RunPython(protect_builds, unprotect_builds),
    ]
