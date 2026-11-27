"""Register RADAI Intelligence as an RBAC module.

Every active role is granted the module, so everyone who uses RADAI Intelligence today keeps
using it. Administrators can then revoke it per role or per user in Roles & Access; project
invitations and shared-project access check it.
"""
from django.db import migrations

CODE = 'radai_intelligence'


def register(apps, schema_editor):
    from apps.rbac.module_actions import ensure_module_actions
    Module = apps.get_model('rbac', 'Module')
    Permission = apps.get_model('rbac', 'Permission')
    Role = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db = schema_editor.connection.alias
    module, created = Module.objects.using(db).get_or_create(code=CODE, defaults={
        'name': 'RADAI Intelligence', 'icon': 'SparklesIcon', 'is_active': True,
        'description': 'Project-aware AI workspace: general chat, AI projects and project invitations.',
    })
    ensure_module_actions(Module, Permission, using=db, module_ids=[module.pk])
    if created:  # grant once; never re-grant a module an administrator later revoked
        granted = set(RoleModule.objects.using(db).filter(module=module).values_list('role_id', flat=True))
        RoleModule.objects.using(db).bulk_create([
            RoleModule(role=role, module=module) for role in Role.objects.using(db).filter(is_active=True)
            if role.pk not in granted
        ])


def unregister(apps, schema_editor):
    apps.get_model('rbac', 'Module').objects.using(schema_editor.connection.alias).filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0027_ai_workspace_member_invitations'),
        ('rbac', '0071_two_organizations'),
    ]

    operations = [migrations.RunPython(register, unregister)]
