#!/usr/bin/env python
"""
Smart Approval Hierarchy Setup - Fully Soft-Coded
Automatically routes invoices based on type using environment variables
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.finance.models import ApprovalRoute, InvoiceType

print("=" * 80)
print("SMART APPROVAL HIERARCHY SETUP - Fully Soft-Coded from .env")
print("=" * 80)

# SMART: Read all emails from .env dynamically
def get_email(key, fallback_name=""):
    """Get email from env, return dict with name and email if found"""
    email = os.getenv(key, '').strip()
    if email and '@' in email:
        # Extract name from key: FINANCE_RICHA_EMAIL -> Richa
        name_part = key.replace('FINANCE_', '').replace('_EMAIL', '')
        return {'email': email, 'name': name_part.title()}
    return None

# Load all team emails - NO HARDCODED FALLBACKS
emails = {
    'richa': get_email('FINANCE_RICHA_EMAIL'),
    'jamal': get_email('FINANCE_JAMAL_EMAIL'),
    'rafat': get_email('FINANCE_RAFAT_EMAIL'),
    'moe': get_email('FINANCE_MOE_EMAIL'),
    'jarmo': get_email('FINANCE_JARMO_EMAIL'),
    'aneef': get_email('FINANCE_ANEEF_EMAIL'),
    'aleksi': get_email('FINANCE_ALEKSI_EMAIL'),
    'sherwin': get_email('FINANCE_SHERWIN_EMAIL'),
    'nijum': get_email('FINANCE_NIJUM_EMAIL'),
    'hr_admin': get_email('FINANCE_HR_ADMIN_EMAIL'),
}

print("\n📧 Team Emails Loaded:")
for role, data in emails.items():
    if data:
        print(f"  ✓ {role.upper()}: {data['email']}")
    else:
        print(f"  ⚠ {role.upper()}: Not configured (will be skipped)")

print("\n" + "=" * 80)
print("Building Approval Hierarchies")
print("=" * 80)

def build_chain(roles_config):
    """Build approval chain, skipping unconfigured emails"""
    chain = []
    level = 1
    for role_name, role_key, title, cc_list in roles_config:
        email_data = emails.get(role_key)
        if email_data and email_data['email']:
            chain_entry = {
                "level": level,
                "name": role_name,
                "email": email_data['email'],
                "title": title
            }
            
            # Add CC emails if provided and configured
            if cc_list:
                cc_emails = []
                for cc_key in cc_list:
                    cc_data = emails.get(cc_key)
                    if cc_data and cc_data['email']:
                        cc_emails.append(cc_data['email'])
                if cc_emails:
                    chain_entry['cc'] = cc_emails
            
            chain.append(chain_entry)
            level += 1
    return chain

# Define approval hierarchies for each invoice type
hierarchies = {
    InvoiceType.PROJECT: {
        'name': 'Project Invoice',
        'roles': [
            ('Richa (Procurement)', 'richa', 'Procurement Manager', None),
            ('Project Manager', 'jamal', 'Project Manager', ['rafat']),  # CC to Jamal & Rafat
            ('Mo (VP)', 'moe', 'Vice President', None),
            ('Jarmo (CEO)', 'jarmo', 'Chief Executive Officer', None),
        ]
    },
    InvoiceType.ADMIN: {
        'name': 'General/Admin Invoice',
        'roles': [
            ('Richa (Procurement)', 'richa', 'Procurement Manager', None),
            ('HR/Admin', 'hr_admin', 'HR/Admin Manager', None),
            ('Jarmo (CEO)', 'jarmo', 'Chief Executive Officer', None),
        ]
    },
    InvoiceType.FINANCE: {
        'name': 'Accounts/Finance Invoice',
        'roles': [
            ('Richa (Procurement)', 'richa', 'Procurement Manager', None),
            ('Aneef (Finance)', 'aneef', 'Finance Manager', None),
            ('Aleksi (CFO)', 'aleksi', 'Chief Financial Officer', None),
            ('Jarmo (CEO)', 'jarmo', 'Chief Executive Officer', None),
        ]
    },
    InvoiceType.IT: {
        'name': 'IT Invoice',
        'roles': [
            ('Richa (Procurement)', 'richa', 'Procurement Manager', None),
            ('Sherwin/Nijum (ICT)', 'sherwin', 'ICT Manager', ['nijum']),  # CC to Nijum
            ('Aleksi (CFO)', 'aleksi', 'Chief Financial Officer', None),
            ('Jarmo (CEO)', 'jarmo', 'Chief Executive Officer', None),
        ]
    }
}

# Create/Update approval routes
for invoice_type, config in hierarchies.items():
    print(f"\n📋 {config['name']}:")
    chain = build_chain(config['roles'])
    
    if not chain:
        print(f"  ⚠ No configured approvers for {config['name']}, skipping...")
        continue
    
    route, created = ApprovalRoute.objects.update_or_create(
        invoice_type=invoice_type,
        defaults={
            'approval_chain': chain,
            'is_active': True,
            'priority': 10
        }
    )
    
    action = "Created" if created else "Updated"
    print(f"  ✓ {action} approval route with {len(chain)} levels:")
    for level_data in chain:
        cc_info = f" (CC: {', '.join(level_data.get('cc', []))})" if level_data.get('cc') else ""
        print(f"     Level {level_data['level']}: {level_data['name']} - {level_data['email']}{cc_info}")

print("\n" + "=" * 80)
print("SMART APPROVAL HIERARCHY COMPLETE")
print("=" * 80)
print("\n✅ Approval Flow:")
print("   1. Finance Team receives invoice (khanabdullahomar886@gmail.com)")
print("   2. System automatically sends to Richa (Level 1)")
print("   3. Richa approves → Routes based on invoice type")
print("   4. Each level approves → Moves to next level")
print("   5. Final approval → Invoice fully approved in RAD AI")
print("\n✅ All emails include PDF attachments + Accept/Reject buttons")
print("✅ Status updates reflected in RAD AI system in real-time")
print("✅ Fully soft-coded - add emails to .env to activate levels")
