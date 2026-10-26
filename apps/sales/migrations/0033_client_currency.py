from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0032_letter_fingerprints')]

    operations = [migrations.AddField(
        model_name='client', name='currency',
        field=models.CharField(max_length=3, blank=True, default=''),
    )]
