from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.api.urls import EmailTokenObtainPairView


class LoginViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = EmailTokenObtainPairView.as_view()

    def post(self, data):
        request = self.factory.post('/api/v1/auth/login/', data, format='json')
        return self.view(request)

    def test_missing_credentials_returns_validation_response(self):
        response = self.post({})

        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
        self.assertIn('password', response.data)

    def test_successful_login_returns_tokens(self):
        class Refresh:
            access_token = 'test-access'

            def __str__(self):
                return 'test-refresh'

        user = SimpleNamespace(email='login@example.test', is_active=True)
        with (
            patch('apps.users.serializers_jwt.authenticate', return_value=user),
            patch('apps.users.serializers_jwt.EmailTokenObtainPairSerializer.get_token', return_value=Refresh()),
            patch('apps.users.serializers_jwt.record_successful_login') as record_login,
        ):
            response = self.post({'email': user.email, 'password': 'test-password'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'access': 'test-access', 'refresh': 'test-refresh'})
        self.assertEqual(record_login.call_args.args[0], user)

    def test_invalid_credentials_preserve_validation_response(self):
        with patch('apps.users.serializers_jwt.authenticate', return_value=None):
            response = self.post({'email': 'login@example.test', 'password': 'invalid'})

        self.assertEqual(response.status_code, 400)

    def test_authentication_error_preserves_unauthorized_response(self):
        with patch.object(TokenObtainPairView, 'post', side_effect=AuthenticationFailed('Denied')):
            response = self.post({})

        self.assertEqual(response.status_code, 401)

    def test_malformed_json_returns_bad_request(self):
        request = self.factory.post('/api/v1/auth/login/', '{', content_type='application/json')

        self.assertEqual(self.view(request).status_code, 400)

    @override_settings(DEBUG=False)
    def test_unexpected_error_is_logged_and_returns_safe_json(self):
        with (
            patch.object(TokenObtainPairView, 'post', side_effect=RuntimeError('internal detail')),
            self.assertLogs('apps.api.urls', level='ERROR') as logs,
        ):
            response = self.post({})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data['error_type'], 'RuntimeError')
        self.assertIsNone(response.data['debug_info'])
        self.assertNotIn('internal detail', str(response.data))
        self.assertIn('RuntimeError: internal detail', logs.output[0])
