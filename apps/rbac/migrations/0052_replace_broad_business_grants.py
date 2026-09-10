from importlib import import_module

from django.db import migrations


SERVICES = import_module('apps.rbac.migrations.0051_business_services_and_enquiry_rbac').SERVICE_MODULES + [
    ('finance_salary', 'Salary Slips', 'finance', 'View salary slips subject to payroll record permissions'),
]


def replace_broad_grants(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    db = schema_editor.connection.alias
    for index, (code, name, parent, description) in enumerate(SERVICES):
        module, _ = Module.objects.using(db).get_or_create(code=code, defaults={
            'name': name, 'description': description, 'icon': 'LayoutGrid',
            'order': 900 + index, 'is_active': True,
        })
        # Only active historical modules provided access before this migration.
        for grant in RoleModule.objects.using(db).filter(module__code=parent, module__is_active=True):
            RoleModule.objects.using(db).get_or_create(role_id=grant.role_id, module_id=module.pk,
                                                     defaults={'granted_by_id': grant.granted_by_id})
    RoleModule.objects.using(db).filter(module__code__in=['finance', 'sales']).delete()
    # Retain module records for existing permission references and audit history.
    Module.objects.using(db).filter(code__in=['finance', 'sales']).update(is_active=False)
    from django.core.cache import cache
    ids = UserProfile.objects.using(db).values_list('pk', flat=True)
    cache.delete_many([key for pk in ids for key in (f'user_modules_{pk}', f'user_permissions_{pk}')])


class Migration(migrations.Migration):
    dependencies = [('rbac', '0051_business_services_and_enquiry_rbac')]
    operations = [migrations.RunPython(replace_broad_grants, migrations.RunPython.noop)]
