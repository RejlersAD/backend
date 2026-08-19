from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('qhse', '0003_alter_qhserunningproject_project_quality_eng'),
    ]

    operations = [
        migrations.AlterField(
            model_name='qhserunningproject',
            name='quality_billability_percent',
            field=models.CharField(default='0%', max_length=20, verbose_name='Quality Billability Percentage'),
        ),
        migrations.AlterField(
            model_name='qhserunningproject',
            name='project_kpis_achieved_percent',
            field=models.CharField(default='0%', max_length=20, verbose_name='Project KPIs Achieved (%)'),
        ),
        migrations.AlterField(
            model_name='qhserunningproject',
            name='project_completion_percent',
            field=models.CharField(default='0%', max_length=20, verbose_name='Project Completion (%)'),
        ),
        migrations.AlterField(
            model_name='qhserunningproject',
            name='rejection_of_deliverables_percent',
            field=models.CharField(blank=True, max_length=20, null=True, verbose_name='Rejection of Deliverables (%)'),
        ),
    ]
