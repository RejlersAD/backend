#!/usr/bin/env python
"""
Verify QHSE Access and Features
Comprehensive test for user: shaju.chacko@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile, Role, Module
from apps.qhse.models import QHSERunningProject
from django.contrib.auth import get_user_model

User = get_user_model()

print('\n' + '='*80)
print('QHSE ACCESS VERIFICATION FOR: shaju.chacko@rejlers.ae')
print('='*80 + '\n')

email = 'shaju.chacko@rejlers.ae'

try:
    # Get user
    user = User.objects.get(email=email)
    print(f'✅ User Found')
    print(f'   Name: {user.get_full_name()}')
    print(f'   Email: {user.email}')
    print(f'   Active: {user.is_active}')
    print(f'   Staff: {user.is_staff}')
    print(f'   Superuser: {user.is_superuser}\n')
    
    # Get profile
    profile = UserProfile.objects.get(user=user)
    print(f'✅ Profile Found')
    print(f'   Status: {profile.status}')
    print(f'   Department: {profile.department or "N/A"}')
    print(f'   Job Title: {profile.job_title or "N/A"}\n')
    
    # Get roles
    roles = profile.roles.all()
    print(f'✅ Roles ({len(roles)}):')
    for role in roles:
        print(f'   - {role.name} ({role.code})')
        print(f'     Level: {role.level}')
        print(f'     Active: {role.is_active}')
    print()
    
    # Get modules from roles
    print(f'✅ Accessible Modules:')
    all_modules = []
    for role in roles:
        role_modules = role.modules.all()
        for module in role_modules:
            if module.code not in [m.code for m in all_modules]:
                all_modules.append(module)
    
    for module in all_modules:
        indicator = '🎯' if module.code == 'qhse' else '  '
        print(f'   {indicator} {module.name} ({module.code})')
    print()
    
    # Check QHSE specific access
    qhse_module = Module.objects.get(code='qhse')
    has_qhse = qhse_module in all_modules
    
    print(f'{"✅" if has_qhse else "❌"} QHSE Module Access: {has_qhse}')
    
    if has_qhse:
        print(f'   Module: {qhse_module.name}')
        print(f'   Code: {qhse_module.code}')
        print(f'   Active: {qhse_module.is_active}')
        
        # Check QHSE data access
        print(f'\n✅ QHSE Data Access Test:')
        projects = QHSERunningProject.objects.filter(is_active=True)
        print(f'   Total QHSE Projects: {projects.count()}')
        
        if projects.exists():
            print(f'   Sample projects:')
            for project in projects[:3]:
                print(f'   - {project.project_no}: {project.project_title}')
    else:
        print(f'\n❌ NO QHSE ACCESS!')
        print(f'   The user does not have QHSE module in their roles.')
        print(f'   Available modules: {[m.name for m in all_modules]}')
    
    print()
    
    # Check permissions
    print(f'✅ Role Permissions:')
    all_permissions = []
    for role in roles:
        role_perms = role.permissions.all()
        for perm in role_perms:
            key = f'{perm.module.code}.{perm.action}'
            if key not in [p['key'] for p in all_permissions]:
                all_permissions.append({
                    'key': key,
                    'module': perm.module.name,
                    'action': perm.action,
                    'role': role.name
                })
    
    qhse_perms = [p for p in all_permissions if 'qhse' in p['key']]
    if qhse_perms:
        print(f'   QHSE Permissions ({len(qhse_perms)}):')
        for perm in qhse_perms[:10]:
            print(f'   - {perm["action"]} (via {perm["role"]})')
    else:
        print(f'   No direct QHSE permissions found')
        print(f'   Total permissions: {len(all_permissions)}')
    
    print()
    
    # Summary
    print('='*80)
    print('SUMMARY')
    print('='*80)
    print(f'User: {user.get_full_name()} ({email})')
    print(f'QHSE Access: {"✅ GRANTED" if has_qhse else "❌ DENIED"}')
    print(f'Roles: {", ".join([r.name for r in roles])}')
    print(f'Total Modules: {len(all_modules)}')
    print(f'QHSE Module: {"✅ YES" if has_qhse else "❌ NO"}')
    
    if has_qhse:
        print(f'\n🎉 USER HAS FULL QHSE ACCESS!')
        print(f'   They can access: https://www.radai.ae/qhse/general')
        print(f'   All QHSE features are available.')
    else:
        print(f'\n⚠️  USER DOES NOT HAVE QHSE ACCESS!')
        print(f'   They need a role with QHSE module.')
        print(f'   Contact system administrator.')
    
    print('='*80 + '\n')
    
except User.DoesNotExist:
    print(f'❌ User not found: {email}')
except UserProfile.DoesNotExist:
    print(f'❌ User profile not found for: {email}')
except Exception as e:
    print(f'❌ Error: {str(e)}')
    import traceback
    traceback.print_exc()
