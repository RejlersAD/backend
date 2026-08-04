"""
Smart ICT Admin Setup Script
Run in Railway Shell with: exec(open('smart_ict_setup.py').read())
"""
import os
import sys
import subprocess

print("=" * 80)
print("🚀 SMART ICT ADMIN SETUP - AUTOMATED")
print("=" * 80)
print()
print("📧 Target User: radai@rejlers.ae")
print("🎯 Role: ICT Administrator (ict_admin)")
print("📦 Modules: 6 admin section features (Dashboard, Users, Roles, Wrench, AI Champion, Enquiries)")
print()

# Step 1: Run migration
print("⏳ Step 1: Creating ICT Admin role in database...")
print("-" * 80)
try:
    result = subprocess.run(
        ["python", "manage.py", "migrate", "rbac"],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    if "0033_seed_ict_admin_role" in result.stdout or "No migrations to apply" in result.stdout:
        print("✅ Migration completed successfully")
        if "Applying rbac.0033" in result.stdout:
            print("✅ ICT Admin role created in database")
    else:
        print(result.stdout)
    
    if result.returncode != 0 and "No migrations to apply" not in result.stdout:
        print("⚠️  Migration error:")
        print(result.stderr)
except Exception as e:
    print(f"❌ Migration failed: {e}")
    sys.exit(1)

print()

# Step 2: Assign role to user
print("⏳ Step 2: Assigning ICT Admin role to radai@rejlers.ae...")
print("-" * 80)
try:
    result = subprocess.run(
        ["python", "manage.py", "setup_ict_admin"],
        capture_output=False,  # Show live output
        timeout=30
    )
    
    if result.returncode == 0:
        print()
        print("=" * 80)
        print("✅ SMART SETUP COMPLETE!")
        print("=" * 80)
    else:
        print("⚠️  Setup completed with warnings")
except Exception as e:
    print(f"❌ Setup failed: {e}")
    sys.exit(1)

print()
print("📋 Next Steps for radai@rejlers.ae:")
print("  1. Log out from https://www.radai.ae")
print("  2. Clear browser cache (Ctrl+Shift+Delete)")
print("  3. Log back in")
print("  4. Test admin URLs:")
print("     • https://www.radai.ae/admin/dashboard")
print("     • https://www.radai.ae/admin/users")
print("     • https://www.radai.ae/admin/roles")
print("     • https://www.radai.ae/admin/wrench")
print("     • https://www.radai.ae/admin/ai-champion")
print("     • https://www.radai.ae/admin/enquiries")
print()
print("🔒 Security: ICT Admin has ONLY admin section access (no Engineering, HR, Finance)")
print()
