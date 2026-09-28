from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0046_receipt_recorded_date')]

    operations = [
        migrations.AddField(
            model_name='receipt', name='delivery_location',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name='receipt', name='supplier_reference',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='receipt', name='condition',
            field=models.CharField(blank=True, max_length=20, choices=[
                ('good', 'Accepted with no damage'), ('damaged', 'Damage observed'),
                ('not_inspected', 'Not inspected'),
            ]),
        ),
        migrations.AddField(
            model_name='receipt', name='delivery_status',
            field=models.CharField(blank=True, max_length=20, choices=[
                ('full', 'Full'), ('partial', 'Partial'), ('rejected', 'Rejected'),
            ]),
        ),
        migrations.AddField(
            model_name='receipt', name='exception_reason',
            field=models.TextField(blank=True, max_length=4000),
        ),
    ]
