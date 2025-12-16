# CORS Environment Variables Setup

## Overview
This document explains the soft-coded CORS configuration for AIFlow. All CORS settings use environment variables for maximum flexibility across development, staging, and production environments.

## Required Environment Variables (Railway Production)

### Core URLs
```bash
# Frontend URL (Vercel deployment)
FRONTEND_URL=https://airflow-frontend.vercel.app

# Backend URL (Railway deployment)
BACKEND_URL=https://aiflowbackend-production.up.railway.app
```

### CORS Configuration (Optional - uses defaults if not set)
```bash
# Additional allowed origins (comma-separated)
# Leave empty to use defaults (localhost + FRONTEND_URL)
CORS_ALLOWED_ORIGINS=

# Allow all Vercel deployments (default: true)
CORS_ALLOW_VERCEL=true

# Allow localhost/127.0.0.1 (default: true)
CORS_ALLOW_LOCALHOST=true

# Enable CORS for ALL origins (ONLY for testing, NOT recommended for production)
CORS_ALLOW_ALL_ORIGINS=false
```

### CSRF Configuration (Optional)
```bash
# Additional CSRF trusted origins (comma-separated)
CSRF_TRUSTED_ORIGINS=

# Railway public domain (auto-detected by Railway)
RAILWAY_PUBLIC_DOMAIN=aiflowbackend-production.up.railway.app
```

## Default Behavior (No Environment Variables)

If no environment variables are set, the system uses these defaults:

### Allowed Origins:
- `https://airflow-frontend.vercel.app` (production frontend)
- `http://localhost:3000` (React dev server)
- `http://localhost:5173` (Vite dev server)
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5173`
- All `*.vercel.app` domains (via regex)
- All `localhost:*` ports (via regex)

### Trusted Origins (CSRF):
- `https://airflow-frontend.vercel.app`
- `https://aiflowbackend-production.up.railway.app`

## How to Configure Railway

1. Go to your Railway project: https://railway.app/project/your-project-id
2. Click on your backend service
3. Go to **Variables** tab
4. Add these variables:

```bash
FRONTEND_URL=https://airflow-frontend.vercel.app
BACKEND_URL=https://aiflowbackend-production.up.railway.app
```

## How to Configure Vercel

1. Go to your Vercel project: https://vercel.com/your-project
2. Go to **Settings** → **Environment Variables**
3. Add:

```bash
VITE_API_URL=https://aiflowbackend-production.up.railway.app/api/v1
```

## Testing CORS Locally

### Development Environment (.env file)
```bash
# backend/.env
DEBUG=True
FRONTEND_URL=http://localhost:5173
BACKEND_URL=http://localhost:8000
CORS_ALLOW_LOCALHOST=true
```

## CORS Headers Applied

### Preflight (OPTIONS) Requests:
- `Access-Control-Allow-Origin`: (requesting origin)
- `Access-Control-Allow-Credentials`: true
- `Access-Control-Allow-Methods`: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD
- `Access-Control-Allow-Headers`: Accept, Authorization, Content-Type, X-CSRFToken, etc.
- `Access-Control-Max-Age`: 86400 (24 hours)

### Actual Requests:
- `Access-Control-Allow-Origin`: (requesting origin)
- `Access-Control-Allow-Credentials`: true
- `Access-Control-Expose-Headers`: Content-Type, Content-Disposition, X-CSRFToken
- `Vary`: Origin

## Troubleshooting

### Error: "No 'Access-Control-Allow-Origin' header"

**Solution 1: Verify Environment Variables**
```bash
# Railway dashboard → Variables → Check:
FRONTEND_URL=https://airflow-frontend.vercel.app
```

**Solution 2: Check Railway Logs**
```bash
# Look for these lines in Railway logs:
[CorsMiddleware] Loaded allowed origins: ['https://airflow-frontend.vercel.app', ...]
[CorsMiddleware] ✓ OPTIONS request from https://airflow-frontend.vercel.app - ALLOWED
```

**Solution 3: Verify Frontend URL**
```bash
# In frontend .env:
VITE_API_URL=https://aiflowbackend-production.up.railway.app/api/v1
```

### Error: "CORS policy blocking preflight"

This means OPTIONS request is failing. Check:

1. **Railway service is running**
   ```bash
   curl -I https://aiflowbackend-production.up.railway.app/api/v1/health
   ```

2. **CORS middleware is loaded** (check Railway logs for startup messages)

3. **No firewall blocking OPTIONS requests**

### Error: "Mixed Content" or "Blocked by CORS policy"

Ensure all URLs use HTTPS in production:
```bash
# ❌ Wrong
FRONTEND_URL=http://airflow-frontend.vercel.app

# ✅ Correct
FRONTEND_URL=https://airflow-frontend.vercel.app
```

## Architecture

```
┌─────────────────────┐
│  Vercel Frontend    │
│  airflow-frontend   │
│  .vercel.app        │
└──────────┬──────────┘
           │
           │ HTTP Request
           │ Origin: https://airflow-frontend.vercel.app
           ↓
┌─────────────────────┐
│  Railway Backend    │
│  CorsMiddleware     │ ← Checks FRONTEND_URL env var
│  Django             │
└─────────────────────┘
           │
           │ Response with CORS headers:
           │ Access-Control-Allow-Origin: https://airflow-frontend.vercel.app
           ↓
```

## Security Best Practices

1. **Never use `CORS_ALLOW_ALL_ORIGINS=true` in production**
2. **Always specify exact frontend URL in `FRONTEND_URL`**
3. **Use HTTPS for all production URLs**
4. **Keep credentials enabled** (`CORS_ALLOW_CREDENTIALS=true`) for authentication
5. **Review Railway logs** regularly for blocked CORS requests

## Files Modified

- `backend/config/settings.py` - Main CORS configuration with soft-coded defaults
- `backend/apps/core/middleware.py` - Custom CORS middleware with environment variable support
- Both files use `python-decouple` to read environment variables with fallback defaults

## Deployment Checklist

Before deploying to production:

- [ ] Set `FRONTEND_URL` in Railway dashboard
- [ ] Set `BACKEND_URL` in Railway dashboard
- [ ] Set `VITE_API_URL` in Vercel dashboard
- [ ] Verify Railway service is running
- [ ] Test upload from https://airflow-frontend.vercel.app/pid/upload
- [ ] Check Railway logs for CORS messages
- [ ] Confirm no CORS errors in browser console

## Support

If CORS issues persist after following this guide:

1. Check Railway logs: `railway logs` or dashboard
2. Check browser console: Developer Tools → Console → Network tab
3. Verify environment variables are set correctly
4. Ensure both frontend and backend are deployed and running
