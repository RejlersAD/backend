from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0016_purchaseorder_approval_status_log_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='purchaserequisition',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('submitted', 'Submitted'),
                    ('in_review', 'In Review'),
                    ('pending_level_2', 'Pending Level 2 Approval'),
                    ('approved', 'Approved'),
                    ('pm_approved', 'PM Approved'),
                    ('vp_approved', 'VP Approved'),
                    ('fully_approved', 'Fully Approved'),
                    ('rejected', 'Rejected'),
                    ('cancelled', 'Cancelled'),
                    ('converted', 'Converted to PO'),
                ],
                default='draft',
                max_length=20,
            ),
        ),
    ]
