from django.db import migrations


def grant_default_enquiry_access(apps, schema_editor):
    """Keep existing Default roles aligned with the configured module policy."""
    Module = apps.get_model('rbac', 'Module')
    Role = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias = schema_editor.connection.alias

    module, _ = Module.objects.using(db_alias).get_or_create(
        code='enquiry_management',
        defaults={
            'name': 'Enquiry Management',
            'description': 'Customer enquiries from public contact form',
            'icon': 'Envelope',
            'order': 55,
            'is_active': True,
        },
    )

    try:
        default_role = Role.objects.using(db_alias).get(code='default')
    except Role.DoesNotExist:
        return

    RoleModule.objects.using(db_alias).get_or_create(
        role=default_role,
        module=module,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0047_rename_purchase_recommendations_module'),
    ]

    operations = [
        migrations.RunPython(grant_default_enquiry_access, migrations.RunPython.noop),
    ]
