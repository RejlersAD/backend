from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0015_rename_procurement_budget_idx1_procurement_project_cf65fc_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='approval_log',
            field=models.JSONField(blank=True, default=list, help_text='Approval stage log list: [{"stage": "Technical Approval", "approver": "", "status": "Pending", "date": "", "comments": ""}]'),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='financial_approver',
            field=models.CharField(blank=True, help_text='Assigned financial approver name or identifier', max_length=200),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='final_approver_notes',
            field=models.TextField(blank=True, help_text='Final sign-off notes and approval handover comments'),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='management_approver',
            field=models.CharField(blank=True, help_text='Assigned management approver name or identifier', max_length=200),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='technical_approver',
            field=models.CharField(blank=True, help_text='Assigned technical approver name or identifier', max_length=200),
        ),
    ]
