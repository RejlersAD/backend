"""
Automated Import Script for PO and PR Excel Data
Migrates both PO_Generated.xlsx and PO_PR_Data.xlsx to PostgreSQL

USAGE:
    python backend/_import_po_pr_data.py [--dry-run] [--production]
"""

import os
import sys
import django
from pathlib import Path

# Setup Django environment
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.core.management import call_command
from django.db import connection
from config.environments import get_current_environment_name

# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

EXCEL_FILES_CONFIG = {
    'po_generated': {
        'file_path': r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Procurement\PO_Generated.xlsx',
        'type': 'po',
        'description': 'Generated Purchase Orders',
        'sheet': 0,  # First sheet
    },
    'po_pr_data': {
        'file_path': r'C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\Procurement\PO_PR_Data.xlsx',
        'type': 'auto',  # Auto-detect whether PO or PR
        'description': 'Purchase Orders and Purchase Requisitions Data',
        'sheet': 0,  # First sheet
    }
}


def print_banner(text):
    """Print styled banner"""
    print("\n" + "=" * 100)
    print(f"  {text}")
    print("=" * 100 + "\n")


def check_file_exists(file_path):
    """Check if Excel file exists"""
    if not os.path.exists(file_path):
        print(f"❌ ERROR: File not found: {file_path}")
        return False
    return True


def get_database_info():
    """Get current database connection info"""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user;")
        db_name, db_user = cursor.fetchone()
    
    env = get_current_environment_name()
    
    return {
        'database': db_name,
        'user': db_user,
        'environment': env,
        'host': connection.settings_dict.get('HOST', 'localhost'),
        'port': connection.settings_dict.get('PORT', '5432'),
    }


def confirm_import(db_info, dry_run=False):
    """Confirm with user before running import"""
    print("\n📋 DATABASE CONNECTION INFORMATION:")
    print(f"   Environment: {db_info['environment']}")
    print(f"   Database: {db_info['database']}")
    print(f"   Host: {db_info['host']}:{db_info['port']}")
    print(f"   User: {db_info['user']}")
    print(f"   Mode: {'DRY RUN (No Changes)' if dry_run else 'LIVE IMPORT (Will Modify Database)'}")
    
    if not dry_run:
        print("\n⚠️  WARNING: This will import data into the database!")
        response = input("\nProceed with import? (yes/no): ")
        if response.lower() != 'yes':
            print("❌ Import cancelled by user")
            return False
    
    return True


def import_excel_file(file_key, config, dry_run=False):
    """Import a single Excel file"""
    print_banner(f"Importing: {config['description']}")
    
    file_path = config['file_path']
    
    # Check file exists
    if not check_file_exists(file_path):
        return False
    
    print(f"📄 File: {file_path}")
    print(f"📊 Type: {config['type']}")
    print(f"📃 Sheet: {config['sheet']}")
    print("")
    
    # Run Django management command
    try:
        call_command(
            'import_po_pr_excel',
            file=file_path,
            type=config['type'],
            sheet=str(config['sheet']),
            dry_run=dry_run,
        )
        return True
    except Exception as e:
        print(f"\n❌ ERROR importing {file_key}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main import orchestration"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Import PO and PR data from Excel to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode (no database changes)')
    parser.add_argument('--production', action='store_true', help='Allow import to production database')
    parser.add_argument('--file', type=str, help='Import specific file only (po_generated or po_pr_data)')
    
    args = parser.parse_args()
    
    # Banner
    print_banner("🚀 PO/PR EXCEL DATA MIGRATION TOOL")
    
    # Get database info
    db_info = get_database_info()
    
    # Safety check for production
    if db_info['environment'] == 'production' and not args.production:
        print("❌ ERROR: Cannot import to production database without --production flag")
        print("   This is a safety measure to prevent accidental data imports to production.")
        print("   If you are sure, run with: python _import_po_pr_data.py --production")
        sys.exit(1)
    
    # Confirm with user
    if not confirm_import(db_info, args.dry_run):
        sys.exit(0)
    
    # Determine which files to import
    if args.file:
        if args.file not in EXCEL_FILES_CONFIG:
            print(f"❌ ERROR: Unknown file key '{args.file}'")
            print(f"   Available: {', '.join(EXCEL_FILES_CONFIG.keys())}")
            sys.exit(1)
        files_to_import = {args.file: EXCEL_FILES_CONFIG[args.file]}
    else:
        files_to_import = EXCEL_FILES_CONFIG
    
    # Import each file
    results = {}
    for file_key, config in files_to_import.items():
        success = import_excel_file(file_key, config, args.dry_run)
        results[file_key] = success
    
    # Final summary
    print_banner("📊 FINAL SUMMARY")
    
    for file_key, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"  {status}: {EXCEL_FILES_CONFIG[file_key]['description']}")
    
    total_success = sum(1 for s in results.values() if s)
    total_files = len(results)
    
    print(f"\n  Total: {total_success}/{total_files} files imported successfully")
    
    if args.dry_run:
        print("\n⚠️  DRY RUN MODE - No changes were made to the database")
        print("   Run without --dry-run to perform actual import")
    else:
        print("\n✅ Import process completed!")
        print("   Check the summaries above for details on each file import")
    
    print("=" * 100 + "\n")
    
    # Exit code
    sys.exit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    main()
