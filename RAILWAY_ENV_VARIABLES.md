# Railway Environment Variables Configuration

## Required Environment Variables for Production Deployment

Add these environment variables in your Railway project settings:

### Core Django Settings
```
SECRET_KEY=your-super-secret-key-change-this-in-production
DEBUG=False
ALLOWED_HOSTS=*
```

### Frontend Configuration
```
FRONTEND_URL=https://airflow-frontend.vercel.app
```

### CORS Configuration (IMPORTANT!)
```
CORS_ALLOW_ALL_ORIGINS=True
```

### Database (Railway provides PostgreSQL automatically)
```
DATABASE_URL=postgresql://user:password@host:port/dbname
```
*Note: Railway auto-injects this from your PostgreSQL service*

### Redis (for Celery)
```
CELERY_BROKER_URL=redis://your-redis-host:6379/0
CELERY_RESULT_BACKEND=redis://your-redis-host:6379/0
```

### MongoDB (if needed)
```
MONGODB_URI=mongodb://user:password@host:port/dbname
```

### Optional - Additional CORS Origins (comma-separated)
```
CORS_ALLOWED_ORIGINS=https://airflow-frontend.vercel.app,https://your-other-domain.com
```

### Optional - Backend URL
```
BACKEND_URL=https://aiflowbackend-production.up.railway.app
```

---

## Quick Fix for Current CORS Issue

### Option 1: Enable Emergency Mode (Temporary - allows all origins)
In Railway dashboard, add/update:
```
CORS_ALLOW_ALL_ORIGINS=True
```

### Option 2: Specific Origins (Production-Ready)
In Railway dashboard, add:
```
CORS_ALLOWED_ORIGINS=https://airflow-frontend.vercel.app
FRONTEND_URL=https://airflow-frontend.vercel.app
```

---

## How to Add Environment Variables in Railway

1. Go to your Railway project: https://railway.app/dashboard
2. Click on your backend service
3. Go to **Variables** tab
4. Click **+ New Variable**
5. Add each variable with its value
6. Click **Deploy** to apply changes

---

## Verify CORS is Working

After adding environment variables, check the logs:
```
[CORS] EMERGENCY MODE: Allow All Origins = True
```

If you see this message with `True`, CORS should work for all origins.

---

## Production Best Practices

For production, instead of `CORS_ALLOW_ALL_ORIGINS=True`, use specific origins:

```
CORS_ALLOWED_ORIGINS=https://airflow-frontend.vercel.app,https://preview-*.vercel.app
CORS_ALLOW_CREDENTIALS=True
```

This is more secure and only allows your specific frontend domains.
