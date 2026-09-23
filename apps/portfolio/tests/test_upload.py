from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.portfolio.access import can_upload_workbook
from apps.portfolio.importer import import_workbook
from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from apps.portfolio.views import PREVIEW_MAX_AGE, PREVIEW_SALT
from apps.rbac.models import (
    AuditLog, Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from .test_workbook import workbook_bytes


class PortfolioUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('portfolio-uploader', email='uploader@example.test', is_staff=True)
        organization, _ = Organization.objects.get_or_create(code='portfolio-upload', defaults={'name': 'Portfolio upload'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='portfolio-upload-test', name='Portfolio upload', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = '/api/v1/dashboard/executive/portfolio-workbook/'
        self.content = workbook_bytes()

    def grant(self, module_code, *actions):
        module, _ = Module.objects.get_or_create(code=module_code, defaults={'name': module_code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=actions, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def grant_upload(self):
        self.grant('executive_dashboard', 'read')
        self.grant('project_control', 'read', 'update')

    def post(self, action, *, content=None, name='portfolio.xlsx', token=None):
        payload = {'file': SimpleUploadedFile(name, self.content if content is None else content,
                                             content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        if token is not None:
            payload['preview_token'] = token
        return self.client.post(self.base + action + '/', payload, format='multipart')

    def preview(self, **kwargs):
        response = self.post('preview', **kwargs)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['preview_token']

    def test_preview_requires_all_grants_and_does_not_publish(self):
        self.assertEqual(self.post('preview').status_code, 403)
        self.grant('executive_dashboard', 'read')
        self.grant('project_control', 'read')
        self.assertFalse(can_upload_workbook(self.user))
        self.assertEqual(self.post('preview').status_code, 403)
        self.grant('project_control', 'update')
        self.assertTrue(can_upload_workbook(self.user))
        response = self.post('preview')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['row_count'], 1)
        self.assertEqual(response.data['reporting_date'], '2026-09-18')
        self.assertEqual(response.data['expires_in_seconds'], PREVIEW_MAX_AGE)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(PortfolioSource.objects.exists())
        self.assertFalse(PortfolioSnapshot.objects.exists())
        self.assertFalse(PortfolioRow.objects.exists())

    def test_confirm_publishes_and_records_uploader_then_repeat_reuses_snapshot(self):
        self.grant_upload()
        token = self.preview()
        response = self.post('import', token=token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['created'])
        self.assertTrue(response.data['activated'])
        snapshot = PortfolioSnapshot.objects.get()
        self.assertEqual(PortfolioSource.objects.get().active_snapshot_id, snapshot.pk)
        audit = AuditLog.objects.get(resource_type='PortfolioSnapshot')
        self.assertEqual(audit.user_id, self.user.pk)
        self.assertEqual(audit.metadata['snapshot_id'], snapshot.pk)
        self.assertEqual(audit.metadata['sha256'], snapshot.sha256)
        self.assertNotIn('preview_token', audit.metadata)
        repeated = self.post('import', token=self.preview())
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.data['created'])
        self.assertFalse(repeated.data['activated'])
        self.assertEqual(PortfolioSnapshot.objects.count(), 1)

    def test_ordinary_project_editor_cannot_replace_global_source(self):
        self.grant_upload()
        self.user.is_staff = False
        self.user.save(update_fields=['is_staff'])
        self.assertFalse(can_upload_workbook(self.user))
        self.assertEqual(self.post('preview').status_code, 403)
        self.assertEqual(self.post('import', token='unused').status_code, 403)
        self.assertFalse(PortfolioSource.objects.exists())

    def promote_with_role_only(self):
        self.grant_upload()
        self.user.is_staff = False
        self.user.is_superuser = False
        self.user.save(update_fields=['is_staff', 'is_superuser'])
        role, _ = Role.objects.get_or_create(
            code='super_admin', defaults={'name': 'Super Administrator', 'level': 1},
        )
        UserRole.objects.create(user_profile=self.profile, role=role)
        return role

    def test_promoted_super_administrator_can_upload_without_django_admin_flags(self):
        self.promote_with_role_only()
        self.assertTrue(self.profile.is_super_admin())
        self.assertTrue(can_upload_workbook(self.user))
        report = self.client.get(self.base)
        self.assertEqual(report.status_code, 200, report.data)
        self.assertTrue(report.data['can_upload'])
        self.assertIsNone(report.data['source'])
        token = self.preview()
        response = self.post('import', token=token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['activated'])
        revenue = self.client.get(self.base + 'revenue/')
        self.assertEqual(revenue.status_code, 200, revenue.data)
        self.assertTrue(revenue.data['scope']['full_source'])
        self.assertEqual(revenue.data['scope']['row_count'], 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_role_only_super_administrator_still_honors_each_explicit_deny(self):
        self.promote_with_role_only()
        token = self.preview()
        for module, action in (
            ('executive_dashboard', 'read'), ('project_control', 'read'), ('project_control', 'update'),
        ):
            with self.subTest(module=module, action=action):
                permission = Permission.objects.get(module__code=module, action=action, is_active=True)
                override = UserPermissionOverride.objects.create(
                    user_profile=self.profile, permission=permission, allowed=False,
                )
                self.assertFalse(can_upload_workbook(self.user))
                self.assertEqual(self.post('preview').status_code, 403)
                self.assertEqual(self.post('import', token=token).status_code, 403)
                override.delete()
        self.assertFalse(PortfolioSource.objects.exists())

    def test_inactive_or_revoked_super_administrator_role_cannot_upload(self):
        role = self.promote_with_role_only()
        token = self.preview()
        role.is_active = False
        role.save(update_fields=['is_active'])
        self.assertFalse(can_upload_workbook(self.user))
        self.assertFalse(self.client.get(self.base).data['can_upload'])
        self.assertEqual(self.post('import', token=token).status_code, 403)
        role.is_active = True
        role.save(update_fields=['is_active'])
        UserRole.objects.filter(user_profile=self.profile, role=role).delete()
        self.assertFalse(can_upload_workbook(self.user))
        self.assertEqual(self.post('preview').status_code, 403)
        self.assertEqual(self.post('import', token=token).status_code, 403)
        self.assertFalse(PortfolioSource.objects.exists())

    def test_changed_file_and_missing_or_tampered_token_cannot_publish(self):
        self.grant_upload()
        token = self.preview()
        other = workbook_bytes(rows=[{'Modified Contract Value (AED)': 999}])
        response = self.post('import', content=other, token=token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'file_changed')
        self.assertEqual(self.post('import').status_code, 400)
        self.assertEqual(self.post('import', token=token + 'tamper').status_code, 400)
        self.assertFalse(PortfolioSource.objects.exists())

    def test_expired_preview_requires_a_new_preview(self):
        self.grant_upload()
        token = self.preview()
        claims = signing.loads(token, salt=PREVIEW_SALT)
        with patch('django.core.signing.time.time', return_value=1):
            expired = signing.dumps(claims, salt=PREVIEW_SALT)
        response = self.post('import', token=expired)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'preview_expired')
        self.assertFalse(PortfolioSnapshot.objects.exists())

    def test_preview_is_bound_to_the_authenticated_user(self):
        self.grant_upload()
        token = self.preview()
        other = get_user_model().objects.create_user('other-uploader', email='other@example.test', is_staff=True)
        profile, _ = UserProfile.objects.get_or_create(user=other, defaults={'organization': self.profile.organization})
        profile.roles.clear()
        UserRole.objects.create(user_profile=profile, role=self.role)
        self.client.force_authenticate(other)
        response = self.post('import', token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'invalid_preview_user')
        self.assertFalse(PortfolioSnapshot.objects.exists())

    def test_source_change_after_preview_is_rejected_under_publication_lock(self):
        self.grant_upload()
        token = self.preview()
        other = import_workbook(workbook_bytes(rows=[{'Modified Contract Value (AED)': 999}]))
        response = self.post('import', token=token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'source_changed')
        self.assertEqual(PortfolioSource.objects.get().active_snapshot_id, other['snapshot_id'])
        self.assertEqual(PortfolioSnapshot.objects.count(), 1)

    def test_revoked_update_grant_blocks_confirm_even_for_superuser(self):
        self.grant_upload()
        token = self.preview()
        permission = Permission.objects.filter(module__code='project_control', action='update', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.assertFalse(can_upload_workbook(self.user))
        self.assertEqual(self.post('import', token=token).status_code, 403)
        self.assertFalse(PortfolioSnapshot.objects.exists())

    def test_inactive_account_and_anonymous_requests_are_denied(self):
        self.grant_upload()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertFalse(can_upload_workbook(self.user))
        self.assertEqual(self.post('preview').status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.post('preview').status_code, (401, 403))

    def test_invalid_file_type_size_or_contents_are_human_validation_errors(self):
        self.grant_upload()
        self.assertEqual(self.post('preview', name='old.xls').status_code, 400)
        self.assertEqual(self.post('preview', content=b'not an excel file').status_code, 400)
        with patch('apps.portfolio.views.MAX_BYTES', 10):
            self.assertEqual(self.post('preview').status_code, 400)
        broken = workbook_bytes(rows=[{}, {'PROJECT ID': None, 'PROJECT SUB ID': None}])
        response = self.post('preview', content=broken)
        self.assertEqual(response.status_code, 400)
        self.assertIn('both project and subproject', str(response.data))
        self.assertFalse(PortfolioSnapshot.objects.exists())

    def test_request_accepts_exactly_one_file(self):
        self.grant_upload()
        response = self.client.post(self.base + 'preview/', {
            'file': SimpleUploadedFile('one.xlsx', self.content),
            'another': SimpleUploadedFile('two.xlsx', self.content),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_audit_failure_rolls_back_publication(self):
        self.grant_upload()
        token = self.preview()
        with patch('apps.rbac.utils.create_audit_log', side_effect=DatabaseError('audit unavailable')):
            response = self.post('import', token=token)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PortfolioSource.objects.exists())
        self.assertFalse(PortfolioSnapshot.objects.exists())

    def test_older_workbook_preserves_last_good_snapshot(self):
        self.grant_upload()
        current = import_workbook(self.content)
        old = workbook_bytes(reporting_date=date(2026, 8, 31))
        response = self.post('import', content=old, token=self.preview(content=old))
        self.assertEqual(response.status_code, 400)
        self.assertIn('older', str(response.data))
        self.assertEqual(PortfolioSource.objects.get().active_snapshot_id, current['snapshot_id'])
