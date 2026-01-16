"""
Script to seed RBAC data on Railway production database
Run this once after deploying RBAC models
"""
import os
import django

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command

if __name__ == '__main__':
    print("Starting RBAC seeding on Railway database...")
    call_command('seed_rbac')
    print("RBAC seeding completed!")
