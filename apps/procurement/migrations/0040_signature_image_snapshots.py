from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0039_remove_vendor_pending_approval')]

    operations = [
        migrations.AlterField(
            model_name='purchaserequisition', name='pm_signature',
            field=models.TextField(blank=True, help_text='PM signature image snapshot (data URL or S3 URL)'),
        ),
        migrations.AlterField(
            model_name='purchaserequisition', name='eng_manager_signature',
            field=models.TextField(blank=True, help_text='Engineering Manager signature image snapshot (data URL or S3 URL)'),
        ),
        migrations.AlterField(
            model_name='purchaserequisition', name='manager_projects_signature',
            field=models.TextField(blank=True, help_text='Manager of Projects signature image snapshot (data URL or S3 URL)'),
        ),
        migrations.AlterField(
            model_name='purchaserequisition', name='vp_op_signature',
            field=models.TextField(blank=True, help_text='VP signature image snapshot (data URL or S3 URL)'),
        ),
        migrations.AlterField(
            model_name='purchaseorder', name='approval_signature',
            field=models.TextField(blank=True, help_text='Digital signature image snapshot (data URL or S3 URL)'),
        ),
    ]
