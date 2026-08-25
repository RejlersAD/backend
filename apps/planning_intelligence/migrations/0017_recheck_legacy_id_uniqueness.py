from django.db import migrations


LEGACY_ID_INDEXES = (
    ('planning_intelligence_planningproject', 'planning_project_id_uniq'),
    ('planning_intelligence_planninggeneration', 'planning_generation_id_uniq'),
    ('planning_intelligence_planningfile', 'planning_file_id_uniq'),
)


def restore_legacy_id_uniqueness(apps, schema_editor):
    """Recheck imported tables even when the historical 0005 was faked."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    quote_name = connection.ops.quote_name
    existing_tables = set(connection.introspection.table_names())
    with connection.cursor() as cursor:
        for table_name, index_name in LEGACY_ID_INDEXES:
            if table_name not in existing_tables:
                continue
            constraints = connection.introspection.get_constraints(cursor, table_name)
            id_is_unique = any(
                constraint['unique'] and constraint['columns'] == ['id']
                for constraint in constraints.values()
            )
            if not id_is_unique:
                schema_editor.execute(
                    f'CREATE UNIQUE INDEX {quote_name(index_name)} '
                    f'ON {quote_name(table_name)} ({quote_name("id")})'
                )


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0016_project_date_range'),
    ]

    operations = [
        migrations.RunPython(
            code=restore_legacy_id_uniqueness,
            # Existing PK/unique indexes may predate this migration. Never
            # remove an ownership constraint during a targeted rollback.
            reverse_code=migrations.RunPython.noop,
        ),
    ]
