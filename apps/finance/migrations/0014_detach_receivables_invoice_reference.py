"""Detach legacy source-row relationships without changing invoice identities.

Pending installations get a scalar provenance column directly from 0012. The
same migration may already have created a physical FK on other installations,
so compare the actual schema instead of the revised historical model state.
"""
from django.db import migrations, models


def detach_register_invoice_reference(apps, schema_editor):
    connection = schema_editor.connection
    row = apps.get_model('finance', 'ReceivablesSourceRow')
    invoice = apps.get_model('invoice_tracker', 'CustomerInvoice')
    field = row._meta.get_field('register_invoice_id')
    table, column = row._meta.db_table, field.column
    target = (invoice._meta.db_table, invoice._meta.pk.column)
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    foreign_keys = [
        name for name, constraint in constraints.items()
        if constraint.get('columns') == [column] and constraint.get('foreign_key') == target
    ]
    if not foreign_keys:
        return

    if connection.vendor == 'sqlite':
        # SQLite needs a table rebuild. Supply the original field explicitly:
        # the migration state already has the scalar version from revised 0012.
        # Preserve extra explicit indexes/triggers as well as the model's own
        # snapshot FK, unique row constraint, indexes, and primary key.
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT name, sql FROM sqlite_master
                WHERE tbl_name = %s AND type IN ('index', 'trigger') AND sql IS NOT NULL
            ''', [table])
            extra_objects = cursor.fetchall()
        legacy_field = models.ForeignKey(
            invoice, null=True, blank=True, on_delete=models.SET_NULL,
            related_name='+', db_column=column,
        )
        legacy_field.set_attributes_from_name('register_invoice')
        legacy_field.model = row
        schema_editor.alter_field(row, legacy_field, field)
        with connection.cursor() as cursor:
            cursor.execute('SELECT name FROM sqlite_master WHERE tbl_name = %s', [table])
            existing_names = {name for name, in cursor.fetchall()}
        for name, sql in extra_objects:
            if name not in existing_names:
                schema_editor.execute(sql)
        return

    # PostgreSQL can drop exactly the obsolete source-row FK. Its column,
    # index, existing values, snapshot relationship, and all invoice-side
    # constraints remain intact. Other backends use their own DROP FK syntax.
    for name in foreign_keys:
        schema_editor.execute(schema_editor.sql_delete_fk % {
            'table': schema_editor.quote_name(table),
            'name': schema_editor.quote_name(name),
        })


class Migration(migrations.Migration):
    dependencies = [('finance', '0013_verify_customer_invoice_reference_key')]
    # Recreating an invoice FK on rollback would be unsafe when its target IDs
    # are not unique. Provenance values remain preserved in both directions.
    operations = [migrations.RunPython(detach_register_invoice_reference, migrations.RunPython.noop)]
