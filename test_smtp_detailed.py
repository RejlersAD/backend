"""
Detailed SMTP authentication test
"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
import smtplib
import base64

print("=" * 70)
print("DETAILED SMTP AUTHENTICATION TEST")
print("=" * 70)

print("\n📧 Configuration:")
print(f"Host: {settings.EMAIL_HOST}")
print(f"Port: {settings.EMAIL_PORT}")
print(f"User: {settings.EMAIL_HOST_USER}")
print(f"Pass: {settings.EMAIL_HOST_PASSWORD[:5]}...{settings.EMAIL_HOST_PASSWORD[-5:]}")
print(f"Pass Length: {len(settings.EMAIL_HOST_PASSWORD)}")

print("\n🔌 Step 1: Connecting to SMTP server...")
try:
    server = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=15)
    print("✅ Connected successfully")
except Exception as e:
    print(f"❌ Connection failed: {e}")
    sys.exit(1)

print("\n👋 Step 2: Sending EHLO...")
try:
    code, msg = server.ehlo()
    print(f"✅ EHLO response: {code}")
except Exception as e:
    print(f"❌ EHLO failed: {e}")
    server.quit()
    sys.exit(1)

print("\n🔒 Step 3: Starting TLS...")
try:
    server.starttls()
    print("✅ TLS started")
except Exception as e:
    print(f"❌ TLS failed: {e}")
    server.quit()
    sys.exit(1)

print("\n👋 Step 4: Sending EHLO after TLS...")
try:
    code, msg = server.ehlo()
    print(f"✅ EHLO response: {code}")
except Exception as e:
    print(f"❌ EHLO after TLS failed: {e}")
    server.quit()
    sys.exit(1)

print("\n🔐 Step 5: Attempting LOGIN authentication...")
print(f"Username: {settings.EMAIL_HOST_USER}")
try:
    server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
    print("✅ ✅ ✅ Authentication SUCCESSFUL! ✅ ✅ ✅")
    server.quit()
    print("\n" + "=" * 70)
    print("SMTP authentication is working correctly!")
    print("The credentials are valid for AWS SES.")
    print("=" * 70)
except smtplib.SMTPAuthenticationError as e:
    print(f"❌ Authentication failed: {e}")
    print("\n⚠️  Possible reasons:")
    print("1. The SMTP username or password is incorrect")
    print("2. The IAM user doesn't have SES sending permissions")
    print("3. The credentials might be for a different AWS region")
    print("4. The IAM user might be deactivated")
    server.quit()
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    server.quit()
    sys.exit(1)
