#!/usr/bin/env python
"""Verify Planning Package feature is registered in backend feature registry"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.core.feature_registry import FeatureRegistry

registry = FeatureRegistry()
features = [f for f in registry.features if f.id == 'planning_package']

print(f'✓ Found {len(features)} Planning Package feature(s) in registry')
if features:
    f = features[0]
    print(f'  - Name: {f.name}')
    print(f'  - Category: {f.category.value}')
    print(f'  - Frontend Route: {f.frontend_route}')
    print(f'  - Backend URL: {f.backend_url_pattern}')
    print(f'  - Status: {f.status.value}')
    print(f'  - Order: {f.order}')
    print(f'  - Is New: {f.is_new}')
    print(f'  - Icon: {f.icon}')
else:
    print('✗ Planning Package feature NOT FOUND!')
