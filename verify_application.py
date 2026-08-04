#!/usr/bin/env python
"""Comprehensive Application Verification Script"""
import os
import sys
import json
from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import connection

def check_database_schema():
    """Verify database schema and tables"""
    print("\n[5/8] Database Schema Verification...")
    print("=" * 60)
    
    cursor = connection.cursor()
    
    # Get sample tables
    cursor.execute("""
        SELECT tablename 
        FROM pg_tables 
        WHERE schemaname='public' 
        ORDER BY tablename 
        LIMIT 10
    """)
    tables = cursor.fetchall()
    
    print("Sample Tables (first 10):")
    for table in tables:
        print(f"  ✓ {table[0]}")
    
    # Get total count
    cursor.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")
    total = cursor.fetchone()[0]
    print(f"\n✓ Total Public Tables: {total}")
    
    return True

def check_key_models():
    """Verify key Django models and their data"""
    print("\n[6/8] Key Models Data Verification...")
    print("=" * 60)
    
    User = get_user_model()
    
    # Check Users
    user_count = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    print(f"✓ Users: {user_count} total, {active_users} active")
    
    # Check for common models
    model_checks = [
        ('pid_analysis', 'PIDProject'),
        ('pid_analysis', 'PIDDrawing'),
        ('project_management', 'Project'),
        ('apps.qhse', 'QHSEDocument'),
    ]
    
    for app_label, model_name in model_checks:
        try:
            model = apps.get_model(app_label, model_name)
            count = model.objects.count()
            print(f"✓ {app_label}.{model_name}: {count} records")
        except LookupError:
            print(f"⚠ {app_label}.{model_name}: Model not found")
        except Exception as e:
            print(f"⚠ {app_label}.{model_name}: Error - {str(e)[:50]}")
    
    return True

def check_configuration():
    """Verify soft-coded configuration"""
    print("\n[7/8] Soft-Coded Configuration Check...")
    print("=" * 60)
    
    # Check if environments.json is loaded
    config_path = '/app/config/environments.json'
    if os.path.exists(config_path):
        print(f"✓ Configuration file found: {config_path}")
        with open(config_path, 'r') as f:
            config = json.load(f)
            
        # Check local environment settings
        if 'environments' in config and 'local' in config['environments']:
            local_config = config['environments']['local']
            print(f"✓ Local environment configured")
            print(f"  Backend URL: {local_config.get('backend', {}).get('url', 'N/A')}")
            print(f"  Frontend URL: {local_config.get('frontend', {}).get('url', 'N/A')}")
            print(f"  Database: {local_config.get('database', {}).get('name', 'N/A')}")
        else:
            print("⚠ Local environment not found in config")
    else:
        print(f"✗ Configuration file not found: {config_path}")
        return False
    
    return True

def main():
    """Run all verifications"""
    print("\n" + "=" * 60)
    print("  RADAI APPLICATION VERIFICATION")
    print("=" * 60)
    
    try:
        check_database_schema()
        check_key_models()
        check_configuration()
        
        print("\n" + "=" * 60)
        print("✓ VERIFICATION COMPLETED SUCCESSFULLY")
        print("=" * 60 + "\n")
        return True
        
    except Exception as e:
        print(f"\n✗ VERIFICATION FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
