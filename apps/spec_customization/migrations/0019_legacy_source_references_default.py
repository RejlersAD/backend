"""Keep inserts compatible with the legacy plural source-evidence column.

Some databases retain this column from a separate schema revision. Preserve its
contents and constraint; current writers omit it and require an empty default.
Fresh databases without that historical column need no schema change.
"""
from django.db import migrations


def repair_default(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    table = 'spec_customization_pipingclasscomponent'
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(cursor, table)
        if any(column.name == 'source_references' for column in columns):
            cursor.execute('ALTER TABLE spec_customization_pipingclasscomponent ALTER COLUMN source_references SET DEFAULT \'[]\'::jsonb')


class Migration(migrations.Migration):
    dependencies = [('spec_customization', '0018_component_pms_source')]
    operations = [migrations.RunPython(repair_default, migrations.RunPython.noop)]
