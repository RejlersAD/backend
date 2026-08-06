import re

from django.db import migrations, models


PR_NUMBER_PATTERN = re.compile(r'^RAD-(GEN|PRJ)-PR-(\d+)_(\d{4})$')


def seed_requisition_sequences(apps, schema_editor):
    PurchaseRequisition = apps.get_model('procurement', 'PurchaseRequisition')
    ProcurementNumberSequence = apps.get_model('procurement', 'ProcurementNumberSequence')
    maxima = {}
    for number in PurchaseRequisition.objects.values_list('pr_number', flat=True):
        match = PR_NUMBER_PATTERN.match(str(number))
        if not match:
            continue
        prefix, value, year = match.groups()
        scope = (prefix, int(year))
        maxima[scope] = max(maxima.get(scope, 0), int(value))

    for (prefix, year), last_value in maxima.items():
        ProcurementNumberSequence.objects.update_or_create(
            document_type='PR',
            prefix=prefix,
            year=year,
            defaults={'last_value': last_value},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0018_normalize_purchase_requisition_statuses'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcurementNumberSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_type', models.CharField(max_length=10)),
                ('prefix', models.CharField(max_length=10)),
                ('year', models.PositiveIntegerField()),
                ('last_value', models.PositiveBigIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'procurement_number_sequences',
                'constraints': [
                    models.UniqueConstraint(
                        fields=('document_type', 'prefix', 'year'),
                        name='proc_num_seq_scope_uniq',
                    ),
                ],
            },
        ),
        migrations.RunPython(seed_requisition_sequences, migrations.RunPython.noop),
    ]
