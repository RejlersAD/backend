"""Profile failures must not misrepresent the authenticated user's identity."""
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.views import UserProfileViewSet


class ProfileIdentityFallbackTests(SimpleTestCase):
    def request(self, superuser=False, authenticated=True):
        request = APIRequestFactory().get('/api/v1/rbac/users/me/', {'is_superuser': 'true'})
        if authenticated:
            force_authenticate(request, user=SimpleNamespace(
                id=1, pk=1, email='identity@example.test', username='identity',
                first_name='Test', last_name='User', is_authenticated=True,
                is_active=True, is_superuser=superuser, is_staff=superuser,
            ))
        return UserProfileViewSet.as_view({'get': 'me'})(request)

    def test_schema_failure_preserves_actual_superuser_flags(self):
        with patch('apps.rbac.views.UserProfile.objects.select_related',
                   side_effect=DatabaseError('Synthetic schema mismatch')):
            response = self.request(superuser=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_superuser'])
        self.assertTrue(response.data['is_staff'])
        self.assertEqual(response.data['roles'], [])
        self.assertEqual(response.data['message'], 'Profile temporarily unavailable')

    def test_missing_profile_preserves_actual_superuser_flags(self):
        with patch('apps.rbac.views.UserProfile.objects.select_related') as query:
            query.return_value.filter.return_value.first.return_value = None
            response = self.request(superuser=True)
        self.assertTrue(response.data['is_superuser'])
        self.assertTrue(response.data['is_staff'])
        self.assertEqual(response.data['roles'], [])

    def test_ordinary_user_cannot_claim_superuser_in_request(self):
        with patch('apps.rbac.views.UserProfile.objects.select_related',
                   side_effect=DatabaseError('Synthetic schema mismatch')):
            response = self.request()
        self.assertFalse(response.data['is_superuser'])
        self.assertFalse(response.data['is_staff'])

    def test_anonymous_request_cannot_receive_identity_fallback(self):
        response = self.request(authenticated=False)
        self.assertIn(response.status_code, (401, 403))
        self.assertNotIn('is_superuser', response.data)
