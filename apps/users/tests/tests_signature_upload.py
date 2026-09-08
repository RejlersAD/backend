import base64
from io import BytesIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image, ImageDraw
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.models import Organization, UserProfile
from apps.users.views import EmployeeProfileViewSet


User = get_user_model()


class SignatureUploadAPITests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:10]
        self.user = User.objects.create_user(
            username=f'signer-{suffix}',
            email=f'signer-{suffix}@example.com',
        )
        organization = Organization.objects.create(
            name=f'Signature Test {suffix}',
            code=f'SIGN-{suffix}',
        )
        self.profile = UserProfile.objects.create(user=self.user, organization=organization)
        self.factory = APIRequestFactory()

    def request_signature(self, method, data=None, format=None):
        request = getattr(self.factory, method)(
            '/api/v1/users/employees/my-signature/',
            data=data,
            format=format,
        )
        force_authenticate(request, self.user)
        view = EmployeeProfileViewSet.as_view({method: 'my_signature'})
        return view(request)

    @staticmethod
    def scanned_signature():
        image = Image.new('RGB', (1200, 800), 'white')
        draw = ImageDraw.Draw(image)
        draw.line((430, 390, 770, 410), fill='navy', width=10)
        output = BytesIO()
        image.save(output, 'JPEG', quality=90)
        return SimpleUploadedFile('scan.jpg', output.getvalue(), content_type='image/jpeg')

    def test_upload_crops_paper_and_saves_transparent_png(self):
        response = self.request_signature(
            'post',
            data={'signature': self.scanned_signature()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['has_signature'])
        self.assertTrue(response.data['signature'].startswith('data:image/png;base64,'))
        encoded = response.data['signature'].split(',', 1)[1]
        cropped = Image.open(BytesIO(base64.b64decode(encoded)))
        self.assertEqual(cropped.mode, 'RGBA')
        self.assertLess(cropped.width, 500)
        self.assertLess(cropped.height, 100)
        visible_bbox = cropped.getchannel('A').getbbox()
        self.assertIsNotNone(visible_bbox)
        self.assertGreaterEqual(visible_bbox[2] - visible_bbox[0], cropped.width * 0.8)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.signature_image, response.data['signature'])

    def test_delete_removes_saved_profile_signature(self):
        self.profile.signature_image = 'data:image/png;base64,c2lnbmF0dXJl'
        self.profile.save(update_fields=['signature_image'])

        response = self.request_signature('delete')

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.signature_image, '')
