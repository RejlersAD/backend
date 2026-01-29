"""
Django management command to wait for database availability
Useful for Docker container startup synchronization
"""

import time
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = 'Wait for database to be available'

    def handle(self, *args, **options):
        self.stdout.write('Waiting for database...')
        db_conn = None
        retries = 30
        
        while retries > 0 and db_conn is None:
            try:
                db_conn = connections['default']
                db_conn.ensure_connection()
                self.stdout.write(self.style.SUCCESS('Database available!'))
            except OperationalError:
                retries -= 1
                self.stdout.write(f'Database unavailable, waiting... ({retries} retries left)')
                time.sleep(1)
        
        if db_conn is None:
            self.stdout.write(self.style.ERROR('Database connection failed!'))
            exit(1)
