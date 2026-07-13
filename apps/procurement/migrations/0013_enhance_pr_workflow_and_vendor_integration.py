# Generated migration for Purchase Requisition enhancement
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('procurement', '0012_rename_procurement_budget_idx1_procurement_project_cf65fc_idx_and_more'),
    ]

    operations = [
        # 1. Rename "special_notes" to "purchase_recommendation"
        migrations.RenameField(
            model_name='purchaserequisition',
            old_name='special_notes',
            new_name='purchase_recommendation',
        ),
        
        # 2. Add dynamic approval workflow fields
        migrations.AddField(
            model_name='purchaserequisition',
            name='approval_workflow_config',
            field=models.JSONField(
                default=list,
                blank=True,
                help_text='Dynamic approval workflow: [{"step": 1, "role": "Project Manager", "user_id": "uuid", "status": "pending"}]'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='current_approval_step',
            field=models.IntegerField(
                default=0,
                help_text='Current step in approval workflow (0 = not started, 1+ = step number)'
            ),
        ),
        
        # 3. Add Engineering Manager approval fields (new approval tier)
        migrations.AddField(
            model_name='purchaserequisition',
            name='eng_manager_name',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name='prs_eng_manager_approved',
                to=settings.AUTH_USER_MODEL,
                help_text='Engineering Manager name'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='eng_manager_signature',
            field=models.CharField(max_length=500, blank=True, help_text='Engineering Manager signature (base64 or S3 URL)'),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='eng_manager_approval_status',
            field=models.CharField(
                max_length=20,
                choices=[('pending', 'Pending'), ('approved', 'Approved'), ('not_approved', 'Not Approved')],
                default='pending',
                help_text='Engineering Manager Approval Status'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='eng_manager_approved_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Engineering Manager approval timestamp'),
        ),
        
        # 4. Add Manager of Projects approval fields (new approval tier)
        migrations.AddField(
            model_name='purchaserequisition',
            name='manager_projects_name',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name='prs_manager_projects_approved',
                to=settings.AUTH_USER_MODEL,
                help_text='Manager of Projects name'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='manager_projects_signature',
            field=models.CharField(max_length=500, blank=True, help_text='Manager of Projects signature (base64 or S3 URL)'),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='manager_projects_approval_status',
            field=models.CharField(
                max_length=20,
                choices=[('pending', 'Pending'), ('approved', 'Approved'), ('not_approved', 'Not Approved')],
                default='pending',
                help_text='Manager of Projects Approval Status'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='manager_projects_approved_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Manager of Projects approval timestamp'),
        ),
        
        # 5. Add Vendor integration fields
        migrations.AddField(
            model_name='purchaserequisition',
            name='vendor',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name='purchase_requisitions',
                to='procurement.vendor',
                help_text='Linked vendor from vendor master database'
            ),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='vendor_selection_reason',
            field=models.TextField(blank=True, help_text='Reason for selecting this vendor (AI recommendation or manual)'),
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='ai_vendor_recommendations',
            field=models.JSONField(
                default=list,
                blank=True,
                help_text='AI-generated vendor recommendations based on historical data: [{"vendor_id": "uuid", "score": 0.95, "reason": "..."}]'
            ),
        ),
        
        # 6. Enhance price_remarks with dynamic pricing data
        migrations.AddField(
            model_name='purchaserequisition',
            name='price_remarks_data',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text='Advanced pricing data: {"budget_allocation": "HSE", "cost_center": "CC-001", "payment_terms": "Net 45", "discount": 10, "comparative_prices": [...]}'
            ),
        ),
        
        # 7. Add indexes for performance
        migrations.AddIndex(
            model_name='purchaserequisition',
            index=models.Index(fields=['vendor', 'status'], name='proc_pr_vend_stat_idx'),
        ),
        migrations.AddIndex(
            model_name='purchaserequisition',
            index=models.Index(fields=['current_approval_step', 'status'], name='proc_pr_appr_step_idx'),
        ),
        
        # 8. Add PurchaseOrder vendor integration (already exists but ensure FK is properly set)
        # No changes needed - PurchaseOrder already has vendor FK
    ]
