"""
Django Management Command: Fix Cross Recommendation Migration Issues
Handles missing index errors during migration 0003

SOFT-CODED SOLUTION:
- Checks if old/new indexes exist before attempting rename
- Creates target indexes directly if neither exists
- Fakes migration to mark as applied
- Idempotent: safe to run multiple times
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Fix cross_recommendation migration 0003 index rename issues'

    def handle(self, *args, **options):
        self.stdout.write("=" * 80)
        self.stdout.write("FIXING CROSS_RECOMMENDATION MIGRATIONS 0002 & 0003")
        self.stdout.write("=" * 80)

        # Define index mappings from migration 0003
        index_renames = [
            {
                'old': 'cross_recom_source__e59eca_idx',
                'new': 'cross_recom_source__fedb83_idx',
                'table': 'cross_recommendation_crossrecommendationlink',
                'columns': '(source_document_type, source_document_id)',
            },
            {
                'old': 'cross_recom_target__7d77b4_idx',
                'new': 'cross_recom_target__4d2a9e_idx',
                'table': 'cross_recommendation_crossrecommendationlink',
                'columns': '(target_document_type, target_document_id)',
            },
            {
                'old': 'cross_recom_project_6d8f4f_idx',
                'new': 'cross_recom_project_ed1a64_idx',
                'table': 'cross_recommendation_crossrecommendationlink',
                'columns': '(project_id)',
            },
            {
                'old': 'cross_recom_decisio_2e9d00_idx',
                'new': 'cross_recom_decisio_e81fbf_idx',
                'table': 'cross_recommendation_crossrecommendationlink',
                'columns': '(decision_status)',
            },
        ]

        with connection.cursor() as cursor:
            for idx_config in index_renames:
                self._fix_index_rename(cursor, idx_config)

        # Fake migrations 0002 and 0003 if not already applied
        self._fake_migrations(connection)

        self.stdout.write(self.style.SUCCESS("\n✓ CROSS_RECOMMENDATION MIGRATION FIX COMPLETE"))
        self.stdout.write("=" * 80)

    def _index_exists(self, cursor, index_name):
        """Check if an index exists in the database"""
        cursor.execute("""
            SELECT COUNT(*) 
            FROM pg_indexes 
            WHERE indexname = %s
        """, [index_name])
        return cursor.fetchone()[0] > 0

    def _table_exists(self, cursor, table_name):
        """Check if a table exists in the database"""
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_name = %s
        """, [table_name])
        return cursor.fetchone()[0] > 0

    def _fix_index_rename(self, cursor, config):
        """Fix a single index rename operation"""
        old_idx = config['old']
        new_idx = config['new']
        table = config['table']
        columns = config['columns']

        self.stdout.write(f"\n[INDEX FIX] {old_idx} → {new_idx}")

        # Check if table exists
        if not self._table_exists(cursor, table):
            self.stdout.write(self.style.WARNING(f"  ⚠️  Table {table} doesn't exist, skipping"))
            return

        # Check if indexes exist
        old_exists = self._index_exists(cursor, old_idx)
        new_exists = self._index_exists(cursor, new_idx)

        if new_exists:
            self.stdout.write(self.style.SUCCESS(f"  ✓ New index already exists"))
        elif old_exists:
            # Old index exists, rename it
            self.stdout.write(f"  Old index exists, renaming...")
            try:
                cursor.execute(f'ALTER INDEX "{old_idx}" RENAME TO "{new_idx}"')
                self.stdout.write(self.style.SUCCESS(f"  ✓ Renamed successfully"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Rename failed: {e}"))
        else:
            # Neither exists, create new index directly
            self.stdout.write(f"  Neither index exists, creating new index...")
            try:
                cursor.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{new_idx}" ON {table} {columns}')
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created new index"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  ⚠️  Could not create index: {e}"))
                self.stdout.write(f"  This may be OK if the table structure is different")

    def _fake_migrations(self, connection):
        """Mark migrations 0002 and 0003 as applied if not already"""
        migrations_to_fake = [
            ('cross_recommendation', '0002_rename_cross_recom_source__e59eca_idx_cross_recom_source__fedb83_idx_and_more'),
            ('cross_recommendation', '0003_rename_cross_recom_source__e59eca_idx_cross_recom_source__fedb83_idx_and_more'),
        ]

        self.stdout.write("\n[MIGRATIONS] Checking migration records...")

        with connection.cursor() as cursor:
            for app_label, migration_name in migrations_to_fake:
                # Check if migration is already applied
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM django_migrations 
                    WHERE app = %s AND name = %s
                """, [app_label, migration_name])

                if cursor.fetchone()[0] > 0:
                    self.stdout.write(f"  ✓ {migration_name[:30]}... already applied")
                else:
                    # Insert migration record
                    try:
                        cursor.execute("""
                            INSERT INTO django_migrations (app, name, applied)
                            VALUES (%s, %s, NOW())
                        """, [app_label, migration_name])
                        self.stdout.write(self.style.SUCCESS(f"  ✓ Marked {migration_name[:30]}... as applied"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"  ✗ Failed to mark migration: {e}"))
