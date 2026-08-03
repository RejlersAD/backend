#!/usr/bin/env python
"""
Import General Purchase Requisitions from PO_Generated.xlsx to database
Using soft-coded approach for data mapping
"""
import os
import sys
import django
from decimal import Decimal
from datetime import datetime, date
import pandas as pd

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aiflow.settings')
django.setup()

from django.db import transaction
from apps.procurement.models import PurchaseRequisition, Vendor
from django.contrib.auth import get_user_model

User = get_user_model()

# Soft-coded configuration
EXCEL_FILE = '/app/PO_Generated.xlsx'  # File copied into Docker container
SHEET_NAME = 'PUR_GEN_2026'

# Soft-coded column mapping (0-indexed after header row)
COLUMN_MAP = {
    'pr_number': 1,  # Unnamed: 1
    'pr_date': 2,  # Unnamed: 2  
    'po_number': 3,  # Unnamed: 3
    'po_date': 4,  # Unnamed: 4
    'supplier_name': 5,  # Unnamed: 5
    'product_service': 6,  # Unnamed: 6
    'project': 7,  # Unnamed: 7
    'delivery_date': 9,  # Unnamed: 9
    'payment_terms': 10,  # Unnamed: 10
    'total_price': 11,  # Unnamed: 11
    'currency': 12,  # Unnamed: 12
}


def parse_date(date_val):
    """Soft-coded date parser"""
    if pd.isna(date_val) or date_val == 'NaN' or str(date_val).strip() in ['', 'NaN', 'nan']:
        return date.today()
    
    if isinstance(date_val, (datetime, pd.Timestamp)):
        return date_val.date()
    
    try:
        # Try parsing string dates
        return datetime.strptime(str(date_val), '%Y-%m-%d').date()
    except:
        try:
            return datetime.strptime(str(date_val), '%d/%m/%Y').date()
        except:
            return date.today()


def parse_decimal(value):
    """Soft-coded decimal parser"""
    if pd.isna(value) or value == '' or value == 'NaN':
        return Decimal('0.00')
    
    try:
        # Remove currency symbols and commas
        clean_value = str(value).replace(',', '').replace('$', '').replace('AED', '').replace('USD', '').strip()
        return Decimal(clean_value)
    except:
        return Decimal('0.00')


def determine_status(pr_date, po_number):
    """Soft-coded status determination logic"""
    if pd.isna(po_number) or str(po_number).strip() in ['', 'NaN', 'nan', 'No PO', 'HOLD']:
        # No PO yet - determine based on date
        if pd.isna(pr_date):
            return 'draft'
        pr_date_obj = parse_date(pr_date)
        days_old = (date.today() - pr_date_obj).days
        
        if days_old < 3:
            return 'draft'
        elif days_old < 7:
            return 'submitted'
        elif days_old < 14:
            return 'pm_approved'
        else:
            return 'fully_approved'
    else:
        # Has PO - likely converted or fully approved
        return 'fully_approved'


def determine_priority(pr_date, total_price):
    """Soft-coded priority determination logic"""
    if total_price > Decimal('100000'):
        return 'urgent'
    elif total_price > Decimal('50000'):
        return 'high'
    elif total_price > Decimal('10000'):
        return 'normal'
    else:
        return 'low'


def import_general_prs():
    """Import General Purchase Requisitions from Excel"""
    
    print("=" * 80)
    print("🚀 IMPORTING GENERAL PURCHASE REQUISITIONS")
    print("=" * 80)
    print(f"\nFile: {EXCEL_FILE}")
    print(f"Sheet: {SHEET_NAME}\n")
    
    # Get default user
    try:
        default_user = User.objects.filter(is_superuser=True).first()
        if not default_user:
            default_user = User.objects.first()
    except:
        default_user = User.objects.first()
    
    print(f"👤 Using default user: {default_user.email if default_user else 'None'}\n")
    
    # Read Excel file
    df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME)
    
    # Find PRs (skip header rows)
    pr_records = []
    for idx, row in df.iterrows():
        pr_number = row.iloc[COLUMN_MAP['pr_number']]
        
        # Check if this is a valid PR number
        if pd.notna(pr_number) and isinstance(pr_number, str) and 'RAD-GEN-PR' in pr_number:
            pr_records.append((idx, row))
    
    print(f"📋 Found {len(pr_records)} General PRs in Excel\n")
    
    created_count = 0
    skipped_count = 0
    
    for idx, row in pr_records:
        pr_number = str(row.iloc[COLUMN_MAP['pr_number']]).strip()
        
        # Check if already exists
        if PurchaseRequisition.objects.filter(pr_number=pr_number).exists():
            print(f"  ⏭️  Skipped: {pr_number} (already exists)")
            skipped_count += 1
            continue
        
        # Use savepoint for each PR to avoid transaction rollback
        try:
            with transaction.atomic():
                # Extract data using soft-coded column mapping
                pr_date = parse_date(row.iloc[COLUMN_MAP['pr_date']])
                po_number = row.iloc[COLUMN_MAP['po_number']]
                supplier_name = str(row.iloc[COLUMN_MAP['supplier_name']]) if pd.notna(row.iloc[COLUMN_MAP['supplier_name']]) else ''
                product_service = str(row.iloc[COLUMN_MAP['product_service']]) if pd.notna(row.iloc[COLUMN_MAP['product_service']]) else ''
                project = str(row.iloc[COLUMN_MAP['project']]) if pd.notna(row.iloc[COLUMN_MAP['project']]) else ''
                total_price = parse_decimal(row.iloc[COLUMN_MAP['total_price']])
                
                # Soft-coded currency parsing - ensure max 3 characters
                currency_raw = str(row.iloc[COLUMN_MAP['currency']]) if pd.notna(row.iloc[COLUMN_MAP['currency']]) else 'AED'
                currency = currency_raw.strip()[:3]  # Trim to 3 characters maximum
                
                # Soft-coded status and priority determination
                status = determine_status(pr_date, po_number)
                priority = determine_priority(pr_date, total_price)
                
                # Create PR
                pr = PurchaseRequisition.objects.create(
                    pr_number=pr_number,
                    title=f"General Procurement - {supplier_name[:50]}" if supplier_name else f"General PR {pr_number}",
                    product_service=product_service[:500] if product_service else 'General procurement item',
                    description_reason=f"Imported from Excel - {product_service[:200]}" if product_service else 'General procurement requirement',
                    priority=priority,
                    status=status,
                    total_price=total_price,
                    currency=currency,
                    requisition_type='general',
                    supplier_name=supplier_name[:200] if supplier_name else 'TBD',
                    project=project[:100] if project else '',
                    project_department='Procurement',
                    issued_by=default_user,
                    issued_date=pr_date
                )
                
                print(f"  ✅ Created PR: {pr_number} - {status} - {currency} {total_price}")
                created_count += 1
            
        except Exception as e:
            print(f"  ❌ Error creating {pr_number}: {e}")
            continue
    
    print("\n" + "=" * 80)
    print("📊 IMPORT SUMMARY")
    print("=" * 80)
    print(f"✅ Created: {created_count}")
    print(f"⏭️  Skipped: {skipped_count}")
    print(f"📝 Total in DB: {PurchaseRequisition.objects.filter(pr_number__startswith='RAD-GEN-PR').count()}")
    print("=" * 80)


if __name__ == '__main__':
    try:
        import_general_prs()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
