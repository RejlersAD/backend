"""
PRODUCTION FIX SCRIPT
====================
This script must be uploaded to production and run via Railway shell.

STEPS TO USE:
1. Upload this file and procurement_export.json to production
2. Run: python production_complete_fix.py

This will:
- Apply missing migrations
- Fix schema if migrations fail
- Import all data from local database
- Verify everything works
"""

import os
import sys
import json
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from django.db import connection, transaction
from django.core.management import call_command
from django.contrib.auth import get_user_model
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from decimal import Decimal
from datetime import datetime

User = get_user_model()


def print_section(title):
    """Print formatted section header"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def check_migrations():
    """Check if critical migrations exist"""
    print_section("STEP 1: Checking Migrations")
    
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name 
            FROM django_migrations 
            WHERE app = 'procurement'
            AND (name LIKE '%0013%' OR name LIKE '%0014%')
        """)
        applied = [row[0] for row in cursor.fetchall()]
        
        has_0013 = any('0013' in m for m in applied)
        has_0014 = any('0014' in m for m in applied)
        
        print(f"  Migration 0013 (vendor integration): {'✓ APPLIED' if has_0013 else '✗ MISSING'}")
        print(f"  Migration 0014 (vendor recommendations): {'✓ APPLIED' if has_0014 else '✗ MISSING'}")
        
        return has_0013 and has_0014


def apply_migrations():
    """Apply missing migrations"""
    print_section("STEP 2: Applying Migrations")
    
    try:
        call_command('migrate', 'procurement', verbosity=0)
        print("  ✓ Migrations applied successfully")
        return True
    except Exception as e:
        print(f"  ✗ Migration failed: {e}")
        print("  → Will try manual schema fix...")
        return False


def fix_schema_manually():
    """Manually add missing columns via SQL"""
    print_section("STEP 3: Manual Schema Fix")
    
    with connection.cursor() as cursor:
        try:
            with transaction.atomic():
                # Add vendor_id
                print("  → Adding vendor_id column...")
                cursor.execute("""
                    DO $$ 
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'procurement_purchaserequisition' 
                            AND column_name = 'vendor_id'
                        ) THEN
                            ALTER TABLE procurement_purchaserequisition 
                            ADD COLUMN vendor_id uuid NULL
                            REFERENCES procurement_vendor(id) ON DELETE SET NULL;
                            
                            CREATE INDEX IF NOT EXISTS procurement_purchas_vendor_id_idx 
                            ON procurement_purchaserequisition(vendor_id);
                        END IF;
                    END $$;
                """)
                
                # Add vendor_selection_reason
                print("  → Adding vendor_selection_reason column...")
                cursor.execute("""
                    DO $$ 
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'procurement_purchaserequisition' 
                            AND column_name = 'vendor_selection_reason'
                        ) THEN
                            ALTER TABLE procurement_purchaserequisition 
                            ADD COLUMN vendor_selection_reason text NOT NULL DEFAULT '';
                        END IF;
                    END $$;
                """)
                
                # Add ai_vendor_recommendations
                print("  → Adding ai_vendor_recommendations column...")
                cursor.execute("""
                    DO $$ 
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = 'procurement_purchaserequisition' 
                            AND column_name = 'ai_vendor_recommendations'
                        ) THEN
                            ALTER TABLE procurement_purchaserequisition 
                            ADD COLUMN ai_vendor_recommendations jsonb NOT NULL DEFAULT '[]'::jsonb;
                        END IF;
                    END $$;
                """)
                
                # Record migrations
                print("  → Recording migrations...")
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied)
                    SELECT 'procurement', '0013_enhance_pr_workflow_and_vendor_integration', NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM django_migrations 
                        WHERE app = 'procurement' 
                        AND name = '0013_enhance_pr_workflow_and_vendor_integration'
                    )
                """)
                
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied)
                    SELECT 'procurement', '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more', NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM django_migrations 
                        WHERE app = 'procurement' 
                        AND name = '0014_alter_purchaserequisition_ai_vendor_recommendations_and_more'
                    )
                """)
                
            print("  ✓ Schema fixed successfully")
            return True
        except Exception as e:
            print(f"  ✗ Schema fix failed: {e}")
            return False


def verify_schema():
    """Verify all required columns exist"""
    print_section("STEP 4: Verifying Schema")
    
    required_columns = ['vendor_id', 'vendor_selection_reason', 'ai_vendor_recommendations']
    
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'procurement_purchaserequisition'
        """)
        existing = [row[0] for row in cursor.fetchall()]
        
        all_ok = True
        for col in required_columns:
            exists = col in existing
            print(f"  Column '{col}': {'✓ EXISTS' if exists else '✗ MISSING'}")
            if not exists:
                all_ok = False
        
        return all_ok


def import_data():
    """Import data from export file"""
    print_section("STEP 5: Importing Data")
    
    export_file = 'procurement_export.json'
    
    if not os.path.exists(export_file):
        print(f"  ✗ Export file not found: {export_file}")
        print(f"  → Please upload procurement_export.json to this directory")
        return False
    
    try:
        with open(export_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"  ✓ Loaded export file")
        print(f"    - Vendors: {len(data.get('Vendor', []))}")
        print(f"    - Purchase Requisitions: {len(data.get('PurchaseRequisition', []))}")
        print(f"    - Purchase Orders: {len(data.get('PurchaseOrder', []))}")
        print(f"    - Receipts: {len(data.get('Receipt', []))}")
        
        # Import vendors first
        print("\n  → Importing Vendors...")
        vendor_map = {}
        for vendor_data in data.get('Vendor', []):
            fields = vendor_data['fields']
            vendor, created = Vendor.objects.get_or_create(
                name=fields['name'],
                defaults={
                    'vendor_id': fields.get('vendor_id', ''),
                    'email': fields.get('email', ''),
                    'phone': fields.get('phone', ''),
                    'address': fields.get('address', ''),
                    'contact_person': fields.get('contact_person', ''),
                    'tax_id': fields.get('tax_id', ''),
                    'payment_terms': fields.get('payment_terms', ''),
                    'bank_details': fields.get('bank_details', {}),
                    'performance_rating': Decimal(str(fields.get('performance_rating', 0))),
                    'is_active': fields.get('is_active', True),
                    'approved_by_id': fields.get('approved_by'),
                    'approved_at': fields.get('approved_at'),
                    'notes': fields.get('notes', ''),
                }
            )
            vendor_map[vendor_data['pk']] = vendor.id
            if created:
                print(f"    + Created vendor: {vendor.name}")
        
        print(f"  ✓ Imported {len(vendor_map)} vendors")
        
        # Import PRs
        print("\n  → Importing Purchase Requisitions...")
        pr_map = {}
        for pr_data in data.get('PurchaseRequisition', []):
            fields = pr_data['fields']
            pr, created = PurchaseRequisition.objects.get_or_create(
                requisition_number=fields['requisition_number'],
                defaults={
                    'description': fields.get('description', ''),
                    'items': fields.get('items', []),
                    'status': fields.get('status', 'draft'),
                    'priority': fields.get('priority', 'medium'),
                    'department': fields.get('department', ''),
                    'budget_code': fields.get('budget_code', ''),
                    'total_estimated_cost': Decimal(str(fields.get('total_estimated_cost', 0))),
                    'currency': fields.get('currency', 'USD'),
                    'required_by_date': fields.get('required_by_date'),
                    'justification': fields.get('justification', ''),
                    'approver_id': fields.get('approver'),
                    'approved_at': fields.get('approved_at'),
                    'approved_by_id': fields.get('approved_by'),
                    'approval_notes': fields.get('approval_notes', ''),
                    'rejected_at': fields.get('rejected_at'),
                    'rejected_by_id': fields.get('rejected_by'),
                    'rejection_reason': fields.get('rejection_reason', ''),
                    'submitted_at': fields.get('submitted_at'),
                    'requester_id': fields.get('requester'),
                    'vendor_id': vendor_map.get(fields.get('vendor')) if fields.get('vendor') else None,
                    'vendor_selection_reason': fields.get('vendor_selection_reason', ''),
                    'ai_vendor_recommendations': fields.get('ai_vendor_recommendations', []),
                    'attachments': fields.get('attachments', []),
                    'notes': fields.get('notes', ''),
                }
            )
            pr_map[pr_data['pk']] = pr.id
            if created:
                print(f"    + Created PR: {pr.requisition_number}")
        
        print(f"  ✓ Imported {len(pr_map)} purchase requisitions")
        
        # Import POs
        print("\n  → Importing Purchase Orders...")
        po_count = 0
        for po_data in data.get('PurchaseOrder', []):
            fields = po_data['fields']
            po, created = PurchaseOrder.objects.get_or_create(
                po_number=fields['po_number'],
                defaults={
                    'requisition_id': pr_map.get(fields.get('requisition')) if fields.get('requisition') else None,
                    'vendor_id': vendor_map.get(fields.get('vendor')) if fields.get('vendor') else None,
                    'description': fields.get('description', ''),
                    'items': fields.get('items', []),
                    'status': fields.get('status', 'draft'),
                    'total_amount': Decimal(str(fields.get('total_amount', 0))),
                    'currency': fields.get('currency', 'USD'),
                    'payment_terms': fields.get('payment_terms', ''),
                    'delivery_address': fields.get('delivery_address', ''),
                    'expected_delivery_date': fields.get('expected_delivery_date'),
                    'actual_delivery_date': fields.get('actual_delivery_date'),
                    'created_by_id': fields.get('created_by'),
                    'approved_by_id': fields.get('approved_by'),
                    'approved_at': fields.get('approved_at'),
                    'attachments': fields.get('attachments', []),
                    'terms_and_conditions': fields.get('terms_and_conditions', ''),
                    'notes': fields.get('notes', ''),
                }
            )
            if created:
                po_count += 1
        
        print(f"  ✓ Imported {po_count} purchase orders")
        
        print("\n  ✓ Data import complete!")
        return True
        
    except Exception as e:
        print(f"  ✗ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_data():
    """Verify final data counts"""
    print_section("STEP 6: Final Verification")
    
    vendor_count = Vendor.objects.count()
    pr_count = PurchaseRequisition.objects.count()
    po_count = PurchaseOrder.objects.count()
    
    print(f"  Vendors: {vendor_count}")
    print(f"  Purchase Requisitions: {pr_count}")
    print(f"  Purchase Orders: {po_count}")
    
    success = vendor_count > 0 and pr_count > 0 and po_count > 0
    
    if success:
        print("\n  ✓ All systems operational!")
        print("\n  You can now access:")
        print("    - https://www.radai.ae/procurement/orders")
        print("    - https://www.radai.ae/procurement/requisitions")
    else:
        print("\n  ⚠ Warning: Some tables are empty")
    
    return success


def main():
    """Main execution flow"""
    print_section("PRODUCTION PROCUREMENT FIX")
    print("This script will fix migrations, schema, and import data")
    print("from local database export.")
    
    # Step 1: Check migrations
    migrations_ok = check_migrations()
    
    # Step 2: Apply migrations if needed
    if not migrations_ok:
        migrations_applied = apply_migrations()
        
        # Step 3: Manual schema fix if migrations failed
        if not migrations_applied:
            schema_fixed = fix_schema_manually()
            if not schema_fixed:
                print("\n✗ FAILED: Could not fix schema")
                return 1
    
    # Step 4: Verify schema
    schema_ok = verify_schema()
    if not schema_ok:
        print("\n✗ FAILED: Schema verification failed")
        return 1
    
    # Step 5: Import data
    data_imported = import_data()
    if not data_imported:
        print("\n✗ FAILED: Data import failed")
        return 1
    
    # Step 6: Verify everything
    verify_data()
    
    print_section("COMPLETE")
    print("Production procurement module is now fully operational!")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
