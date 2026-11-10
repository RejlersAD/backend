from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('spec_customization', '0016_import_pilot')]

    operations = [
        migrations.AlterField(
            model_name='componentmatchdecision',
            name='review_decision',
            field=models.CharField(blank=True, db_index=True, default='', max_length=16,
                                   choices=[('approved', 'Approved'), ('rejected', 'Rejected'),
                                            ('remapped', 'Remapped'), ('clarification', 'Clarification requested')]),
        ),
    ]
