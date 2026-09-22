from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0032_simple_planning_state')]

    operations = [
        migrations.AddField(
            model_name='documentprofile', name='extraction_coverage',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='plandeliverable', name='workflow_family',
            field=models.CharField(max_length=32, choices=[
                ('not_specified', 'Not Specified'), ('engineering_document', 'Engineering Document'),
                ('inspection_report', 'Inspection Report'), ('technical_study', 'Technical Study'),
                ('drawing', 'Drawing'), ('plan_procedure', 'Plan / Procedure'),
                ('recurring_report', 'Recurring Report'), ('tender_package', 'Tender Package'),
                ('final_dossier', 'Final Dossier'), ('cost_estimate', 'Cost Estimate'),
            ]),
        ),
    ]
