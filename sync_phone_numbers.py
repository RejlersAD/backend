#!/usr/bin/env python
"""
Phone Number Sync Script
Smartly syncs phone/mobile numbers from Excel to database
Soft-coded with flexible column detection and comprehensive logging
"""
import os
import django
import sys
import pandas as pd
from pathlib import Path
import re

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

# Soft-coded configuration
CONFIG = {
    'excel_path': Path('/app/radai_user-registration_template.xlsx'),
    'email_columns': ['Email Address', 'Email', 'email', 'EMAIL'],
    'phone_columns': ['Phone Number', 'Mobile', 'Mobile Number', 'Contact Number', 
                      'Phone', 'phone', 'mobile', 'Contact', 'Tel', 'Telephone'],
    'name_columns': ['Name', 'Full Name', 'First Name'],
    'phone_patterns': [
        r'^\+?\d{8,15}$',  # International format with optional +
        r'^\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{4}$',  # Standard formats
        r'^\(\d{3}\)[\s\-]?\d{3,4}[\s\-]?\d{4}$'  # (XXX) XXX-XXXX
    ]
}


def clean_phone_number(phone_str):
    """
    Clean and standardize phone number
    Removes spaces, dashes, parentheses
    Returns None if invalid
    """
    if pd.isna(phone_str) or str(phone_str).strip() in ['', 'nan', 'None']:
        return None
    
    # Convert to string and clean
    phone = str(phone_str).strip()
    
    # Remove common formatting
    phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('.', '')
    
    # Remove leading zeros if phone starts with country code
    if phone.startswith('00'):
        phone = '+' + phone[2:]
    
    # Validate against patterns
    for pattern in CONFIG['phone_patterns']:
        if re.match(pattern, phone):
            return phone
    
    # If length is reasonable, accept it
    if 8 <= len(phone) <= 15 and phone.replace('+', '').isdigit():
        return phone
    
    return None


def detect_column(df, possible_names):
    """
    Soft-coded column detection
    Finds the first matching column name
    """
    for col_name in possible_names:
        if col_name in df.columns:
            return col_name
    return None


def sync_phone_numbers():
    """Main sync function with smart detection and updates"""
    print("\n" + "="*90)
    print("📱 SMART PHONE NUMBER SYNC - Excel → Database")
    print("="*90)
    
    # Check Excel file
    excel_path = CONFIG['excel_path']
    if not excel_path.exists():
        print(f"❌ Excel file not found: {excel_path}")
        return False
    
    print(f"✅ Found Excel file: {excel_path}")
    
    # Load Excel
    try:
        df = pd.read_excel(excel_path)
        print(f"✅ Loaded {len(df)} rows from Excel")
        print(f"📋 Available columns: {', '.join(df.columns.tolist())}")
    except Exception as e:
        print(f"❌ Error reading Excel: {e}")
        return False
    
    # Detect columns (soft-coded)
    email_col = detect_column(df, CONFIG['email_columns'])
    phone_col = detect_column(df, CONFIG['phone_columns'])
    name_col = detect_column(df, CONFIG['name_columns'])
    
    if not email_col:
        print(f"❌ Email column not found. Tried: {CONFIG['email_columns']}")
        return False
    
    if not phone_col:
        print(f"❌ Phone column not found. Tried: {CONFIG['phone_columns']}")
        return False
    
    print(f"✅ Detected Email Column: '{email_col}'")
    print(f"✅ Detected Phone Column: '{phone_col}'")
    if name_col:
        print(f"✅ Detected Name Column: '{name_col}'")
    
    # Statistics
    stats = {
        'total_rows': len(df),
        'valid_phones': 0,
        'invalid_phones': 0,
        'users_found': 0,
        'users_not_found': 0,
        'phones_updated': 0,
        'phones_added': 0,
        'phones_unchanged': 0,
        'errors': 0
    }
    
    print(f"\n🔄 Processing {stats['total_rows']} employees...\n")
    
    # Process each row
    for idx, row in df.iterrows():
        email = str(row[email_col]).strip().lower()
        phone_raw = row[phone_col]
        name = str(row[name_col]).strip() if name_col and name_col in row else email
        
        # Skip invalid emails
        if not email or email == 'nan' or '@' not in email:
            continue
        
        # Clean phone number
        phone_clean = clean_phone_number(phone_raw)
        
        if not phone_clean:
            stats['invalid_phones'] += 1
            continue
        
        stats['valid_phones'] += 1
        
        # Find user in database
        try:
            user = User.objects.get(email=email)
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            stats['users_found'] += 1
            
            # Check current phone
            current_phone = profile.phone or ''
            
            if not current_phone:
                # Add new phone
                profile.phone = phone_clean
                profile.save()
                stats['phones_added'] += 1
                print(f"  ➕ {email:45} | Added: {phone_clean:15} | {name}")
            elif current_phone != phone_clean:
                # Update phone
                old_phone = current_phone
                profile.phone = phone_clean
                profile.save()
                stats['phones_updated'] += 1
                print(f"  🔄 {email:45} | {old_phone} → {phone_clean} | {name}")
            else:
                # Phone unchanged
                stats['phones_unchanged'] += 1
                
        except User.DoesNotExist:
            stats['users_not_found'] += 1
            print(f"  ⚠️  {email:45} | User not found in database")
        except UserProfile.DoesNotExist:
            stats['users_not_found'] += 1
            print(f"  ⚠️  {email:45} | Profile not found in database")
        except Exception as e:
            stats['errors'] += 1
            print(f"  ❌ {email:45} | Error: {e}")
    
    # Summary
    print("\n" + "="*90)
    print("📊 SYNC SUMMARY")
    print("="*90)
    print(f"  📄 Total Rows in Excel:        {stats['total_rows']}")
    print(f"  ✅ Valid Phone Numbers:        {stats['valid_phones']}")
    print(f"  ❌ Invalid Phone Numbers:      {stats['invalid_phones']}")
    print(f"  👤 Users Found in DB:          {stats['users_found']}")
    print(f"  ⚠️  Users Not Found:            {stats['users_not_found']}")
    print(f"\n  📱 Phone Number Updates:")
    print(f"     • Added (New):              {stats['phones_added']}")
    print(f"     • Updated (Changed):        {stats['phones_updated']}")
    print(f"     • Unchanged (Same):         {stats['phones_unchanged']}")
    print(f"     • Errors:                   {stats['errors']}")
    print(f"\n  🎯 Total Changes Applied:      {stats['phones_added'] + stats['phones_updated']}")
    print("="*90)
    
    return True


if __name__ == '__main__':
    try:
        success = sync_phone_numbers()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
