import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('project_control', '0003_cost_ledger_and_allocations'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommercialEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_key', models.CharField(max_length=255, unique=True)),
                ('event_type', models.CharField(choices=[('po_approved', 'Purchase Order Approved'), ('receipt_accepted', 'Receipt Accepted'), ('invoice_approved', 'Invoice Approved'), ('invoice_verified', 'Invoice Three-Way Match Verified'), ('payment_scheduled', 'Payment Scheduled'), ('payment_recorded', 'Payment Recorded'), ('payment_held', 'Payment Held'), ('payment_released', 'Payment Released'), ('payment_cancelled', 'Payment Cancelled'), ('historical_reconciliation', 'Historical Reconciliation')], db_index=True, max_length=40)),
                ('source_type', models.CharField(db_index=True, max_length=40)),
                ('source_id', models.CharField(db_index=True, max_length=64)),
                ('source_reference', models.CharField(blank=True, max_length=160)),
                ('amount', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('currency', models.CharField(blank=True, max_length=8)),
                ('event_at', models.DateTimeField(db_index=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('ledger_rebuilt', models.BooleanField(default=False)),
                ('processing_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_commercial_events', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commercial_events', to='core.project')),
            ],
            options={'ordering': ['-event_at', '-created_at']},
        ),
        migrations.AddIndex(model_name='commercialevent', index=models.Index(fields=['project', 'event_type', '-event_at'], name='pc_com_event_project_idx')),
        migrations.AddIndex(model_name='commercialevent', index=models.Index(fields=['source_type', 'source_id'], name='pc_com_event_source_idx')),
    ]
