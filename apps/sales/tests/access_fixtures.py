"""Explicit access policies for sales workflow tests (no mocked authorization)."""
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions


def grant_sales_actions(user, *codes):
    organization, _ = Organization.objects.get_or_create(code='sales-tests', defaults={'name': 'Sales tests'})
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
    role, _ = Role.objects.get_or_create(code='sales-test-operator', defaults={'name': 'Sales test operator', 'level': 4})
    UserRole.objects.get_or_create(user_profile=profile, role=role)
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=role, module=module)
        for permission in module.permissions.filter(is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
