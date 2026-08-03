"""
Production Migration Runner - Run migrations in production without console encoding issues
"""
import os
import django
import sys

# Minimal setup to avoid Unicode print issues
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Disable Unicode output
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='ascii', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='ascii', errors='replace')

django.setup()

from django.core.management import call_command
from django.db import connection

print("=" * 80)
print("PRODUCTION MIGRATION RUNNER")
print("=" * 80)

# Check current migration status
print("\nChecking procurement migrations...")
try:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name, applied 
            FROM django_migrations 
            WHERE app = 'procurement'
            ORDER BY name DESC 
            LIMIT 5
        """)
        migrations = cursor.fetchall()
        
        print(f"\nLatest migrations in database:")
        for name, applied in migrations:
            print(f"  - {name}")
        
        # Check if critical migrations exist
        cursor.execute("""
            SELECT COUNT(*) 
            FROM django_migrations 
            WHERE app = 'procurement' 
            AND (name LIKE '%0013%' OR name LIKE '%0014%')
        """)
        count = cursor.fetchone()[0]
        
        if count >= 2:
            print(f"\n[OK] Migrations 0013 and 0014 already applied")
        else:
            print(f"\n[WARNING] Migrations 0013 and/or 0014 NOT applied")
            print(f"[ACTION] Running migrations now...")
            
            # Run migrations
            call_command('migrate', 'procurement', verbosity=2)
            
            print(f"\n[SUCCESS] Migrations completed")
            
except Exception as e:
    print(f"\n[ERROR] Migration failed: {e}")
    sys.exit(1)

# Verify tables exist
print("\nVerifying tables...")
try:
    with connection.cursor() as cursor:
        # Check if vendor_id column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'procurement_purchaserequisition'
            AND column_name = 'vendor_id'
        """)
        
        if cursor.fetchone():
            print("[OK] Column 'vendor_id' exists in procurement_purchaserequisition")
        else:
            print("[ERROR] Column 'vendor_id' MISSING from procurement_purchaserequisition")
            print("[ACTION] Migration may have failed - check manually")
            
except Exception as e:
    print(f"[ERROR] Table verification failed: {e}")

print("\n" + "=" * 80)
print("MIGRATION CHECK COMPLETE")
print("=" * 80)
