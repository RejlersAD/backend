"""
Django settings for AIFlow project.
Smart configuration using environment variables for security and flexibility.
"""

import os
from pathlib import Path
from decouple import config
import dj_database_url

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Helper function to safely cast config values, handling empty strings
def safe_cast_int(value, default):
    """Safely cast to int, returning default if value is empty or invalid"""
    if not value or value == '':
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def safe_cast_bool(value, default):
    """Safely cast to bool, returning default if value is empty or invalid"""
    if not value or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'on')

def app_exists(app_path):
    """
    Check if a Django app exists before loading it.
    Prevents ModuleNotFoundError during deployment.
    
    Args:
        app_path (str): Django app path (e.g., 'apps.ml_detection')
    
    Returns:
        bool: True if app directory exists with __init__.py
    """
    try:
        # Convert app path to file system path
        # e.g., 'apps.ml_detection' -> BASE_DIR/apps/ml_detection
        parts = app_path.split('.')
        app_dir = BASE_DIR.joinpath(*parts)
        
        # Check if directory exists and has __init__.py
        return app_dir.is_dir() and app_dir.joinpath('__init__.py').exists()
    except Exception as e:
        print(f"[WARNING] Could not check app existence for '{app_path}': {e}")
        return False

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = safe_cast_bool(config('DEBUG', default='False'), False)

# ================================================================
# USER MANAGEMENT SECURITY SETTINGS
# ================================================================
# Default password for admin-initiated password resets
# This password should be changed by users on first login
DEFAULT_USER_PASSWORD = config('DEFAULT_USER_PASSWORD', default='Rejlers@123')

# Railway-friendly ALLOWED_HOSTS configuration
try:
    ALLOWED_HOSTS_ENV = config('ALLOWED_HOSTS', default='*')  # Allow all by default for Railway
    if ALLOWED_HOSTS_ENV == '*':
        ALLOWED_HOSTS = ['*']
    else:
        ALLOWED_HOSTS = [s.strip() for s in ALLOWED_HOSTS_ENV.split(',')]

    # Add Railway domain automatically
    RAILWAY_STATIC_URL = config('RAILWAY_STATIC_URL', default='')
    if RAILWAY_STATIC_URL:
        railway_domain = RAILWAY_STATIC_URL.replace('https://', '').replace('http://', '')
        if railway_domain and railway_domain not in ALLOWED_HOSTS and '*' not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(railway_domain)

    # Add .railway.app domains if not using wildcard
    if '*' not in ALLOWED_HOSTS and not any(host.endswith('.railway.app') for host in ALLOWED_HOSTS):
        ALLOWED_HOSTS.append('.railway.app')
    
    print(f"[DJANGO] ALLOWED_HOSTS: {ALLOWED_HOSTS}")
except Exception as e:
    print(f"[ERROR] ALLOWED_HOSTS configuration failed: {e}")
    # Fallback to allow all hosts to prevent 500 error
    ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'corsheaders',
    'drf_spectacular',  # API documentation
    'channels',  # Django Channels for WebSocket support
    
    # Local apps - Core
    'apps.core',
    'apps.users',
    'apps.api',
    'apps.rbac',
    
    # Local apps - Features (Plugin Architecture)
    'apps.pid_analysis',
    'apps.pfd',  # PFD Project Management - Reference Documents & Verification
    'apps.pfd_converter',
    'apps.crs',
    'apps.finance',  # Finance Invoice Automation
    'apps.sales',  # Sales Management - CRM, Pipeline, AI-Powered Insights
    'apps.designiq',  # DesignIQ - AI-Powered Engineering Design Intelligence
    'apps.procurement',  # Procurement Management - Vendor & PO Tracking
    'apps.notifications',  # Notification System - Multi-Channel Alerts & Email
    'apps.process_datasheet',  # Process Datasheet - AI-Powered Equipment Datasheet Generation
    'apps.electrical_datasheet',  # Electrical Datasheet - Transformer & Switchgear Technical Data Sheets
]

# ✨ SMART APP LOADING - Only load apps that exist (prevents deployment crashes)
OPTIONAL_APPS = [
    'apps.qhse',  # QHSE Management - Quality, Health, Safety, Environment
    'apps.ml_detection',  # ML Detection & Real-time Alerts
    'apps.activity',  # Real-time Activity Tracking
]

for app in OPTIONAL_APPS:
    if app_exists(app):
        INSTALLED_APPS.append(app)
        print(f"[✓] Loaded optional app: {app}")
    else:
        print(f"[⚠] Skipped missing app: {app}")

# Add remaining apps
INSTALLED_APPS.extend([
    # ⚠️ CRITICAL: MLflow MUST STAY DISABLED for Railway
    # Enabling this will cause startup hangs (MLflow server not available)
    # 'apps.mlflow_integration',  # DO NOT UNCOMMENT
    
    # AWS S3 Storage (always include - it's in requirements.txt)
    'storages',
    # Add new features here - no core changes needed!
])

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # MUST be before CommonMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.rbac.middleware.LoginTrackingMiddleware',
    'apps.users.middleware.PasswordExpiryMiddleware',  # Password expiry checking
    'apps.rbac.middleware.RBACMiddleware',
    'apps.activity.tracker.ActivityMiddleware',  # Activity tracking middleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

# ==============================================================================
# HOST HEADER HANDLING (Fix for Docker internal hostnames in API responses)
# ==============================================================================
# When running in Docker, the request.get_host() may return 'backend:8000'
# which is not accessible from the browser. These settings ensure proper hostname.
USE_X_FORWARDED_HOST = True  # Use X-Forwarded-Host header if present
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # For HTTPS detection
# ==============================================================================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ==============================================================================
# CHANNEL LAYERS CONFIGURATION (WebSocket support)
# ==============================================================================
# Redis-backed channel layer for Django Channels (WebSocket real-time features)
# Railway: Will use REDIS_URL if available, otherwise falls back to in-memory
# Docker: Uses redis:6379
# Fallback: In-memory channel layer (single-server only, no WebSocket persistence)

# Parse Redis configuration for Channel Layers
REDIS_URL_FOR_CHANNELS = config('REDIS_URL', default=None)

if REDIS_URL_FOR_CHANNELS:
    # Extract host and port from Redis URL for channels_redis
    # channels_redis expects (host, port) tuple, not full URL
    import re
    redis_match = re.match(r'redis://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)', REDIS_URL_FOR_CHANNELS)
    if redis_match:
        redis_host = redis_match.group(3)
        redis_port = int(redis_match.group(4))
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels_redis.core.RedisChannelLayer',
                'CONFIG': {
                    'hosts': [(redis_host, redis_port)],
                    'capacity': 1500,
                    'expiry': 10,
                },
            },
        }
        print(f"[CHANNELS] ✅ Channel layer configured (URL-based): {redis_host}:{redis_port}")
    else:
        # Could not parse URL - use in-memory fallback
        print(f"[CHANNELS] ⚠️  Could not parse REDIS_URL")
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels.layers.InMemoryChannelLayer',
            },
        }
        print(f"[CHANNELS] ⚠️ Using in-memory channels (single-server only)")
else:
    # Check if REDIS_HOST is configured
    REDIS_HOST_FOR_CHANNELS = config('REDIS_HOST', default=None)
    if REDIS_HOST_FOR_CHANNELS and REDIS_HOST_FOR_CHANNELS != 'None':
        # Docker Compose: host/port configuration
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels_redis.core.RedisChannelLayer',
                'CONFIG': {
                    'hosts': [(REDIS_HOST_FOR_CHANNELS, config('REDIS_PORT', default=6379, cast=int))],
                    'capacity': 1500,
                    'expiry': 10,
                },
            },
        }
        print(f"[CHANNELS] ✅ Channel layer configured (host-based): {REDIS_HOST_FOR_CHANNELS}:{config('REDIS_PORT', default=6379)}")
    else:
        # No Redis available - use in-memory channel layer
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels.layers.InMemoryChannelLayer',
            },
        }
        print(f"[CHANNELS] ⚠️ Using in-memory channels (Redis not configured)")
        print(f"[CHANNELS] Note: WebSockets limited to single server. Set REDIS_URL for multi-server support.")

# ==============================================================================
# End of Channel Layers Configuration
# ==============================================================================

# Database
# ⚠️ CRITICAL: DATABASE_URL is REQUIRED for Railway deployment
# Railway Env Var: DATABASE_URL=postgresql://postgres:PASSWORD@HOST:PORT/railway
# Use DATABASE_URL if available (Railway), otherwise use individual DB settings
try:
    DATABASE_URL = config('DATABASE_URL', default='')
    if DATABASE_URL:
        db_config = dj_database_url.parse(DATABASE_URL)
        # Add timeout options to prevent hanging
        db_config['CONN_MAX_AGE'] = 60
        db_config['OPTIONS'] = {
            'connect_timeout': 10,  # Reduced from 30 to 10 seconds
            'options': '-c statement_timeout=30000'
        }
        DATABASES = {'default': db_config}
        print(f"[DJANGO] Using DATABASE_URL configuration")
        print(f"[DJANGO] DB Host: {db_config.get('HOST', 'unknown')}")
    else:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': config('DB_NAME', default='radai_db'),
                'USER': config('DB_USER', default='postgres'),
                'PASSWORD': config('DB_PASSWORD', default='postgres'),
                'HOST': config('DB_HOST', default='db'),
                'PORT': config('DB_PORT', default='5432'),
                'CONN_MAX_AGE': 60,
                'OPTIONS': {
                    'connect_timeout': 10,
                    'options': '-c statement_timeout=30000'
                }
            }
        }
        print(f"[DJANGO] Using individual DB configuration")
        print(f"[DJANGO] DB_HOST: {config('DB_HOST', default='db')}")
except Exception as e:
    print(f"[ERROR] Database configuration failed: {e}")
    # Set a minimal database config to prevent crashes
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
    print(f"[WARNING] Falling back to SQLite due to database configuration error")
    print(f"[WARNING] Falling back to SQLite due to database configuration error")

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# Note: STATIC_URL, STATIC_ROOT, MEDIA_URL, MEDIA_ROOT are configured
# in the S3 section below based on USE_S3 setting
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Authentication Backends
# Use custom backend for case-insensitive email authentication
AUTHENTICATION_BACKENDS = [
    'apps.users.auth_backend.CaseInsensitiveEmailBackend',  # Custom case-insensitive email auth
    'django.contrib.auth.backends.ModelBackend',  # Default Django auth (fallback)
]

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,  # Increased from 10 to show more items per page
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'rest_framework.views.exception_handler',
}

# ==============================================================================
# JWT CONFIGURATION (Simple JWT)
# ==============================================================================
from datetime import timedelta

SIMPLE_JWT = {
    # Token Lifetimes
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),  # Access token valid for 24 hours
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),   # Refresh token valid for 7 days
    'ROTATE_REFRESH_TOKENS': True,                  # Rotate refresh token on use
    'BLACKLIST_AFTER_ROTATION': False,              # Keep old refresh tokens valid
    
    # Token Types
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    
    # Token Classes
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    
    # Signing
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    
    # Blacklisting (optional - can be enabled later)
    'JTI_CLAIM': 'jti',
    
    # Sliding tokens (disabled)
    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(hours=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

# print(f"[JWT] ====== Configuration Loaded ======")
# print(f"[JWT] Access Token Lifetime: {SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']}")
# print(f"[JWT] Refresh Token Lifetime: {SIMPLE_JWT['REFRESH_TOKEN_LIFETIME']}")
# print(f"[JWT] Rotate Refresh Tokens: {SIMPLE_JWT['ROTATE_REFRESH_TOKENS']}")
# print(f"[JWT] ===================================")

# ==============================================================================
# End of JWT Configuration
# ==============================================================================

# ==============================================================================
# CORS CONFIGURATION - RAILWAY PRODUCTION READY
# ==============================================================================

# PRODUCTION URLS
PRODUCTION_FRONTEND = config('FRONTEND_URL', default='https://airflow-frontend.vercel.app')
PRODUCTION_BACKEND = config('BACKEND_URL', default='https://aiflowbackend-production.up.railway.app')
FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:5173')  # For email links

# ⚠️ CRITICAL: DO NOT CHANGE - CORS_ALLOW_ALL_ORIGINS MUST BE FALSE
# Setting this to True will break JWT authentication with credentials
# Railway Env Var: CORS_ALLOW_ALL_ORIGINS=False (or omit to use default)
CORS_ALLOW_ALL_ORIGINS = safe_cast_bool(config('CORS_ALLOW_ALL_ORIGINS', default='False'), False)

if CORS_ALLOW_ALL_ORIGINS:
    # If allowing all origins, disable credentials for security
    CORS_ALLOW_CREDENTIALS = False
    CORS_ALLOWED_ORIGINS = []  # Not used when allow all is True
    print("[CORS] ⚠️  WARNING: CORS_ALLOW_ALL_ORIGINS is True - ALL origins allowed!")
else:
    # Use specific origins for better security
    CORS_ORIGINS_ENV = config('CORS_ALLOWED_ORIGINS', default='')
    if CORS_ORIGINS_ENV:
        # If env var is set, use it (comma-separated list)
        CORS_ALLOWED_ORIGINS = [origin.strip() for origin in CORS_ORIGINS_ENV.split(',')]
    else:
        # Use default list
        CORS_ALLOWED_ORIGINS = [
            # Production - Custom Domain
            'https://radai.ae',
            'https://www.radai.ae',
            'http://radai.ae',  # Include HTTP for redirects
            'http://www.radai.ae',
            # Production - Vercel
            PRODUCTION_FRONTEND,
            PRODUCTION_BACKEND,
            # Development
            'http://localhost:3000',
            'http://localhost:5173',
            'http://127.0.0.1:3000',
            'http://127.0.0.1:5173',
        ]
    
    # Allow credentials (for JWT tokens in Authorization header)
    CORS_ALLOW_CREDENTIALS = safe_cast_bool(config('CORS_ALLOW_CREDENTIALS', default='True'), True)

# Allow all standard methods
CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']

# Allow all necessary headers
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'cache-control',
    'pragma',
]

# Expose headers for downloads
CORS_EXPOSE_HEADERS = ['content-disposition', 'content-type', 'cache-control']

# Cache preflight for 1 hour
CORS_PREFLIGHT_MAX_AGE = safe_cast_int(config('CORS_PREFLIGHT_MAX_AGE', default='3600'), 3600)

# Allow regex patterns for Vercel previews and localhost (only if not allowing all)
if not CORS_ALLOW_ALL_ORIGINS:
    CORS_ALLOWED_ORIGIN_REGEXES = [
        r'^https://.*\.vercel\.app$',
        r'^http://localhost:\d+$',
        r'^http://127\.0\.0\.1:\d+$',
    ]
else:
    CORS_ALLOWED_ORIGIN_REGEXES = []

# Additional CORS settings for preflight
CORS_ALLOW_PRIVATE_NETWORK = True

print("\n" + "="*70)
# print("[CORS] ====== CORS CONFIGURATION ======")
print("="*70)
print(f"[CORS] Allow All Origins: {CORS_ALLOW_ALL_ORIGINS}")
if not CORS_ALLOW_ALL_ORIGINS:
    print(f"[CORS] Allowed Origins Count: {len(CORS_ALLOWED_ORIGINS)}")
    print(f"[CORS] Allowed Origins:")
    for origin in CORS_ALLOWED_ORIGINS:
        print(f"  - {origin}")
print(f"[CORS] Allow Credentials: {CORS_ALLOW_CREDENTIALS}")
print(f"[CORS] Preflight Max Age: {CORS_PREFLIGHT_MAX_AGE}s")
print(f"[CORS] Frontend URL: {PRODUCTION_FRONTEND}")
print(f"[CORS] Backend URL: {PRODUCTION_BACKEND}")
print("="*70 + "\n")

# ==============================================================================
# CSRF CONFIGURATION
# ==============================================================================

# Build CSRF trusted origins from CORS origins
CSRF_TRUSTED_ORIGINS = [
    'https://radai.ae',
    'https://www.radai.ae',
    PRODUCTION_FRONTEND,
    PRODUCTION_BACKEND,
    'http://localhost:3000',
    'http://localhost:5173',
]

# Add any additional origins from environment
if not CORS_ALLOW_ALL_ORIGINS and CORS_ALLOWED_ORIGINS:
    for origin in CORS_ALLOWED_ORIGINS:
        if origin not in CSRF_TRUSTED_ORIGINS and origin.startswith('https'):
            CSRF_TRUSTED_ORIGINS.append(origin)

# CSRF settings - Important for API endpoints
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'None' if not DEBUG else 'Lax'
CSRF_USE_SESSIONS = False
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript to read for API calls

print(f"[CSRF] Trusted Origins: {len(CSRF_TRUSTED_ORIGINS)} domains")
for origin in CSRF_TRUSTED_ORIGINS:
    print(f"  - {origin}")

# ==============================================================================
# End of CORS/CSRF Configuration
# ==============================================================================

# ==============================================================================
# CACHE CONFIGURATION (Redis)
# ==============================================================================
# Cache backend for session storage, task progress tracking, and performance optimization
# Railway: Set REDIS_URL environment variable (e.g., redis://default:password@host:port)
# Docker: Uses redis:6379 by default
# Fallback: Uses in-memory cache if Redis not available (Railway without Redis plugin)

REDIS_URL = config('REDIS_URL', default=None)
REDIS_HOST = config('REDIS_HOST', default=None)

if REDIS_URL:
    # Railway or external Redis (URL-based configuration)
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,  # seconds
                'SOCKET_TIMEOUT': 5,  # seconds
                'RETRY_ON_TIMEOUT': True,
                'MAX_CONNECTIONS': 50,
                'CONNECTION_POOL_KWARGS': {
                    'max_connections': 50,
                    'retry_on_timeout': True,
                },
            },
            'KEY_PREFIX': 'radai',
            'TIMEOUT': 300,  # 5 minutes default
        }
    }
    print(f"[CACHE] ✅ Redis cache configured (URL-based)")
    print(f"[CACHE] URL: {REDIS_URL.split('@')[0]}@***")  # Hide credentials
elif REDIS_HOST and REDIS_HOST != 'None':
    # Docker Compose: host/port configuration
    REDIS_PORT = config('REDIS_PORT', default=6379, cast=int)
    REDIS_PASSWORD = config('REDIS_PASSWORD', default=None)
    
    redis_location = f"redis://{':' + REDIS_PASSWORD + '@' if REDIS_PASSWORD else ''}{REDIS_HOST}:{REDIS_PORT}/1"
    
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': redis_location,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,
                'SOCKET_TIMEOUT': 5,
                'RETRY_ON_TIMEOUT': True,
                'MAX_CONNECTIONS': 50,
            },
            'KEY_PREFIX': 'radai',
            'TIMEOUT': 300,
        }
    }
    print(f"[CACHE] ✅ Redis cache configured (host-based)")
    print(f"[CACHE] Host: {REDIS_HOST}:{REDIS_PORT}")
else:
    # Fallback: In-memory cache (Railway without Redis plugin)
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'radai-cache',
            'TIMEOUT': 300,
            'OPTIONS': {
                'MAX_ENTRIES': 1000,
            }
        }
    }
    print(f"[CACHE] ⚠️ Using in-memory cache (Redis not configured)")
    print(f"[CACHE] Note: Cache will be lost on restart. Set REDIS_URL for persistent cache.")

# ==============================================================================
# End of Cache Configuration
# ==============================================================================

# ==============================================================================
# CELERY CONFIGURATION (Task Queue)
# ==============================================================================
# Celery broker and result backend - uses same Redis configuration as cache
# Railway: Set REDIS_URL or CELERY_BROKER_URL environment variable
# Docker: Uses redis:6379 by default
# Fallback: Celery disabled if Redis not available

if REDIS_URL:
    # Use the same Redis URL for Celery (different database number for separation)
    CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL.replace('/1', '/0'))
    CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=REDIS_URL.replace('/1', '/0'))
    print(f"[CELERY] ✅ Broker configured (URL-based)")
elif REDIS_HOST and REDIS_HOST != 'None':
    # Fallback to host/port configuration
    REDIS_PORT = config('REDIS_PORT', default=6379, cast=int)
    CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=f'redis://{REDIS_HOST}:{REDIS_PORT}/0')
    CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=f'redis://{REDIS_HOST}:{REDIS_PORT}/0')
    print(f"[CELERY] ✅ Broker configured (host-based): {REDIS_HOST}:{REDIS_PORT}")
else:
    # No Redis available - disable Celery (tasks will run synchronously)
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = None
    CELERY_RESULT_BACKEND = 'django-db'  # Use database for minimal task tracking
    print(f"[CELERY] ⚠️ Running in EAGER mode (Redis not configured)")
    print(f"[CELERY] Note: Tasks run synchronously. Set REDIS_URL for async tasks.")

CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

print(f"[CELERY] Broker: {CELERY_BROKER_URL.split('@')[0] if '@' in CELERY_BROKER_URL else CELERY_BROKER_URL}")
print(f"[CELERY] Result Backend: {CELERY_RESULT_BACKEND.split('@')[0] if '@' in CELERY_RESULT_BACKEND else CELERY_RESULT_BACKEND}")

# ==============================================================================
# End of Celery Configuration
# ==============================================================================

# Process Datasheet - Dynamic Retry Configuration
DATASHEET_MAX_RETRIES = config('DATASHEET_MAX_RETRIES', default=5, cast=int)
DATASHEET_TASK_TIMEOUT = config('DATASHEET_TASK_TIMEOUT', default=600, cast=int)  # 10 minutes
DATASHEET_RETRY_BACKOFF = config('DATASHEET_RETRY_BACKOFF', default=2, cast=int)  # Exponential backoff multiplier

# API Documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'RADAI API',
    'DESCRIPTION': 'Smart API for RADAI application',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ==============================================================================
# AWS S3 CONFIGURATION (SECURE)
# ==============================================================================

# Enable S3 storage (set to True to use S3, False to use local storage)
# IMPORTANT: Requires S3_READY=True to prevent deployment failures with invalid credentials
USE_S3_CONFIG = safe_cast_bool(config('USE_S3', default='False'), False)
S3_READY = safe_cast_bool(config('S3_READY', default='False'), False)

# Smart S3 validation: Require explicit S3_READY flag to prevent credential errors
if USE_S3_CONFIG and not S3_READY:
    print("⚠️  [S3] USE_S3=True but S3_READY=False. Using local storage for safety.")
    print("    Set S3_READY=True on Railway after updating AWS credentials.")
    USE_S3 = False
elif USE_S3_CONFIG and S3_READY:
    # Double-check credentials are present
    aws_access_key = config('AWS_ACCESS_KEY_ID', default='')
    aws_secret_key = config('AWS_SECRET_ACCESS_KEY', default='')
    aws_bucket_name = config('AWS_STORAGE_BUCKET_NAME', default='')
    
    # Validate that all required S3 configuration is present
    if not aws_access_key or not aws_secret_key or not aws_bucket_name:
        print("⚠️  [S3] Credentials incomplete despite S3_READY=True. Falling back to local storage.")
        print(f"    - AWS_ACCESS_KEY_ID: {'✓ Set' if aws_access_key else '✗ Missing'}")
        print(f"    - AWS_SECRET_ACCESS_KEY: {'✓ Set' if aws_secret_key else '✗ Missing'}")
        print(f"    - AWS_STORAGE_BUCKET_NAME: {'✓ Set' if aws_bucket_name else '✗ Missing'}")
        USE_S3 = False  # Disable S3 if credentials are incomplete
    else:
        print(f"✅ [S3] Enabled with bucket: {aws_bucket_name}")
        USE_S3 = True
else:
    USE_S3 = False

if USE_S3:
    # AWS Credentials - LOADED FROM ENVIRONMENT (NEVER HARDCODE)
    # Boto3 automatically checks:
    # 1. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    # 2. IAM Role (EC2, ECS, Lambda) - PREFERRED for production
    # 3. AWS credentials file (~/.aws/credentials)
    
    # DO NOT SET THESE IN CODE - Use environment variables or IAM roles
    # AWS_ACCESS_KEY_ID = 'NEVER_HARDCODE_THIS'  ❌ WRONG
    # AWS_SECRET_ACCESS_KEY = 'NEVER_HARDCODE_THIS'  ❌ WRONG
    
    # ⚠️ CRITICAL: S3 bucket must exist before deployment
    # Railway Env Var: AWS_STORAGE_BUCKET_NAME=user-management-rejlers (production bucket)
    # Only configure S3 if bucket name is set (prevents startup errors)
    AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default='')
    
    if AWS_STORAGE_BUCKET_NAME:
        # S3 Configuration
        AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='us-east-1')
        
        # Security: Use AWS Signature Version 4 (required for some regions)
        AWS_S3_SIGNATURE_VERSION = 's3v4'
        
        # Security: Enable encryption at rest
        AWS_S3_ENCRYPTION = True
        
        # Security: All files are private by default
        AWS_DEFAULT_ACL = 'private'
        
        # Security: Use presigned URLs instead of public URLs
        AWS_S3_CUSTOM_DOMAIN = None
        AWS_QUERYSTRING_AUTH = True
        
        # URL expiration for presigned URLs (1 hour)
        AWS_QUERYSTRING_EXPIRE = 3600
        
        # Performance: Connection settings
        AWS_S3_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
        AWS_S3_FILE_OVERWRITE = False  # Don't overwrite files
        
        # Storage backends
        DEFAULT_FILE_STORAGE = 'apps.core.storage_backends.MediaStorage'
        # Keep static files local, only use S3 for media/documents
        STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        
        # Media files (uploaded by users) - use S3
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/media/'
        
        # Static files (CSS/JS) - use local storage
        STATIC_ROOT = BASE_DIR / 'staticfiles'
        STATIC_URL = '/static/'
    else:
        # S3 enabled but bucket not configured - use local storage
        print("⚠️  USE_S3=True but AWS_STORAGE_BUCKET_NAME not set. Using local storage.")
        MEDIA_ROOT = BASE_DIR / 'media'
        MEDIA_URL = '/media/'
        STATIC_ROOT = BASE_DIR / 'staticfiles'
        STATIC_URL = '/static/'
        STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
else:
    # Local storage configuration (development/production without S3)
    MEDIA_ROOT = BASE_DIR / 'media'
    MEDIA_URL = '/media/'
    STATIC_ROOT = BASE_DIR / 'staticfiles'
    STATIC_URL = '/static/'
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# OpenAI Configuration (existing)
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
OPENAI_MODEL = config('OPENAI_MODEL', default='gpt-4o')

# ==============================================================================
# REPORT GENERATION CONFIGURATION (SOFT-CODED)
# ==============================================================================

# Company Branding for Reports
REPORT_COMPANY_NAME = config('REPORT_COMPANY_NAME', default='REJLERS ABU DHABI')
REPORT_COMPANY_SUBTITLE = config('REPORT_COMPANY_SUBTITLE', default='Engineering & Design Consultancy')
REPORT_COMPANY_WEBSITE = config('REPORT_COMPANY_WEBSITE', default='www.rejlers.com/ae')

# Report Colors (Hex values without #)
REPORT_PRIMARY_COLOR = config('REPORT_PRIMARY_COLOR', default='003366')  # Dark blue
REPORT_SECONDARY_COLOR = config('REPORT_SECONDARY_COLOR', default='FFA500')  # Orange
REPORT_TEXT_COLOR = config('REPORT_TEXT_COLOR', default='333333')
REPORT_BORDER_COLOR = config('REPORT_BORDER_COLOR', default='CCCCCC')

# Report Template Settings
REPORT_TITLE = config('REPORT_TITLE', default='P&ID DESIGN VERIFICATION REPORT')
REPORT_FOOTER_TEXT = config('REPORT_FOOTER_TEXT', default='CONFIDENTIAL ENGINEERING DOCUMENT')
REPORT_FOOTER_NOTE = config('REPORT_FOOTER_NOTE', default='This document is the property of {company}. Unauthorized distribution is prohibited.')

# Format footer note with company name
REPORT_FOOTER_NOTE_FORMATTED = REPORT_FOOTER_NOTE.format(company=REPORT_COMPANY_NAME)
# ==============================================================================
# EMAIL CONFIGURATION (AWS SES SMTP)
# ==============================================================================

# Email backend configuration
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')

# AWS SES SMTP Configuration
# Note: ME-CENTRAL-1 region doesn't support SES SMTP, use US-EAST-1
EMAIL_HOST = config('EMAIL_HOST', default='email-smtp.us-east-1.amazonaws.com')
EMAIL_PORT = safe_cast_int(config('EMAIL_PORT', default='587'), 587)
EMAIL_USE_TLS = safe_cast_bool(config('EMAIL_USE_TLS', default='True'), True)
EMAIL_USE_SSL = safe_cast_bool(config('EMAIL_USE_SSL', default='False'), False)

# SMTP Credentials (from AWS SES - rejlers-radai IAM user - Production)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')  # SMTP Username - MUST be set via env var
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')  # SMTP Password - MUST be set via env var

# From Email (using verified email temporarily)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='tanzeem.agra@rejlers.ae')
SERVER_EMAIL = config('SERVER_EMAIL', default='tanzeem.agra@rejlers.ae')

# Email settings
EMAIL_TIMEOUT = 10  # Timeout in seconds
EMAIL_SUBJECT_PREFIX = config('EMAIL_SUBJECT_PREFIX', default='[RADAI] ')

# Email Verification Settings
EMAIL_VERIFICATION_REQUIRED = safe_cast_bool(config('EMAIL_VERIFICATION_REQUIRED', default='True'), True)
EMAIL_VERIFICATION_TOKEN_EXPIRY = safe_cast_int(config('EMAIL_VERIFICATION_TOKEN_EXPIRY', default='86400'), 86400)  # 24 hours

print("\n" + "=" * 60)
# print("EMAIL CONFIGURATION")
print("=" * 60)
print(f"Email Backend: {EMAIL_BACKEND}")
print(f"Email Host: {EMAIL_HOST}")
print(f"Email Port: {EMAIL_PORT}")
print(f"Use TLS: {EMAIL_USE_TLS}")
print(f"SMTP User Configured: {'Yes' if EMAIL_HOST_USER else 'No'}")
print(f"Default From Email: {DEFAULT_FROM_EMAIL}")
print("=" * 60 + "\n")

# ========================================================================
# Finance Module - Approval Team Email Addresses
# ========================================================================
FINANCE_EMAIL = config('FINANCE_EMAIL', default='khanabdullahomar886@gmail.com')
FINANCE_RICHA_EMAIL = config('FINANCE_RICHA_EMAIL', default='test.user1@rejlers.ae')
RICHA_EMAIL = FINANCE_RICHA_EMAIL  # Alias for backward compatibility
JAMAL_EMAIL = config('FINANCE_JAMAL_EMAIL', default='test.user2@rejlers.ae')
RAFAT_EMAIL = config('FINANCE_RAFAT_EMAIL', default='test.user3@rejlers.ae')
MOE_EMAIL = config('FINANCE_MOE_EMAIL', default='test.user4@rejlers.ae')
JARMO_EMAIL = config('FINANCE_JARMO_EMAIL', default='test.user5@rejlers.ae')
ANEEF_EMAIL = config('FINANCE_ANEEF_EMAIL', default='test.user6@rejlers.ae')
ALEKSI_EMAIL = config('FINANCE_ALEKSI_EMAIL', default='test.user7@rejlers.ae')
SHERWIN_EMAIL = config('FINANCE_SHERWIN_EMAIL', default='test.user8@rejlers.ae')
NIJUM_EMAIL = config('FINANCE_NIJUM_EMAIL', default='test.user9@rejlers.ae')
HR_ADMIN_EMAIL = config('FINANCE_HR_ADMIN_EMAIL', default='test.user10@rejlers.ae')