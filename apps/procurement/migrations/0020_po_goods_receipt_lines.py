from decimal import Decimal, InvalidOperation

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def _decimal(value, default='0'):
    try:
        return Decimal(str(value if value not in (None, '') else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def backfill_procurement_lines(apps, schema_editor):
    PurchaseOrder = apps.get_model('procurement', 'PurchaseOrder')
    PurchaseOrderLine = apps.get_model('procurement', 'PurchaseOrderLine')
    Receipt = apps.get_model('procurement', 'Receipt')
    ReceiptLine = apps.get_model('procurement', 'ReceiptLine')

    for po in PurchaseOrder.objects.all().iterator():
        line_by_number = {}
        line_by_description = {}
        for index, item in enumerate(po.items if isinstance(po.items, list) else [], start=1):
            if not isinstance(item, dict):
                continue
            quantity = _decimal(
                item.get('ordered_quantity', item.get('quantity', item.get('qty', 0)))
            )
            if quantity <= 0:
                continue
            description = str(
                item.get('description')
                or item.get('item')
                or item.get('name')
                or f'PO line {index}'
            ).strip()
            line = PurchaseOrderLine.objects.create(
                purchase_order_id=po.id,
                line_number=index,
                item_code=str(item.get('item_code') or item.get('code') or '').strip(),
                description=description[:500],
                line_type=str(item.get('line_type') or 'goods')[:20],
                ordered_quantity=quantity,
                unit_of_measure=str(item.get('unit_of_measure') or item.get('uom') or 'EA')[:30],
                unit_price=max(_decimal(item.get('unit_price', item.get('price', 0))), Decimal('0')),
            )
            line_by_number[index] = line
            line_by_description[description.casefold()] = line

        for receipt in Receipt.objects.filter(purchase_order_id=po.id).iterator():
            for index, item in enumerate(
                receipt.items_received if isinstance(receipt.items_received, list) else [],
                start=1,
            ):
                if not isinstance(item, dict):
                    continue
                description = str(item.get('description') or item.get('item') or item.get('name') or '').strip()
                line_number = item.get('line_number') or index
                try:
                    line_number = int(line_number)
                except (TypeError, ValueError):
                    line_number = index
                po_line = line_by_number.get(line_number) or line_by_description.get(description.casefold())
                if not po_line:
                    continue
                delivered = _decimal(
                    item.get('delivered_quantity', item.get('received_qty', item.get('quantity_received', 0)))
                )
                if delivered <= 0:
                    continue
                accepted = max(_decimal(item.get('accepted_qty', item.get('accepted_quantity', delivered))), Decimal('0'))
                rejected = max(_decimal(item.get('rejected_qty', item.get('rejected_quantity', 0))), Decimal('0'))
                if accepted + rejected > delivered:
                    accepted = max(delivered - rejected, Decimal('0'))
                    rejected = min(rejected, delivered)
                ReceiptLine.objects.get_or_create(
                    receipt_id=receipt.id,
                    purchase_order_line_id=po_line.id,
                    defaults={
                        'delivered_quantity': delivered,
                        'accepted_quantity': accepted,
                        'rejected_quantity': rejected,
                        'rejection_reason': str(item.get('rejection_reason') or ''),
                        'batch_number': str(item.get('batch_number') or '')[:100],
                        'heat_number': str(item.get('heat_number') or '')[:100],
                    },
                )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('procurement', '0019_procurement_number_sequence'),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseOrderLine',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('line_number', models.PositiveIntegerField()),
                ('item_code', models.CharField(blank=True, max_length=100)),
                ('description', models.CharField(max_length=500)),
                ('line_type', models.CharField(choices=[('goods', 'Goods'), ('service', 'Service')], default='goods', max_length=20)),
                ('ordered_quantity', models.DecimalField(decimal_places=4, max_digits=18)),
                ('unit_of_measure', models.CharField(default='EA', max_length=30)),
                ('unit_price', models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ('receipt_tolerance_percentage', models.DecimalField(decimal_places=3, default=0, max_digits=6)),
                ('purchase_order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='procurement.purchaseorder')),
            ],
            options={
                'db_table': 'procurement_order_lines',
                'ordering': ['line_number'],
                'indexes': [models.Index(fields=['purchase_order', 'line_number'], name='proc_po_line_lookup_idx')],
                'constraints': [
                    models.UniqueConstraint(fields=('purchase_order', 'line_number'), name='proc_po_line_number_uniq'),
                    models.CheckConstraint(check=models.Q(('ordered_quantity__gt', 0)), name='proc_po_line_qty_positive'),
                    models.CheckConstraint(check=models.Q(('unit_price__gte', 0)), name='proc_po_line_price_nonnegative'),
                    models.CheckConstraint(check=models.Q(('receipt_tolerance_percentage__gte', 0)), name='proc_po_line_tolerance_nonnegative'),
                ],
            },
        ),
        migrations.AddField(
            model_name='receipt',
            name='cancellation_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='receipt',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='receipt',
            name='inspected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='receipt',
            name='inspected_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='receipts_inspected', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='receipt',
            name='submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='receipt',
            name='purchase_order',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receipts', to='procurement.purchaseorder'),
        ),
        migrations.AlterField(
            model_name='receipt',
            name='quality_check_passed',
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
        migrations.AlterField(
            model_name='receipt',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('pending', 'Pending Inspection'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('partial', 'Partially Accepted'), ('cancelled', 'Cancelled')], default='pending', max_length=20),
        ),
        migrations.CreateModel(
            name='ReceiptLine',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('delivered_quantity', models.DecimalField(decimal_places=4, max_digits=18)),
                ('accepted_quantity', models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ('rejected_quantity', models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ('rejection_reason', models.TextField(blank=True)),
                ('batch_number', models.CharField(blank=True, max_length=100)),
                ('heat_number', models.CharField(blank=True, max_length=100)),
                ('serial_numbers', models.JSONField(blank=True, default=list)),
                ('inspection_notes', models.TextField(blank=True)),
                ('purchase_order_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='receipt_lines', to='procurement.purchaseorderline')),
                ('receipt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='procurement.receipt')),
            ],
            options={
                'db_table': 'procurement_receipt_lines',
                'ordering': ['purchase_order_line__line_number'],
                'indexes': [
                    models.Index(fields=['purchase_order_line'], name='proc_gr_po_line_idx'),
                    models.Index(fields=['receipt'], name='proc_gr_receipt_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('receipt', 'purchase_order_line'), name='proc_gr_po_line_uniq'),
                    models.CheckConstraint(check=models.Q(('delivered_quantity__gt', 0)), name='proc_gr_line_delivered_positive'),
                    models.CheckConstraint(check=models.Q(('accepted_quantity__gte', 0)), name='proc_gr_line_accepted_nonnegative'),
                    models.CheckConstraint(check=models.Q(('rejected_quantity__gte', 0)), name='proc_gr_line_rejected_nonnegative'),
                    models.CheckConstraint(check=models.Q(('delivered_quantity__gte', models.F('accepted_quantity') + models.F('rejected_quantity'))), name='proc_gr_line_disposition_within_delivery'),
                ],
            },
        ),
        migrations.RunPython(backfill_procurement_lines, migrations.RunPython.noop),
    ]
