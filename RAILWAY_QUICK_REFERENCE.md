# Railway Deployment Quick Reference

## ✅ Latest Fixes Applied (2026-01-09)

### Issue 1: PORT Environment Variable
**Error**: `Error: '$PORT' is not a valid port number`
**Fix**: Updated all startup scripts to properly export and expand PORT variable
**Status**: ✅ Fixed in commit 19c3811

### Issue 2: Execute Permissions
**Error**: `We don't have permission to execute your start command`
**Fix**: Set +x permissions on shell scripts using git update-index
**Status**: ✅ Fixed in commit 31a5983

### Issue 3: Dockerfile CMD Format
**Error**: `JSONArgsRecommended` warning and permission issues
**Fix**: Created proper Dockerfile with JSON array CMD and chmod +x
**Status**: ✅ Fixed in commit 6901e04

---

## Current Railway Configuration

### Files Used by Railway (in priority order):
1. **Dockerfile** (highest priority) - Railway will use this
2. railway.toml - Fallback configuration
3. nixpacks.toml - Used if no Dockerfile
4. Procfile - Heroku compatibility

### Dockerfile Configuration:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
# Install deps, copy code, make scripts executable
RUN chmod +x railway_start.sh start.sh
CMD ["bash", "railway_start.sh"]
```

### railway_start.sh:
- Activates venv if exists
- Sets PORT="${PORT:-8000}"
- Runs migrations
- Collects static files
- Starts Gunicorn on correct port

---

## Environment Variables Required on Railway

```bash
# Critical - Must be set
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=<generate-with-django>
PORT=<auto-set-by-railway>

# CORS Configuration
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://www.radai.ae,https://radai.ae

# URLs
FRONTEND_URL=https://www.radai.ae
BACKEND_URL=https://aiflowbackend-production.up.railway.app

# Django Settings
DJANGO_SETTINGS_MODULE=config.settings
DEBUG=False
ENVIRONMENT=production
ALLOWED_HOSTS=aiflowbackend-production.up.railway.app,www.radai.ae,radai.ae

# AWS S3
AWS_ACCESS_KEY_ID=<your-key>
AWS_SECRET_ACCESS_KEY=<your-secret>
AWS_STORAGE_BUCKET_NAME=<your-bucket>
AWS_S3_REGION_NAME=<your-region>
USE_S3=True

# OpenAI
OPENAI_API_KEY=<your-key>
```

---

## Deployment Process

### Automatic (Recommended):
1. Push to `main` branch
2. Railway auto-detects changes
3. Railway runs build (using Dockerfile)
4. Railway deploys container
5. Check logs for success

### Manual via Railway Dashboard:
1. Go to Railway Dashboard
2. Select aiflowbackend-production
3. Click "Deployments" tab
4. Click "Redeploy" button
5. Wait 2-3 minutes
6. Check deployment logs

---

## Troubleshooting

### Deployment fails with "permission denied"
**Solution**: Shell scripts need +x permissions
```bash
git update-index --chmod=+x railway_start.sh start.sh
git commit -m "fix: Add execute permissions"
git push origin main
```

### Deployment fails with "$PORT is not valid"
**Solution**: Check railway_start.sh has proper PORT export
```bash
export PORT="${PORT:-8000}"  # With quotes and braces
```

### Build succeeds but container fails to start
**Check**:
1. Railway logs for Python errors
2. DATABASE_URL is set
3. Migrations succeeded
4. Gunicorn started on correct port

### CORS errors after deployment
**Solution**: Set environment variables on Railway
```bash
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://www.radai.ae,https://radai.ae
```

---

## Verification Commands

### Check if backend is running:
```bash
curl https://aiflowbackend-production.up.railway.app/api/v1/health/
```

### Check CORS configuration:
```bash
curl -I -X OPTIONS https://aiflowbackend-production.up.railway.app/api/v1/auth/login/ \
  -H "Origin: https://www.radai.ae" \
  -H "Access-Control-Request-Method: POST"
```

### Expected successful response:
```
HTTP/2 200
access-control-allow-origin: https://www.radai.ae
access-control-allow-credentials: true
access-control-allow-methods: DELETE, GET, OPTIONS, PATCH, POST, PUT
```

---

## Railway Dashboard URLs

- **Project**: https://railway.app/project/<project-id>
- **Deployments**: Click "Deployments" tab
- **Variables**: Click "Variables" tab
- **Logs**: Click latest deployment → "View Logs"

---

## Success Criteria

✅ Deployment shows "Success" status
✅ Logs show: "🚀 Railway Deployment Starting..."
✅ Logs show: "Starting Gunicorn server..."
✅ Health endpoint responds: `/api/v1/health/`
✅ CORS headers present in OPTIONS response
✅ Login works at https://www.radai.ae/login

---

## Related Documentation

- [RAILWAY_ACTION_PLAN.md](./RAILWAY_ACTION_PLAN.md) - Detailed deployment guide
- [RAILWAY_CORS_FIX.md](./RAILWAY_CORS_FIX.md) - CORS configuration
- [.env.railway.template](./.env.railway.template) - Environment variables
- [verify_railway_cors.py](./verify_railway_cors.py) - CORS testing script
