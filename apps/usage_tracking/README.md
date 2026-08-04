# Usage Tracking & Metering System

## Overview

The RADAI Usage Tracking System provides comprehensive analytics on how employees use the platform. It automatically tracks:

- **User Activity**: Every API request per user
- **Department Usage**: Aggregate statistics by department
- **Feature Adoption**: Which modules are most used
- **Resource Consumption**: AI tokens, processing time
- **Performance Metrics**: Response times, error rates
- **Time-based Analytics**: Daily, weekly, monthly trends

## Key Features

✅ **Non-Invasive Design**: Middleware-based tracking - zero code changes required
✅ **Async Logging**: Background threads prevent performance impact
✅ **Smart Caching**: Redis-cached summaries for fast dashboard loading
✅ **Soft-Coded**: Easy to enable/disable via environment variables
✅ **Role-Based Access**: Admins, department heads, and users see appropriate data
✅ **Auto-Aggregation**: Periodic tasks update summary tables
✅ **Scalable**: Designed for high-traffic production environments

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT REQUEST                            │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              USAGE TRACKING MIDDLEWARE                       │
│  1. Capture request metadata                                 │
│  2. Measure processing time                                  │
│  3. Extract tokens used                                      │
│  4. Log asynchronously (background thread)                   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  DATABASE MODELS                             │
│  - UserUsageLog: Detailed logs                               │
│  - DepartmentUsageSummary: Department aggregates             │
│  - FeatureUsageSummary: Feature aggregates                   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              PERIODIC ASYNC TASKS                            │
│  - Aggregate summaries (every 15 min)                        │
│  - Update cached metrics (every 5 min)                       │
│  - Cleanup old logs (daily)                                  │
│  - Generate reports (daily)                                  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                   REST API                                   │
│  GET /api/v1/usage/summary/                                  │
│  GET /api/v1/usage/user/{id}/                                │
│  GET /api/v1/usage/department/{name}/                        │
│  GET /api/v1/usage/feature/{feature}/                        │
│  GET /api/v1/usage/sales-report/                             │
│  GET /api/v1/usage/top-users/                                │
│  GET /api/v1/usage/trends/                                   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              REACT DASHBOARD                                 │
│  - Overview cards (requests, users, tokens)                  │
│  - Department usage chart                                    │
│  - Feature usage chart                                       │
│  - Top users table                                           │
│  - Usage trends over time                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Installation & Setup

### Step 1: Database Migration

```bash
cd backend
python manage.py makemigrations usage_tracking
python manage.py migrate usage_tracking
```

### Step 2: Verify Configuration

Check `backend/config/settings.py`:

```python
# Should be already added
INSTALLED_APPS = [
    # ...
    'apps.usage_tracking',
]

MIDDLEWARE = [
    # ...
    'apps.usage_tracking.middleware.UsageTrackingMiddleware',  # Added
]

# Usage tracking settings (at end of file)
ENABLE_USAGE_TRACKING = True
USAGE_LOG_RETENTION_DAYS = 90
USAGE_CACHE_TTL = 300
```

### Step 3: Run Initial Aggregation

```bash
python manage.py aggregate_usage_stats
```

### Step 4: Restart Backend

```bash
# Docker
docker-compose --profile local restart backend_local

# Or local dev server
python manage.py runserver
```

### Step 5: Access Dashboard

Frontend: http://localhost:5173/usage-analytics

---

## Configuration

### Environment Variables

Add to your `.env` file:

```bash
# Enable/disable tracking globally
ENABLE_USAGE_TRACKING=True

# How long to keep detailed logs (days)
USAGE_LOG_RETENTION_DAYS=90

# Cache TTL for summary data (seconds)
USAGE_CACHE_TTL=300
```

### Feature Mapping

To track custom features, edit `middleware.py`:

```python
FEATURE_MAP = {
    '/api/v1/your-feature/': 'Your Feature Name',
    # ... add more mappings
}
```

### Excluding Endpoints

To exclude certain endpoints from tracking:

```python
EXCLUDE_PATTERNS = [
    '/api/v1/usage/',  # Don't track usage tracking itself
    '/health/',
    # ... add more patterns
]
```

---

## API Endpoints

### 1. Global Summary
**GET** `/api/v1/usage/summary/`

Returns overall platform usage statistics.

**Response:**
```json
{
  "total_requests": 15420,
  "total_users": 42,
  "total_departments": 5,
  "total_features": 12,
  "total_tokens": 850000,
  "avg_processing_time": 1.23,
  "success_rate": 98.5,
  "today_requests": 320,
  "this_week_requests": 2100,
  "this_month_requests": 8500,
  "top_departments": [...],
  "top_features": [...],
  "top_users": [...],
  "daily_trend": [...],
  "hourly_distribution": [...]
}
```

**Permissions:** Admin only

---

### 2. User Usage
**GET** `/api/v1/usage/user/{user_id}/`

Returns usage statistics for a specific user.

**Response:**
```json
{
  "user_id": 5,
  "username": "john.doe",
  "email": "john@company.com",
  "department": "Engineering",
  "total_requests": 450,
  "total_tokens": 25000,
  "avg_processing_time": 1.12,
  "error_rate": 2.1,
  "success_rate": 97.9,
  "today_requests": 15,
  "this_month_requests": 320,
  "most_used_features": [
    {"feature_name": "PID Analysis", "count": 150},
    {"feature_name": "Process Datasheet", "count": 120}
  ],
  "last_activity": "2026-03-09T10:30:00Z"
}
```

**Permissions:** Admin or self

---

### 3. Department Usage
**GET** `/api/v1/usage/department/{department_name}/`

Returns usage statistics for a department.

**Response:**
```json
{
  "department": "Engineering",
  "total_requests": 5200,
  "total_tokens": 280000,
  "total_users": 12,
  "avg_processing_time": 1.15,
  "error_rate": 1.8,
  "today_requests": 85,
  "this_month_requests": 1850,
  "last_updated": "2026-03-09T11:00:00Z"
}
```

**Permissions:** Admin or department head

---

### 4. Feature Usage
**GET** `/api/v1/usage/feature/{feature_name}/`

Returns usage statistics for a specific feature.

**Response:**
```json
{
  "feature_name": "PID Analysis",
  "total_requests": 3800,
  "total_tokens": 150000,
  "total_users": 28,
  "avg_processing_time": 2.34,
  "error_rate": 1.2,
  "popularity_score": 450.5,
  "today_requests": 45,
  "this_month_requests": 1200
}
```

**Permissions:** Authenticated users

---

### 5. Sales Report
**GET** `/api/v1/usage/sales-report/?type=monthly`

Generate comprehensive report for management.

**Query Parameters:**
- `type`: `daily`, `weekly`, or `monthly` (default: `monthly`)

**Response:**
```json
{
  "report_date": "2026-03-09",
  "report_type": "monthly",
  "total_active_users": 42,
  "total_requests": 8500,
  "total_tokens_consumed": 450000,
  "user_growth": 12.5,
  "request_growth": 18.3,
  "token_growth": 22.1,
  "department_stats": [...],
  "feature_stats": [...],
  "high_engagement_users": 15,
  "medium_engagement_users": 20,
  "low_engagement_users": 7,
  "avg_response_time": 1.28,
  "system_reliability": 98.7,
  "insights": [
    "User base grew by 12.5% - Active user engagement is increasing",
    "Engineering is the most active department with 5200 requests"
  ]
}
```

**Permissions:** Admin only

---

### 6. Top Users
**GET** `/api/v1/usage/top-users/?limit=10`

Get ranking of most active users.

**Query Parameters:**
- `limit`: Number of users (default: 10)

**Response:**
```json
[
  {
    "user_id": 5,
    "username": "john.doe",
    "email": "john@company.com",
    "department": "Engineering",
    "total_requests": 450,
    "total_tokens": 25000,
    "avg_processing_time": 1.12,
    "last_activity": "2026-03-09T10:30:00Z"
  },
  // ... more users
]
```

**Permissions:** Admin or department head (filtered)

---

### 7. Usage Trends
**GET** `/api/v1/usage/trends/?days=30&granularity=daily`

Get time-series usage data.

**Query Parameters:**
- `days`: Lookback period (default: 30)
- `granularity`: `hourly` or `daily` (default: `daily`)

**Response:**
```json
[
  {
    "date": "2026-03-01",
    "requests": 285,
    "users": 38,
    "tokens": 15200,
    "avg_response_time": 1.15,
    "error_rate": 1.8
  },
  // ... more data points
]
```

**Permissions:** Authenticated users

---

## Async Tasks

### Manual Execution

Run all tasks synchronously:

```bash
python manage.py aggregate_usage_stats
```

### Celery Setup (Production)

Add to `settings.py`:

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'aggregate-usage-stats': {
        'task': 'usage_tracking.aggregate_summaries',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
    'update-cached-metrics': {
        'task': 'usage_tracking.update_cached_metrics',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'cleanup-old-logs': {
        'task': 'usage_tracking.cleanup_old_logs',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3 AM
    },
    'generate-daily-report': {
        'task': 'usage_tracking.generate_daily_report',  'schedule': crontab(hour=0, minute=5),  # Daily at 00:05
    },
}
```

Start Celery beat:

```bash
celery -A config beat --loglevel=info
```

---

## Frontend Usage

### Import Service

```javascript
import usageTrackingService from '../services/usageTrackingService';
```

### Fetch Dashboard Data

```javascript
const fetchDashboard = async () => {
  try {
    const data = await usageTrackingService.getSummary();
    console.log('Usage summary:', data);
  } catch (error) {
    console.error('Failed to fetch usage data:', error);
  }
};
```

### Access Dashboard

Navigate to: `/usage-analytics`

---

## Security

### Permission Levels

1. **Superuser/Staff**
   - View all data across platform
   - Access sales reports
   - See all departments and users

2. **Department Head**
   - View own department data
   - See department users
   - Cannot see other departments

3. **Regular User**
   - View only own usage data
   - See personal statistics
   - Cannot see other users

### Setting Department Heads

Add permission via Django admin or code:

```python
from django.contrib.auth.models import Permission

# Grant department usage view permission
perm = Permission.objects.get(codename='view_department_usage')
user.user_permissions.add(perm)
```

---

## Performance Considerations

### 1. Async Logging

Middleware uses background threads to avoid blocking requests:

```python
thread = Thread(target=self._save_usage_log, args=(usage_data,))
thread.daemon = True
thread.start()
```

### 2. Redis Caching

Summary data cached for 5 minutes:

```python
cache.set(cache_key, data, 300)  # 5 minutes
```

### 3. Database Indexing

Models have optimized indexes:

```python
class Meta:
    indexes = [
        models.Index(fields=['user', 'timestamp']),
        models.Index(fields=['department', 'timestamp']),
        models.Index(fields=['feature_name', 'timestamp']),
    ]
```

### 4. Data Retention

Old logs auto-deleted after 90 days (configurable).

---

## Troubleshooting

### Issue: No data in dashboard

**Solution:**
1. Check if middleware is enabled in `MIDDLEWARE` setting
2. Verify `ENABLE_USAGE_TRACKING=True` in settings
3. Check logs for errors: `docker logs aiflow_backend_local | grep UsageTracking`
4. Run manual aggregation: `python manage.py aggregate_usage_stats`

### Issue: Dashboard loads slowly

**Solution:**
1. Ensure Redis is running for caching
2. Run periodic aggregation tasks
3. Check if summaries are being updated
4. Verify database indexes are created

### Issue: Permission denied

**Solution:**
1. Check user role (admin/department head/user)
2. Verify department assignment in user profile
3. Grant view_department_usage permission for department heads

### Issue: Missing department data

**Solution:**
1. Ensure users have department assigned
2. Check middleware FEATURE_MAP for correct mappings
3. Verify user model has `department` field or profile

---

## Monitoring

### Check Logs

```bash
# Docker
docker logs aiflow_backend_local | grep "UsageTracking"

# Output example:
# [UsageTracking] Middleware initialized
# [UsageTracking] Logged: john.doe - PID Analysis
# [UsageTracking] Starting summary aggregation...
# [UsageTracking] ✅ Summary aggregation completed
```

### Verify Middleware

```python
# In Django shell
from django.conf import settings
print('Usage tracking enabled:', settings.ENABLE_USAGE_TRACKING)
print('Middleware:', 'UsageTrackingMiddleware' in str(settings.MIDDLEWARE))
```

### Check Database

```bash
python manage.py dbshell

SELECT COUNT(*) FROM usage_tracking_user_log;
SELECT COUNT(*) FROM usage_tracking_department_summary;
SELECT COUNT(*) FROM usage_tracking_feature_summary;
```

---

## Analytics Queries

### Most Active Department

```python
from apps.usage_tracking.models import DepartmentUsageSummary

top_dept = DepartmentUsageSummary.objects.order_by('-total_requests').first()
print(f"Top department: {top_dept.department} with {top_dept.total_requests} requests")
```

### User Activity Ranking

```python
from apps.usage_tracking.models import UserUsageLog
from django.db.models import Count

rankings = UserUsageLog.objects.values('user__username').annotate(
    count=Count('id')
).order_by('-count')[:10]
```

### Feature Popularity

```python
from apps.usage_tracking.models import FeatureUsageSummary

features = FeatureUsageSummary.objects.order_by('-popularity_score')[:5]
for f in features:
    print(f"{f.feature_name}: {f.total_requests} requests, {f.total_users} users")
```

### Daily Trend

```python
from apps.usage_tracking.models import UserUsageLog
from django.db.models.functions import TruncDate
from django.db.models import Count
from datetime import timedelta
from django.utils import timezone

start_date = timezone.now() - timedelta(days=7)
trend = UserUsageLog.objects.filter(
    timestamp__gte=start_date
).annotate(
    date=TruncDate('timestamp')
).values('date').annotate(
    requests=Count('id')
).order_by('date')
```

---

## Extending the System

### Track Custom Metrics

Modify `middleware.py`:

```python
def _log_usage_async(self, request, response, processing_time):
    usage_data = {
        # ... existing fields
        'custom_metric': request.META.get('X-Custom-Metric', 0),
    }
```

Add field to model:

```python
class UserUsageLog(models.Model):
    # ... existing fields
    custom_metric = models.FloatField(default=0.0)
```

### AI Token Tracking

AI views should set token count:

```python
# In your AI view
def your_ai_view(request):
    # ... AI processing
    response = Response(data)
    response['X-Tokens-Used'] = tokens_consumed
    return response
```

Middleware automatically extracts `X-Tokens-Used` header.

### Custom Aggregations

Create new summary model:

```python
class CustomSummary(models.Model):
    metric_name = models.CharField(max_length=200)
    total_value = models.FloatField(default=0.0)
    
    def update_metrics(self):
        # Your aggregation logic
        pass
```

---

## Best Practices

1. **Regular Aggregation**: Run tasks every 15 minutes
2. **Data Retention**: Keep 90 days of detailed logs
3. **Cache Summaries**: Use Redis for fast dashboard access
4. **Monitor Performance**: Check middleware impact on response times
5. **Security**: Enforce role-based access controls
6. **Cleanup**: Auto-delete old logs to prevent bloat
7. **Backup**: Include usage tracking tables in backups

---

## Support

For issues or questions:

1. Check logs: `docker logs aiflow_backend_local | grep UsageTracking`
2. Review documentation above
3. Contact: Engineering Team
4. GitHub Issues: [Project Repository]

---

## License

Internal use only - RADAI Engineering AI Platform

**Created:** March 2026  
**Version:** 1.0.0  
**Author:** RADAI Engineering Team
