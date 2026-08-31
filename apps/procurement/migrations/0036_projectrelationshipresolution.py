import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0008_document_documentaccesslog'),
        ('procurement', '0035_canonical_enterprise_project_relationships'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectRelationshipResolution',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('record_type', models.CharField(choices=[('procurement_project', 'Procurement Project'), ('purchase_requisition', 'Purchase Requisition'), ('purchase_order', 'Purchase Order')], db_index=True, max_length=30)),
                ('record_id', models.UUIDField(db_index=True)),
                ('resolution', models.CharField(choices=[('manual', 'Manual'), ('propagated', 'Propagated')], default='manual', max_length=20)),
                ('reason', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('enterprise_project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='procurement_relationship_resolutions', to='core.project')),
                ('previous_enterprise_project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.project')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='procurement_project_resolutions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'procurement_project_relationship_resolutions',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='projectrelationshipresolution',
            index=models.Index(fields=['record_type', 'record_id'], name='proc_rel_record_idx'),
        ),
        migrations.AddIndex(
            model_name='projectrelationshipresolution',
            index=models.Index(fields=['-created_at'], name='proc_rel_created_idx'),
        ),
    ]
