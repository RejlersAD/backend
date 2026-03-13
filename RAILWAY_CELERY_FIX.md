# Railway Celery Configuration Fix - Line List Timeout Issue

## Problem
Line List uploads were timing out after 10 minutes because the backend was running in EAGER mode, causing synchronous task execution that blocks HTTP requests.

## Solution
Set `CELERY_TASK_ALWAYS_EAGER=False` to enable async processing.

## Railway Deployment Steps

### 1. Update Railway Environment Variables

Go to Railway Dashboard: https://railway.app/dashboard

1. Select project: **aiflowbackend-production**
2. Click **Variables** tab
3. Add/Update these variables:

```env
CELERY_TASK_ALWAYS_EAGER=False
CELERY_BASE_EXTRACTION_PREFER_CELERY=False
```

4. Click **Save** (Railway will auto-redeploy)
5. Wait 2-3 minutes for deployment

### 2. Verify the Fix

After deployment, check logs:
```bash
# Should see:
[CELERY] base_extraction prefer_celery: False
# Should NOT see:
[CELERY] ✓ EAGER mode enabled via environment variable
```

### 3. Test Line List Upload

1. Go to https://www.radai.ae/engineering/process/line-list
2. Upload a P&ID file
3. **Expected behavior:**
   - Upload completes in 1-2 seconds (returns HTTP 202)
   - Progress bar shows "Processing in background"
   - Frontend polls status every few seconds
   - No timeout errors

## Technical Details

**Before Fix:**
- `CELERY_TASK_ALWAYS_EAGER=True` (or not set, defaults to True in local dev)
- Backend processes Line List extraction synchronously
- HTTP request blocks for 10+ minutes
- Frontend AbortController times out after 10 minutes
- Result: "Upload timed out" error

**After Fix:**
- `CELERY_TASK_ALWAYS_EAGER=False`
- Backend returns HTTP 202 immediately with `task_id`
- Extraction runs in background thread
- Frontend polls `/base_extraction_status/{task_id}/` for progress
- Result: Smooth async experience with progress updates

## Files Changed
- `.env.railway.template` - Added Celery configuration section
- This guide (RAILWAY_CELERY_FIX.md)

## Local Testing (Already Verified)
✅ Tested locally with Docker Compose
✅ Backend running in async mode
✅ Line List upload completes without timeout
✅ Progress polling works correctly

## Deployment Checklist
- [ ] Update Railway environment variables
- [ ] Wait for auto-redeploy (2-3 min)
- [ ] Check Railway logs for async mode confirmation
- [ ] Test Line List upload on production
- [ ] Verify no timeout errors
- [ ] Confirm progress updates work

---
**Date:** March 13, 2026
**Issue:** Line List upload timeout after 10 minutes
**Resolution:** Enable async Celery processing in Railway
