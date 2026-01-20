#!/usr/bin/env python
"""Quick script to check QHSE data statistics"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.qhse.models import QHSERunningProject
from django.db.models import Sum, Avg, Count

# Get statistics
total = QHSERunningProject.objects.count()
cars_sum = QHSERunningProject.objects.aggregate(Sum('cars_open'))['cars_open__sum'] or 0
obs_sum = QHSERunningProject.objects.aggregate(Sum('obs_open'))['obs_open__sum'] or 0

print(f"Total Projects: {total}")
print(f"Total CARs Open: {cars_sum}")
print(f"Total Observations Open: {obs_sum}")
print("\n" + "="*50)
print("First 10 projects:")
print("="*50)

projects = QHSERunningProject.objects.all()[:10]
for p in projects:
    print(f"Project: {p.project_no}")
    print(f"  CARs Open: {p.cars_open}, Obs Open: {p.obs_open}")
    print(f"  KPI: {p.project_kpis_achieved_percent}, Completion: {p.project_completion_percent}")
    print(f"  Starting Date: {p.project_starting_date}")
    print()
