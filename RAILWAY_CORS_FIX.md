# Railway CORS Configuration Fix

## Problem
CORS error on production: `No 'Access-Control-Allow-Origin' header is present on the requested resource`

Frontend: https://www.radai.ae  
Backend: https://aiflowbackend-production.up.railway.app

## Solution: Set Environment Variables on Railway

### Step 1: Go to Railway Dashboard
1. Open [Railway Dashboard](https://railway.app)
2. Select your `aiflowbackend-production` project
3. Click on **Variables** tab

### Step 2: Add/Update These Environment Variables

```bash
# CORS Configuration (CRITICAL - Must be set correctly)
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://www.radai.ae,https://radai.ae,http://localhost:5173

# Frontend URL (for email links and redirects)
FRONTEND_URL=https://www.radai.ae

# Backend URL (for absolute URLs)
BACKEND_URL=https://aiflowbackend-production.up.railway.app

# Django Settings
DJANGO_SETTINGS_MODULE=config.settings
DEBUG=False
ENVIRONMENT=production

# Database (should already be set)
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Security
SECRET_KEY=<your-secret-key>
ALLOWED_HOSTS=aiflowbackend-production.up.railway.app,www.radai.ae,radai.ae

# AWS S3 (if used)
AWS_ACCESS_KEY_ID=<your-aws-key>
AWS_SECRET_ACCESS_KEY=<your-aws-secret>
AWS_STORAGE_BUCKET_NAME=<your-bucket-name>
AWS_S3_REGION_NAME=<your-region>

# OpenAI API
OPENAI_API_KEY=<your-openai-key>
```

### Step 3: Redeploy Backend
After setting environment variables, Railway will automatically redeploy.

If not, manually trigger a redeploy:
1. Go to **Deployments** tab
2. Click on the latest deployment
3. Click **Redeploy**

### Step 4: Verify CORS Headers

Test with curl:
```bash
curl -I -X OPTIONS https://aiflowbackend-production.up.railway.app/api/v1/auth/login/ \
  -H "Origin: https://www.radai.ae" \
  -H "Access-Control-Request-Method: POST"
```

Expected response should include:
```
Access-Control-Allow-Origin: https://www.radai.ae
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: DELETE, GET, OPTIONS, PATCH, POST, PUT
```

## Alternative: Use Railway CLI

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Link to project
railway link

# Set environment variables
railway variables set CORS_ALLOW_ALL_ORIGINS=False
railway variables set CORS_ALLOWED_ORIGINS="https://www.radai.ae,https://radai.ae,http://localhost:5173"
railway variables set FRONTEND_URL="https://www.radai.ae"
railway variables set BACKEND_URL="https://aiflowbackend-production.up.railway.app"

# Trigger redeploy
railway up
```

## Important Notes

1. **DO NOT** set `CORS_ALLOW_ALL_ORIGINS=True` - This will break JWT authentication
2. **MUST** include both `https://www.radai.ae` and `https://radai.ae` (with and without www)
3. **MUST** set `CORS_ALLOWED_ORIGINS` as comma-separated list without spaces
4. After changing variables, wait 2-3 minutes for Railway to redeploy

## Troubleshooting

### If CORS error persists:

1. **Check Railway Logs:**
   ```bash
   railway logs
   ```
   Look for: `[CORS] Allowed Origins:` line

2. **Check if variables are set:**
   ```bash
   railway variables
   ```

3. **Force fresh deployment:**
   - Go to Railway Dashboard
   - Settings → Delete all environment variables
   - Re-add them one by one
   - Redeploy

4. **Check DNS:**
   Make sure www.radai.ae points to the correct Vercel deployment

## Quick Check Commands

```bash
# Check if backend is responding
curl https://aiflowbackend-production.up.railway.app/api/v1/health/

# Check CORS preflight
curl -I -X OPTIONS https://aiflowbackend-production.up.railway.app/api/v1/auth/login/ \
  -H "Origin: https://www.radai.ae" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type"
```

## Expected Behavior

✅ Login from https://www.radai.ae should work  
✅ Login from https://radai.ae should work  
✅ Login from http://localhost:5173 should work (development)  
❌ Login from other origins should be blocked
