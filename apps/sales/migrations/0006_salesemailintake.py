import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0005_salesmailboxconnection_delegated_oauth'),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesEmailIntake',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_message_id', models.CharField(db_index=True, max_length=512, unique=True)),
                ('internet_message_id', models.CharField(blank=True, db_index=True, max_length=512)),
                ('subject', models.CharField(max_length=500)),
                ('sender_name', models.CharField(blank=True, max_length=255)),
                ('sender_email', models.EmailField(max_length=254)),
                ('received_at', models.DateTimeField(db_index=True)),
                ('body_preview', models.TextField(blank=True)),
                ('has_attachments', models.BooleanField(default=False)),
                ('importance', models.CharField(blank=True, max_length=20)),
                ('status', models.CharField(choices=[('received', 'Received'), ('under_review', 'Under review'), ('converted', 'Converted to opportunity'), ('rejected', 'Rejected')], db_index=True, default='received', max_length=24)),
                ('opportunity', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='email_intakes', to='sales.deal')),
            ],
            options={
                'db_table': 'sales_email_intakes',
                'ordering': ['-received_at'],
                'indexes': [models.Index(fields=['status', '-received_at'], name='sales_email_status_125333_idx')],
            },
        ),
    ]
