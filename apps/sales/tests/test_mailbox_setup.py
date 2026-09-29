"""Administrator shared-mailbox registration through the real HTTP guards."""

from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Permission, Role, UserPermissionOverride, UserRole
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import SalesEmailIntake, SalesMailboxConnection, SalesMailboxSyncState
from apps.sales.serializers import SalesMailboxConnectionSerializer
from apps.sales.views import SalesMailboxConnectionViewSet

from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('mailbox-connections', SalesMailboxConnectionViewSet, basename='setup-mailboxes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SharedMailboxSetupTests(TestCase):
    endpoint = '/api/v1/sales/mailbox-connections/'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ('celery.app.task.Task.apply_async', 'requests.request', 'requests.post'):
            patcher = patch(target)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if target != 'celery.app.task.Task.apply_async':
                mocked.side_effect = AssertionError('Mailbox registration must not contact Microsoft.')
        users = get_user_model()
        self.admin = users.objects.create_user('setup-admin', email='admin@example.test')
        self.employee = users.objects.create_user('setup-employee', email='employee@example.test', is_staff=True)
        for actor in (self.admin, self.employee):
            grant_sales_actions(actor, 'sales_email_intake')
        role, _ = Role.objects.get_or_create(code='ict_admin', defaults={'name': 'Synthetic ICT administrator', 'level': 1})
        UserRole.objects.get_or_create(user_profile=self.admin.rbac_profile, role=role)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.payload = {
            'name': 'Synthetic shared mailbox', 'auth_mode': 'application',
            'tenant_id': 'synthetic-tenant', 'client_id': 'synthetic-client',
            'mailbox_address': 'sales@example.test', 'enabled': False,
        }

    def post(self, **overrides):
        return self.client.post(self.endpoint, {**self.payload, **overrides}, format='json')

    def deny(self, actor, action):
        permission = Permission.objects.get(module__code='sales_email_intake', action=action)
        return UserPermissionOverride.objects.create(
            user_profile=actor.rbac_profile, permission=permission, allowed=False,
        )

    def test_administrator_creates_pending_owned_connection_without_provider_or_sync_work(self):
        response = self.post(mailbox_address='  Sales@Example.Test  ')
        self.assertEqual(response.status_code, 201, response.data)
        connection = SalesMailboxConnection.objects.get()
        self.assertEqual(connection.mailbox_address, 'sales@example.test')
        self.assertEqual(connection.auth_mode, 'application')
        self.assertEqual(connection.created_by, self.admin)
        self.assertEqual(connection.updated_by, self.admin)
        self.assertFalse(connection.enabled)
        self.assertEqual(connection.last_status, 'not_tested')
        self.assertIsNone(connection.last_health_check_at)
        self.assertEqual(response.data['sync']['status'], 'not_configured')
        self.assertFalse(SalesMailboxSyncState.objects.exists())
        self.assertFalse(SalesEmailIntake.objects.exists())
        self.assertNotIn('encrypted_refresh_token', response.data)
        self.assertIn('no-store', response['Cache-Control'])

    def test_nonadministrator_application_request_is_denied_without_delegated_record(self):
        self.client.force_authenticate(self.employee)
        response = self.post()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(SalesMailboxConnection.objects.exists())

    def test_read_and_create_denials_block_administrator(self):
        for action in ('read', 'create'):
            with self.subTest(action=action):
                denied = self.deny(self.admin, action)
                response = self.post()
                self.assertEqual(response.status_code, 403, response.data)
                self.assertFalse(SalesMailboxConnection.objects.exists())
                denied.delete()

    def test_nonadministrator_delegated_creation_is_preserved(self):
        self.client.force_authenticate(self.employee)
        response = self.post(auth_mode='delegated')
        self.assertEqual(response.status_code, 201, response.data)
        connection = SalesMailboxConnection.objects.get()
        self.assertEqual(connection.auth_mode, 'delegated')
        self.assertEqual(connection.created_by, self.employee)

    def test_administrator_revocation_during_validation_does_not_create_delegated_fallback(self):
        validate = SalesMailboxConnectionSerializer.is_valid

        def validate_then_revoke(serializer, *args, **kwargs):
            result = validate(serializer, *args, **kwargs)
            UserRole.objects.filter(user_profile=self.admin.rbac_profile, role__code='ict_admin').delete()
            return result

        with patch.object(SalesMailboxConnectionSerializer, 'is_valid', validate_then_revoke):
            response = self.post()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(SalesMailboxConnection.objects.exists())

    def test_retry_and_case_variation_cannot_create_a_second_connection(self):
        self.assertEqual(self.post().status_code, 201)
        original = SalesMailboxConnection.objects.values().get()
        for address in ('sales@example.test', 'SALES@EXAMPLE.TEST', ' Sales@Example.Test '):
            with self.subTest(address=address):
                response = self.post(mailbox_address=address)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('mailbox_address', response.data)
                self.assertEqual(SalesMailboxConnection.objects.values().get(), original)

    def test_existing_mixed_case_mailbox_cannot_be_claimed_again(self):
        connection = SalesMailboxConnection.objects.create(
            **{**self.payload, 'mailbox_address': 'Sales@Example.Test'}, created_by=self.employee,
        )
        response = self.post()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('mailbox_address', response.data)
        self.assertEqual(SalesMailboxConnection.objects.count(), 1)
        connection.refresh_from_db()
        self.assertEqual(connection.created_by, self.employee)

    def test_database_duplicate_after_validation_returns_safe_field_error(self):
        # Model the database uniqueness race after serializer validation. This
        # verifies HTTP recovery, not PostgreSQL concurrent lock behavior.
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.admin)
        with patch('apps.sales.serializers.SalesMailboxConnectionSerializer.is_valid', return_value=True), \
                patch('apps.sales.serializers.SalesMailboxConnectionSerializer.validated_data',
                      new_callable=PropertyMock, return_value=self.payload), \
                patch('apps.sales.serializers.SalesMailboxConnectionSerializer.save',
                      side_effect=IntegrityError('synthetic uniqueness failure')):
            response = self.post()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(str(response.data['mailbox_address']), 'This mailbox connection already exists.')
        self.assertEqual(SalesMailboxConnection.objects.get().pk, connection.pk)

    def test_setup_rejects_caller_supplied_state_ownership_and_secrets(self):
        attempts = {
            'created_by': self.employee.pk, 'updated_by': self.employee.pk,
            'last_status': 'connected', 'last_health_check_at': '2026-09-29T00:00:00Z',
            'status': 'approved', 'approved_by': self.employee.pk,
            'client_secret': 'synthetic-untrusted-secret',
            'encrypted_refresh_token': 'synthetic-untrusted-token',
            'secret_configured': True, 'sync': {'status': 'up_to_date'},
        }
        for field, value in attempts.items():
            with self.subTest(field=field):
                response = self.post(**{field: value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assertFalse(SalesMailboxConnection.objects.exists())
                self.assertNotIn('synthetic-untrusted', str(response.data))

    def test_registration_cannot_enable_automatic_sync(self):
        response = self.post(enabled=True)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('enabled', response.data)
        self.assertFalse(SalesMailboxConnection.objects.exists())

    def test_invalid_fields_preserve_no_partial_record(self):
        for fields in ({'mailbox_address': 'invalid'}, {'tenant_id': ''}, {'client_id': ''}):
            with self.subTest(fields=fields):
                response = self.post(**fields)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(SalesMailboxConnection.objects.exists())

    def test_connection_test_failure_response_is_private_and_excludes_provider_payload(self):
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.admin)
        with patch('apps.sales.views.SalesMicrosoftGraphService.health_check', return_value={
            'connected': False, 'error': 'sensitive-provider-account-payload',
        }):
            response = self.client.post(f'{self.endpoint}{connection.pk}/test-connection/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(response.data['connected'])
        self.assertNotIn('sensitive-provider', str(response.data))
        self.assertIn('no-store', response['Cache-Control'])

    def test_saved_provider_error_is_not_exposed_by_reopening_or_listing_setup(self):
        connection = SalesMailboxConnection.objects.create(
            **self.payload, created_by=self.admin, last_status='error',
            last_error='sensitive-provider-account-payload',
        )
        for endpoint in (self.endpoint, f'{self.endpoint}{connection.pk}/'):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertNotIn('sensitive-provider', str(response.data))
                self.assertIn('Microsoft could not verify this mailbox.', str(response.data))
                self.assertIn('no-store', response['Cache-Control'])
        connection.refresh_from_db()
        self.assertEqual(connection.last_error, 'sensitive-provider-account-payload')

    def test_administrator_can_correct_application_address_without_create_permission(self):
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.admin)
        self.deny(self.admin, 'create')
        response = self.client.patch(f'{self.endpoint}{connection.pk}/', {
            'name': 'Corrected mailbox', 'mailbox_address': ' Corrected@Example.Test ',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        connection.refresh_from_db()
        self.assertEqual(connection.mailbox_address, 'corrected@example.test')
        self.assertEqual(connection.name, 'Corrected mailbox')
        self.assertEqual(connection.auth_mode, 'application')
        self.assertFalse(connection.enabled)
        self.assertIn('no-store', response['Cache-Control'])

    def test_application_correction_excludes_self_but_rejects_case_varied_duplicate(self):
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.admin)
        SalesMailboxConnection.objects.create(
            **{**self.payload, 'mailbox_address': 'Existing@Example.Test'}, created_by=self.admin,
        )
        endpoint = f'{self.endpoint}{connection.pk}/'
        unchanged = self.client.patch(endpoint, {'mailbox_address': 'SALES@EXAMPLE.TEST'}, format='json')
        self.assertEqual(unchanged.status_code, 200, unchanged.data)
        before = SalesMailboxConnection.objects.values().get(pk=connection.pk)
        duplicate = self.client.patch(endpoint, {'mailbox_address': 'existing@example.test'}, format='json')
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn('mailbox_address', duplicate.data)
        self.assertEqual(SalesMailboxConnection.objects.values().get(pk=connection.pk), before)

    def test_nonadministrator_cannot_edit_or_downgrade_an_owned_application_mailbox(self):
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.employee)
        before = SalesMailboxConnection.objects.values().get(pk=connection.pk)
        self.client.force_authenticate(self.employee)
        for data in ({'name': 'Attempted change'}, {'auth_mode': 'delegated'}):
            with self.subTest(data=data):
                response = self.client.patch(f'{self.endpoint}{connection.pk}/', data, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(SalesMailboxConnection.objects.values().get(pk=connection.pk), before)
                self.assertIn('no-store', response['Cache-Control'])

    def test_nonadministrator_cannot_promote_delegated_mailbox_but_can_edit_its_name(self):
        connection = SalesMailboxConnection.objects.create(
            **{**self.payload, 'auth_mode': 'delegated'}, created_by=self.employee,
        )
        self.client.force_authenticate(self.employee)
        endpoint = f'{self.endpoint}{connection.pk}/'
        self.assertEqual(self.client.patch(endpoint, {'auth_mode': 'application'}, format='json').status_code, 403)
        response = self.client.patch(endpoint, {'name': 'Personal mailbox'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        connection.refresh_from_db()
        self.assertEqual(connection.auth_mode, 'delegated')
        self.assertEqual(connection.name, 'Personal mailbox')

    def test_application_edit_requires_read_and_update_permissions(self):
        connection = SalesMailboxConnection.objects.create(**self.payload, created_by=self.admin)
        before = SalesMailboxConnection.objects.values().get(pk=connection.pk)
        for action in ('read', 'update'):
            with self.subTest(action=action):
                denied = self.deny(self.admin, action)
                response = self.client.patch(f'{self.endpoint}{connection.pk}/', {'name': 'Attempted change'}, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                self.assertEqual(SalesMailboxConnection.objects.values().get(pk=connection.pk), before)
                denied.delete()

    def test_protected_mixed_case_identity_is_not_normalized_or_repointed(self):
        connection = SalesMailboxConnection.objects.create(
            **{**self.payload, 'mailbox_address': 'Sales@Example.Test'}, created_by=self.admin,
        )
        SalesMailboxSyncState.objects.create(connection=connection, authorized_by=self.admin)
        endpoint = f'{self.endpoint}{connection.pk}/'
        unchanged = self.client.patch(endpoint, {'mailbox_address': 'Sales@Example.Test'}, format='json')
        self.assertEqual(unchanged.status_code, 200, unchanged.data)
        connection.refresh_from_db()
        self.assertEqual(connection.mailbox_address, 'Sales@Example.Test')
        before = SalesMailboxConnection.objects.values().get(pk=connection.pk)
        denied = self.client.patch(endpoint, {'mailbox_address': 'sales@example.test'}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(SalesMailboxConnection.objects.values().get(pk=connection.pk), before)
