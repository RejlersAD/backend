# Deployment Workflow Guide

## 🆕 New Production Database (Sakura - PostgreSQL 18.4)

**Status:** ✅ Fresh setup  
**External URL (local testing):** `sakura.proxy.rlwy.net:31281`  
**Internal URL (Railway):** `postgres.railway.internal:5432`  

**Setup Guide:** See [FRESH_PRODUCTION_SETUP.md](FRESH_PRODUCTION_SETUP.md)  
**Database Sync:** See [DB_SYNC_GUIDE.md](DB_SYNC_GUIDE.md)

---

## Branch Structure
```
development (active development)
    ↓
preprod (pre-production testing)
    ↓
main (production - Railway deployment)
```

## Automated Batch Files

### 1. `merge_development_to_preprod.bat`
**Purpose:** Merge development into preprod for testing  
**When to use:** After completing features in development branch  
**What it does:**
- ✅ Switches to preprod branch
- ✅ Fetches latest changes
- ✅ Merges development into preprod
- ✅ Pushes to GitHub
- ✅ Triggers Railway preprod deployment

**Usage:**
```bash
# Simply double-click the file, or run:
.\merge_development_to_preprod.bat
```

---

### 2. `merge_preprod_to_main.bat`
**Purpose:** Merge preprod into main for production deployment  
**When to use:** After testing passes in preprod environment  
**What it does:**
- ✅ Switches to main branch
- ✅ Fetches latest changes
- ✅ Merges preprod into main
- ✅ Pushes to GitHub
- ✅ Triggers Railway production deployment

**Usage:**
```bash
# Simply double-click the file, or run:
.\merge_preprod_to_main.bat
```

---

## Complete Workflow

### Step 1: Develop Features
```bash
git checkout development
# Make your changes
git add .
git commit -m "feat: your feature description"
git push origin development
```

### Step 2: Test in Preprod
```bash
# Run the batch file:
.\merge_development_to_preprod.bat

# Or manually:
git checkout preprod
git merge development
git push origin preprod
```

### Step 3: Deploy to Production
```bash
# After testing passes, run:
.\merge_preprod_to_main.bat

# Or manually:
git checkout main
git merge preprod
git push origin main
```

---

## Handling Merge Conflicts

If you see "MERGE CONFLICTS DETECTED":

### Option 1: Manual Resolution (Recommended)
```bash
# 1. Open conflicted files in VS Code
# 2. Resolve conflicts manually
# 3. Stage resolved files
git add .
# 4. Complete the merge
git commit -m "Resolve merge conflicts"
# 5. Push to GitHub
git push origin main
```

### Option 2: Accept All Incoming Changes
```bash
# Accept all changes from the branch being merged
git checkout --theirs .
git add .
git commit -m "Merge with incoming changes"
git push origin main
```

---

## Railway Configuration

### Current Setup (as of 2026-08-04):
- **Main branch:** Connected to Railway production
- **Health check:** `/api/v1/health/` (300s timeout)
- **Start script:** `railway_start_fast.sh` (fast health checks)
- **Dockerfile:** Single-stage production build

### Railway Dashboard:
https://railway.app

**To change deployment branch:**
1. Go to Railway Dashboard
2. Select your backend service
3. Click Settings → Source
4. Change "Branch" to desired branch (main/preprod/development)

---

## Quick Reference Commands

### Check current branch:
```bash
git branch
```

### View recent commits:
```bash
git log --oneline -10
```

### Check remote status:
```bash
git fetch origin
git status
```

### Force sync with remote (DANGEROUS - loses local changes):
```bash
git reset --hard origin/main
```

---

## Troubleshooting

### "Already up-to-date" message:
- No new changes to merge
- Branches are already synchronized

### "Failed to push to GitHub":
- Check internet connection
- Verify GitHub credentials
- Check if you have write access to repository

### "Railway not deploying":
1. Check Railway dashboard for errors
2. Verify branch configuration in Railway settings
3. Check build logs in Railway
4. Verify `railway.toml` configuration

---

## Database Sync (Production)

After deploying to main, if you need to sync databases:
```bash
.\sync_databases.bat
```

Select option 2 for production sync (requires correct Railway production DB credentials).

---

## Files Reference

### Deployment Scripts:
- `merge_development_to_preprod.bat` - Dev → Preprod merge
- `merge_preprod_to_main.bat` - Preprod → Main merge

### Database Scripts:
- `sync_databases.bat` - Interactive database sync menu
- `sync_preprod_quick.bat` - Quick preprod sync
- `copy_preprod_to_production.bat` - Copy local preprod to local production

### Configuration:
- `railway.toml` - Railway deployment configuration
- `railway_start_fast.sh` - Fast startup script for health checks
- `db_sync_config_multi_env.json` - Multi-environment database config

---

## Best Practices

✅ **DO:**
- Test thoroughly in development before merging to preprod
- Test in preprod before deploying to main/production
- Use the batch files for consistency
- Review merge conflicts carefully
- Check Railway dashboard after deployment

❌ **DON'T:**
- Push directly to main without testing in preprod
- Merge with unresolved conflicts
- Skip testing in preprod environment
- Force push to main (`git push -f`) unless absolutely necessary
- Commit database backup files or sensitive data

---

## Emergency Rollback

If production deployment fails:

### Quick rollback to previous commit:
```bash
git checkout main
git log --oneline -10  # Find the last working commit
git reset --hard <commit-hash>
git push -f origin main  # Force push (use with caution!)
```

### Or revert specific commit:
```bash
git checkout main
git revert <commit-hash>
git push origin main
```

---

**Last Updated:** 2026-08-04  
**Main Branch Status:** ✅ Synced with preprod  
**Latest Merge:** Preprod → Main (commit 5de6c54)
