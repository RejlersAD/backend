from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_ai_custom_skills'),
    ]

    operations = [
        migrations.AddField(
            model_name='enquiry',
            name='requisition_details',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AlterField(
            model_name='enquiry',
            name='inquiry_type',
            field=models.CharField(choices=[
                ('general', 'General Inquiry'),
                ('technical_support', 'Technical Support'),
                ('complaint', 'Complaint'),
                ('suggestion', 'Suggestion'),
                ('partnership', 'Partnership'),
                ('legal', 'Legal'),
                ('hr', 'HR'),
                ('talent_requisition', 'Talent Requisition'),
                ('it_request', 'IT Request'),
                ('finance_request', 'Finance Request'),
                ('procurement', 'Procurement'),
                ('facility_request', 'Facility Request'),
                ('other', 'Other'),
            ], db_index=True, default='general', max_length=30),
        ),
        migrations.AlterField(
            model_name='enquiryroutingrule',
            name='inquiry_type',
            field=models.CharField(choices=[
                ('general', 'General Inquiry'),
                ('technical_support', 'Technical Support'),
                ('complaint', 'Complaint'),
                ('suggestion', 'Suggestion'),
                ('partnership', 'Partnership'),
                ('legal', 'Legal'),
                ('hr', 'HR'),
                ('talent_requisition', 'Talent Requisition'),
                ('it_request', 'IT Request'),
                ('finance_request', 'Finance Request'),
                ('procurement', 'Procurement'),
                ('facility_request', 'Facility Request'),
                ('other', 'Other'),
            ], max_length=30, unique=True),
        ),
    ]
