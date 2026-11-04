import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [('sales', '0036_unified_document_evidence')]

    operations = [
        migrations.CreateModel(
            name='SalesEmailIntakeAttachment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_part_index', models.PositiveSmallIntegerField()),
                ('name', models.CharField(max_length=255)),
                ('mime_type', models.CharField(blank=True, max_length=255)),
                ('size', models.PositiveBigIntegerField()),
                ('sha256', models.CharField(max_length=64)),
                ('storage_name', models.CharField(max_length=1024)),
                ('storage_fingerprint', models.CharField(max_length=64)),
                ('text_chunks', models.JSONField(blank=True, default=list)),
                ('extraction_status', models.CharField(default='unread', max_length=24)),
                ('extraction_diagnostic', models.CharField(blank=True, max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('intake', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='source_attachments', to='sales.salesemailintake')),
            ],
            options={'ordering': ['source_part_index', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='salesemailintakeattachment',
            constraint=models.UniqueConstraint(fields=('intake', 'source_part_index'), name='sales_intake_attachment_part_uq'),
        ),
    ]
