# Generated migration for password expiry feature

from django.db import migrations
from django.utils import timezone


def set_default_password_change_date(apps, schema_editor):
    """Set last_password_change for users who don't have it set"""
    User = apps.get_model('users', 'User')
    
    # Update users with no last_password_change
    users_to_update = User.objects.filter(last_password_change__isnull=True)
    count = users_to_update.count()
    
    for user in users_to_update:
        # Set to date_joined as initial value
        user.last_password_change = user.date_joined
    
    User.objects.bulk_update(users_to_update, ['last_password_change'])
    
    print(f"Updated {count} users with default last_password_change dates")


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_user_is_first_login_user_last_password_change_and_more'),
    ]

    operations = [
        migrations.RunPython(set_default_password_change_date, migrations.RunPython.noop),
    ]
