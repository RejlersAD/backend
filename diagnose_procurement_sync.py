"""
COMPREHENSIVE PROCUREMENT SYNC DIAGNOSTIC
Compare Local vs Production and identify sync issues
"""
import os
import django
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Suppress Django startup output
import warnings
warnings.filterwarnings('ignore')

django.setup()

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor, Receipt, PODocument
from django.contrib.auth import get_user_model
from django.db import connection
from django.conf import settings

User = get_user_model()

print("\n" + "=" * 100)
print(" " * 30 + "PROCUREMENT SYNC DIAGNOSTIC")
print("=" * 100)

# 1. Current Environment
print(f"\n📍 CURRENT ENVIRONMENT: {os.environ.get('ENVIRONMENT', 'local')}")
print(f"   Database: {settings.DATABASES['default']['NAME']}")
print(f"   Host: {settings.DATABASES['default'].get('HOST', 'localhost')}")
print(f"   Engine: {settings.DATABASES['default']['ENGINE']}")

# 2. Data Counts
print("\n" + "=" * 100)
print("📊 DATA COUNTS")
print("=" * 100)

pr_count = PurchaseRequisition.objects.count()
po_count = PurchaseOrder.objects.count()
vendor_count = Vendor.objects.count()
receipt_count = Receipt.objects.count()
user_count = User.objects.count()

print(f"  Purchase Requisitions:  {pr_count:6d}")
print(f"  Purchase Orders:         {po_count:6d}")
print(f"  Vendors:                 {vendor_count:6d}")
print(f"  Receipts:                {receipt_count:6d}")
print(f"  Users:                   {user_count:6d}")

# 3. Sample Data
if pr_count > 0:
    print("\n" + "-" * 100)
    print("📋 SAMPLE PURCHASE REQUISITIONS (Latest 5)")
    print("-" * 100)
    for pr in PurchaseRequisition.objects.order_by('-created_at')[:5]:
        pr_id_str = str(pr.id)[:8]  # Show first 8 chars of UUID
        item_count = len(pr.items) if pr.items else 0
        issued_by_name = pr.issued_by.username if pr.issued_by else 'N/A'
        print(f"  ID: {pr_id_str} | PR #: {pr.pr_number:20s} | Status: {pr.status:15s}")
        print(f"           Created: {pr.created_at.strftime('%Y-%m-%d %H:%M:%S')} | Issued By: {issued_by_name:20s} | Items: {item_count}")

if po_count > 0:
    print("\n" + "-" * 100)
    print("📦 SAMPLE PURCHASE ORDERS (Latest 5)")
    print("-" * 100)
    for po in PurchaseOrder.objects.order_by('-created_at')[:5]:
        po_id_str = str(po.id)[:8]  # Show first 8 chars of UUID
        vendor_name = po.vendor.name if po.vendor else 'N/A'
        print(f"  ID: {po_id_str} | PO #: {po.po_number:20s} | Status: {po.status:15s}")
        print(f"           Created: {po.created_at.strftime('%Y-%m-%d %H:%M:%S')} | Vendor: {vendor_name:30s} | Amount: ${po.total_amount}")

# 4. Check Migrations
print("\n" + "=" * 100)
print("🔄 MIGRATION STATUS")
print("=" * 100)

with connection.cursor() as cursor:
    cursor.execute("""
        SELECT name, applied 
        FROM django_migrations 
        WHERE app = 'procurement'
        ORDER BY applied DESC 
        LIMIT 10
    """)
    migrations = cursor.fetchall()
    
    print(f"\n  Total procurement migrations applied: {len(migrations)}")
    print("\n  Latest migrations:")
    for name, applied in migrations[:5]:
        print(f"    ✓ {name:70s} | {applied.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check for specific new migrations
    cursor.execute("""
        SELECT name 
        FROM django_migrations 
        WHERE app = 'procurement' 
        AND name LIKE '%0013%' OR name LIKE '%0014%'
    """)
    new_migrations = cursor.fetchall()
    
    print(f"\n  Recent enhancement migrations:")
    if new_migrations:
        for (name,) in new_migrations:
            print(f"    ✓ {name}")
    else:
        print(f"    ⚠️  Migrations 0013 and 0014 NOT FOUND in this database")

# 5. Database Connection Info
print("\n" + "=" * 100)
print("🔌 DATABASE CONNECTION")
print("=" * 100)
print(f"  Name:     {settings.DATABASES['default']['NAME']}")
print(f"  User:     {settings.DATABASES['default']['USER']}")
print(f"  Host:     {settings.DATABASES['default'].get('HOST', 'localhost')}")
print(f"  Port:     {settings.DATABASES['default'].get('PORT', '5432')}")
print(f"  Engine:   {settings.DATABASES['default']['ENGINE']}")

# 6. Diagnosis
print("\n" + "=" * 100)
print("🔍 DIAGNOSIS")
print("=" * 100)

issues = []

if pr_count == 0 and po_count == 0:
    issues.append("❌ NO PROCUREMENT DATA FOUND")
    issues.append("   → This database has zero purchase requisitions and orders")
    issues.append("   → Data created in one environment does NOT automatically sync to another")
    
if pr_count > 0 or po_count > 0:
    issues.append("✅ PROCUREMENT DATA EXISTS")
    issues.append(f"   → {pr_count} Purchase Requisitions")
    issues.append(f"   → {po_count} Purchase Orders")

# Check if this is local or production
db_host = settings.DATABASES['default'].get('HOST', 'localhost')
if 'railway' in db_host.lower() or 'prod' in db_host.lower():
    issues.append("📍 CONNECTED TO: PRODUCTION DATABASE")
elif 'localhost' in db_host or 'postgres_local' in db_host or db_host == 'db':
    issues.append("📍 CONNECTED TO: LOCAL DEVELOPMENT DATABASE")
else:
    issues.append(f"📍 CONNECTED TO: {db_host}")

for issue in issues:
    print(f"  {issue}")

# 7. Key Understanding
print("\n" + "=" * 100)
print("💡 KEY UNDERSTANDING")
print("=" * 100)
print("""
  LOCAL vs PRODUCTION DATABASES ARE SEPARATE:
  
  ┌─────────────────────┐         ┌─────────────────────┐
  │   LOCAL DATABASE    │         │  PRODUCTION DATABASE │
  │  (PostgreSQL Local) │         │  (Railway Postgres)  │
  ├─────────────────────┤         ├─────────────────────┤
  │ • Development data  │   ✗     │ • Production data    │
  │ • Test records      │  NO     │ • Real user data     │
  │ • Sample data       │  SYNC   │ • Live transactions  │
  └─────────────────────┘         └─────────────────────┘
  
  WHEN YOU PUSH CODE TO GITHUB:
    ✓ Code changes sync (models, views, serializers)
    ✓ Migration files sync (.py files in migrations/)
    ✗ DATABASE DATA does NOT sync (PR/PO records)
  
  WHEN RAILWAY DEPLOYS:
    ✓ Pulls latest code from GitHub
    ✓ Runs migrations (if configured)
    ✗ Does NOT copy your local database data
  
  TO SEE DATA IN PRODUCTION:
    1. Create data directly in production (via production UI)
    2. OR: Export local data → Import to production (manual)
    3. OR: Use Django fixtures (loaddata/dumpdata)
""")

print("\n" + "=" * 100)
print("🎯 NEXT STEPS")
print("=" * 100)
print("""
  TO CHECK PRODUCTION:
    1. Run this script in production:
       cd backend
       railway run -- python diagnose_procurement_sync.py
    
    2. Or connect to production UI:
       https://www.radai.ae/procurement/orders
       (You need to CREATE data in production first!)
    
    3. Check migrations in production:
       railway run -- python manage.py showmigrations procurement
""")

print("=" * 100 + "\n")
