import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('spec_customization', '0010_pipingclasscomponent_revision_number'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.CreateModel(
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
    )]
