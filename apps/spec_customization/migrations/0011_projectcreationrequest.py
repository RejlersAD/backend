import uuid
from importlib import import_module
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def ensure_creation_reference_keys(apps, schema_editor):
    """Restore missing physical keys before deferred foreign keys are created."""
    import_module('apps.project_control.migrations.0007_epc_foundation').ensure_reference_keys(
        apps, schema_editor, (
            ('spec_customization', 'SpecProject'),
            tuple(settings.AUTH_USER_MODEL.split('.')),
        ),
    )


class Migration(migrations.Migration):
    dependencies = [
        ('spec_customization', '0010_pipingclasscomponent_revision_number'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.RunPython(ensure_creation_reference_keys, migrations.RunPython.noop),
        migrations.CreateModel(
            name='ProjectCreationRequest',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('request_id', models.UUIDField()),
                ('payload_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE)),
                ('project', models.ForeignKey(to='spec_customization.specproject', null=True, on_delete=django.db.models.deletion.SET_NULL)),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('owner', 'request_id'), name='spec_project_creation_request_unique')]},
        ),
    ]
