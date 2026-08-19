"""Repair columns skipped by the previously merged migration branches.

The migration state already contains these fields, but some databases recorded
the branch migration as applied without receiving the physical columns.  This
operation is intentionally idempotent and leaves existing columns/data intact.
"""

from django.db import migrations


FIELD_NAMES = (
    'current_approval_level',
    'po_completion_verified',
    'po_linked',
    'po_manually_entered',
)


def add_missing_columns(apps, schema_editor):
    Requisition = apps.get_model('procurement', 'PurchaseRequisition')
    table_name = Requisition._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor, table_name
        )
    existing_columns = {column.name for column in description}

    for field_name in FIELD_NAMES:
        field = Requisition._meta.get_field(field_name)
        if field.column not in existing_columns:
            schema_editor.add_field(Requisition, field)
            existing_columns.add(field.column)


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0022_rename_procurement_budget_idx1_procurement_project_cf65fc_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(add_missing_columns, migrations.RunPython.noop),
    ]
