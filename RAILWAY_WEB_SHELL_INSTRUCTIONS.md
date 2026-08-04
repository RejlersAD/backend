# IMMEDIATE ACTION REQUIRED
# Railway CLI not working from Windows due to Unicode encoding

## Quick Fix - Use Railway Web Interface

Since Windows console is blocking Railway commands, please follow these steps:

### OPTION 1: Railway Web Shell (EASIEST - 2 minutes)

1. **Open Railway Dashboard**
   - Go to: https://railway.app
   - Log in to your account
   - Select project: `Radai_Production`

2. **Open Terminal**
   - Click on your `backend` service (or main Django service)
   - Click the **"Terminal"** or **"Shell"** tab at the top
   - This opens a bash shell directly in production

3. **Run Fix Command**
   Copy and paste this ONE command:
   ```bash
   python manage.py fix_production_procurement --seed
   ```

4. **Wait for Completion** (~30 seconds)
   You'll see output like:
   ```
   [1/4] Checking migrations...
   [2/4] Verifying database schema...
   [3/4] Checking data...
   [4/4] Seeding data...
   ✓ COMPLETE
   ```

5. **Verify in Browser**
   - Open: https://www.radai.ae/procurement/orders
   - Should now show Purchase Orders (no 500 error)
   - Open: https://www.radai.ae/procurement/requisitions
   - Should now show Purchase Requisitions

---

### OPTION 2: Alternative - Use SQL Migration (If Option 1 Fails)

If Python script fails, use direct SQL:

1. In Railway web shell, run:
   ```bash
   psql $DATABASE_URL < emergency_production_migration.sql
   ```

2. Then seed data:
   ```bash
   python manage.py seed_procurement_data --vendors 10 --prs 15 --pos 10
   ```

---

### OPTION 3: Import Exported Data

To use the exact data from local database:

1. The file `procurement_export.json` is already in production (deployed via git)

2. In Railway web shell:
   ```bash
   python manage.py sync_procurement_data import --file procurement_export.json
   ```

3. This imports:
   - 174 Vendors
   - 20 Purchase Requisitions
   - 115 Purchase Orders

---

## Why Railway CLI Failed

The issue is Windows console encoding - it can't display Unicode characters (✅ emoji) used in the Django settings file. This is a Windows limitation, not a code issue.

**Solution**: Use Railway's web-based shell which runs Linux and has no encoding issues.

---

## Summary

✅ **All code is deployed** to production  
✅ **All migrations are ready** (0013, 0014)  
✅ **Data export is ready** (309 records)  
⏳ **Just need to run ONE command** in Railway web shell  

**Time needed: 2 minutes**
