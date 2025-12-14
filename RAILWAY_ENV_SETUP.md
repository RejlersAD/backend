# Railway Environment Variables Setup

## Required Environment Variables for CORS

To fix CORS issues and enable proper frontend-backend communication, set these environment variables in your Railway project:

### Go to Railway Dashboard
1. Navigate to: https://railway.app/
2. Select your `aiflowbackend` project
3. Click on **Variables** tab
4. Add the following variables:

### Critical CORS Variables

```bash
# Frontend URL (your Vercel deployment)
FRONTEND_URL=https://airflow-frontend.vercel.app

# Additional CORS allowed origins (comma-separated)
CORS_ALLOWED_ORIGINS=https://airflow-frontend.vercel.app,http://localhost:3000,http://localhost:5173

# Debug mode (set to False in production)
DEBUG=False

# Allowed hosts (comma-separated)
ALLOWED_HOSTS=aiflowbackend-production.up.railway.app,.railway.app,*
```

### Existing Variables (verify these are set)

```bash
# Database (already provided by Railway PostgreSQL)
DATABASE_URL=<automatically set by Railway>

# Security
SECRET_KEY=<your-secret-key>

# AWS S3 (if using)
USE_S3=True
AWS_STORAGE_BUCKET_NAME=<your-bucket>
AWS_ACCESS_KEY_ID=<your-key>
AWS_SECRET_ACCESS_KEY=<your-secret>
AWS_S3_REGION_NAME=<your-region>
```

## Quick Setup Commands

After adding the variables in Railway dashboard, the deployment will automatically restart. 

### Verify CORS is Working

1. Check Railway deployment logs for:
   ```
   [CORS] Allowed Origins: ['https://airflow-frontend.vercel.app', ...]
   [CorsMiddleware] Loaded allowed origins: ['https://airflow-frontend.vercel.app', ...]
   ```

2. Test with curl:
   ```bash
   curl -I -X OPTIONS \
     -H "Origin: https://airflow-frontend.vercel.app" \
     -H "Access-Control-Request-Method: GET" \
     https://aiflowbackend-production.up.railway.app/api/v1/rbac/users/stats/
   ```

   Should return headers including:
   ```
   Access-Control-Allow-Origin: https://airflow-frontend.vercel.app
   Access-Control-Allow-Credentials: true
   ```

## Troubleshooting

### If CORS errors persist:

1. **Check Railway Logs**:
   - Go to Railway dashboard → Deployments → View logs
   - Look for "[CORS]" messages showing allowed origins

2. **Verify Environment Variables**:
   - Railway dashboard → Variables
   - Ensure FRONTEND_URL is set correctly
   - No trailing slashes in URLs

3. **Force Redeploy**:
   ```bash
   # From your local backend directory
   git commit --allow-empty -m "Force redeploy"
   git push origin main
   ```

4. **Clear Browser Cache**:
   - Open DevTools (F12)
   - Right-click refresh → "Empty Cache and Hard Reload"

### Common Issues

- **Error**: "No 'Access-Control-Allow-Origin' header"
  - **Fix**: Ensure FRONTEND_URL environment variable is set in Railway

- **Error**: "CORS policy: credentials mode"
  - **Fix**: Already handled - CORS_ALLOW_CREDENTIALS=True is set

- **Error**: "Method not allowed"
  - **Fix**: Already handled - all methods (GET, POST, PUT, DELETE) are allowed

## Soft-Coded Configuration

All CORS settings use environment variables:
- `FRONTEND_URL` - Main frontend domain
- `CORS_ALLOWED_ORIGINS` - Additional allowed origins (comma-separated)
- `VERCEL_URL` - Automatically detected Vercel deployments
- Pattern matching for `*.vercel.app` domains is enabled

No code changes needed to add new origins - just update Railway environment variables!
