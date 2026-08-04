"""
Django-based PO/PR Import Script
Imports data from extracted JSON into PostgreSQL via Django ORM

Run this with: python backend/_django_import_po_pr.py
"""

import os
import sys
import json
import django
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime, date

# Django setup
BASE_DIR = Path(__file__).resolve().parent / 'backend'
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.db import transaction
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from django.contrib.auth import get_user_model

User = get_user_model()

# Configuration
JSON_DATA_FILE = '/app/_po_pr_import_data.json'  # Inside Docker container

# Status normalization
STATUS_MAP = {
    'draft': 'draft',
    'pending': 'pending',
    'approved': 'approved',
    'ongoing': 'approved',
    'completed': 'completed',
    'cancelled': 'cancelled',
    'hold': 'pending',
}

def parse_date(date_str):
    """Parse date from various formats"""
    if not date_str or date_str == '':
        return None
    
    if isinstance(date_str, (date, datetime)):
        return date_str.date() if isinstance(date_str, datetime) else date_str
    
    date_str = str(date_str).strip()
    
    # Try various formats
    formats = [
        '%d.%m.%Y',
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%Y-%m-%d %H:%M:%S',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except:
            continue
    
    return None

def get_or_create_vendor(vendor_name, country=''):
    """Get or create vendor"""
    if not vendor_name or vendor_name == '':
        return None
    
    # Clean vendor name
    vendor_name = vendor_name.strip()
    
    # Try to find existing
    vendor = Vendor.objects.filter(name__iexact=vendor_name).first()
    
    if not vendor:
        # Create vendor code from name
        vendor_code = 'VEN-' + ''.join(c for c in vendor_name[:20].upper() if c.isalnum())
        
        vendor = Vendor.objects.create(
            vendor_code=vendor_code,
            name=vendor_name,
            status='active',
            country=country,
            rating=Decimal('0.0')
        )
        print(f"  ✅ Created vendor: {vendor_name}")
    
    return vendor

def import_po(record, default_user):
    """Import a single PO record"""
    po_number = record['po_number']
    
    # Check if exists
    if PurchaseOrder.objects.filter(po_number=po_number).exists():
        print(f"  ⏭️  Skipped (duplicate): {po_number}")
        return False
    
    # Get or create vendor
    vendor = get_or_create_vendor(record['vendor'], record.get('country', ''))
    
    if not vendor:
        print(f"  ⚠️  Skipped (no vendor): {po_number}")
        return False
    
    # Parse dates
    order_date = parse_date(record.get('order_date'))
    delivery_date = parse_date(record.get('delivery_date'))
    
    # Parse amount
    amount = record.get('amount')
    if amount and str(amount).strip() not in ['', 'nan', 'NaN', 'None', 'null']:
        try:
            amount = Decimal(str(amount))
        except (ValueError, InvalidOperation):
            amount = Decimal('0.00')
    else:
        amount = Decimal('0.00')
    
    # Status
    status = STATUS_MAP.get(record.get('status', 'draft'), 'draft')
    
    # Currency
    currency = record.get('currency', 'USD').upper()
    if not currency or currency == '':
        currency = 'AED'
    
    # Create PO
    try:
        po = PurchaseOrder.objects.create(
            po_number=po_number,
            vendor=vendor,
            title=record['description'][:300] if record['description'] else 'Purchase Order',
            description=record['description'] or '',
            project_number=record.get('project', ''),
            total_amount=amount,
            currency=currency,
            status=status,
            payment_terms=record.get('payment_terms', ''),
            po_date=order_date or date.today(),
            expected_delivery=delivery_date,
            notes=record.get('remarks', ''),
            created_by=default_user,
            category='other'
        )
        
        print(f"  ✅ Imported PO: {po_number} - {vendor.name} - {amount} {currency}")
        return True
    
    except Exception as e:
        print(f"  ❌ Error importing {po_number}: {str(e)}")
        return False

def import_all():
    """Import all records from JSON"""
    print("\n" + "="*100)
    print("🚀 DJANGO PO/PR DATABASE IMPORT")
    print("="*100 + "\n")
    
    # Load JSON data
    if not os.path.exists(JSON_DATA_FILE):
        print(f"❌ JSON file not found: {JSON_DATA_FILE}")
        return
    
    with open(JSON_DATA_FILE, 'r', encoding='utf-8') as f:
        records = json.load(f)
    
    print(f"📊 Loaded {len(records)} records from JSON\n")
    
    # Get default user
    default_user = User.objects.filter(is_superuser=True).first()
    if not default_user:
        default_user = User.objects.first()
    
    if not default_user:
        print("❌ No users found in database. Please create a user first.")
        return
    
    print(f"👤 Using default user: {default_user.email}\n")
    
    # Stats
    imported = 0
    skipped = 0
    errors = 0
    
    # Import each record
    for i, record in enumerate(records, 1):
        print(f"[{i}/{len(records)}] Processing: {record['po_number']}")
        
        try:
            result = import_po(record, default_user)
            if result:
                imported += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ❌ Error: {str(e)}")
            errors += 1
    
    print("\n" + "="*100)
    print("📊 IMPORT SUMMARY")
    print("="*100)
    print(f"✅ Imported: {imported}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"❌ Errors: {errors}")
    print(f"📝 Total: {len(records)}")
    print("="*100 + "\n")
    
    # Verification
    print("🔍 Database Verification:")
    print(f"   Total POs in database: {PurchaseOrder.objects.count()}")
    print(f"   Total Vendors in database: {Vendor.objects.count()}")
    print("="*100 + "\n")

def main():
    try:
        import_all()
    except Exception as e:
        print(f"\n❌ Fatal error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
