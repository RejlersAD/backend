from types import SimpleNamespace
from unittest import TestCase

from apps.users.middleware import PasswordExpiryMiddleware


class PasswordExpiryMiddlewareAuthExemptionTests(TestCase):
    def setUp(self):
        self.middleware = PasswordExpiryMiddleware(lambda request: None)

    def test_versioned_login_skips_session_user_lookup(self):
        class Request:
            path = '/api/v1/auth/login/'

            @property
            def user(self):
                raise AssertionError('login exemption must run before request.user')

        self.assertIsNone(self.middleware._check_password_expiry(Request()))

    def test_unversioned_login_skips_session_user_lookup(self):
        class Request:
            path = '/api/auth/login/'

            @property
            def user(self):
                raise AssertionError('login exemption must run before request.user')

        self.assertIsNone(self.middleware._check_password_expiry(Request()))

    def test_versioned_auth_refresh_is_exempt(self):
        request = SimpleNamespace(path='/api/v1/auth/refresh/', user=None)

        self.assertIsNone(self.middleware._check_password_expiry(request))
