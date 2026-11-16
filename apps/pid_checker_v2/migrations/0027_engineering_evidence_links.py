from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from importlib import import_module


def ensure_engineering_reference_keys(apps, schema_editor):
    """Restore missing PostgreSQL ID keys before deferred foreign keys run."""
    import_module('apps.project_control.migrations.0007_epc_foundation').ensure_reference_keys(
        apps, schema_editor, (
            tuple(settings.AUTH_USER_MODEL.split('.')),
            ('core', 'Project'),
            ('project_control', 'EPCWorkItem'),
            ('pid_checker_v2', 'PidCheckerV2Extraction'),
            ('crs', 'CRSRevisionChain'),
            ('crs', 'CRSRevision'),
        ),
    )


class Migration(migrations.Migration):
    dependencies = [
        ('pid_checker_v2', '0026_legend_multi_active'),
        ('core', '0016_ai_hub_seed'),
        ('project_control', '0011_hour_employee_identity'),
        ('crs', '0003_add_s3_excel_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(ensure_engineering_reference_keys, migrations.RunPython.noop),
        migrations.CreateModel(
            name='CRSChainProjectBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('chain', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='canonical_project_binding', to='crs.crsrevisionchain')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_crs_chain_bindings', to='core.project')),
                ('linked_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PidEngineeringEvidenceLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('extraction', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='engineering_evidence', to='pid_checker_v2.pidcheckerv2extraction')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_engineering_evidence', to='core.project')),
                ('work_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_evidence_links', to='project_control.epcworkitem')),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_evidence_links', to='crs.crsrevision')),
                ('linked_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_evidence_links_created', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pid_evidence_links_updated', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
