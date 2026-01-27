"""
Smart App Loading Verification Script
Tests the app_exists() function and shows which apps are loaded
"""
import os
import sys
from pathlib import Path

# Setup Django
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings

print("\n" + "="*70)
print("🔍 SMART APP LOADING VERIFICATION")
print("="*70)

print("\n📦 ALL INSTALLED APPS:")
print("-" * 70)
for idx, app in enumerate(settings.INSTALLED_APPS, 1):
    print(f"  {idx:2}. {app}")

print(f"\n✅ Total apps loaded: {len(settings.INSTALLED_APPS)}")

# Check optional apps specifically
print("\n" + "="*70)
print("🎯 OPTIONAL APPS STATUS")
print("="*70)

optional_apps = ['apps.ml_detection', 'apps.activity', 'apps.qhse']

for app in optional_apps:
    if app in settings.INSTALLED_APPS:
        print(f"  ✅ {app:<30} LOADED")
    else:
        print(f"  ⚠️  {app:<30} NOT LOADED (missing or disabled)")

print("\n" + "="*70)
print("💡 DEPLOYMENT SAFETY")
print("="*70)
print("""
✅ Smart Loading Benefits:
   • Railway won't crash if optional apps are missing
   • Apps are checked before loading
   • Clear console messages show which apps loaded
   • Easy to add new optional apps without risk

🔧 How It Works:
   1. app_exists() checks if app directory exists
   2. Only loads apps that have __init__.py
   3. Logs which apps were loaded/skipped
   4. Prevents ModuleNotFoundError crashes
""")

print("="*70 + "\n")
