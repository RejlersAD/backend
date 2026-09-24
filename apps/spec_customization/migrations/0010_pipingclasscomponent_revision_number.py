# Generated for REV NO source-column capture (soft-coded alignment fix)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spec_customization', '0009_workbookcelloverride_provenance'),
    ]

    operations = [
        migrations.AddField(
            model_name='pipingclasscomponent',
            name='revision_number',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
