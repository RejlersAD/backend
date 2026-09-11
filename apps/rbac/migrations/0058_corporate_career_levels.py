from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('rbac', '0057_verify_override_reference_keys')]
    operations = [
        migrations.AlterField(
            model_name='engineerprofile', name='expertise_level',
            field=models.CharField(blank=True, max_length=20, choices=[
                ('junior', 'Junior'), ('mid', 'Mid-Level'), ('senior', 'Senior'),
                ('principal', 'Principal'), ('lead', 'Lead'), ('manager', 'Engineering Manager'),
                ('fellow', 'Engineering Fellow'), ('corp_associate', 'Associate'),
                ('corp_specialist', 'Specialist'), ('corp_senior', 'Senior Specialist'),
                ('corp_lead', 'Team Lead'), ('corp_manager', 'Manager'), ('corp_head', 'Department Head'),
            ]),
        ),
    ]
