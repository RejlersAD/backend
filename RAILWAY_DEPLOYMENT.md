# Railway Deployment Guide

## Environment Variables Required

Set these in your Railway dashboard:

### Database (Automatic - provided by Railway Postgres)
- `DATABASE_URL` - Auto-populated by Railway

### Django Settings
- `SECRET_KEY` - Django secret key (generate new for production)
- `DEBUG` - Set to `False` for production
- `ALLOWED_HOSTS` - Set to `*` or your domain

### Optional Services
- `REDIS_URL` - For Celery (if using Redis addon)
- `MONGODB_URI` - For MongoDB (if using MongoDB addon)
- `OPENAI_API_KEY` - For AI features
- `AWS_ACCESS_KEY_ID` - For S3 storage
- `AWS_SECRET_ACCESS_KEY` - For S3 storage
- `AWS_STORAGE_BUCKET_NAME` - S3 bucket name
- `AWS_S3_REGION_NAME` - S3 region

## Deployment Process

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Update deployment configuration"
   git push origin main
   ```

2. **Railway Auto-Deploy**
   - Railway detects changes
   - Builds using nixpacks
   - Runs railway_start.sh
   - Health check at /api/v1/health/
   - Deployment complete

3. **Monitor Deployment**
   - Check Railway dashboard logs
   - Verify health check passes
   - Test application endpoints

## Health Check Endpoint

URL: `/api/v1/health/`
- Returns 200 OK when app is running
- No authentication required
- Lightweight check

## Manual Operations

### Run Migrations (if needed separately)
```bash
railway run python manage.py migrate
```

### Create Superuser
```bash
railway run python manage.py createsuperuser
```

### Collect Static Files (manual)
```bash
railway run python manage.py collectstatic --noinput
```

## Troubleshooting

### Deployment Fails
1. Check Railway logs for errors
2. Verify environment variables are set
3. Ensure DATABASE_URL is configured
4. Check railway_start.sh has execute permissions

### Health Check Fails
1. Verify /api/v1/health/ endpoint exists
2. Check app starts successfully
3. Review gunicorn logs
4. Ensure port $PORT is used

### Database Issues
1. Railway Postgres should auto-configure
2. Check DATABASE_URL is set
3. Verify migrations ran successfully
4. Check database connection in logs

## Production Checklist

- [ ] DEBUG = False
- [ ] SECRET_KEY is secure and unique
- [ ] ALLOWED_HOSTS configured
- [ ] DATABASE_URL set (Railway Postgres)
- [ ] Static files collected
- [ ] Migrations applied
- [ ] Environment variables configured
- [ ] Health check passing
- [ ] SSL/HTTPS enabled (Railway default)

## Deployment Files

- `railway.toml` - Railway configuration
- `nixpacks.toml` - Build configuration
- `railway_start.sh` - Startup script
- `Procfile` - Process definitions
- `requirements.txt` - Python dependencies

## Support

For Railway-specific issues:
- Railway Documentation: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
