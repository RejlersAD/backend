#!/usr/bin/env python
"""
Employee Department Sync Script
Syncs employee data from radai_user-registration_template.xlsx to database
Updates UserProfile with department and job title information
"""
import os
import django
import sys
import pandas as pd
from pathlib import Path

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization

User = get_user_model()

# Path to Excel file (soft-coded for container environment)
EXCEL_PATH = Path('/app/radai_user-registration_template.xlsx')


def sync_employee_departments():
    """Sync employee departments from Excel to database"""
    print("\n" + "="*80)
    print("📊 SYNCING EMPLOYEE DEPARTMENTS FROM EXCEL")
    print("="*80)
    
    # Check if file exists
    if not EXCEL_PATH.exists():
        print(f"❌ Excel file not found: {EXCEL_PATH}")
        return False
    
    print(f"✅ Found Excel file: {EXCEL_PATH}")
    
    # Read Excel file
    try:
        df = pd.read_excel(EXCEL_PATH)
        print(f"✅ Loaded {len(df)} employees from Excel")
    except Exception as e:
        print(f"❌ Error reading Excel: {e}")
        return False
    
    # Validate columns
    required_columns = ['Email Address', 'First Name', 'Last Name', 'Department', 'Job Title']
    missing_columns = [col for col in required_columns if col not in df.columns]
    
    if missing_columns:
        print(f"❌ Missing columns: {missing_columns}")
        return False
    
    print(f"✅ All required columns present")
    
    # Get default organization
    default_org = Organization.objects.filter(is_active=True).first()
    if not default_org:
        print("❌ No active organization found")
        return False
    
    print(f"✅ Using organization: {default_org.name}")
    
    # Process each employee
    stats = {
        'total': len(df),
        'updated': 0,
        'created': 0,
        'skipped': 0,
        'errors': 0
    }
    
    print(f"\n📝 Processing {stats['total']} employees...")
    
    for idx, row in df.iterrows():
        email = str(row['Email Address']).strip().lower()
        first_name = str(row['First Name']).strip()
        last_name = str(row['Last Name']).strip()
        department = str(row['Department']).strip()
        job_title = str(row['Job Title']).strip()
        phone = str(row.get('Phone Number', '')).strip() if 'Phone Number' in row else ''
        
        if not email or email == 'nan':
            stats['skipped'] += 1
            continue
        
        try:
            # Get or create user
            user, user_created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email.split('@')[0],
                    'first_name': first_name,
                    'last_name': last_name,
                    'is_active': True
                }
            )
            
            # Get or create profile
            profile, profile_created = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'organization': default_org,
                    'department': department,
                    'job_title': job_title,
                    'phone': phone,
                    'status': 'active'
                }
            )
            
            # Update profile if it exists
            if not profile_created:
                updated = False
                
                if profile.department != department:
                    profile.department = department
                    updated = True
                
                if profile.job_title != job_title:
                    profile.job_title = job_title
                    updated = True
                
                if phone and profile.phone != phone:
                    profile.phone = phone
                    updated = True
                
                if updated:
                    profile.save()
                    stats['updated'] += 1
                    print(f"  ✅ Updated: {email} → {department}")
                else:
                    stats['skipped'] += 1
            else:
                stats['created'] += 1
                print(f"  ✅ Created: {email} → {department}")
                
        except Exception as e:
            stats['errors'] += 1
            print(f"  ❌ Error processing {email}: {e}")
    
    # Print summary
    print("\n" + "="*80)
    print("📊 SYNC SUMMARY")
    print("="*80)
    print(f"  • Total Employees: {stats['total']}")
    print(f"  • Created Profiles: {stats['created']}")
    print(f"  • Updated Profiles: {stats['updated']}")
    print(f"  • Skipped: {stats['skipped']}")
    print(f"  • Errors: {stats['errors']}")
    
    # Department distribution
    print("\n📊 Department Distribution:")
    dept_counts = df['Department'].value_counts()
    for dept, count in dept_counts.items():
        print(f"   • {dept}: {count} employees")
    
    return stats['errors'] == 0


if __name__ == '__main__':
    success = sync_employee_departments()
    sys.exit(0 if success else 1)
