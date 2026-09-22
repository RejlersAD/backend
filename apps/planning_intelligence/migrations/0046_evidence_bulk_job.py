from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0045_risk_impact_mitigation')]

    operations = [
        migrations.AlterField(
            model_name='planningjob', name='job_type',
            field=models.CharField(choices=[
                ('analyze', 'Analyze Documents'), ('preview', 'Preview Schedule'),
                ('generate', 'Generate Schedule'), ('build_plan', 'Build Generation Plan'),
                ('workable_plan', 'Build Workable Project Plan'), ('calculate', 'Calculate CPM'),
                ('assurance', 'Run Schedule Assurance'), ('evidence_bulk', 'Bulk Evidence Review'),
            ], max_length=16),
        ),
    ]
