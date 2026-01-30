"""
List all email domains in the database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from collections import defaultdict

User = get_user_model()

domains = defaultdict(list)

for user in User.objects.all():
    if '@' in user.email:
        domain = user.email.split('@')[1]
    else:
        domain = 'no-domain'
    domains[domain].append(user.email)

print('EMAIL DOMAINS IN DATABASE:')
print('=' * 60)

for domain in sorted(domains.keys()):
    emails = domains[domain]
    unique_emails = sorted(set(emails))
    print(f'\n{domain}: {len(emails)} users')
    
    # Show first 10 unique emails
    for email in unique_emails[:10]:
        print(f'  - {email}')
    
    if len(unique_emails) > 10:
        print(f'  ... and {len(unique_emails) - 10} more')

print('\n' + '=' * 60)
print(f'\nTotal domains: {len(domains)}')
print(f'Total users: {User.objects.count()}')
