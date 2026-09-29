"""
Management command: fix out-of-sync PostgreSQL sequences.

After a data import / restore / fixture load that inserts rows with explicit
primary keys, the underlying auto-increment sequences keep their old value
and the next INSERT fails with:
    IntegrityError: duplicate key value violates unique constraint "<table>_pkey"
    DETAIL: Key (id)=(NN) already exists.

This command resets every sequence to max(pk) so inserts work again.

Usage:
    python manage.py fix_db_sequences            # all tables
    python manage.py fix_db_sequences --table users   # one table
    python manage.py fix_db_sequences --dry-run       # report only
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Reset PostgreSQL sequences that are out of sync with their table data.'

    def add_arguments(self, parser):
        parser.add_argument('--table', help='Fix only this table')
        parser.add_argument('--dry-run', action='store_true', help='Report without changing')

    def handle(self, *args, **options):
        only_table = options.get('table')
        dry_run = options.get('dry_run')

        with connection.cursor() as cursor:
            # Find every sequence owned by a table column
            cursor.execute("""
                SELECT
                    t.relname  AS table_name,
                    a.attname  AS column_name,
                    s.relname  AS sequence_name
                FROM pg_class s
                JOIN pg_depend d   ON d.objid = s.oid
                JOIN pg_class t    ON d.refobjid = t.oid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                WHERE s.relkind = 'S'
                  AND t.relkind = 'r'
                ORDER BY t.relname
            """)
            rows = cursor.fetchall()

            fixed = 0
            for table_name, column_name, sequence_name in rows:
                if only_table and table_name != only_table:
                    continue
                cursor.execute(f'SELECT MAX("{column_name}") FROM "{table_name}"')
                max_id = cursor.fetchone()[0]
                if max_id is None:
                    continue
                cursor.execute(f'SELECT last_value FROM "{sequence_name}"')
                last_value = cursor.fetchone()[0]
                if last_value >= max_id:
                    continue  # sequence is fine
                self.stdout.write(
                    f'  {table_name}.{column_name}: seq={last_value} < max(id)={max_id} -> reset'
                )
                if not dry_run:
                    cursor.execute(
                        f"SELECT setval('\"{sequence_name}\"', %s, true)", [max_id]
                    )
                    fixed += 1

            verb = 'would fix' if dry_run else 'fixed'
            self.stdout.write(self.style.SUCCESS(f'Done — {verb} {fixed} sequence(s).'))
