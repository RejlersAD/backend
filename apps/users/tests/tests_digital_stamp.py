"""The stamp owner manages durable, lossless artwork through guarded routes."""

import base64
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from apps.rbac.models import Organization, UserProfile
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.digital_stamps import MAX_STAMP_BYTES


urlpatterns = [path('api/v1/users/', include('apps.users.urls'))]
secure_module_endpoints(urlpatterns)
URL = '/api/v1/users/employees/my-digital-stamp/'


def upload_image(image=None, *, file_format='PNG', **save_options):
    image = image if image is not None else Image.new('RGBA', (1200, 1200), (15, 55, 190, 155))
    content = BytesIO()
    image.save(content, format=file_format, **save_options)
    return SimpleUploadedFile(f'stamp.{file_format.lower()}', content.getvalue(), content_type=f'image/{file_format.lower()}')


def decoded(data_url):
    return Image.open(BytesIO(base64.b64decode(data_url.split(',', 1)[1])))


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[])
class DigitalStampAPITests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(code='STAMP-OWNER', name='Stamp owner tests')
        self.owner = get_user_model().objects.create_user(
            username='stamp-owner', email='jarmo.suominen@rejlers.ae', first_name='Jarmo', last_name='Suominen',
        )
        self.profile = UserProfile.objects.create(user=self.owner, organization=self.organization)
        self.other = get_user_model().objects.create_user(username='stamp-other', email='other@example.test')
        self.other_profile = UserProfile.objects.create(user=self.other, organization=self.organization)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def post(self, image=None, **extra):
        return self.client.post(URL, {'stamp': image if image is not None else upload_image(), **extra}, format='multipart')

    def test_actual_guarded_owner_route_needs_no_hr_or_procurement_module_grants(self):
        self.assertFalse(self.profile.roles.exists())
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {'can_manage': True, 'has_stamp': False, 'stamp': None, 'updated_at': None})
        self.assertIn('no-store', response['Cache-Control'])
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['can_manage'])
        self.assertTrue(response.data['has_stamp'])

    def test_png_resolution_ink_color_and_alpha_are_preserved_exactly(self):
        original = Image.new('RGBA', (1600, 1400), (0, 0, 0, 0))
        ImageDraw.Draw(original).ellipse((100, 100, 1450, 1300), fill=(22, 80, 210, 175))
        self.profile.signature_image = 'untouched-signature'
        self.profile.save(update_fields=['signature_image'])
        response = self.post(upload_image(original))
        self.assertEqual(response.status_code, 200, response.data)
        with decoded(response.data['stamp']) as saved:
            self.assertEqual(saved.size, original.size)
            self.assertEqual(saved.mode, 'RGBA')
            self.assertEqual(saved.tobytes(), original.tobytes())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, response.data['stamp'])
        self.assertIsNotNone(self.profile.stamp_updated_at)
        self.assertEqual(self.profile.signature_image, 'untouched-signature')
        self.assertEqual(self.client.get(URL).data['stamp'], self.profile.stamp_image)

    def test_jpeg_is_stored_as_lossless_png_without_recoloring_or_small_resizing(self):
        upload = upload_image(Image.new('RGB', (1300, 1250), (18, 75, 195)), file_format='JPEG', quality=95)
        with Image.open(BytesIO(upload.read())) as jpeg:
            expected = jpeg.convert('RGBA').tobytes()
        upload.seek(0)
        response = self.post(upload)
        self.assertEqual(response.status_code, 200, response.data)
        with decoded(response.data['stamp']) as saved:
            self.assertEqual(saved.size, (1300, 1250))
            self.assertEqual(saved.tobytes(), expected)

    def test_large_edge_is_bounded_without_upscaling_normal_images(self):
        response = self.post(upload_image(Image.new('RGBA', (6000, 120), (10, 50, 150, 255))))
        self.assertEqual(response.status_code, 200, response.data)
        with decoded(response.data['stamp']) as saved:
            self.assertEqual(saved.width, 4096)
            self.assertAlmostEqual(saved.width / saved.height, 50, delta=1)

    def test_oversized_dimensions_are_rejected_before_image_decode(self):
        header = MagicMock()
        header.__enter__.return_value = header
        header.format, header.width, header.height = 'PNG', 5001, 5001
        with patch('apps.users.digital_stamps.Image.open', return_value=header):
            response = self.post(SimpleUploadedFile('stamp.png', b'header', content_type='image/png'))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('dimensions', response.data['error'])
        header.verify.assert_not_called()
        header.load.assert_not_called()

    def test_missing_oversized_invalid_spoofed_and_transparent_images_are_rejected(self):
        gif = BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(gif, format='GIF')
        uploads = [
            SimpleUploadedFile('too-large.png', b'x' * (MAX_STAMP_BYTES + 1), content_type='image/png'),
            SimpleUploadedFile('not-an-image.png', b'invalid', content_type='image/png'),
            SimpleUploadedFile('spoof.png', gif.getvalue(), content_type='image/png'),
            upload_image(Image.new('RGBA', (30, 30), (0, 0, 0, 0))),
        ]
        self.assertEqual(self.client.post(URL, {}, format='multipart').status_code, 400)
        for upload in uploads:
            with self.subTest(upload=upload.name):
                response = self.post(upload)
                self.assertEqual(response.status_code, 400, response.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, '')

    def test_normalized_png_size_limit_prevents_large_database_payload(self):
        image = Image.new('RGB', (128, 128))
        image.putdata([(x * 17 % 256, y * 31 % 256, x * y % 256) for y in range(128) for x in range(128)])
        upload = upload_image(image, file_format='JPEG', quality=5)
        self.assertLess(upload.size, 2500)
        with patch('apps.users.digital_stamps.MAX_STAMP_BYTES', 2500):
            response = self.post(upload)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('processed', response.data['error'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, '')

    def test_authentication_is_required_for_every_operation(self):
        self.client.force_authenticate(user=None)
        for method in ('get', 'post', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(URL)
                self.assertIn(response.status_code, (401, 403))

    def test_other_users_and_admins_get_no_private_stamp_and_cannot_mutate_owner(self):
        self.profile.stamp_image = 'private-owner-stamp'
        self.profile.save(update_fields=['stamp_image'])
        for admin in (False, True):
            with self.subTest(admin=admin):
                self.other.is_superuser = admin
                self.other.save(update_fields=['is_superuser'])
                self.client.force_authenticate(self.other)
                response = self.client.get(URL)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertFalse(response.data['can_manage'])
                self.assertIsNone(response.data['stamp'])
                self.assertEqual(self.post(user_id=str(self.owner.pk)).status_code, 403)
                self.assertEqual(self.client.delete(URL).status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, 'private-owner-stamp')

    def test_owner_cannot_use_payload_to_target_another_user(self):
        response = self.post(user_id=str(self.other.pk))
        self.assertEqual(response.status_code, 400, response.data)
        self.profile.refresh_from_db()
        self.other_profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, '')
        self.assertEqual(self.other_profile.stamp_image, '')

    def test_inactive_deleted_and_ambiguous_owner_accounts_cannot_upload(self):
        for changes in ({'status': 'inactive'}, {'status': 'suspended'}, {'is_deleted': True}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(pk=self.profile.pk).update(**{'status': 'active', 'is_deleted': False, **changes})
                self.assertFalse(self.client.get(URL).data['can_manage'])
                self.assertEqual(self.post().status_code, 403)
        UserProfile.objects.filter(pk=self.profile.pk).update(status='active', is_deleted=False)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(self.post().status_code, 403)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=True)
        get_user_model().objects.filter(pk=self.other.pk).update(email='JARMO.SUOMINEN@REJLERS.AE')
        self.assertEqual(self.post().status_code, 403)

    def test_delete_clears_only_the_owners_stamp_and_returns_fallback_state(self):
        self.assertEqual(self.post().status_code, 200)
        self.profile.refresh_from_db()
        self.profile.signature_image = 'unchanged-signature'
        self.profile.save(update_fields=['signature_image'])
        response = self.client.delete(URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {
            'success': True, 'can_manage': True, 'has_stamp': False, 'stamp': None, 'updated_at': None,
        })
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, '')
        self.assertIsNone(self.profile.stamp_updated_at)
        self.assertEqual(self.profile.signature_image, 'unchanged-signature')

    def test_owner_eligibility_is_rechecked_after_image_processing(self):
        from apps.users.digital_stamps import stamp_image_data_url

        def suspend_during_decode(upload):
            value = stamp_image_data_url(upload)
            UserProfile.objects.filter(pk=self.profile.pk).update(status='suspended')
            return value

        with patch('apps.users.digital_stamps.stamp_image_data_url', side_effect=suspend_during_decode):
            response = self.post()
        self.assertEqual(response.status_code, 403, response.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.stamp_image, '')

    def test_head_is_read_only_for_owner_and_discloses_no_stamp_to_other_users(self):
        self.assertEqual(self.post().status_code, 200)
        self.profile.refresh_from_db()
        before = UserProfile.objects.values().get(pk=self.profile.pk)
        response = self.client.head(URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.content, b'')
        self.assertTrue(response.data['can_manage'])
        self.assertTrue(response.data['has_stamp'])
        self.assertEqual(before, UserProfile.objects.values().get(pk=self.profile.pk))
        self.client.force_authenticate(self.other)
        response = self.client.head(URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['can_manage'])
        self.assertIsNone(response.data['stamp'])
        self.assertEqual(before, UserProfile.objects.values().get(pk=self.profile.pk))

    def test_stamp_blob_is_not_added_to_directory_or_general_profile_serializers(self):
        from apps.rbac.serializers import UserProfileListSerializer, UserProfileSelfSerializer, UserProfileSerializer
        for serializer in (UserProfileListSerializer, UserProfileSelfSerializer, UserProfileSerializer):
            with self.subTest(serializer=serializer.__name__):
                self.assertNotIn('stamp_image', serializer().fields)
