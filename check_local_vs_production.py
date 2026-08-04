"""
Quick Local vs Production Comparison Script
Run this to see differences between environments

Usage: python check_local_vs_production.py
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import UserProfile, Role, Module
from django.db import connection

User = get_user_model()

print("=" * 80)
print("LOCAL vs PRODUCTION COMPARISON")
print("=" * 80)

# 1. Environment Detection
print("\n[1] ENVIRONMENT")
db_host = connection.settings_dict.get('HOST', 'unknown')
db_name = connection.settings_dict.get('NAME', 'unknown')
print(f"  Database Host: {db_host}")
print(f"  Database Name: {db_name}")

if 'railway' in db_host or 'railway' in db_name:
    print("  ✅ PRODUCTION (Railway)")
elif 'localhost' in db_host or '127.0.0.1' in db_host or db_name == 'aiflow_dev':
    print("  ✅ LOCAL (Development)")
else:
    print("  ⚠️  UNKNOWN")

# 2. Data Counts
print("\n[2] DATA COUNTS")
po_count = PurchaseOrder.objects.count()
pr_count = PurchaseRequisition.objects.count()
vendor_count = Vendor.objects.count()
user_count = User.objects.count()

print(f"  Purchase Orders: {po_count}")
print(f"  Purchase Requisitions: {pr_count}")
print(f"  Vendors: {vendor_count}")
print(f"  Users: {user_count}")

if po_count == 0:
    print("  ⚠️  WARNING: No purchase orders (frontend will show empty)")
else:
    print(f"  ✅ Data exists")

# 3. Sample Purchase Orders
print("\n[3] SAMPLE PURCHASE ORDERS")
if po_count > 0:
    sample_orders = PurchaseOrder.objects.all().select_related('vendor')[:5]
    for po in sample_orders:
        vendor_name = po.vendor.name if po.vendor else 'No vendor'
        print(f"  - {po.po_number} | {vendor_name} | {po.status} | ${po.total_amount}")
else:
    print("  (No orders to display)")

# 4. RBAC Configuration
print("\n[4] RBAC - PROCUREMENT MODULE ACCESS")
try:
    procurement_module = Module.objects.get(code='procurement_orders', is_active=True)
    print(f"  Module: {procurement_module.name} (✅ exists)")
    
    # Roles with access
    roles_with_access = Role.objects.filter(modules=procurement_module, is_active=True)
    print(f"  Roles with access ({roles_with_access.count()}):")
    for role in roles_with_access:
        user_count_role = UserProfile.objects.filter(roles=role).count()
        print(f"    - {role.name} ({role.code}) - {user_count_role} users")
    
    if roles_with_access.count() == 0:
        print("  ⚠️  WARNING: No roles have procurement_orders access")
        print("  Fix: python manage.py grant_procurement_access")
    
except Module.DoesNotExist:
    print("  ✗ Module 'procurement_orders' NOT FOUND")
    print("  This is a critical issue!")

# 5. Sample User Check
print("\n[5] SAMPLE USER ACCESS CHECK")
test_emails = ['tanzeem.agra@rejlers.ae', 'mohammed.agra@rejlers.ae', 'admin@radai.ae']

for email in test_emails:
    try:
        user = User.objects.get(email=email)
        print(f"\n  User: {email}")
        print(f"    Active: {user.is_active}")
        print(f"    Superuser: {user.is_superuser}")
        
        try:
            profile = user.rbac_profile
            roles = profile.roles.filter(is_active=True)
            print(f"    Roles: {', '.join([r.name for r in roles])}")
            
            has_access = user.is_superuser or profile.has_module_access('procurement_orders')
            if has_access:
                print(f"    procurement_orders: ✅ GRANTED")
            else:
                print(f"    procurement_orders: ✗ DENIED")
                
        except UserProfile.DoesNotExist:
            if user.is_superuser:
                print(f"    procurement_orders: ✅ GRANTED (superuser)")
            else:
                print(f"    procurement_orders: ✗ DENIED (no profile)")
                
    except User.DoesNotExist:
        print(f"\n  User: {email} - NOT FOUND")

# 6. API Endpoint Check
print("\n[6] API ENDPOINT CONFIGURATION")
print("  Frontend calls: GET /api/v1/procurement/orders/")
print("  ViewSet: PurchaseOrderViewSet")
print("  Permissions: [IsAuthenticated, HasModuleAccess]")
print("  Module Required: 'procurement_orders'")
print("  Pagination: 100 items per page")

# 7. Diagnosis Summary
print("\n" + "=" * 80)
print("DIAGNOSIS")
print("=" * 80)

issues = []

if po_count == 0:
    issues.append("❌ No purchase order data in database")
    print("❌ No purchase order data in database")
    print("   Fix: python manage.py seed_procurement_data --vendors 5 --prs 5 --pos 5")

try:
    procurement_module = Module.objects.get(code='procurement_orders', is_active=True)
    roles_with_access = Role.objects.filter(modules=procurement_module, is_active=True)
    
    if roles_with_access.count() == 0:
        issues.append("❌ No roles have 'procurement_orders' module access")
        print("❌ No roles have 'procurement_orders' module access")
        print("   Fix: python manage.py grant_procurement_access")
        
except Module.DoesNotExist:
    issues.append("❌ Module 'procurement_orders' not found")
    print("❌ Module 'procurement_orders' not found")

if not issues:
    print("✅ NO ISSUES FOUND")
    print("✅ Data exists and RBAC is configured correctly")
    print("\nIf frontend still shows no data:")
    print("  1. User needs to logout and login again (refresh JWT with new permissions)")
    print("  2. Clear browser cache and cookies")
    print("  3. Check browser console for errors (F12)")
    print("  4. Verify API_BASE_URL in frontend .env file")
    print("  5. Run: python manage.py test_procurement_api --email your.email@example.com")

print("=" * 80)
