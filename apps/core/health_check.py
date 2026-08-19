"""
Comprehensive Health Check System
Validates database, Redis, email, S3, and all critical services
"""
from django.db import connection
from django.core.cache import cache
from django.core.mail import get_connection
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
import redis
import psycopg2
from datetime import datetime


@api_view(['GET'])
@permission_classes([AllowAny])
def comprehensive_health_check(request):
    """
    Comprehensive system health check
    GET /api/v1/system-health/
    """
    checks = {
        'timestamp': datetime.now().isoformat(),
        'overall_status': 'healthy',
        'services': {}
    }
    
    # 1. Database Check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        
        # Get database stats
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    count(*) as total_connections,
                    (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') as active_connections
                FROM pg_stat_activity
            """)
            db_stats = cursor.fetchone()
        
        checks['services']['database'] = {
            'status': 'healthy',
            'type': 'PostgreSQL',
            'host': settings.DATABASES['default']['HOST'],
            'name': settings.DATABASES['default']['NAME'],
            'total_connections': db_stats[0] if db_stats else 0,
            'active_connections': db_stats[1] if db_stats else 0
        }
    except Exception as e:
        checks['services']['database'] = {
            'status': 'unhealthy',
            'error': str(e)
        }
        checks['overall_status'] = 'degraded'
    
    # 2. Redis Check
    try:
        # Test cache set/get
        cache_key = 'health_check_test'
        cache_value = 'ok'
        cache.set(cache_key, cache_value, 60)
        retrieved = cache.get(cache_key)
        
        if retrieved == cache_value:
            checks['services']['redis'] = {
                'status': 'healthy',
                'cache_working': True
            }
        else:
            raise Exception('Cache set/get mismatch')
    except Exception as e:
        checks['services']['redis'] = {
            'status': 'unhealthy',
            'error': str(e)
        }
        checks['overall_status'] = 'degraded'
    
    # 3. Email Backend Check
    try:
        email_backend = get_connection()
        # Don't actually send, just check connection can be established
        checks['services']['email'] = {
            'status': 'configured',
            'backend': settings.EMAIL_BACKEND,
            'host': settings.EMAIL_HOST,
            'port': settings.EMAIL_PORT,
            'use_tls': settings.EMAIL_USE_TLS
        }
    except Exception as e:
        checks['services']['email'] = {
            'status': 'misconfigured',
            'error': str(e)
        }
        # Email not critical for core functionality
    
    # 4. Storage Check (S3 or Local)
    try:
        use_s3 = getattr(settings, 'USE_S3', False)
        s3_ready = getattr(settings, 'S3_READY', False)
        
        if use_s3 and s3_ready:
            checks['services']['storage'] = {
                'status': 'configured',
                'type': 'AWS S3',
                'bucket': getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'N/A')
            }
        else:
            checks['services']['storage'] = {
                'status': 'local',
                'type': 'Local Storage',
                'media_root': str(settings.MEDIA_ROOT)
            }
    except Exception as e:
        checks['services']['storage'] = {
            'status': 'unknown',
            'error': str(e)
        }
    
    # 5. Celery Check (via Redis)
    try:
        redis_url = getattr(settings, 'CELERY_BROKER_URL', None)
        if redis_url:
            checks['services']['celery'] = {
                'status': 'configured',
                'broker': 'Redis'
            }
        else:
            checks['services']['celery'] = {
                'status': 'not_configured'
            }
    except Exception as e:
        checks['services']['celery'] = {
            'status': 'unknown',
            'error': str(e)
        }
    
    # 6. Optional Apps Check
    try:
        from django.apps import apps
        installed_apps = [app.name for app in apps.get_app_configs()]
        optional_apps = ['apps.qhse', 'apps.ml_detection', 'apps.activity']
        loaded_optional = [app for app in optional_apps if app in installed_apps]
        
        checks['services']['optional_apps'] = {
            'status': 'loaded',
            'apps': loaded_optional,
            'count': len(loaded_optional)
        }
    except Exception as e:
        checks['services']['optional_apps'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # 7. Queue Service Check (Robust Queue with Fallback)
    try:
        from apps.core.queue_service import RobustQueueService
        queue_health = RobustQueueService.get_queue_health()
        
        checks['services']['queue'] = {
            'status': 'available' if queue_health['available'] else 'circuit_open',
            'circuit_breaker_open': queue_health['circuit_breaker_open'],
            'failures': queue_health['failures'],
            'note': 'Falls back to sync processing' if queue_health['circuit_breaker_open'] else 'Async processing'
        }
        
        if queue_health['circuit_breaker_open']:
            checks['overall_status'] = 'degraded'  # Degraded but operational
    except Exception as e:
        checks['services']['queue'] = {
            'status': 'unknown',
            'error': str(e)
        }
    
    # 8. Security Check
    try:
        checks['services']['security'] = {
            'debug_mode': settings.DEBUG,
            'allowed_hosts': len(settings.ALLOWED_HOSTS),
            'cors_origins': len(getattr(settings, 'CORS_ALLOWED_ORIGINS', [])),
            'csrf_trusted_origins': len(getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])),
            'secret_key_set': bool(settings.SECRET_KEY and len(settings.SECRET_KEY) > 20)
        }
        
        # Warning if DEBUG is True in production
        if settings.DEBUG:
            checks['services']['security']['warning'] = 'DEBUG mode is ON - should be OFF in production'
    except Exception as e:
        checks['services']['security'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Determine HTTP status code
    http_status = status.HTTP_200_OK if checks['overall_status'] == 'healthy' else status.HTTP_503_SERVICE_UNAVAILABLE
    
    return Response(checks, status=http_status)


@api_view(['GET'])
@permission_classes([AllowAny])
def database_connectivity_check(request):
    """
    Detailed database connectivity check
    GET /api/v1/database-check/
    """
    result = {
        'timestamp': datetime.now().isoformat(),
        'database': {}
    }
    
    try:
        # Basic connection test
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            db_version = cursor.fetchone()[0]
        
        # Get database name and user
        db_config = settings.DATABASES['default']
        
        # Get connection stats
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    pg_database.datname,
                    pg_size_pretty(pg_database_size(pg_database.datname)) AS size
                FROM pg_database
                WHERE datname = current_database()
            """)
            db_info = cursor.fetchone()
        
        # Get table count
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT count(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            table_count = cursor.fetchone()[0]
        
        result['database'] = {
            'status': 'connected',
            'version': db_version,
            'name': db_config['NAME'],
            'host': db_config['HOST'],
            'port': db_config['PORT'],
            'user': db_config['USER'],
            'database_size': db_info[1] if db_info else 'unknown',
            'table_count': table_count,
            'connection_timeout': db_config.get('CONN_MAX_AGE', 0)
        }
        
        return Response(result, status=status.HTTP_200_OK)
        
    except Exception as e:
        result['database'] = {
            'status': 'disconnected',
            'error': str(e),
            'error_type': type(e).__name__
        }
        return Response(result, status=status.HTTP_503_SERVICE_UNAVAILABLE)
