# Generated migration for ensuring default organization exists

from django.db import migrations


def create_default_organization(apps, schema_editor):
    """Ensure a default organization exists for profile provisioning."""
    Organization = apps.get_model('rbac', 'Organization')
    
    # Check if any organization exists
    if not Organization.objects.exists():
        Organization.objects.create(
            code='DEFAULT_ORG',
            name='Default Organization',
            description='Auto-created default organization for user profiles',
            is_active=True,
        )
        print('✅ Created default organization')
    else:
        # Ensure at least one organization is active
        active_count = Organization.objects.filter(is_active=True).count()
        if active_count == 0:
            # Activate the first organization
            first_org = Organization.objects.first()
            if first_org:
                first_org.is_active = True
                first_org.save()
                print(f'✅ Activated organization: {first_org.name}')


def reverse_migration(apps, schema_editor):
    """Reverse is a no-op - we don't want to delete the organization."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0045_fix_orphan_is_hidden_column'),
    ]

    operations = [
        migrations.RunPython(create_default_organization, reverse_migration),
    ]
