#!/usr/bin/env python
"""Check specific users in production"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole

User = get_user_model()

emails = [
    'rahul.more@rejlers.ae',
    'hadef.omar@rejlers.ae',
    'sherwin.palle@rejlers.ae',
    'sohail.nasir@rejlers.ae',
    'himanshu.mehta@rejlers.ae',
    'rahul.lapalikar@rejlers.ae',
    'pushpendra.lahariya@rejlers.ae',
    'mahaboobkhan.salaudeen@rejlers.ae',
    'jyothish.mohan@rejlers.ae',
    'allan.mendoza@rejlers.ae',
    'kirankumar.rakshe@rejlers.ae',
    'muniyasamy.rengasamy@rejlers.ae',
    'rajesh.parmar@rejlers.ae',
    'selva.perumal@rejlers.ae',
    'rajeshkumar.patanwadia@rejlers.ae',
    'rakhi.rajendran@rejlers.ae',
    'prasad.parte@rejlers.ae',
    'ovais.rahman@rejlers.ae',
    'rajesh.rajagopal@rejlers.ae',
    'nafeesuddin.qazi@rejlers.ae',
    'sandesh.patil@rejlers.ae',
    'rupesh.shete@rejlers.ae',
    'shreeram.selvaraj@rejlers.ae',
    'ismail.sayyed@rejlers.ae',
    'mohd.risal@rejlers.ae',
    'salimuddin.siddiqui@rejlers.ae',
    'arvind.sharma@rejlers.ae',
    'habib.shaikh@rejlers.ae',
    'shehbaaz.shaikh@rejlers.ae',
    'laik.shaikh@rejlers.ae',
    'musheer.ahmed@rejlers.ae',
    'aniket.sur@rejlers.ae',
    'anthoni.victor@rejlers.ae',
    'rajnesh.yadav@rejlers.ae',
    'aniket.bagal@rejlers.ae',
    'tejal.chaudhari@rejlers.ae',
    'richahannah.thomas@rejlers.ae',
    'vinit.vyas@rejlers.ae',
    'ibrahim.butt@rejlers.ae',
    'abdulrahman.alyahyaee@rejlers.ae',
    'salem.albreiki@rejlers.ae',
    'wazid.hasan@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae',
    'praful.indulkar@rejlers.ae',
    'ashiq.sathakathulla@rejlers.ae',
    'bibeesh.mohanan@rejlers.ae',
    'tejas.gangan@rejlers.ae',
    'mitul.patel@rejlers.ae',
    'shanmugaraja.thamizharasan@rejlers.ae',
    'ruksana.thahira@rejlers.ae',
    'yahya.mubarak@rejlers.ae',
    'muhammad.rashad@rejlers.ae',
    'sanat.yarkhan@rejlers.ae',
    'dona.dominic@rejlers.ae'
]

print("\n" + "=" * 80)
print("  USER STATUS CHECK")
print("=" * 80)

for email in emails:
    user = User.objects.filter(email=email).first()
    if not user:
        print(f"\n❌ {email}: USER NOT FOUND")
        continue
    
    print(f"\n📧 {email}")
    print(f"   User ID: {user.id}")
    print(f"   is_active: {user.is_active}")
    print(f"   is_superuser: {user.is_superuser}")
    print(f"   is_staff: {user.is_staff}")
    
    try:
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        roles = UserRole.objects.filter(
            user_profile=profile,
            role__is_active=True
        ).select_related('role')
        
        role_codes = [ur.role.code for ur in roles]
        print(f"   RBAC Roles: {', '.join(role_codes)}")
        
        # Check if this is a problem case
        ADMIN_ROLES = ['super_admin', 'admin', 'ict_admin']
        has_admin_role = any(r in ADMIN_ROLES for r in role_codes)
        has_django_flags = user.is_superuser or user.is_staff
        
        if has_django_flags and not has_admin_role:
            print(f"   ⚠️  WARNING: Has Django flags but non-admin RBAC role!")
            print(f"   → Should be fixed with: python manage.py fix_rbac_production")
        elif has_django_flags and has_admin_role:
            print(f"   ✅ OK: Admin role matches Django flags")
        elif not has_django_flags and has_admin_role:
            print(f"   ⚠️  WARNING: Admin role but missing Django flags")
        else:
            print(f"   ✅ OK: Non-admin, no Django flags")
            
    except UserProfile.DoesNotExist:
        print(f"   ❌ RBAC Profile: NOT FOUND")

print("\n" + "=" * 80)
