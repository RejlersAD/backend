"""
Create Sample Purchase Requisitions for Testing
This script creates a few sample PRs in the database for testing the PR functionality
"""

import os
import sys
import django
from pathlib import Path
from decimal import Decimal
from datetime import datetime, date, timedelta

# Django setup
BASE_DIR = Path(__file__).resolve().parent / 'backend'
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.db import transaction
from apps.procurement.models import PurchaseRequisition, Vendor
from django.contrib.auth import get_user_model

User = get_user_model()

# Sample PR data - soft-coded configuration
SAMPLE_REQUISITIONS = [
    {
        'pr_number': 'RAD-GEN-PR-0001_2026',
        'title': 'Office Supplies - Computer Equipment',
        'product_service': 'New laptop computers for engineering team (5 units)',
        'description_reason': 'Engineering team expansion requires additional computing resources',
        'priority': 'high',
        'status': 'submitted',
        'total_price': Decimal('25000.00'),
        'currency': 'AED',
        'requisition_type': 'general',
        'supplier_name': 'City Computer Company LLC',
        'project_department': 'Engineering Department'
    },
    {
        'pr_number': 'RAD-GEN-PR-0002_2026',
        'title': 'Software Licenses - Engineering Tools',
        'product_service': 'Annual renewal of engineering software licenses (AutoCAD, PDMS)',
        'description_reason': 'Software license renewal for engineering design tools',
        'priority': 'urgent',
        'status': 'pm_approved',
        'total_price': Decimal('45000.00'),
        'currency': 'USD',
        'requisition_type': 'general',
        'supplier_name': 'Majid Informatic Solutions',
        'project_department': 'Engineering Department'
    },
    {
        'pr_number': 'RAD-PRJ-PR-0001_2026',
        'title': 'Stress Analysis Services - Project 5901055',
        'product_service': 'Stress analysis consultancy services for delayed coker unit',
        'description_reason': 'Required for project safety analysis and regulatory compliance',
        'priority': 'high',
        'status': 'fully_approved',
        'total_price': Decimal('150000.00'),
        'currency': 'USD',
        'requisition_type': 'project',
        'supplier_name': 'Noveltech Surveys',
        'project_department': 'Process Engineering',
        'project': '5901055'
    },
    {
        'pr_number': 'RAD-GEN-PR-0003_2026',
        'title': 'Office Furniture - New Hires',
        'product_service': 'Office furniture for 3 new engineering positions',
        'description_reason': 'Furniture required for new employee workstations',
        'priority': 'normal',
        'status': 'draft',
        'total_price': Decimal('15000.00'),
        'currency': 'AED',
        'requisition_type': 'general',
        'supplier_name': 'To be determined',
        'project_department': 'Administration'
    },
    {
        'pr_number': 'RAD-PRJ-PR-0002_2026',
        'title': 'Safety Equipment - Project Site',
        'product_service': 'Safety helmets, vests, and equipment for site personnel',
        'description_reason': 'QHSE compliance - required for site safety',
        'priority': 'high',
        'status': 'submitted',
        'total_price': Decimal('8000.00'),
        'currency': 'AED',
        'requisition_type': 'project',
        'supplier_name': 'Emirates Technical & Safety Development Centre',
        'project_department': 'QHSE Department'
    }
]

@transaction.atomic
def create_sample_requisitions():
    """Create sample requisitions in database"""
    print("\n" + "="*100)
    print("🚀 CREATING SAMPLE PURCHASE REQUISITIONS")
    print("="*100 + "\n")
    
    # Get default user
    default_user = User.objects.filter(is_superuser=True).first()
    if not default_user:
        default_user = User.objects.first()
    
    if not default_user:
        print("❌ No users found in database. Please create a user first.")
        return
    
    print(f"👤 Using default user: {default_user.email}\n")
    
    created = 0
    skipped = 0
    
    for pr_data in SAMPLE_REQUISITIONS:
        pr_number = pr_data['pr_number']
        
        # Check if exists
        if PurchaseRequisition.objects.filter(pr_number=pr_number).exists():
            print(f"  ⏭️  Skipped (exists): {pr_number}")
            skipped += 1
            continue
        
        try:
            # Create requisition with correct field names
            requisition = PurchaseRequisition.objects.create(
                pr_number=pr_number,
                title=pr_data['title'],
                product_service=pr_data['product_service'],
                description_reason=pr_data['description_reason'],
                priority=pr_data['priority'],
                status=pr_data['status'],
                total_price=pr_data['total_price'],
                currency=pr_data['currency'],
                requisition_type=pr_data['requisition_type'],
                supplier_name=pr_data['supplier_name'],
                project_department=pr_data.get('project_department', ''),
                project=pr_data.get('project', ''),
                issued_by=default_user,
                issued_date=date.today()
            )
            
            print(f"  ✅ Created PR: {pr_number} - {pr_data['title']} ({pr_data['status']})")
            created += 1
            
        except Exception as e:
            print(f"  ❌ Error creating {pr_number}: {str(e)}")
    
    print("\n" + "="*100)
    print("📊 SUMMARY")
    print("="*100)
    print(f"✅ Created: {created}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"📝 Total in DB: {PurchaseRequisition.objects.count()}")
    print("="*100 + "\n")

def main():
    try:
        create_sample_requisitions()
    except Exception as e:
        print(f"\n❌ Fatal error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
