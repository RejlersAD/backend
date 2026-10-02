"""Conservative table reconciliation for historical migration divergence.

Used by forward-only repair migrations. Never replace an existing table, infer
business data, or mark an incompatible schema as healthy. Keep this helper
compatible with its checked-in migration callers.
"""
from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError


def ensure_declared_table(model, schema_editor):
    """Create an absent declared table, or validate a retained existing table."""
    connection = schema_editor.connection
    table = model._meta.db_table
    with connection.cursor() as cursor:
        if table not in connection.introspection.table_names(cursor):
            schema_editor.create_model(model)
            return
        columns = {item.name: item for item in connection.introspection.get_table_description(cursor, table)}
        constraints = connection.introspection.get_constraints(cursor, table)
        postgres_columns = {}
        postgres_checks = []
        if connection.vendor == 'postgresql':
            cursor.execute(
                'SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attidentity, '
                'pg_get_expr(d.adbin, d.adrelid), pg_get_serial_sequence(%s, a.attname) '
                'FROM pg_attribute a LEFT JOIN pg_attrdef d '
                'ON d.adrelid = a.attrelid AND d.adnum = a.attnum '
                'WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped', [table, table])
            postgres_columns = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                           "WHERE conrelid = %s::regclass AND contype = 'c'", [table])
            postgres_checks = [row[0] for row in cursor.fetchall()]

    def require(condition, detail):
        if not condition:
            raise RuntimeError(f'Existing table {table} is incompatible ({detail}); preserve it and repair explicitly.')

    for field in model._meta.local_fields:
        column = field.column
        require(column in columns, f'missing column {column}')
        require(columns[column].null_ok == field.null, f'nullability for {column}')
        if postgres_columns:
            declared_type, identity, default, sequence = postgres_columns[column]
            actual_type = declared_type.replace('character varying', 'varchar')
            require(actual_type == field.db_type(connection), f'type for {column}')
            if field.get_internal_type() in {'AutoField', 'BigAutoField', 'SmallAutoField'}:
                require(identity in {'a', 'd'} or (sequence and (default or '').startswith('nextval(')),
                        f'automatic identity/sequence for {column}')
        matching = [item for item in constraints.values() if item['columns'] == [column]]
        if field.primary_key:
            require(any(item['primary_key'] for item in matching), f'primary key {column}')
        elif field.unique:
            require(any(item['unique'] for item in matching), f'unique key {column}')
        if field.db_index:
            require(any(item.get('index') or item['unique'] or item['primary_key'] for item in matching),
                    f'index for {column}')
        if field.is_relation and field.db_constraint:
            target = (field.remote_field.model._meta.db_table, field.target_field.column)
            require(any(item.get('foreign_key') == target for item in matching), f'foreign key {column}')
        check = field.db_parameters(connection).get('check')
        if check:
            if postgres_columns:
                def normalized(expression):
                    return ''.join(character for character in expression.lower()
                                   if not character.isspace() and character not in '()"')
                expected_check = normalized(check)
                require(any(normalized(expression) == f'check{expected_check}' for expression in postgres_checks),
                        f'check definition for {column}')
            else:
                require(any(item.get('check') and column in item['columns'] for item in constraints.values()),
                        f'check for {column}')
    for index in model._meta.indexes:
        expected = [model._meta.get_field(name.lstrip('-')).column for name in index.fields]
        require(any(item.get('index') and item['columns'] == expected for item in constraints.values()),
                f'index {index.name}')


class CreateModelIfMissing(migrations.CreateModel):
    """Add normal model state while preserving a compatible preexisting table.

Reversal cannot safely infer who created the table. Retain its schema/data and
use a reviewed forward fix instead of deleting potentially preexisting facts.
"""
    reversible = False

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            ensure_declared_table(model, schema_editor)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        raise IrreversibleError('Retain reconciled tables and their data; use a reviewed forward fix.')
