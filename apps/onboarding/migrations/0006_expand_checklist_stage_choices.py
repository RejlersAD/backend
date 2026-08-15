from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('onboarding', '0005_checklist_stage'),
    ]

    operations = [
        migrations.AlterField(
            model_name='checklist',
            name='stage',
            field=models.CharField(
                choices=[
                    ('general', 'General'),
                    ('pre_hire', 'Pre-Hire Initiation'),
                    ('it_provisioning', 'IT Provisioning'),
                    ('first_day', 'First Day Orientation'),
                    ('final_validation', 'Final Checklist Validation'),
                    ('exit_initiation', 'Exit Initiation'),
                    ('access_revocation', 'Access Revocation'),
                    ('asset_return', 'Asset Return'),
                    ('exit_clearance', 'Exit Interview & Clearance'),
                    ('final_settlement', 'Final Settlement'),
                ],
                default='general',
                max_length=40,
            ),
        ),
    ]
