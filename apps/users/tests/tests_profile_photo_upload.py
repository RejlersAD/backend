from datetime import date
from io import BytesIO
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from PIL import Image

from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService
from apps.rbac.models import Organization, UserProfile


User = get_user_model()
TEST_MIDDLEWARE = [
    middleware for middleware in settings.MIDDLEWARE
    if middleware not in {
        'apps.activity.tracker.ActivityMiddleware',
        'apps.usage_tracking.middleware.UsageTrackingMiddleware',
        'apps.core.middleware.ApiUsageLoggingMiddleware',
        'apps.rbac.middleware.RBACMiddleware',
    }
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE, ROOT_URLCONF='config.urls_permissions_test')
class ProfilePhotoUploadAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='photo.employee',
            email='photo.employee@example.com',
        )
        self.employee = EmployeeMaster.objects.create(
            user=self.user,
            employee_number='EMP-PHOTO',
            employee_code='PHOTO',
            emp_code='PHOTO',
            email=self.user.email,
            first_name='Photo',
            last_name='Employee',
            join_date=date(2026, 1, 1),
        )
        self.client.force_authenticate(self.user)

    @override_settings(USE_S3=False)
    def test_managed_photo_uses_canonical_storage_and_infers_missing_mime(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=['is_superuser', 'is_staff'])
        organization = Organization.objects.create(name='Photo Read', code='PHOTO-READ')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        profile.canonical_employee = self.employee
        profile.save(update_fields=['canonical_employee'])
        self.employee.photo_file_path = 'employee_photos/example.png'
        self.employee.photo_mime_type = ''
        self.employee.save(update_fields=['photo_file_path', 'photo_mime_type'])
        with patch('apps.users.profile_photos.default_storage.open', side_effect=lambda *args: BytesIO(b'photo-bytes')):
            for path in ['/api/v1/users/employees/my-profile-photo/', f'/api/v1/rbac/users/{profile.pk}/profile-photo/']:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'image/png')
                self.assertEqual(b''.join(response.streaming_content), b'photo-bytes')
                self.assertIn('private', response['Cache-Control'])

    def test_managed_photo_rejects_users_without_user_management_access(self):
        organization = Organization.objects.create(name='Photo Scope', code='PHOTO-SCOPE')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        response = self.client.get(f'/api/v1/rbac/users/{profile.pk}/profile-photo/')
        self.assertEqual(response.status_code, 403)

    @staticmethod
    def image_file(content_type='image/png'):
        # A valid 1x1 transparent PNG.
        return SimpleUploadedFile(
            'profile.png',
            (
                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
                b'\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00'
                b'\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND'
                b'\xaeB`\x82'
            ),
            content_type=content_type,
        )

    def test_self_service_requires_authentication(self):
        self.client.force_authenticate(user=None)
        for path, method in [('my-profile-photo', 'post'), ('my-employee-profile', 'patch'), ('my-signature', 'post')]:
            with self.subTest(path=path):
                response = getattr(self.client, method)(f'/api/v1/users/employees/{path}/', {})
                self.assertIn(response.status_code, (401, 403))

    def test_employee_directory_still_requires_hr_permission(self):
        response = self.client.get('/api/v1/users/employees/active_employees/')
        self.assertEqual(response.status_code, 403)

    def test_profile_update_without_hr_grants_is_scoped_to_current_user(self):
        other = User.objects.create_user(username='other.employee', email='other@example.com')
        response = self.client.patch(
            '/api/v1/users/employees/my-employee-profile/',
            {'phone_number': '+971501234567', 'user_id': str(other.pk)},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.phone_number, '+971501234567')
        self.assertEqual(self.employee.user_id, self.user.pk)

    def test_signature_read_without_hr_grants_reaches_owner_handler(self):
        response = self.client.get('/api/v1/users/employees/my-signature/')
        self.assertIn(response.status_code, (200, 404))

    @patch('apps.users.views.EmployeeService.upload_employee_photo')
    def test_upload_uses_existing_canonical_employee(self, upload_photo):
        upload_photo.return_value = '/media/employee_photos/photo.png'
        organization = Organization.objects.create(name='Photo Test', code='PHOTO-TEST')
        profile, _ = UserProfile.objects.get_or_create(
            user=self.user,
            defaults={'organization': organization},
        )
        profile.canonical_employee = None
        profile.save(update_fields=['canonical_employee'])

        response = self.client.post(
            '/api/v1/users/employees/my-profile-photo/',
            {'photo': self.image_file()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['success'])
        self.assertIn('/media/employee_photos/photo.png', response.data['photo_url'])
        upload_photo.assert_called_once()
        self.assertEqual(upload_photo.call_args.kwargs['employee'], self.employee)
        self.assertEqual(upload_photo.call_args.kwargs['uploaded_by'], self.user)
        profile.refresh_from_db()
        self.assertEqual(profile.canonical_employee, self.employee)

    def test_upload_does_not_create_a_missing_employee_record(self):
        self.employee.delete()

        response = self.client.post(
            '/api/v1/users/employees/my-profile-photo/',
            {'photo': self.image_file()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(EmployeeMaster.objects.filter(user=self.user).exists())

    def test_upload_rejects_unsupported_file_type(self):
        file = SimpleUploadedFile('profile.gif', b'GIF89a', content_type='image/gif')

        response = self.client.post(
            '/api/v1/users/employees/my-profile-photo/',
            {'photo': file},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('Invalid file type', response.data['error'])

    def test_upload_rejects_content_that_is_not_an_image(self):
        file = SimpleUploadedFile(
            'profile.jpg',
            b'this is not an image',
            content_type='image/jpeg',
        )

        response = self.client.post(
            '/api/v1/users/employees/my-profile-photo/',
            {'photo': file},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('not a valid image', response.data['error'])

    def test_service_saves_a_verified_image_to_local_storage(self):
        image_bytes = BytesIO()
        Image.new('RGB', (32, 32), color='navy').save(image_bytes, format='PNG')
        file = SimpleUploadedFile(
            'profile.png',
            image_bytes.getvalue(),
            content_type='image/png',
        )

        with tempfile.TemporaryDirectory() as media_root, self.settings(
            USE_S3=False,
            MEDIA_ROOT=media_root,
        ):
            photo_url = EmployeeService.upload_employee_photo(
                employee=self.employee,
                photo_file=file,
                uploaded_by=self.user,
            )

            self.employee.refresh_from_db()
            self.user.refresh_from_db()
            self.assertTrue(photo_url.startswith('/media/employee_photos/'))
            self.assertEqual(self.employee.photo_url, photo_url)
            self.assertEqual(self.employee.photo_mime_type, 'image/png')
            self.assertEqual(self.user.avatar.name, self.employee.photo_file_path)

            response = self.client.get('/api/v1/users/employees/my-profile-photo/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertGreater(len(b''.join(response.streaming_content)), 0)
