"""
Quick Production Database Check via Railway CLI
"""
import subprocess
import json

print("=" * 80)
print("CHECKING PRODUCTION DATABASE VIA RAILWAY")
print("=" * 80)

# Check if Railway CLI is installed
try:
    result = subprocess.run(['railway', '--version'], capture_output=True, text=True)
    print(f"✅ Railway CLI installed: {result.stdout.strip()}\n")
except FileNotFoundError:
    print("❌ Railway CLI not found!")
    print("\nTo install Railway CLI:")
    print("  PowerShell: iwr https://railway.app/install.ps1 | iex")
    print("  Or: npm i -g @railway/cli")
    print("\nThen login: railway login")
    print("=" * 80)
    exit(1)

# Check if logged in
try:
    result = subprocess.run(['railway', 'whoami'], capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ Not logged in to Railway!")
        print("\nRun: railway login")
        exit(1)
    print(f"✅ Logged in to Railway\n")
except Exception as e:
    print(f"❌ Error checking Railway login: {e}")
    exit(1)

# Query production database
print("🔍 Querying production database...\n")

queries = [
    ("Purchase Requisitions", "SELECT COUNT(*) FROM procurement_purchaserequisition;"),
    ("Purchase Orders", "SELECT COUNT(*) FROM procurement_purchaseorder;"),
    ("PR Items", "SELECT COUNT(*) FROM procurement_requisitionitem;"),
    ("Vendors", "SELECT COUNT(*) FROM procurement_vendor;"),
]

for label, query in queries:
    try:
        # Use railway run to execute psql command
        cmd = [
            'railway', 'run',
            '--',
            'psql', '$DATABASE_URL',
            '-t',  # tuples only
            '-c', query
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            count = result.stdout.strip()
            print(f"  {label:30s}: {count} records")
        else:
            print(f"  {label:30s}: ❌ Error - {result.stderr[:100]}")
    
    except subprocess.TimeoutExpired:
        print(f"  {label:30s}: ⏱️  Timeout")
    except Exception as e:
        print(f"  {label:30s}: ❌ {str(e)[:100]}")

print("\n" + "=" * 80)
print("\n💡 To manually check production database:")
print("   railway run psql \\$DATABASE_URL")
print("=" * 80)
