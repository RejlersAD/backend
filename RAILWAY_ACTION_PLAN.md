# 🚨 URGENT: Railway Backend CORS Fix Action Plan

## Problem Analysis
- **Error**: `No 'Access-Control-Allow-Origin' header` when logging in from https://www.radai.ae
- **Backend**: https://aiflowbackend-production.up.railway.app (currently timing out)
- **Root Cause**: Railway backend either not running OR missing CORS environment variables

## ✅ Solution Steps (Do these IN ORDER)

### Step 1: Check Railway Deployment Status
1. Go to https://railway.app/dashboard
2. Find project: **aiflowbackend-production**
3. Check if deployment is:
   - ✅ **Active/Running** → Proceed to Step 2
   - ❌ **Stopped/Sleeping** → Click "Redeploy" → Wait 3 minutes → Proceed to Step 2
   - ❌ **Failed** → Check logs, fix errors → Redeploy

### Step 2: Pull Latest Code from GitHub
Railway should auto-deploy from GitHub, but if not:

1. In Railway Dashboard → Settings → GitHub
2. Verify connected to: `rejlersabudhabi1-RAD/aiflow_backend`
3. Verify watching branch: `main`
4. If not auto-deployed, click: **"Redeploy"** button

### Step 3: Set Environment Variables (CRITICAL)

Click **Variables** tab in Railway, then **Raw Editor**, paste this:

```bash
# CRITICAL: CORS Configuration
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://www.radai.ae,https://radai.ae,http://localhost:5173

# Django Settings
DJANGO_SETTINGS_MODULE=config.settings
DEBUG=False
ENVIRONMENT=production
SECRET_KEY=<copy-from-existing-or-generate-new>
ALLOWED_HOSTS=aiflowbackend-production.up.railway.app,www.radai.ae,radai.ae

# URLs
FRONTEND_URL=https://www.radai.ae
BACKEND_URL=https://aiflowbackend-production.up.railway.app

# Database (Should already exist)
DATABASE_URL=${{Postgres.DATABASE_URL}}

# AWS S3 (Copy from existing)
AWS_ACCESS_KEY_ID=<copy-from-existing>
AWS_SECRET_ACCESS_KEY=<copy-from-existing>
AWS_STORAGE_BUCKET_NAME=<copy-from-existing>
AWS_S3_REGION_NAME=<copy-from-existing>
USE_S3=True

# OpenAI (Copy from existing)
OPENAI_API_KEY=<copy-from-existing>
```

**⚠️ IMPORTANT**: Replace `<copy-from-existing>` values with actual credentials already in Railway

### Step 4: Verify Variables Are Set
After saving variables, Railway will auto-redeploy. Verify:

1. Click **Variables** tab
2. Check these are present:
   - ✅ `CORS_ALLOW_ALL_ORIGINS` = `False`
   - ✅ `CORS_ALLOWED_ORIGINS` = `https://www.radai.ae,https://radai.ae,...`
   - ✅ `DATABASE_URL` = `postgresql://...`
   - ✅ `SECRET_KEY` = (exists)

### Step 5: Wait for Deployment
1. Click **Deployments** tab
2. Wait for latest deployment to show **"Success"** (takes 2-3 minutes)
3. Check logs for: `[CORS] Allowed Origins:` line

### Step 6: Test CORS
Run verification script:
```bash
cd backend
python verify_railway_cors.py
```

Expected output:
```
✅ Status Code: 200
✅ CORS Origin: https://www.radai.ae
✅ Allow Credentials: true
```

### Step 7: Test Login
1. Go to https://www.radai.ae/login
2. Enter credentials:
   - Email: `tanzeem.agra@rejlers.ae`
   - Password: `Tanzeem@123`
3. Should login successfully ✅

---

## 🔧 Alternative Solution: Railway CLI

If dashboard doesn't work, use CLI:

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Link to project
railway link

# Check current variables
railway variables

# Set CORS variables
railway variables set CORS_ALLOW_ALL_ORIGINS=False
railway variables set CORS_ALLOWED_ORIGINS="https://www.radai.ae,https://radai.ae,http://localhost:5173"

# Check logs
railway logs

# Force redeploy
railway up
```

---

## 🔍 Troubleshooting

### Problem: Backend still timing out after 5 minutes
**Solution**:
1. Check Railway logs: `railway logs` or Dashboard → Logs
2. Look for errors like:
   - `ModuleNotFoundError` → requirements.txt missing package
   - `django.db.utils.OperationalError` → DATABASE_URL not set
   - `ImproperlyConfigured` → Missing environment variable

### Problem: CORS headers still missing
**Solution**:
1. Verify `corsheaders` is in requirements.txt
2. Check logs show: `[CORS] Allowed Origins: ['https://www.radai.ae', ...]`
3. Try accessing: https://aiflowbackend-production.up.railway.app/api/v1/health/
4. If 500 error → Django config issue, check logs

### Problem: Login works but shows 401 Unauthorized
**Solution**:
1. Wrong credentials or user doesn't exist
2. Check database: Railway → PostgreSQL → Query
3. Verify user: `SELECT email FROM auth_user;`

---

## 📋 Quick Checklist

Before testing login, confirm ALL of these:

- [ ] Railway deployment status = "Success"  
- [ ] Railway logs show Django starting successfully
- [ ] `CORS_ALLOWED_ORIGINS` environment variable is set
- [ ] `DATABASE_URL` environment variable is set
- [ ] `SECRET_KEY` environment variable is set
- [ ] Backend responds to: https://aiflowbackend-production.up.railway.app/api/v1/health/
- [ ] Browser console shows OPTIONS request succeeds (200)
- [ ] Browser console shows CORS headers in response

---

## 📞 If Nothing Works

Last resort - Create fresh Railway deployment:

1. Railway Dashboard → New Project → Deploy from GitHub
2. Select: `rejlersabudhabi1-RAD/aiflow_backend`
3. Branch: `main`
4. Add PostgreSQL plugin
5. Copy ALL environment variables from old project
6. Update domain: Settings → Generate Domain
7. Update frontend `.env.production` with new backend URL
8. Redeploy frontend on Vercel

---

## 📚 Related Documentation
- [RAILWAY_CORS_FIX.md](./RAILWAY_CORS_FIX.md) - Detailed CORS configuration
- [.env.railway.template](./.env.railway.template) - All environment variables
- [verify_railway_cors.py](./verify_railway_cors.py) - CORS verification script

---

## ✅ Success Criteria

Login is fixed when:
1. ✅ https://www.radai.ae/login loads without errors
2. ✅ Browser console shows NO CORS errors
3. ✅ Login button shows "Logging in..." then redirects to dashboard
4. ✅ User sees their name/email in dashboard
