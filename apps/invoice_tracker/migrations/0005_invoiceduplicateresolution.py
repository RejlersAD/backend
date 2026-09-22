import uuid

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ('invoice_tracker', '0004_rename_invoice_tra_account_dab1bb_idx_invoice_tra_account_55b104_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceDuplicateResolution',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('invoice_id', models.BigIntegerField(db_index=True)),
                ('actor_id', models.CharField(max_length=255)),
                ('retained_record', models.JSONField()),
                ('removed_records', models.JSONField()),
                ('created_at', models.DateTimeField(db_index=True, default=timezone.now, editable=False)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
