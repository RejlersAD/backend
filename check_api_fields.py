#!/usr/bin/env python
import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from apps.rbac.serializers import UserProfileSerializer

User = get_user_model()

# Find user
user = User.objects.filter(email='tanzeem.agra@rejlers.ae').first()
if not user:
    print('User not found!')
    exit(1)

# Get UserProfile
profile = UserProfile.objects.filter(user=user).first()
if not profile:
    print('UserProfile not found!')
    exit(1)

# Serialize the data
serializer = UserProfileSerializer(profile)
api_response = serializer.data

print('\n=== API RESPONSE ===')
print(json.dumps(api_response, indent=2, default=str))
