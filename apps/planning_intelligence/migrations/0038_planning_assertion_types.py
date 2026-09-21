from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0037_planning_profiles')]

    operations = [
        migrations.AlterField(
            model_name='intelligencefact', name='fact_type',
            field=models.CharField(max_length=32, db_index=True, choices=[
                ('project_name', 'Project Name'), ('effective_date', 'Effective Date'),
                ('duration_months', 'Duration Months'), ('client', 'Client'),
                ('location', 'Location'), ('discipline', 'Discipline'),
                ('deliverable', 'Deliverable'), ('hse_study', 'HSE Study'),
                ('milestone', 'Milestone'), ('calendar', 'Calendar'),
                ('review_cycle', 'Review Cycle'), ('requirement', 'Requirement'),
                ('exclusion', 'Exclusion'), ('constraint', 'Constraint'),
                ('package', 'Work Package'), ('responsibility', 'Responsibility'),
                ('resource_requirement', 'Resource Requirement'),
                ('dependency', 'Dependency Statement'), ('risk', 'Risk'),
            ]),
        ),
    ]
