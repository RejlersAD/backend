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

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

# Railway-friendly ALLOWED_HOSTS configuration
ALLOWED_HOSTS_ENV = config('ALLOWED_HOSTS', default='localhost,127.0.0.1')
if ALLOWED_HOSTS_ENV == '*':
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = [s.strip() for s in ALLOWED_HOSTS_ENV.split(',')]

# Add Railway domain automatically
RAILWAY_STATIC_URL = config('RAILWAY_STATIC_URL', default='')
if RAILWAY_STATIC_URL:
    railway_domain = RAILWAY_STATIC_URL.replace('https://', '').replace('http://', '')
    if railway_domain and railway_domain not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(railway_domain)

# Add .railway.app domains
if not any(host.endswith('.railway.app') or host == '*' for host in ALLOWED_HOSTS):
    ALLOWED_HOSTS.append('.railway.app')

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
    'drf_spectacular',
    
    # Local apps
    'apps.core',
    'apps.users',
    'apps.api',
    'apps.pid_analysis',
    'apps.pfd_converter',
    'apps.rbac',
]

# Add storages only if S3 is enabled (prevents import errors when not needed)
if config('USE_S3', default=False, cast=bool) and config('AWS_STORAGE_BUCKET_NAME', default=''):
    INSTALLED_APPS.append('storages')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # Use django-cors-headers only
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.rbac.middleware.LoginTrackingMiddleware',
    'apps.rbac.middleware.RBACMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

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

# Database
# Use DATABASE_URL if available (Railway), otherwise use individual DB settings
DATABASE_URL = config('DATABASE_URL', default='')
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='aiflow_db'),
            'USER': config('DB_USER', default='postgres'),
            'PASSWORD': config('DB_PASSWORD', default='postgres'),
            'HOST': config('DB_HOST', default='db'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }

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
    'PAGE_SIZE': 10,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# CORS Configuration - Soft-coded using environment variables
# Default allowed origins - can be overridden completely via CORS_ALLOWED_ORIGINS env variable
DEFAULT_CORS_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]

# Add production frontend URL from environment (soft-coded)
FRONTEND_URL = config('FRONTEND_URL', default='https://airflow-frontend.vercel.app')
if FRONTEND_URL:
    DEFAULT_CORS_ORIGINS.append(FRONTEND_URL)

# Get additional origins from environment variable (comma-separated)
additional_origins = config('CORS_ALLOWED_ORIGINS', default='', cast=str)
if additional_origins:
    additional_list = [s.strip() for s in additional_origins.split(',') if s.strip()]
    DEFAULT_CORS_ORIGINS.extend(additional_list)

# Remove duplicates while preserving order
CORS_ALLOWED_ORIGINS = list(dict.fromkeys(DEFAULT_CORS_ORIGINS))

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=False, cast=bool)  # Only enable via env var for testing

# Enhanced CORS headers for file uploads and complex requests
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
    'x-custom-header',
    'cache-control',
]

# Allow all HTTP methods for API endpoints
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# Expose additional headers to frontend
CORS_EXPOSE_HEADERS = [
    'content-disposition',
    'content-length',
    'content-type',
]

# Soft-coded regex patterns for dynamic origins (Vercel previews, localhost ports)
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://.*\.vercel\.app$",  # All Vercel deployments (production + previews)
    r"^http://localhost:\d+$",  # All localhost ports
    r"^http://127\.0\.0\.1:\d+$",  # All 127.0.0.1 ports
]

# Auto-add dynamic Vercel deployment URL if specified
vercel_domain = config('VERCEL_URL', default='')
if vercel_domain:
    vercel_origin = f'https://{vercel_domain}' if not vercel_domain.startswith('http') else vercel_domain
    if vercel_origin not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(vercel_origin)

# CSRF Trusted Origins - Soft-coded for production security
# Default trusted origins
DEFAULT_CSRF_ORIGINS = []

# Add production frontend from environment
if FRONTEND_URL:
    DEFAULT_CSRF_ORIGINS.append(FRONTEND_URL)

# Add backend URL from environment (Railway production)
BACKEND_URL = config('BACKEND_URL', default='https://aiflowbackend-production.up.railway.app')
if BACKEND_URL:
    DEFAULT_CSRF_ORIGINS.append(BACKEND_URL)

# Add Railway domain dynamically if available
railway_domain = config('RAILWAY_PUBLIC_DOMAIN', default='')
if railway_domain:
    csrf_origin = f'https://{railway_domain}' if not railway_domain.startswith('http') else railway_domain
    if csrf_origin not in DEFAULT_CSRF_ORIGINS:
        DEFAULT_CSRF_ORIGINS.append(csrf_origin)

# Get additional CSRF origins from environment (comma-separated)
additional_csrf = config('CSRF_TRUSTED_ORIGINS', default='', cast=str)
if additional_csrf:
    csrf_list = [s.strip() for s in additional_csrf.split(',') if s.strip()]
    DEFAULT_CSRF_ORIGINS.extend(csrf_list)

# Remove duplicates
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(DEFAULT_CSRF_ORIGINS))

# CSRF exemption for API endpoints
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = 'None'
CSRF_USE_SESSIONS = False

# EMERGENCY CORS SETTINGS - Use only django-cors-headers
CORS_ALLOW_ALL_ORIGINS = True  
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOWED_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# Allow all HTTP methods
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# Simple logging
print(f"[CORS] EMERGENCY MODE: Allow All Origins = {CORS_ALLOW_ALL_ORIGINS}")

# Additional CORS settings for proper functionality
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

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
]

# Expose headers to the frontend
CORS_EXPOSE_HEADERS = [
    'content-type',
    'content-disposition',
    'x-csrftoken',
]

# Preflight request cache duration
CORS_PREFLIGHT_MAX_AGE = 86400  # 24 hours

# Celery Configuration
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://redis:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# API Documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'AIFlow API',
    'DESCRIPTION': 'Smart API for AIFlow application',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ==============================================================================
# AWS S3 CONFIGURATION (SECURE)
# ==============================================================================

# Enable S3 storage (set to True to use S3, False to use local storage)
USE_S3 = config('USE_S3', default=False, cast=bool)

if USE_S3:
    # AWS Credentials - LOADED FROM ENVIRONMENT (NEVER HARDCODE)
    # Boto3 automatically checks:
    # 1. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    # 2. IAM Role (EC2, ECS, Lambda) - PREFERRED for production
    # 3. AWS credentials file (~/.aws/credentials)
    
    # DO NOT SET THESE IN CODE - Use environment variables or IAM roles
    # AWS_ACCESS_KEY_ID = 'NEVER_HARDCODE_THIS'  ❌ WRONG
    # AWS_SECRET_ACCESS_KEY = 'NEVER_HARDCODE_THIS'  ❌ WRONG
    
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
        STATICFILES_STORAGE = 'apps.core.storage_backends.StaticStorage'
        
        # Media files (uploaded by users)
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/media/'
        
        # Static files
        STATIC_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/static/'
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
