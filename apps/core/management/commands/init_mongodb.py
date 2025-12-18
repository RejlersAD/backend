"""
Django management command to initialize MongoDB
Creates indexes and verifies connection
"""

from django.core.management.base import BaseCommand
from apps.core.mongodb_client import mongodb_client


class Command(BaseCommand):
    help = 'Initialize MongoDB: create indexes and verify connection'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-indexes',
            action='store_true',
            help='Skip index creation',
        )
    
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Initializing MongoDB...'))
        
        try:
            # Test connection
            if mongodb_client.ping():
                self.stdout.write(self.style.SUCCESS('✅ MongoDB connection successful'))
            else:
                self.stdout.write(self.style.ERROR('❌ MongoDB connection failed'))
                return
            
            # Get stats
            stats = mongodb_client.get_stats()
            self.stdout.write(self.style.SUCCESS(f'📊 Database: {stats.get("database")}'))
            self.stdout.write(self.style.SUCCESS(f'📦 Collections: {stats.get("collections")}'))
            self.stdout.write(self.style.SUCCESS(f'📄 Documents: {stats.get("objects")}'))
            
            # Create indexes
            if not options['skip_indexes']:
                self.stdout.write(self.style.WARNING('Creating indexes...'))
                mongodb_client.create_indexes()
                self.stdout.write(self.style.SUCCESS('✅ Indexes created'))
            
            self.stdout.write(self.style.SUCCESS('🎉 MongoDB initialization complete!'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error: {str(e)}'))
