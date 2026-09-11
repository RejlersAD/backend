from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.rbac.action_policy import operation_action, module_action_allowed, additional_actions
from apps.rbac.models import (AuditLog, Module, Organization, Permission, Role,
                             RoleModule, RolePermission, UserProfile, UserRole,
                             UserPermissionOverride)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.rbac.user_permissions import permission_state


class ActionEnforcementTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='Action tests', code='actions-test')
        self.role = Role.objects.create(name='Action operator', code='action-operator', level=4)
        self.users = []
        self.profiles = []
        for index in range(2):
            user = get_user_model().objects.create_user(f'actions-{index}', email=f'actions-{index}@example.test')
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            UserRole.objects.filter(user_profile=profile).delete()
            UserRole.objects.create(user_profile=profile, role=self.role)
            self.users.append(user)
            self.profiles.append(profile)
        self.client = APIClient()
        self.login()

    def login(self, index=0):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(self.users[index])))

    def module(self, code, granted=()):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=granted, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        return module

    def override(self, module, action, allowed=False, index=0):
        for permission in module.permissions.filter(action=action, is_active=True):
            UserPermissionOverride.objects.update_or_create(
                user_profile=self.profiles[index], permission=permission, defaults={'allowed': allowed})

    def test_finance_unchecked_action_denied_and_explicit_allow_restored(self):
        module = self.module('finance_incoming')
        rows = permission_state(self.profiles[0], self.users[1])['permissions']
        self.assertFalse(next(p for p in rows if p['module'] == str(module.pk) and p['action'] == 'read')['effective'])
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 403)
        self.assertEqual(self.client.post('/api/v1/finance/invoices/', {}, format='json').status_code, 403)
        self.override(module, 'read', True)
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 200)
        self.login(1)
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 403)

    def test_pfd_function_endpoint_crud_respects_each_action_and_other_user(self):
        from apps.pfd_quality.models import PFDQProject
        module = self.module('pfd_quality', ['read', 'create', 'update', 'delete'])
        url = '/api/v1/pfd-quality/projects/'
        payload = {'project_name': 'Permission project'}
        self.override(module, 'create')
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
        self.assertFalse(PFDQProject.objects.exists())
        self.login(1)
        other = self.client.post(url, payload, format='json')
        self.assertEqual(other.status_code, 201, other.data)
        self.login()
        UserPermissionOverride.objects.filter(user_profile=self.profiles[0]).delete()
        created = self.client.post(url, payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        detail = url + str(created.data['project_id']) + '/'
        self.override(module, 'update')
        self.assertEqual(self.client.put(detail, {'project_name': 'Denied change'}, format='json').status_code, 403)
        self.assertEqual(PFDQProject.objects.get(pk=created.data['id']).project_name, payload['project_name'])
        self.override(module, 'delete')
        self.assertEqual(self.client.delete(detail).status_code, 403)
        self.override(module, 'read')
        self.assertEqual(self.client.get(url).status_code, 403)
        UserPermissionOverride.objects.filter(user_profile=self.profiles[0]).delete()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 200)
        self.assertFalse(PFDQProject.objects.filter(pk=created.data['id']).exists())
        self.assertTrue(PFDQProject.objects.filter(pk=other.data['id']).exists())

    def test_pid_approve_denial_prevents_side_effect_and_inheritance_restores(self):
        from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport, PIDIssue
        module = self.module('pid_analysis', ['read', 'approve'])
        drawing = PIDDrawing.objects.create(uploaded_by=self.users[0], file='test.pdf', original_filename='test.pdf', file_size=1)
        report = PIDAnalysisReport.objects.create(pid_drawing=drawing, report_data={})
        issue = PIDIssue.objects.create(report=report, serial_number=1, pid_reference='P-1', issue_observed='Test', action_required='Review')
        url = f'/api/v1/pid/issues/{issue.pk}/approve/'
        self.override(module, 'approve')
        self.assertEqual(self.client.post(url).status_code, 403)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'pending')
        UserPermissionOverride.objects.filter(user_profile=self.profiles[0]).delete()
        self.assertEqual(self.client.post(url).status_code, 200)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'approved')

    def test_pid_export_requires_login_export_grant_and_ownership(self):
        from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport
        module = self.module('pid_analysis', ['read', 'export'])
        drawing = PIDDrawing.objects.create(uploaded_by=self.users[0], file='test.pdf', original_filename='test.pdf', file_size=1)
        PIDAnalysisReport.objects.create(pid_drawing=drawing, report_data={})
        url = f'/api/v1/pid-export/{drawing.pk}/'
        self.client.credentials()
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login()
        self.override(module, 'export')
        with patch('apps.api.export_wrapper.PIDReportExportService') as service:
            service.return_value.export_pdf.return_value = HttpResponse(b'pdf')
            self.assertEqual(self.client.get(url).status_code, 403)
            service.assert_not_called()
            UserPermissionOverride.objects.filter(user_profile=self.profiles[0]).delete()
            self.assertEqual(self.client.get(url).status_code, 200)
        self.login(1)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_role_revoke_and_super_admin_override_take_effect_immediately(self):
        module = self.module('finance_incoming', ['read'])
        url = '/api/v1/finance/invoices/'
        self.assertEqual(self.client.get(url).status_code, 200)
        RolePermission.objects.filter(role=self.role, permission__module=module).delete()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.users[0].is_superuser = True
        self.users[0].save()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.override(module, 'read')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_guard_survives_custom_get_permissions_and_role_matrix_routes(self):
        for url in ['/api/v1/finance/invoices/', '/api/v1/rbac/roles/', '/api/v1/pfd-quality/projects/']:
            self.assertTrue(issubclass(resolve(url).func.cls, ModuleActionGuardMixin))

    def test_custom_operations_map_to_real_action(self):
        for name, method, expected in [('export_excel', 'POST', 'export'), ('download', 'GET', 'export'),
                                       ('approve', 'POST', 'approve'), ('assign_role', 'POST', 'update'),
                                       ('partial_update', 'PATCH', 'update'), ('delete_output', 'POST', 'delete')]:
            self.assertEqual(operation_action(SimpleNamespace(method=method), SimpleNamespace(action=name)), expected)

    def test_editor_save_is_enforced_on_next_request_and_restore_keeps_memberships(self):
        finance = self.module('finance_incoming', ['read'])
        self.module('user_mgmt')
        self.users[1].is_superuser = True
        self.users[1].save()
        memberships = list(UserRole.objects.order_by('pk').values())
        role_grants = set(self.role.permissions.values_list('pk', flat=True))
        self.login(1)
        editor = f'/api/v1/rbac/users/{self.profiles[0].pk}/permission-overrides/'
        before = self.client.get(editor)
        self.assertEqual(before.status_code, 200)
        payload = {'snapshot': before.data['snapshot'], 'reason': 'Revoke selected user view',
                   'changes': [{'permission_id': str(finance.permissions.get(action='read').pk), 'effect': 'deny'}]}
        saved = self.client.patch(editor, payload, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.login()
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 403)
        self.login(1)
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 200)
        payload['snapshot'] = saved.data['snapshot']
        payload['changes'][0]['effect'] = 'inherit'
        self.assertEqual(self.client.patch(editor, payload, format='json').status_code, 200)
        self.login()
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 200)
        self.assertEqual(list(UserRole.objects.order_by('pk').values()), memberships)
        self.assertEqual(set(self.role.permissions.values_list('pk', flat=True)), role_grants)

    def test_generic_edit_cannot_bypass_approve_checkbox(self):
        from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport, PIDIssue
        module = self.module('pid_analysis', ['read', 'update'])
        drawing = PIDDrawing.objects.create(uploaded_by=self.users[0], file='test.pdf', original_filename='test.pdf', file_size=1)
        report = PIDAnalysisReport.objects.create(pid_drawing=drawing, report_data={})
        issue = PIDIssue.objects.create(report=report, serial_number=1, pid_reference='P-1', issue_observed='Test', action_required='Review')
        response = self.client.patch(f'/api/v1/pid/issues/{issue.pk}/', {'status': 'approved'}, format='json')
        self.assertEqual(response.status_code, 403)
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'pending')
        self.override(module, 'approve', True)
        self.assertEqual(self.client.patch(f'/api/v1/pid/issues/{issue.pk}/', {'status': 'approved'}, format='json').status_code, 200)

    def test_legacy_iframe_token_still_requires_effective_view_grant(self):
        module = self.module('finance_incoming', ['read'])
        token = str(AccessToken.for_user(self.users[0]))
        url = '/api/v1/finance/invoices/00000000-0000-0000-0000-000000000001/preview/'
        self.client.credentials()
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, {'token': token}).status_code, 404)
        self.override(module, 'read')
        self.assertEqual(self.client.get(url, {'token': token}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/finance/approval/00000000-0000-0000-0000-000000000001/details/').status_code, 404)

    def test_shared_lists_and_critical_feature_cannot_use_sibling_grants(self):
        self.module('pid_line_list', ['read'])
        self.assertEqual(self.client.get('/api/v1/designiq/lists/', {'list_type': 'line_list'}).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/designiq/lists/', {'list_type': 'equipment_list'}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/designiq/lists/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/designiq/critical-lists/', {'list_type': 'line_list'}).status_code, 403)
        critical = self.module('piping_critical_line_list', ['read'])
        self.assertEqual(self.client.get('/api/v1/designiq/critical-lists/', {'list_type': 'line_list'}).status_code, 200)
        self.override(critical, 'read')
        self.assertEqual(self.client.get('/api/v1/designiq/critical-lists/', {'list_type': 'line_list'}).status_code, 403)

    def test_partial_legacy_action_does_not_become_full_module_access(self):
        module = self.module('finance_incoming', ['read'])
        extra = Permission.objects.create(module=module, code='legacy_second_view', action='read', name='Second read capability')
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 403)
        RolePermission.objects.create(role=self.role, permission=extra)
        self.assertEqual(self.client.get('/api/v1/finance/invoices/').status_code, 200)

    def test_current_user_ui_policy_honors_super_admin_view_denial(self):
        module = self.module('pid_analysis', ['read'])
        self.users[0].is_superuser = True
        self.users[0].save()
        self.override(module, 'read')
        response = self.client.get('/api/v1/rbac/users/me/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('read', response.data['module_actions'].get(module.code, []))
        self.assertIn('create', response.data['module_actions'].get(module.code, []))

    def test_download_flag_requires_export_even_on_create_endpoint(self):
        self.module('instrument_io_list', ['read', 'create'])
        for flag in [True, '1', 'yes', ' on ']:
            response = self.client.post('/api/v1/instrument-tools/io-list/', {'rows': [], 'download': flag}, format='json')
            self.assertEqual(response.status_code, 403)

    def test_qhse_area_grants_are_independent_and_keep_team_records_visible(self):
        from apps.qhse.models import QHSERunningProject
        module = self.module('qhse_quality', ['read'])
        project = QHSERunningProject.objects.create(sr_no=1, project_no='Q-1', project_title='QHSE area permission')
        url = '/api/v1/qhse/areas/quality/projects/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(self.client.get('/api/v1/qhse/areas/environmental/projects/').status_code, 403)
        self.override(module, 'read')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_new_custom_writes_require_an_explicit_policy(self):
        self.assertIsNone(operation_action(SimpleNamespace(method='POST'), SimpleNamespace(action='future_unclassified_write')))

    def test_generic_status_variants_cannot_hide_approval_changes(self):
        for data in [{'payment_status': 'paid'}, {'pm_approval_status': 'not_approved'},
                     {'approval_log': [{'status': ' Approved '}]}, {'is_approved': True}]:
            self.assertIn('approve', additional_actions(SimpleNamespace(method='PATCH', data=data, query_params={})))

    def test_assigned_record_workflow_keeps_ownership_checks_and_explicit_denials(self):
        from apps.procurement.models import PurchaseRequisition
        module = self.module('procurement_requisitions')
        RoleModule.objects.filter(role=self.role, module=module).delete()
        pr = PurchaseRequisition.objects.create(pr_number='PR-ACTION-TEST', issued_by=self.users[0], requested_by=self.users[0])
        url = f'/api/v1/procurement/requisitions/{pr.pk}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login(1)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login()
        self.override(module, 'read')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertTrue(PurchaseRequisition.objects.filter(pk=pr.pk).exists())

    def test_purchase_order_workflow_cannot_expose_another_users_record(self):
        from apps.procurement.models import PurchaseOrder, Vendor
        module = self.module('procurement_orders')
        RoleModule.objects.filter(role=self.role, module=module).delete()
        vendor = Vendor.objects.create(vendor_code='V-ACTION-TEST', name='Test vendor')
        order = PurchaseOrder.objects.create(po_number='PO-ACTION-TEST', vendor=vendor, created_by=self.users[0], total_amount=0)
        url = f'/api/v1/procurement/orders/{order.pk}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login(1)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login()
        self.override(module, 'read')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_instrument_hub_rollout_preserves_view_and_user_denials(self):
        module = self.module('instrument_datasheet')
        self.override(module, 'read')
        import_module('apps.rbac.migrations.0055_explicit_legacy_module_actions').backfill_legacy_actions(apps, SimpleNamespace(connection=connection))
        migration = import_module('apps.rbac.migrations.0056_instrument_hub_view')
        migration.backfill_hub_view(apps, SimpleNamespace(connection=connection))
        self.assertFalse(module_action_allowed(self.users[0], module.code, 'read'))
        self.assertTrue(module_action_allowed(self.users[1], module.code, 'read'))
        count = RolePermission.objects.count()
        migration.backfill_hub_view(apps, SimpleNamespace(connection=connection))
        self.assertEqual(RolePermission.objects.count(), count)

    def test_migration_preserves_policies_overrides_and_is_idempotent(self):
        implicit = self.module('finance_incoming')
        explicit = self.module('pid_analysis', ['read'])
        self.override(implicit, 'delete')
        migration = import_module('apps.rbac.migrations.0055_explicit_legacy_module_actions')
        migration.backfill_legacy_actions(apps, SimpleNamespace(connection=connection))
        self.assertEqual(RolePermission.objects.filter(role=self.role, permission__module=implicit).count(), 6)
        self.assertEqual(RolePermission.objects.filter(role=self.role, permission__module=explicit).count(), 1)
        self.assertFalse(module_action_allowed(self.users[0], implicit.code, 'delete'))
        self.assertTrue(module_action_allowed(self.users[1], implicit.code, 'delete'))
        count = RolePermission.objects.count()
        migration.backfill_legacy_actions(apps, SimpleNamespace(connection=connection))
        self.assertEqual(RolePermission.objects.count(), count)
        self.assertEqual(AuditLog.objects.filter(metadata__audit_source='0055_explicit_legacy_module_actions').count(), 1)
        reviewed = self.module('pfd_quality')
        AuditLog.objects.create(user_email='', action='update', resource_type='Role', resource_id=self.role.pk,
                                metadata={'audit_source': 'role_access_review'})
        migration.backfill_legacy_actions(apps, SimpleNamespace(connection=connection))
        self.assertFalse(RolePermission.objects.filter(role=self.role, permission__module=reviewed).exists())
