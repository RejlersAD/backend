from django.db import migrations


LEGACY_ID_INDEXES = (
    ('planning_intelligence_planningproject', 'planning_project_id_uniq'),
    ('planning_intelligence_planninggeneration', 'planning_generation_id_uniq'),
    ('planning_intelligence_planningfile', 'planning_file_id_uniq'),
)


def repair_duplicate_ids(cursor, quote_name, table_name):
    """Preserve imported rows by re-keying duplicate/null legacy ids."""
    table = quote_name(table_name)
    cursor.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
    cursor.execute(
        f'''
        WITH ranked AS (
            SELECT
                ctid,
                id,
                ROW_NUMBER() OVER (PARTITION BY id ORDER BY ctid) AS duplicate_rank,
                MAX(id) OVER () AS max_id
            FROM {table}
        ),
        rekey AS (
            SELECT
                ctid,
                COALESCE(max_id, 0)
                    + ROW_NUMBER() OVER (ORDER BY id NULLS FIRST, ctid) AS new_id
            FROM ranked
            WHERE id IS NULL OR duplicate_rank > 1
        )
        UPDATE {table} AS target
        SET id = rekey.new_id
        FROM rekey
        WHERE target.ctid = rekey.ctid
        ''',
    )

    cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [table_name, 'id'])
    sequence_name = cursor.fetchone()[0]
    if sequence_name:
        cursor.execute(
            f'''
            SELECT setval(
                %s::regclass,
                COALESCE(MAX(id), 1),
                EXISTS (SELECT 1 FROM {table})
            )
            FROM {table}
            ''',
            [sequence_name],
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
            repair_duplicate_ids(cursor, quote_name, table_name)
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
