"""
Management command to aggregate usage statistics manually.

Usage:
    python manage.py aggregate_usage_stats
"""

from django.core.management.base import BaseCommand
from apps.usage_tracking.tasks import run_all_sync


class Command(BaseCommand):
    help = 'Aggregate usage tracking statistics into summary tables'
    
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting usage statistics aggregation...'))
        
        run_all_sync()
        
        self.stdout.write(self.style.SUCCESS('✅ Usage statistics aggregation completed!'))
