#!/usr/bin/env python
import sys
import os
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.qhse.models import QHSERunningProject

print("\n" + "="*70)
print("📊 QHSE Data Verification")
print("="*70 + "\n")

total = QHSERunningProject.objects.count()
print(f"✅ Total Projects Imported: {total}\n")

if total > 0:
    print("📋 Sample Projects:")
    for p in QHSERunningProject.objects.all()[:5]:
        print(f"  • {p.project_no} - {p.project_title[:60]}")
    
    print(f"\n📈 Statistics:")
    print(f"  • Active Projects: {QHSERunningProject.objects.filter(is_active=True).count()}")
    print(f"  • Clients: {QHSERunningProject.objects.values('client').distinct().count()}")
    
print("\n" + "="*70 + "\n")
