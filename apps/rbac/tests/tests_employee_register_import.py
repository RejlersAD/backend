import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook

from apps.rbac.employee_import import UPDATE_FIELDS, import_register
from apps.rbac.models import Organization, Role, UserProfile, UserRole

User = get_user_model()


class EmployeeRegisterImportTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(username='import.admin', email='admin@example.test', password='test')
        self.org = Organization.objects.create(name='Import organization', code='IMP')
        self.role = Role.objects.create(name='Default', code='default')
        self.user = User.objects.create_user(username='existing', email='existing@example.test', first_name='Old', password='keep')
        self.profile = UserProfile.objects.create(user=self.user, organization=self.org, employee_id='100', department='Old')
        UserRole.objects.get_or_create(user_profile=self.profile, role=self.role, defaults={'assigned_by': self.actor})

    def file(self, rows):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['First Name', 'Last Name', 'Email', 'Username', 'Employee ID', 'Department', 'Roles', 'Organization'])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return SimpleUploadedFile('register.csv', output.getvalue().encode('utf-8-sig'))

    def run_import(self, rows, preview=False, **kwargs):
        return import_register(self.file(rows), actor=self.actor, preview=preview, mode='upsert',
            update_fields=list(UPDATE_FIELDS), organization_id=self.org.pk, role_id=self.role.pk, **kwargs)

    def test_blanks_are_ignored_with_original_row_numbers(self):
        result = self.run_import([{}, {'Email': 'new@example.test', 'First Name': 'New'}], preview=True)
        self.assertEqual(result['summary']['ignored_blank'], 1)
        self.assertEqual(result['summary']['total'], 1)
        self.assertEqual(result['rows'][0]['row'], 3)

    def test_existing_employee_updates_without_recreation_or_access_changes(self):
        password = self.user.password
        result = self.run_import([{'Email': 'EXISTING@example.test', 'Employee ID': '100', 'First Name': 'Updated', 'Department': 'Process', 'Roles': 'Unknown', 'Organization': 'Unconfigured'}])
        self.assertEqual(result['summary']['updated'], 1)
        self.assertEqual(result['summary']['created'], 0)
        self.user.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(self.user.password, password)
        self.assertEqual(self.profile.department, 'Process')
        self.assertEqual(self.profile.organization_id, self.org.pk)
        self.assertEqual(self.profile.userrole_set.get().role_id, self.role.pk)
        self.assertEqual(self.profile.canonical_employee.department, 'Process')

    def test_missing_email_can_update_by_employee_id_but_cannot_create(self):
        result = self.run_import([{'Employee ID': '100', 'Department': 'Mechanical'}, {'Employee ID': '200', 'First Name': 'Missing'}])
        self.assertEqual(result['summary']['updated'], 1)
        self.assertEqual(result['summary']['failed'], 1)
        self.assertIn('Email is missing', result['details']['failed'][0]['error'])
        self.user.refresh_from_db(); self.assertEqual(self.user.email, 'existing@example.test')

    def test_create_uses_explicit_mapping_and_no_welcome_mail(self):
        with patch('apps.rbac.tasks.send_bulk_welcome_email.delay') as send:
            result = self.run_import([{'Email': 'new@example.test', 'First Name': 'New', 'Employee ID': '200', 'Roles': 'Employee', 'Organization': 'Unconfigured'}])
        self.assertEqual(result['summary']['created'], 1)
        self.assertEqual(result['summary']['emails_sent'], 0)
        send.assert_not_called()
        profile = UserProfile.objects.get(user__email='new@example.test')
        self.assertEqual(profile.organization_id, self.org.pk)
        self.assertEqual(profile.userrole_set.get().role_id, self.role.pk)
        self.assertIsNotNone(profile.canonical_employee_id)

    def test_repeat_import_is_idempotent(self):
        rows = [{'Email': 'new@example.test', 'First Name': 'New', 'Employee ID': '200'}]
        self.run_import(rows)
        result = self.run_import(rows)
        self.assertEqual(result['summary']['created'], 0)
        self.assertEqual(result['summary']['unchanged'], 1)
        self.assertEqual(User.objects.filter(email='new@example.test').count(), 1)

    def test_conflicting_identities_are_rejected(self):
        other = User.objects.create_user(username='other', email='other@example.test')
        UserProfile.objects.create(user=other, organization=self.org, employee_id='200')
        result = self.run_import([{'Email': 'existing@example.test', 'Employee ID': '200', 'Department': 'Wrong'}])
        self.assertEqual(result['summary']['failed'], 1)
        self.assertIn('Identity conflict', result['details']['failed'][0]['error'])
        self.profile.refresh_from_db(); self.assertEqual(self.profile.department, 'Old')

    def test_strong_identity_disambiguates_shared_legacy_identifier(self):
        other = User.objects.create_user(username='EXISTING', email='other@example.test')
        UserProfile.objects.create(user=other, organization=self.org, employee_id='100')
        result = self.run_import([{'Email': 'existing@example.test', 'Username': 'existing', 'Employee ID': '100', 'Department': 'Correct'}])
        self.assertEqual(result['summary']['updated'], 1)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.department, 'Correct')

    def test_ambiguous_identity_without_disambiguating_email_is_rejected(self):
        other = User.objects.create_user(username='other', email='other@example.test')
        UserProfile.objects.create(user=other, organization=self.org, employee_id='100')
        result = self.run_import([{'Employee ID': '100', 'Department': 'Wrong'}])
        self.assertEqual(result['summary']['failed'], 1)
        self.assertIn('Ambiguous identity', result['details']['failed'][0]['error'])

    def test_duplicate_file_identities_do_not_create_duplicate_accounts(self):
        result = self.run_import([{'Email': 'new@example.test'}, {'Email': 'NEW@example.test'}])
        self.assertEqual(result['summary']['created'], 1)
        self.assertEqual(result['summary']['failed'], 1)
        self.assertEqual(User.objects.filter(email__iexact='new@example.test').count(), 1)

    def test_orphan_accounts_are_not_duplicated(self):
        User.objects.create_user(username='orphan', email='orphan@example.test')
        result = self.run_import([{'Email': 'orphan@example.test'}])
        self.assertEqual(result['summary']['created'], 0)
        self.assertEqual(result['summary']['failed'], 1)

    def test_failed_row_rolls_back_user_profile_and_master_changes(self):
        with patch('apps.rbac.employee_import.EmployeeService.sync_from_rbac_profile', side_effect=ValueError('Cannot sync')):
            result = self.run_import([{'Email': 'existing@example.test', 'First Name': 'Wrong', 'Department': 'Wrong'}])
        self.assertEqual(result['summary']['failed'], 1)
        self.user.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Old')
        self.assertEqual(self.profile.department, 'Old')

    def test_preview_is_read_only_and_reuses_validation(self):
        rows = [{'Email': 'existing@example.test', 'Department': 'Updated'}, {'Employee ID': '300'}]
        preview = self.run_import(rows, preview=True)
        self.assertEqual(preview['summary']['existing'], 1)
        self.assertEqual(preview['summary']['invalid'], 1)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.department, 'Old')
        applied = self.run_import(rows)
        self.assertEqual(applied['summary']['failed'], preview['summary']['invalid'])

    def test_earlier_updates_cannot_reclassify_preview_conflicts(self):
        other = User.objects.create_user(username='other', email='other@example.test')
        UserProfile.objects.create(user=other, organization=self.org, employee_id='200')
        rows = [{'Email': 'existing@example.test', 'Employee ID': '300'},
                {'Email': 'unmapped@example.test', 'Username': 'other', 'Employee ID': '100'}]
        preview = self.run_import(rows, preview=True)
        applied = self.run_import(rows)
        self.assertEqual(preview['summary']['invalid'], 1)
        self.assertEqual(applied['summary']['failed'], 1)
        self.assertIn('Identity conflict', applied['details']['failed'][0]['error'])

    def test_alternate_identifiers_cannot_update_one_employee_twice(self):
        result = self.run_import([{'Email': 'existing@example.test', 'Department': 'First'},
                                  {'Employee ID': '100', 'Department': 'Second'}])
        self.assertEqual(result['summary']['updated'], 1)
        self.assertEqual(result['summary']['failed'], 1)
        self.profile.refresh_from_db(); self.assertEqual(self.profile.department, 'First')

    def test_bad_excel_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'readable Excel workbook'):
            import_register(SimpleUploadedFile('bad.xlsx', b'not a zip workbook'), actor=self.actor,
                preview=True, mode='update_existing', update_fields=[])

    def test_xlsx_uses_same_parser_and_preserves_blank_rows(self):
        workbook = Workbook(); sheet = workbook.active
        sheet.append(['Email', 'Employee ID', 'Department']); sheet.append([None, None, None])
        sheet.append(['existing@example.test', 100, 'Mechanical'])
        data = io.BytesIO(); workbook.save(data)
        result = import_register(SimpleUploadedFile('register.xlsx', data.getvalue()), actor=self.actor, preview=True,
            mode='update_existing', update_fields=list(UPDATE_FIELDS))
        self.assertEqual(result['summary']['existing'], 1)
        self.assertEqual(result['summary']['ignored_blank'], 1)

    def test_missing_mappings_and_unsupported_fields_rejected(self):
        with self.assertRaisesRegex(ValueError, 'organization and active role'):
            import_register(self.file([{'Email': 'new@example.test'}]), actor=self.actor, preview=False,
                mode='upsert', update_fields=[])
        with self.assertRaisesRegex(ValueError, 'supported employee fields'):
            import_register(self.file([{'Email': 'new@example.test'}]), actor=self.actor, preview=False,
                mode='update_existing', update_fields=['role_codes'])

    def test_existing_login_bookkeeping_is_unchanged(self):
        from django.utils import timezone
        when = timezone.now()
        User.objects.filter(pk=self.user.pk).update(last_login=when)
        UserProfile.objects.filter(pk=self.profile.pk).update(failed_login_attempts=3, last_login_at=when)
        self.run_import([{'Email': 'existing@example.test', 'First Name': 'Updated'}])
        self.profile.refresh_from_db(); self.user.refresh_from_db()
        self.assertEqual(self.profile.failed_login_attempts, 3)
        self.assertEqual(self.user.last_login, when)
        self.assertEqual(self.profile.last_login_at, when)

    def test_canonical_compatibility_mirror_cannot_erase_reviewed_or_preserved_values(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(phone='+971500000000')
        def mirror_old_master(profile, changed_fields):
            UserProfile.objects.filter(pk=profile.pk).update(employee_id='LEGACY-100', phone='')
            User.objects.filter(pk=profile.user_id).update(email='stale@example.test')
        with patch('apps.rbac.employee_import.EmployeeService.sync_from_rbac_profile', side_effect=mirror_old_master):
            result = self.run_import([{'Email': 'existing@example.test', 'Employee ID': '100', 'Department': 'Correct'}])
        self.assertEqual(result['summary']['updated'], 1)
        self.profile.refresh_from_db(); self.user.refresh_from_db()
        self.assertEqual(self.profile.employee_id, '100')
        self.assertEqual(self.profile.phone, '+971500000000')
        self.assertEqual(self.profile.department, 'Correct')
        self.assertEqual(self.user.email, 'existing@example.test')

    def test_api_rejects_non_admin_before_import(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.rbac.views import UserProfileViewSet
        request = APIRequestFactory().post('/api/v1/rbac/users/bulk_upload/', {'import_mode': 'upsert'}, format='multipart')
        force_authenticate(request, user=self.user)
        with patch('apps.rbac.employee_import.import_register') as importer:
            response = UserProfileViewSet.as_view({'post': 'bulk_upload'})(request)
        self.assertEqual(response.status_code, 403)
        importer.assert_not_called()
