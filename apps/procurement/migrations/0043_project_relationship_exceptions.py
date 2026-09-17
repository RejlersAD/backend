from uuid import UUID

from django.db import migrations, models


def normalize_existing_record_ids(apps, schema_editor):
    """SQLite stores UUIDs without hyphens; retain the public identity on both DBs."""
    Resolution = apps.get_model('procurement', 'ProjectRelationshipResolution')
    for row in Resolution.objects.using(schema_editor.connection.alias).all().iterator():
        normalized = str(UUID(str(row.record_id)))
        if normalized != row.record_id:
            Resolution.objects.using(schema_editor.connection.alias).filter(pk=row.pk).update(record_id=normalized)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0042_optional_confirmed_vat_treatment')]

    operations = [
        migrations.AlterField(
            model_name='projectrelationshipresolution', name='record_id',
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.RunPython(normalize_existing_record_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='projectrelationshipresolution', name='record_type',
            field=models.CharField(db_index=True, max_length=30, choices=[
                ('procurement_project', 'Procurement Project'),
                ('purchase_requisition', 'Purchase Requisition'),
                ('purchase_order', 'Purchase Order'), ('invoice', 'Invoice'),
            ]),
        ),
        migrations.AlterField(
            model_name='projectrelationshipresolution', name='resolution',
            field=models.CharField(default='manual', max_length=20, choices=[
                ('manual', 'Manual'), ('propagated', 'Propagated'), ('exception', 'Exception'),
            ]),
        ),
    ]
