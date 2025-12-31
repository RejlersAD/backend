import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile as RBACProfile, UserRole
from django.contrib.auth import get_user_model

User = get_user_model()

u = User.objects.get(email='shareeq@rejlers.ae')
print(f'\n✅ User: {u.email}')
print(f'   ID: {u.id}')

rbac_profile = RBACProfile.objects.filter(user=u, is_deleted=False).first()
print(f'\n📋 Has RBAC Profile: {rbac_profile is not None}')

if rbac_profile:
    print(f'   RBAC Profile ID: {rbac_profile.id}')
    print(f'   Organization: {rbac_profile.organization.name}')
    print(f'   Status: {rbac_profile.status}')
    
    roles = UserRole.objects.filter(user_profile=rbac_profile)
    print(f'\n🎭 Total roles: {roles.count()}')
    for ur in roles:
        print(f'   - {ur.role.name} ({ur.role.code}) [Primary: {ur.is_primary}]')
else:
    print('\n❌ NO RBAC PROFILE - This is why roles are not showing!')
    print('   Need to create RBAC profile for this user')
