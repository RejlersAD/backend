import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def preserve_reviewed_links(apps, schema_editor):
    alias = schema_editor.connection.alias
    if (apps.get_model('core', 'Project').objects.using(alias).filter(client__isnull=False).exists()
            or apps.get_model('core', 'SharedRecordLinkCommand').objects.using(alias).exists()):
        raise RuntimeError('Retain the identity schema when rolling back application code; reviewed links and audit history exist.')


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0013_ai_provider_credentials'),
        ('sales', '0013_private_opportunity_attachments'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='project', name='client',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='enterprise_projects', to='sales.client',
                                    help_text='Canonical client; client_name retains the recorded project label.'),
        ),
        migrations.CreateModel(
            name='SharedRecordLinkCommand',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.UUIDField()),
                ('source_type', models.CharField(max_length=40)),
                ('source_id', models.CharField(max_length=80)),
                ('request_hash', models.CharField(max_length=64)),
                ('before', models.JSONField(default=dict)), ('after', models.JSONField(default=dict)),
                ('reason', models.CharField(max_length=1000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-pk'],
                     'indexes': [models.Index(fields=['source_type', 'source_id'], name='core_record_link_source')],
                     'constraints': [models.UniqueConstraint(fields=('actor', 'request_id'), name='core_record_link_request')]},
        ),
        migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_links),
    ]
