from django.apps import apps
from django.db import connection
from django.test import TestCase
from importlib import import_module

from apps.rbac.models import Module, Permission, Role, RolePermission, RoleModule
from apps.rbac.module_actions import MODULE_ACTIONS, ensure_module_actions


class ModuleActionCatalogueTests(TestCase):
    def test_migration_fills_all_six_actions_without_changing_grants(self):
        module = Module.objects.create(code='complete_actions', name='Complete actions')
        other = Module.objects.create(code='complete_child', name='Child module')
        inactive = Module.objects.create(code='inactive_child', name='Inactive', is_active=False)
        role = Role.objects.create(code='actions_role', name='Actions role', level=3)
        RoleModule.objects.create(role=role, module=module)
        existing = Permission.objects.create(module=module, code='legacy_view', name='Existing view', action='read')
        RolePermission.objects.create(role=role, permission=existing)
        # Intentionally disabled definitions must not become active through seeding.
        disabled = Permission.objects.create(module=module, code='complete_actions.export', name='Disabled export', action='export', is_active=False)
        migration = import_module('apps.rbac.migrations.0054_complete_module_action_catalogue')
        from types import SimpleNamespace
        migration.complete_catalogue(apps, SimpleNamespace(connection=connection))
        for target in (module, other):
            self.assertSetEqual(set(target.permissions.filter(is_active=True).values_list('action', flat=True)), {a for a, _ in MODULE_ACTIONS})
        self.assertEqual(list(role.permissions.values_list('pk', flat=True)), [existing.pk])
        self.assertEqual(list(role.modules.values_list('pk', flat=True)), [module.pk])
        self.assertFalse(inactive.permissions.exists())
        disabled.refresh_from_db()
        self.assertFalse(disabled.is_active)
        self.assertEqual(ensure_module_actions(Module, Permission), 0)

    def test_new_module_provisions_actions_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            module = Module.objects.create(code='new_actions', name='New module')
        self.assertEqual(module.permissions.count(), 6)
        self.assertFalse(RolePermission.objects.filter(permission__module=module).exists())
