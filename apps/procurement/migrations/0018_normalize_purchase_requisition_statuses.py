from django.db import migrations, models


LEGACY_STATUS_MAP = {
    'pending_level_2': 'in_review',
    'pm_approved': 'in_review',
    'vp_approved': 'approved',
    'fully_approved': 'approved',
}


def normalize_requisition_statuses(apps, schema_editor):
    PurchaseRequisition = apps.get_model('procurement', 'PurchaseRequisition')
    for legacy_status, canonical_status in LEGACY_STATUS_MAP.items():
        PurchaseRequisition.objects.filter(status=legacy_status).update(status=canonical_status)


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0017_alter_purchaserequisition_status_choices'),
    ]

    operations = [
        migrations.RunPython(normalize_requisition_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='purchaserequisition',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('submitted', 'Submitted'),
                    ('in_review', 'In Review'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                    ('cancelled', 'Cancelled'),
                    ('converted', 'Converted to PO'),
                ],
                default='draft',
                max_length=20,
            ),
        ),
    ]
