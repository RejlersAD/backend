"""
Django management command to migrate reports from PostgreSQL to MongoDB
"""

from django.core.management.base import BaseCommand
from apps.pid_analysis.mongodb_migration import MongoDBMigration


class Command(BaseCommand):
    help = 'Migrate P&ID analysis reports from PostgreSQL to MongoDB'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--delete-after',
            action='store_true',
            help='Delete from PostgreSQL after successful migration',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of reports to process at a time',
        )
        parser.add_argument(
            '--report-id',
            type=int,
            help='Migrate a single report by ID',
        )
        parser.add_argument(
            '--verify',
            type=int,
            help='Verify migration for a specific drawing ID',
        )
        parser.add_argument(
            '--rollback',
            type=int,
            help='Rollback migration for a specific drawing ID',
        )
    
    def handle(self, *args, **options):
        migration = MongoDBMigration()
        
        # Verify migration
        if options['verify']:
            self.stdout.write(self.style.WARNING(f'Verifying drawing {options["verify"]}...'))
            results = migration.verify_migration(options['verify'])
            
            for key, value in results.items():
                if isinstance(value, bool):
                    status = '✅' if value else '❌'
                    self.stdout.write(f'{status} {key}: {value}')
                else:
                    self.stdout.write(f'  {key}: {value}')
            
            return
        
        # Rollback migration
        if options['rollback']:
            self.stdout.write(self.style.WARNING(f'Rolling back drawing {options["rollback"]}...'))
            success = migration.rollback_migration(options['rollback'])
            
            if success:
                self.stdout.write(self.style.SUCCESS('✅ Rollback successful'))
            else:
                self.stdout.write(self.style.ERROR('❌ Rollback failed'))
            
            return
        
        # Migrate single report
        if options['report_id']:
            self.stdout.write(self.style.WARNING(f'Migrating report {options["report_id"]}...'))
            success = migration.migrate_single_report(
                options['report_id'],
                options['delete_after']
            )
            
            if success:
                self.stdout.write(self.style.SUCCESS('✅ Migration successful'))
            else:
                self.stdout.write(self.style.ERROR('❌ Migration failed'))
            
            return
        
        # Migrate all reports
        self.stdout.write(self.style.WARNING('Starting migration of all reports...'))
        
        if options['delete_after']:
            self.stdout.write(self.style.ERROR(
                '⚠️  WARNING: PostgreSQL reports will be DELETED after migration!'
            ))
            confirm = input('Type "yes" to continue: ')
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.WARNING('Migration cancelled'))
                return
        
        stats = migration.migrate_all_reports(
            options['batch_size'],
            options['delete_after']
        )
        
        # Display results
        self.stdout.write(self.style.SUCCESS('✅ Migration complete!'))
        self.stdout.write(f'📊 Total reports: {stats["total"]}')
        self.stdout.write(self.style.SUCCESS(f'✅ Migrated: {stats["migrated"]}'))
        self.stdout.write(self.style.WARNING(f'⏭️  Skipped: {stats["skipped"]}'))
        
        if stats['failed'] > 0:
            self.stdout.write(self.style.ERROR(f'❌ Failed: {stats["failed"]}'))
