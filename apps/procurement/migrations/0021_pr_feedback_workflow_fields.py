# Generated migration for Purchase Recommendation feedback requirements
# Reference: RADAI_Feedback on PR.docx (2026-08-10)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0020_rename_current_approval_level_to_step'),
    ]

    operations = [
        # New fields for enhanced Purchase Recommendation workflow
        migrations.AddField(
            model_name='purchaserequisition',
            name='management_approval',
            field=models.BooleanField(
                blank=True,
                null=True,
                help_text='Management approval confirmation; mandatory for AED-equivalent values above 100,000.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='management_approval_evidence',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Evidence of management approval attachments (files uploaded to S3).'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='management_approval_remarks',
            field=models.TextField(
                blank=True,
                help_text='Remarks or notes regarding management approval.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='po_applicable',
            field=models.BooleanField(
                default=False,
                help_text='Indicates whether a Purchase Order is applicable for this requisition.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='project_details',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='One or more linked project/department selections for this recommendation.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='resolution_referral',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='MoE/MoP discussion referral recorded after rejection.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='review_due_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='Submission review deadline derived from priority (urgent=same day, high=1 day, normal=2 days).'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='selected_vendors',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Vendor shortlist captured from the vendor master, including ICV details.'
            )
        ),
        migrations.AddField(
            model_name='purchaserequisition',
            name='single_source_justification',
            field=models.TextField(
                blank=True,
                help_text='Required justification when only one vendor is shortlisted.'
            )
        ),
        
        # Update existing fields
        migrations.AlterField(
            model_name='purchaserequisition',
            name='pr_number',
            field=models.CharField(
                max_length=50,
                unique=True,
                db_index=True,
                help_text='Manually assigned PR number (system validates for duplicates)'
            )
        ),
        migrations.AlterField(
            model_name='purchaserequisition',
            name='priority',
            field=models.CharField(
                max_length=20,
                default='normal',
                choices=[
                    ('urgent', 'Urgent - Same Day Review'),
                    ('high', 'High - 1 Day Review'),
                    ('normal', 'Normal - 2 Days Review'),
                    ('low', 'Low (legacy)')
                ],
                help_text='Priority level determining review cycle deadline'
            )
        ),
        migrations.AlterField(
            model_name='purchaserequisition',
            name='price_remarks',
            field=models.TextField(
                blank=True,
                help_text='Negotiation remarks: outcome, savings, commercial clarifications, or final terms'
            )
        ),
    ]
