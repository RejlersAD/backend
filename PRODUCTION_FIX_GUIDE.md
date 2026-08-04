# 🚨 PRODUCTION HEALTH CHECK FAILURE - QUICK FIX GUIDE

## Current Status
- ✅ **Preprod branch**: Working correctly with `railway_start_fast.sh`
- ❌ **Main branch (Production)**: Health check timing out after merge

## Root Cause
When you merged preprod → main, the **code** came over, but **Railway environment variables** are service-specific and were NOT copied to the production service.

---

## 🔧 IMMEDIATE FIX (Choose One Approach)

### **Option 1: Set Environment Variables (RECOMMENDED)**

1. **Go to Railway Dashboard**: https://railway.app/
2. **Find your MAIN/PRODUCTION backend service**
3. **Go to Variables tab**
4. **Copy these variables from your PREPROD service**:

```bash
# Critical Variables to Copy from Preprod to Main:
DATABASE_URL=postgresql://postgres:PASSWORD@hayabusa.proxy.rlwy.net:46432/railway
REDIS_URL=redis://default:PASSWORD@redis.railway.internal:6379
SECRET_KEY=(generate NEW one for production, don't copy preprod's)
DEBUG=False
ALLOWED_HOSTS=.up.railway.app,www.radai.ae,radai.ae
ENVIRONMENT=production
```

**How to copy from Preprod to Main:**
- Railway → Preprod Service → Variables → **Copy values**
- Railway → Main Service → Variables → **Paste values**
- **⚠️ IMPORTANT**: Generate a NEW `SECRET_KEY` for production (don't use preprod's)
- **⚠️ IMPORTANT**: Check if DATABASE_URL should point to a different database for production

5. **After setting variables, trigger redeploy**:
   - Main Service → Deployments → "Redeploy"

---

### **Option 2: Emergency Deployment (If Option 1 Fails)**

If you need to get the service up immediately to debug:

1. **Update Dockerfile to use emergency startup**:
   ```bash
   # In backend/Dockerfile, line 65:
   CMD ["bash", "railway_start_emergency.sh"]
   ```

2. **Commit and push**:
   ```bash
   cd backend
   git add Dockerfile railway_start_emergency.sh check_railway_vars.sh
   git commit -m "fix: Emergency startup mode for debugging"
   git push origin main
   ```

3. **Once deployed, run diagnostic via Railway shell**:
   ```bash
   railway run bash check_railway_vars.sh
   ```

4. **This will show you exactly which environment variables are missing**

5. **Set the missing variables, then switch back to fast start**:
   ```bash
   # Change Dockerfile back to:
   CMD ["bash", "railway_start_fast.sh"]
   ```

---

## 🔍 Why This Happened

**Railway Services are Isolated:**
- Each Railway service has its own environment variables
- When you have separate services for preprod and main branches:
  - Preprod service = `tokaido.proxy.rlwy.net` database
  - Main service = `hayabusa.proxy.rlwy.net` database (different!)
- Merging code (git) does NOT merge Railway configurations

**What You Need to Check:**
1. Does your Main/Production service have a DATABASE_URL set?
2. Is hayabusa the correct production database?
3. Are the credentials correct?

---

## ✅ Verification Steps

After setting environment variables:

1. **Check Railway deployment logs**:
   - Look for: "✅ Django WSGI application loaded successfully"
   - Look for: "✅ Gunicorn started"

2. **Test health endpoint**:
   ```bash
   curl https://your-production-backend.up.railway.app/api/v1/health/
   ```

3. **Expected response**:
   ```json
   {
     "status": "healthy",
     "service": "radai-backend",
     "timestamp": "2026-08-04T..."
   }
   ```

---

## 📋 Environment Variables Checklist for MAIN Branch

Go to Railway → **Main/Production Service** → Variables and verify:

```bash
✅ DATABASE_URL         = postgresql://postgres:***@hayabusa.proxy.rlwy.net:46432/railway
✅ REDIS_URL           = redis://default:***@redis.railway.internal:6379
✅ SECRET_KEY          = (50+ random characters - DIFFERENT from preprod!)
✅ DEBUG               = False
✅ ALLOWED_HOSTS       = .up.railway.app,www.radai.ae,radai.ae
✅ ENVIRONMENT         = production
✅ PORT                = (usually auto-set by Railway)
```

---

## 🆘 If Still Failing

Run the diagnostic script in Railway shell:

```bash
# In Railway Dashboard → Main Service → Shell (or via Railway CLI)
railway run bash check_railway_vars.sh
```

This will tell you exactly what's missing.

---

## 📝 Key Differences: Preprod vs Main

| Aspect | Preprod Branch | Main Branch (Production) |
|--------|----------------|--------------------------|
| Railway Service | Preprod service | Production service |
| Database | tokaido.proxy.rlwy.net | hayabusa.proxy.rlwy.net |
| Environment Vars | Set in Preprod service | **MUST SET** in Production service |
| Auto-deploy | From preprod branch | From main branch |
| Code | Has railway_start_fast.sh ✅ | Has railway_start_fast.sh ✅ (after merge) |

**The issue**: Code merged ✅, but environment variables NOT copied ❌

---

## 🚀 Quick Action Plan

1. ✅ Verify you're setting variables in the **MAIN/PRODUCTION** Railway service (not preprod)
2. ✅ Set DATABASE_URL (copy from Railway → PostgreSQL service → Connect)
3. ✅ Set REDIS_URL (copy from Railway → Redis service → Connect)
4. ✅ Set SECRET_KEY (generate new one)
5. ✅ Set DEBUG=False and ALLOWED_HOSTS
6. ✅ Redeploy
7. ✅ Check logs for "✅ Django WSGI application loaded successfully"

**Most common mistake**: Setting variables in preprod service instead of main service!
