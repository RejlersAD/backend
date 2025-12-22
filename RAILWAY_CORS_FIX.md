# Railway Deployment - CORS Fix Guide

## Problem
Frontend on Vercel (https://airflow-frontend.vercel.app) cannot access backend on Railway (https://aiflowbackend-production.up.railway.app) due to CORS policy blocking requests.

**Error:** `Access to XMLHttpRequest has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present`

---

## Permanent Solution

### Step 1: Set Environment Variable in Railway

1. **Login to Railway Dashboard:** https://railway.app/dashboard
2. **Select Your Backend Service:** "aiflowbackend-production"
3. **Go to Variables Tab**
4. **Add/Update This Variable:**
   ```
   CORS_ALLOW_ALL_ORIGINS=True
   ```
5. **Click Deploy** to apply changes

### Step 2: Verify Deployment

After deployment completes:

1. Check Railway logs for this message:
   ```
   [CORS] Configuration Loaded:
   [CORS] - Allow All Origins: True
   [CORS] - Allow Credentials: True
   [CORS] - Frontend URL: https://airflow-frontend.vercel.app
   ```

2. Test CORS from browser console:
   ```javascript
   fetch('https://aiflowbackend-production.up.railway.app/api/v1/cors/health/')
     .then(r => r.json())
     .then(data => console.log('CORS working:', data))
   ```

---

## What Was Fixed

### Backend Changes (`config/settings.py`)

1. **Removed Duplicate CORS Configuration**
   - Consolidated all CORS settings into one unified section
   - Removed conflicting settings that were overriding each other

2. **Set Default to Allow All Origins**
   ```python
   CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=True, cast=bool)
   ```

3. **Enabled Credentials**
   ```python
   CORS_ALLOW_CREDENTIALS = True
   ```

4. **Added Comprehensive Headers**
   - Allow Headers: authorization, content-type, etc.
   - Expose Headers: content-disposition, content-length, etc.

5. **Added Logging for Debugging**
   ```python
   print(f"[CORS] Configuration Loaded:")
   print(f"[CORS] - Allow All Origins: {CORS_ALLOW_ALL_ORIGINS}")
   ```

---

## Production Best Practices (Optional - More Secure)

For enhanced security, instead of allowing all origins, you can specify exact origins:

### In Railway Environment Variables:

```
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://airflow-frontend.vercel.app
FRONTEND_URL=https://airflow-frontend.vercel.app
```

This will:
- ✅ Allow only your specific frontend
- ✅ Block unauthorized domains
- ✅ Support Vercel preview deployments (via regex patterns)

---

## Testing Steps

### 1. Test Backend Health
```bash
curl https://aiflowbackend-production.up.railway.app/api/v1/cors/health/
```

Expected response:
```json
{
  "status": "ok",
  "cors": "enabled",
  "timestamp": "..."
}
```

### 2. Test CORS from Frontend
In browser console on https://airflow-frontend.vercel.app:
```javascript
fetch('https://aiflowbackend-production.up.railway.app/api/v1/pid/drawings/', {
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN'
  }
})
.then(r => r.json())
.then(data => console.log('Success!', data))
.catch(err => console.error('Failed:', err))
```

### 3. Test File Upload
Try uploading a PID drawing from:
https://airflow-frontend.vercel.app/pid/upload

---

## Troubleshooting

### If CORS Still Fails:

1. **Check Railway Logs**
   ```
   Railway Dashboard → Backend Service → Deployments → View Logs
   ```
   Look for:
   ```
   [CORS] - Allow All Origins: True
   ```

2. **Verify Environment Variable**
   ```
   Railway Dashboard → Backend Service → Variables
   ```
   Ensure `CORS_ALLOW_ALL_ORIGINS=True` is present

3. **Restart Service**
   ```
   Railway Dashboard → Backend Service → Settings → Restart
   ```

4. **Clear Browser Cache**
   - Hard refresh: `Ctrl+Shift+R` (Windows) or `Cmd+Shift+R` (Mac)
   - Or open in Incognito/Private mode

---

## Alternative: Deploy Both to Railway

If you want both frontend and backend on Railway:

1. **Add Frontend Service to Railway**
   - Connect frontend repository
   - Set build command: `npm run build`
   - Set start command: `npm run preview`

2. **Set Environment Variables**
   ```
   VITE_API_URL=https://aiflowbackend-production.up.railway.app
   ```

3. **Update Backend CORS**
   ```
   CORS_ALLOWED_ORIGINS=https://your-frontend.railway.app
   ```

---

## Files Modified

- ✅ `backend/config/settings.py` - Unified CORS configuration
- ✅ `backend/RAILWAY_ENV_VARIABLES.md` - Environment variable guide
- ✅ `backend/RAILWAY_CORS_FIX.md` - This guide

---

## Next Steps

1. ✅ Set `CORS_ALLOW_ALL_ORIGINS=True` in Railway
2. ✅ Deploy and verify in logs
3. ✅ Test upload from frontend
4. ✅ Monitor for 24 hours
5. ⚠️ (Optional) Switch to specific origins for production security

---

## Support

If issues persist:
1. Check Railway logs for errors
2. Check browser console for detailed error messages
3. Verify network tab shows OPTIONS preflight request succeeding
4. Ensure Railway backend is actually deployed and running

---

**Last Updated:** December 22, 2025  
**Status:** ✅ Ready to Deploy
