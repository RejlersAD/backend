#!/usr/bin/env python
"""
Quick Railway Environment Variables Checker
Prints out what Railway needs to see to avoid 500 errors
"""

import os
from django.core.management.utils import get_random_secret_key

print("\n" + "="*70)
print("🚀 RAILWAY ENVIRONMENT VARIABLES - SETUP GUIDE")
print("="*70 + "\n")

print("📋 Copy these into Railway Dashboard → Variables:\n")

# Generate SECRET_KEY if not exists
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    secret_key = get_random_secret_key()
    print(f"SECRET_KEY={secret_key}")
else:
    print(f"SECRET_KEY={secret_key}  ✅ (already set)")

print(f"\nDATABASE_URL=<automatically set by PostgreSQL plugin>  ⚠️ (add PostgreSQL plugin)")

print("\n" + "-"*70)
print("📝 RECOMMENDED VARIABLES:")
print("-"*70 + "\n")

print("DEBUG=False")
print("ALLOWED_HOSTS=*")
print("CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app,https://radai.ae")
print("FRONTEND_URL=https://your-frontend.vercel.app")
print(f"BACKEND_URL=https://your-app.railway.app")

print("\n" + "-"*70)
print("📧 EMAIL CONFIGURATION (for password reset):")
print("-"*70 + "\n")

print("EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend")
print("EMAIL_HOST=email-smtp.ap-south-1.amazonaws.com")
print("EMAIL_PORT=587")
print("EMAIL_USE_TLS=True")
print("EMAIL_HOST_USER=AKIAQGMP5VCUDN4AZU6O")
print("EMAIL_HOST_PASSWORD=BNkRGHma6/a2j7Z+9E1yDTZWTpYEnQK7lkwo9Ff5S6JG")
print("DEFAULT_FROM_EMAIL=tanzeem.agra@rejlers.ae")

print("\n" + "-"*70)
print("☁️  AWS S3 CONFIGURATION (optional):")
print("-"*70 + "\n")

print("USE_S3=False  # Set to True if using S3")
print("# If USE_S3=True, also add:")
print("# AWS_STORAGE_BUCKET_NAME=your-bucket-name")
print("# AWS_S3_REGION_NAME=us-east-1")
print("# AWS_ACCESS_KEY_ID=your-key")
print("# AWS_SECRET_ACCESS_KEY=your-secret")

print("\n" + "="*70)
print("⚙️  RAILWAY SERVICE SETTINGS:")
print("="*70 + "\n")

print("Root Directory: backend")
print("Start Command: bash railway_start.sh")
print("Build Command: (leave empty)")

print("\n" + "="*70)
print("🔍 AFTER DEPLOYMENT - TEST:")
print("="*70 + "\n")

print("curl https://your-app.railway.app/health/")

print("\n" + "="*70)
print("✅ DONE! Copy variables above to Railway Dashboard")
print("="*70 + "\n")
