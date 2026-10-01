import django.db.models.deletion
from django.db import migrations, models


def preserve_reviewed_references(apps, schema_editor):
    identity = apps.get_model('finance', 'ReceivablesSourceIdentity')
    if identity.objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('Reviewed Finance source references exist. Roll back application code while retaining this schema and evidence.')


class Migration(migrations.Migration):
    dependencies = [
        ('finance', '0015_receivables_sync_state'),
        ('core', '0014_shared_record_identity'),
        ('sales', '0013_private_opportunity_attachments'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReceivablesSourceIdentity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('canonical_identity_basis', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('canonical_client', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                      related_name='receivables_source_references', to='sales.client')),
                ('canonical_project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                       related_name='receivables_source_references', to='core.project')),
                ('source_row', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                   related_name='canonical_identity', to='finance.receivablessourcerow')),
            ],
        ),
        migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_references),
    ]
