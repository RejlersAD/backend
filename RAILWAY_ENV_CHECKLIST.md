# Railway Environment Variables Checklist

## 🚨 Critical - Production Deployment Failing?

If your Railway deployment is failing with health check timeouts, follow this checklist.

---

## ✅ Required Environment Variables

Go to **Railway Dashboard → Your Backend Service → Variables Tab** and verify these are set:

### 1. Database Configuration (CRITICAL)
```bash
DATABASE_URL=postgresql://postgres:PASSWORD@hayabusa.proxy.rlwy.net:46432/railway
```
- **Where to get it**: Railway → PostgreSQL Service → Connect → Connection String
- **Common issue**: Using wrong credentials or wrong database service
- **Test it**: Try connecting with psql or Python before deploying

### 2. Redis Configuration (CRITICAL for Celery/Cache)
```bash
REDIS_URL=redis://default:PASSWORD@redis.railway.internal:6379
```
- **Where to get it**: Railway → Redis Service → Connect → Connection String
- **Note**: Use `.railway.internal` hostname (NOT `.proxy.rlwy.net` for internal services)
- **Optional**: If not using Celery, Django will fall back to in-memory cache

### 3. Django Secret Key (CRITICAL)
```bash
SECRET_KEY=your-super-secret-key-minimum-50-characters-long-and-random
```
- **Generate new key**: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- **Security**: Never commit this to git, only set in Railway dashboard

### 4. Debug Mode (CRITICAL for production)
```bash
DEBUG=False
```
- **Production**: MUST be `False`
- **Warning**: Setting `True` in production exposes sensitive information

### 5. Allowed Hosts (CRITICAL)
```bash
ALLOWED_HOSTS=.up.railway.app,www.radai.ae,radai.ae,aiflowbackend-production.up.railway.app
```
- **Include**: Your Railway domain, custom domain, and subdomains
- **Format**: Comma-separated, no spaces

### 6. Port (Usually auto-set by Railway)
```bash
PORT=8000
```
- **Note**: Railway typically sets this automatically
- **Only set manually if**: You have a specific port requirement

---

## 🔧 Optional But Recommended

### 7. CORS Configuration
```bash
CORS_ALLOWED_ORIGINS=https://www.radai.ae,https://radai.ae
CORS_EMERGENCY_MODE=True
```

### 8. AWS Configuration (if using S3)
```bash
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_S3_REGION_NAME=us-east-1
```

### 9. Gunicorn Configuration (Performance Tuning)
```bash
GUNICORN_WORKERS=3
GUNICORN_TIMEOUT=600
GUNICORN_GRACEFUL_TIMEOUT=30
```

### 10. Environment Indicator
```bash
ENVIRONMENT=production
RAILWAY_ENVIRONMENT=production
```

---

## 🔍 How to Verify Your Setup

### Step 1: Check Database Connection
Run this in your local terminal with production credentials:
```powershell
$env:PGPASSWORD='YOUR_PRODUCTION_PASSWORD'
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h hayabusa.proxy.rlwy.net -p 46432 -U postgres -d railway -c "SELECT 1;"
```

If this fails, your DATABASE_URL is wrong.

### Step 2: Check Railway Deployment Logs
```bash
# In Railway Dashboard → Backend Service → Deployments → Latest → View Logs
# Look for:
# - "✅ Django WSGI application loaded successfully" = Good!
# - "⚠️ Django failed to load" = Check DATABASE_URL
# - "connection refused" = Redis/Database not accessible
# - "timeout" = Health check endpoint not responding
```

### Step 3: Test Health Endpoint Locally
Once deployed, test the health endpoint:
```bash
curl https://your-backend-url.up.railway.app/api/v1/health/
```

Expected response:
```json
{
  "status": "healthy",
  "service": "radai-backend",
  "timestamp": "2026-08-04T12:34:56.789"
}
```

---

## 🚀 Quick Fix Workflow

1. **Go to Railway Dashboard**
   - Your Project → Backend Service → Variables tab

2. **Verify DATABASE_URL**
   - Go to PostgreSQL service → Connect tab
   - Copy the "Public Connection String"
   - Paste it as `DATABASE_URL` in backend service

3. **Verify REDIS_URL** (if using Redis)
   - Go to Redis service → Connect tab
   - Copy the connection string (use `.railway.internal` for internal access)
   - Paste it as `REDIS_URL` in backend service

4. **Set Other Required Variables**
   - SECRET_KEY (generate new one)
   - DEBUG=False
   - ALLOWED_HOSTS=(your domains)

5. **Trigger Redeploy**
   - Backend service → Deployments → "Redeploy"
   - OR: Push a new commit to trigger auto-deploy

6. **Watch Deployment Logs**
   - Look for: "✅ Django WSGI application loaded successfully"
   - Look for: "✅ Gunicorn started"
   - Look for: "✅ Health check PASSED"

---

## 🆘 Still Failing? Check These

### Issue: "Database connection failed"
- ✅ Is hayabusa the correct production database?
- ✅ Are you using the PUBLIC connection string (not internal)?
- ✅ Is the password correct? (Check Railway dashboard)
- ✅ Is the database actually created? (Check Railway → Database → Tables)

### Issue: "Redis connection failed"
- ✅ Is Redis service running in Railway?
- ✅ Are you using `.railway.internal` hostname (not `.proxy.rlwy.net`)?
- ✅ Is the password correct?
- ℹ️ Optional: Remove REDIS_URL to use in-memory cache (less ideal but works)

### Issue: "Health check timeout"
- ✅ Is Gunicorn starting? (Check logs for "Gunicorn started")
- ✅ Is Django loading? (Check for bulletproof WSGI fallback messages)
- ✅ Is PORT set correctly? (Should be 8000 or Railway's automatic value)
- ✅ Is the health endpoint path correct? (`/api/v1/health/`)

### Issue: "Migration errors"
- ✅ Are you using railway_start_fast.sh? (Runs migrations in background)
- ✅ Is the database schema initialized? (May need first migration)
- ℹ️ Consider: Run migrations manually via Railway shell first

---

## 📞 Next Steps

After setting all environment variables:

1. **Redeploy from Railway Dashboard**
2. **Watch deployment logs carefully**
3. **Test health endpoint**: `curl https://your-backend.up.railway.app/api/v1/health/`
4. **If still failing**: Share the deployment logs for further diagnosis

---

## 🔐 Security Reminder

- ❌ **Never** commit environment variables to git
- ✅ **Always** set sensitive values in Railway dashboard
- ✅ **Always** use `DEBUG=False` in production
- ✅ **Always** generate a new SECRET_KEY for production (don't use the default)
