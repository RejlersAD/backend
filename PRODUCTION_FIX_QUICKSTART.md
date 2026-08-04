# PRODUCTION FIX - QUICK START GUIDE

## Problem
Production at `https://www.radai.ae/procurement/orders` shows:
- ❌ "Failed to load requisitions: Request failed with status code 500"
- ❌ No Purchase Orders or Purchase Requisitions visible
- ❌ Backend error: "column vendor_id does not exist"

## Root Cause
Migrations 0013 and 0014 were not applied in production database.

## Solution
Run the automated fix script that handles everything in one command.

---

## OPTION 1: Via Railway CLI (Recommended)

If you have Railway CLI installed:

```bash
# Navigate to backend directory
cd backend

# Run the fix script
railway run python production_complete_fix.py
```

This will:
1. ✓ Apply migrations 0013 and 0014
2. ✓ Fix schema manually if migrations fail
3. ✓ Import all 309 records from local database
4. ✓ Verify everything works

---

## OPTION 2: Via Railway Web Shell

1. **Go to Railway Dashboard**
   - Open: https://railway.app/project/<your-project-id>
   - Select: `Postgres` or `backend` service
   - Click: "Shell" tab

2. **Run the fix script**
   ```bash
   python production_complete_fix.py
   ```

3. **Verify completion**
   - Open: https://www.radai.ae/procurement/orders
   - You should see 115 Purchase Orders
   - Open: https://www.radai.ae/procurement/requisitions
   - You should see 20 Purchase Requisitions

---

## OPTION 3: Manual SQL Fix (Emergency Fallback)

If Python scripts fail, use direct SQL:

```bash
railway run -- psql $DATABASE_URL < emergency_production_migration.sql
```

Then seed sample data:
```bash
railway run -- python manage.py seed_procurement_data --vendors 10 --prs 15 --pos 10
```

---

## What Gets Imported

From `procurement_export.json`:
- **174 Vendors** - Complete vendor database
- **20 Purchase Requisitions** - Various statuses (draft, pending, approved)
- **115 Purchase Orders** - Complete order history
- **All relationships** - Vendors linked to PRs and POs

---

## Verification Commands

After running the fix, verify with:

```bash
# Check status
railway run -- python manage.py check_procurement_status --verbose

# Should show:
#   Purchase Requisitions: 20
#   Purchase Orders: 115
#   Vendors: 174
```

---

## Timeline

- **Code pushed**: ✅ Done
- **Railway auto-deploy**: ~2 minutes (automatic)
- **Run fix script**: ~30 seconds
- **Total time**: ~3 minutes from now

---

## Troubleshooting

### If script fails with "procurement_export.json not found":
The file is already in the repository. Railway should have it after deployment.
If not, you can use seed command instead:
```bash
railway run -- python manage.py seed_procurement_data --vendors 20 --prs 20 --pos 30 --demo-mode
```

### If you see "migrations already applied":
Good! The schema is fixed. Just need to import/seed data:
```bash
railway run -- python manage.py sync_procurement_data import --file procurement_export.json
```

### If Railway CLI not working on Windows:
Use Railway web interface shell (Option 2 above).

---

## Expected Output

```
================================================================================
  PRODUCTION PROCUREMENT FIX
================================================================================

================================================================================
  STEP 1: Checking Migrations
================================================================================
  Migration 0013 (vendor integration): ✓ APPLIED
  Migration 0014 (vendor recommendations): ✓ APPLIED

================================================================================
  STEP 4: Verifying Schema
================================================================================
  Column 'vendor_id': ✓ EXISTS
  Column 'vendor_selection_reason': ✓ EXISTS
  Column 'ai_vendor_recommendations': ✓ EXISTS

================================================================================
  STEP 5: Importing Data
================================================================================
  ✓ Loaded export file
    - Vendors: 174
    - Purchase Requisitions: 20
    - Purchase Orders: 115
    - Receipts: 0

  → Importing Vendors...
  ✓ Imported 174 vendors

  → Importing Purchase Requisitions...
  ✓ Imported 20 purchase requisitions

  → Importing Purchase Orders...
  ✓ Imported 115 purchase orders

  ✓ Data import complete!

================================================================================
  STEP 6: Final Verification
================================================================================
  Vendors: 174
  Purchase Requisitions: 20
  Purchase Orders: 115

  ✓ All systems operational!

  You can now access:
    - https://www.radai.ae/procurement/orders
    - https://www.radai.ae/procurement/requisitions

================================================================================
  COMPLETE
================================================================================
Production procurement module is now fully operational!
```

---

## Files Deployed

| File | Purpose |
|------|---------|
| `production_complete_fix.py` | Main fix script (use this) |
| `procurement_export.json` | Data from local database (309 records) |
| `emergency_production_migration.sql` | SQL fallback if Python fails |
| `auto_fix_procurement.sh` | Can be added to Railway build command |
| `apps/procurement/management/commands/fix_production_procurement.py` | Django command version |

---

## Next Steps After Fix

Once the fix completes successfully:

1. ✅ Test Purchase Orders page: https://www.radai.ae/procurement/orders
2. ✅ Test Purchase Requisitions page: https://www.radai.ae/procurement/requisitions
3. ✅ Verify data appears correctly
4. ✅ Test creating new PR/PO
5. ✅ Check vendor selection workflow

---

## Support

If you encounter issues:
1. Check Railway logs: `railway logs`
2. Run status check: `railway run -- python manage.py check_procurement_status`
3. Check this guide's troubleshooting section

The fix script includes comprehensive error messages and will tell you exactly what failed if something goes wrong.
