"""
Fake the problematic migration on Railway
Run this on Railway to fix the migration conflict
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.management import call_command

# Fake the migration that's already applied in the database
print("🔧 Faking pid_analysis.0002_piddrawing_error_message_alter_piddrawing_file...")
call_command('migrate', 'pid_analysis', '0002', '--fake')

print("✅ Migration faked successfully!")
print("🚀 Now run: python manage.py migrate")
