from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('planning_intelligence', '0023_operational_reliability')]

    operations = [
        migrations.AddField(
            model_name='scheduleassurancereview', name='input_fingerprint',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='planningjob', name='job_type',
            field=models.CharField(choices=[('analyze', 'Analyze Documents'), ('preview', 'Preview Schedule'), ('generate', 'Generate Schedule'), ('calculate', 'Calculate CPM'), ('assurance', 'Run Schedule Assurance')], max_length=16),
        ),
    ]
