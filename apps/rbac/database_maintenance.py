"""Database inventory and single-table maintenance, without cascading deletes."""
import re

from django.apps import apps
from django.db import connection


SUPPORTED_ENGINES = {'postgresql', 'sqlite'}
PROTECTED_APPS = {'auth', 'users', 'contenttypes', 'sessions', 'admin'}
PROTECTED_RBAC_MODELS = {
    'organization', 'module', 'permission', 'role', 'userprofile', 'auditlog',
    'userpermissionoverride',
}


def quote_identifier(value):
    # Identifiers come from the catalog, never directly from the request. Escape
    # embedded quotes too: legitimate imported table names can contain them.
    return '"' + value.replace('"', '""') + '"'


def _postgres_table_name(schema, name):
    # Keep ordinary names readable and unusual names unambiguous, including
    # periods within an identifier instead of a schema separator.
    return '.'.join(
        part if re.fullmatch(r'[a-z_][a-z0-9_$]*', part) else quote_identifier(part)
        for part in (schema, name)
    )


def _models_and_protected_tables():
    models = {}
    protected = {'django_migrations', 'sqlite_sequence'}
    for model in apps.get_models(include_auto_created=True):
        opts = model._meta
        models[opts.db_table] = model
        owner = opts.auto_created if opts.auto_created else model
        owner_opts = owner._meta
        if owner_opts.app_label in PROTECTED_APPS or (
            owner_opts.app_label == 'rbac'
            and owner_opts.model_name in PROTECTED_RBAC_MODELS
        ):
            protected.add(opts.db_table)
            for field in opts.local_many_to_many:
                protected.add(field.remote_field.through._meta.db_table)
    return models, protected


def _postgres_catalog(cursor):
    cursor.execute("""
        SELECT c.oid, n.nspname, c.relname, c.reltuples, c.relkind,
               c.relrowsecurity,
               EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i
                       WHERE i.inhrelid = c.oid OR i.inhparent = c.oid),
               EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t
                       WHERE t.tgrelid = c.oid AND NOT t.tgisinternal),
               EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite r
                       WHERE r.ev_class = c.oid),
               EXISTS (SELECT 1 FROM pg_catalog.pg_depend d
                       WHERE d.classid = 'pg_catalog.pg_class'::regclass
                         AND d.objid = c.oid AND d.deptype = 'e'),
               has_table_privilege(c.oid, 'DELETE'),
               pg_has_role(c.relowner, 'USAGE')
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg\\_%'
        ORDER BY n.nspname, c.relname
    """)
    tables = {}
    by_oid = {}
    for (oid, schema, name, count, kind, row_security, inherited, triggers,
         rules, extension, can_delete, can_drop) in cursor.fetchall():
        identity = _postgres_table_name(schema, name)
        table = {
            'name': identity, '_table': name, '_oid': oid,
            '_sql_name': f'{quote_identifier(schema)}.{quote_identifier(name)}',
            'row_count': max(0, int(count)) if count is not None and count >= 0 else None,
            'row_count_is_estimate': True, 'referenced_by': [],
            '_special_reason': (
                'Extension-owned tables require database-level maintenance.' if extension else
                'Foreign tables require maintenance in their source database.' if kind == 'f' else
                'Partitioned or inherited tables require database-level maintenance.' if inherited or kind == 'p' else
                'Tables with row security require database-level maintenance.' if row_security else
                'Tables with custom triggers or rules require database-level maintenance.' if triggers or rules else ''
            ),
            '_can_delete': can_delete, '_can_drop': can_drop,
            '_drop_dependencies': [],
        }
        tables[identity] = table
        by_oid[oid] = table

    # The whole database is checked, including referencing tables outside the
    # application's search_path. Never let ON DELETE CASCADE select more data.
    cursor.execute("""
        SELECT con.confrelid, ns.nspname, rel.relname
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE con.contype = 'f'
    """)
    for target_oid, schema, name in cursor.fetchall():
        if target_oid in by_oid:
            by_oid[target_oid]['referenced_by'].append(_postgres_table_name(schema, name))

    cursor.execute("""
        SELECT DISTINCT d.refobjid, ns.nspname, v.relname
        FROM pg_catalog.pg_depend d
        JOIN pg_catalog.pg_rewrite r ON r.oid = d.objid
        JOIN pg_catalog.pg_class v ON v.oid = r.ev_class
        JOIN pg_catalog.pg_namespace ns ON ns.oid = v.relnamespace
        WHERE d.classid = 'pg_catalog.pg_rewrite'::regclass
          AND d.refclassid = 'pg_catalog.pg_class'::regclass
          AND v.relkind IN ('v', 'm')
    """)
    for target_oid, schema, name in cursor.fetchall():
        if target_oid in by_oid:
            by_oid[target_oid]['_drop_dependencies'].append(_postgres_table_name(schema, name))
    cursor.execute("""
        SELECT evtname FROM pg_catalog.pg_event_trigger
        WHERE evtenabled <> 'D'
          AND evtevent IN ('sql_drop', 'ddl_command_start', 'ddl_command_end')
          AND (evttags IS NULL OR 'DROP TABLE' = ANY(evttags))
    """)
    event_triggers = [row[0] for row in cursor.fetchall()]
    if event_triggers:
        for table in tables.values():
            table['_drop_special_reason'] = (
                'Database DDL triggers require database-level table deletion: ' + ', '.join(event_triggers)
            )
    return tables


def _sqlite_catalog(cursor, include_counts):
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
    rows = cursor.fetchall()
    cursor.execute("SELECT DISTINCT tbl_name FROM sqlite_master WHERE type = 'trigger'")
    triggered = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    views = [row[0] for row in cursor.fetchall()]
    cursor.execute('PRAGMA table_list')
    internal_tables = {row[1] for row in cursor.fetchall() if row[2] in {'shadow', 'virtual'}}
    tables = {}
    for name, sql in rows:
        sql_name = quote_identifier(name)
        count = None
        if include_counts:
            cursor.execute(f'SELECT COUNT(*) FROM {sql_name}')
            count = cursor.fetchone()[0]
        tables[name] = {
            'name': name, '_table': name, '_sql_name': sql_name,
            'row_count': count, 'row_count_is_estimate': False, 'referenced_by': [],
            '_special_reason': (
                'Internal database tables are protected.' if name.startswith('sqlite_') else
                'Tables with custom triggers require database-level maintenance.' if name in triggered else
                'Virtual tables and their internal tables require database-level maintenance.'
                if name in internal_tables or 'CREATE VIRTUAL TABLE' in (sql or '').upper() else ''
            ),
            '_can_delete': True, '_can_drop': True,
            # SQLite does not enforce view dependencies when dropping a table.
            # Without a dependency catalog, refuse DDL when views are present.
            '_drop_dependencies': views,
        }
    # SQLite folds ASCII letters in identifiers, including REFERENCES targets.
    # Python lower/casefold would also fold non-ASCII names that SQLite keeps distinct.
    ascii_case = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
    by_sqlite_name = {name.translate(ascii_case): table for name, table in tables.items()}
    for name, table in tables.items():
        cursor.execute(f'PRAGMA foreign_key_list({table["_sql_name"]})')
        for row in cursor.fetchall():
            target = by_sqlite_name.get(row[2].translate(ascii_case))
            if target is not None:
                target['referenced_by'].append(name)
    return tables


def table_catalog(cursor, *, can_manage, include_counts=True):
    if connection.vendor == 'postgresql':
        tables = _postgres_catalog(cursor)
    elif connection.vendor == 'sqlite':
        tables = _sqlite_catalog(cursor, include_counts)
    else:
        tables = {
            info.name: {
                'name': info.name, '_table': info.name, 'row_count': None,
                'row_count_is_estimate': False, 'referenced_by': [],
            }
            for info in connection.introspection.get_table_list(cursor)
            if info.type in {'t', 'p'}
        }
    models, protected = _models_and_protected_tables()
    for table in tables.values():
        model = models.get(table['_table'])
        table['model'] = model._meta.label if model else None
        table['managed'] = bool(model and model._meta.managed)
        table['referenced_by'] = sorted(set(table['referenced_by']))
        reason = (
            'Super-admin access is required to delete database data or tables.' if not can_manage else
            'Maintenance actions support PostgreSQL and SQLite databases only.' if connection.vendor not in SUPPORTED_ENGINES else
            'This table is required for authentication, permissions, audit history, or migrations.' if table['_table'] in protected else
            table.get('_special_reason', '')
        )
        if not reason and table['referenced_by']:
            reason = 'Referenced by: ' + ', '.join(table['referenced_by']) + '. Clear or remove dependencies first.'
        table['clear_blocked_reason'] = reason or (
            '' if table.get('_can_delete') else 'The database account cannot delete rows from this table.'
        )
        table['drop_blocked_reason'] = reason or table.get('_drop_special_reason') or (
            'Dependent views must be reviewed before deleting tables: ' + ', '.join(sorted(set(table['_drop_dependencies'])))
            if table.get('_drop_dependencies') else
            '' if table.get('_can_drop') else 'The database account does not own this table.'
        )
        table['can_clear'] = not bool(table['clear_blocked_reason'])
        table['can_drop'] = not bool(table['drop_blocked_reason'])
    return tables


def public_table(table):
    return {key: value for key, value in table.items() if not key.startswith('_')}
