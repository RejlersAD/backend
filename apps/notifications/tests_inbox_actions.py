"""Exercise the inbox router with ordinary accounts and production RBAC middleware."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.notifications.models import Notification, NotificationLog
from apps.rbac.models import Organization, UserProfile
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/notifications/', include('apps.notifications.urls'))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/notifications/'


@override_settings(
    ROOT_URLCONF=__name__, WEB_PUSH_VAPID_PRIVATE_KEY='',
    MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'apps.rbac.middleware.RBACMiddleware',
    ],
)
class NotificationInboxActionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        organization = Organization.objects.create(name='Inbox actions', code='INBOX-ACTIONS')
        with patch('apps.notifications.services.NotificationService.create_notification'):
            self.owner, self.other = [get_user_model().objects.create_user(
                username=f'inbox-actions-{index}', email=f'inbox-actions-{index}@example.test',
            ) for index in range(2)]
        for user in (self.owner, self.other):
            UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
        self.client = APIClient()
        self.client.force_login(self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')
        self.mine = self.notice()
        self.second = self.notice()
        self.foreign = self.notice(self.other)

    def notice(self, recipient=None, **kwargs):
        return Notification.objects.create(
            recipient=recipient or self.owner, title='Leave request updated',
            message='Your leave request is ready for review.',
            status=kwargs.pop('status', 'SENT'), **kwargs,
        )

    def unread_count(self):
        response = self.client.get(f'{BASE}unread_count/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['unread_count']

    def test_normal_user_marks_selected_read_and_persists_timestamp_status_and_audit(self):
        self.assertFalse(self.owner.is_staff)
        self.assertFalse(self.owner.rbac_profile.roles.exists())
        self.assertEqual(self.unread_count(), 2)
        response = self.client.post(f'{BASE}mark_as_read/', {
            'notification_ids': [self.mine.pk],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['marked_read'], 1)
        self.assertEqual(response.data['unread_count'], 1)
        self.mine.refresh_from_db()
        self.assertTrue(self.mine.is_read)
        self.assertEqual(self.mine.status, 'READ')
        self.assertIsNotNone(self.mine.read_at)
        self.assertEqual(self.unread_count(), 1)
        log = self.mine.logs.get(action='READ')
        self.assertEqual(log.details['user_id'], self.owner.pk)
        self.second.refresh_from_db()
        self.assertFalse(self.second.is_read)

    def test_retried_or_duplicate_ids_do_not_duplicate_audit_or_reset_read_time(self):
        payload = {'notification_ids': [self.mine.pk, self.mine.pk]}
        first = self.client.post(f'{BASE}mark_as_read/', payload, format='json')
        self.assertEqual(first.data['marked_read'], 1)
        self.mine.refresh_from_db()
        read_at = self.mine.read_at
        second = self.client.post(f'{BASE}mark_as_read/', payload, format='json')
        self.assertEqual(second.data['marked_read'], 0)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.read_at, read_at)
        self.assertEqual(self.mine.logs.filter(action='READ').count(), 1)

    def test_malformed_ids_return_validation_error_without_changing_inbox(self):
        response = self.client.post(f'{BASE}mark_as_read/', {
            'notification_ids': ['not-an-id'],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Notification.objects.filter(is_read=True).count(), 0)

    def test_bulk_read_ignores_foreign_and_nonexistent_ids(self):
        response = self.client.post(f'{BASE}mark_as_read/', {
            'notification_ids': [self.mine.pk, self.foreign.pk, self.foreign.pk + 1000],
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['marked_read'], 1)
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_read)
        self.assertFalse(self.foreign.logs.exists())

    def test_mark_all_route_covers_later_pages_without_mutating_other_recipients(self):
        for _ in range(60):
            self.notice()
        self.assertEqual(self.unread_count(), 62)
        response = self.client.post(f'{BASE}mark_all_as_read/?category=SYSTEM&page=2')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['marked_read'], 62)
        self.assertEqual(response.data['unread_count'], 0)
        self.assertEqual(self.unread_count(), 0)
        self.assertFalse(Notification.objects.filter(recipient=self.owner, is_read=False).exists())
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_read)

    def test_legacy_empty_ids_still_mark_own_inbox_read(self):
        response = self.client.post(f'{BASE}mark_as_read/', {'notification_ids': []}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['marked_read'], 2)
        self.assertEqual(response.data['unread_count'], 0)

    def test_mutation_count_matches_unread_endpoint_excluding_expired_and_archived(self):
        self.notice(expires_at=timezone.now() - timedelta(days=1))
        self.notice(status='ARCHIVED')
        self.notice(status='PENDING')
        response = self.client.post(f'{BASE}mark_as_read/', {
            'notification_ids': [self.mine.pk],
        }, format='json')
        self.assertEqual(response.data['unread_count'], 1)
        self.assertEqual(self.unread_count(), 1)

    def test_delete_removes_only_owned_notification_and_refreshes_cached_badge(self):
        self.assertEqual(self.unread_count(), 2)
        NotificationLog.objects.create(notification=self.mine, action='CREATED')
        response = self.client.delete(f'{BASE}{self.mine.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=self.mine.pk).exists())
        self.assertFalse(NotificationLog.objects.filter(notification_id=self.mine.pk).exists())
        self.assertEqual(self.unread_count(), 1)
        self.assertTrue(Notification.objects.filter(pk=self.foreign.pk).exists())

    def test_read_notifications_can_be_deleted_without_decrementing_unread_count(self):
        self.mine.mark_as_read()
        self.assertEqual(self.unread_count(), 1)
        self.assertEqual(self.client.delete(f'{BASE}{self.mine.pk}/').status_code, 204)
        self.assertEqual(self.unread_count(), 1)

    def test_foreign_detail_read_and_delete_are_unavailable_even_to_admin(self):
        for is_admin in (False, True):
            self.owner.is_staff = is_admin
            self.owner.is_superuser = is_admin
            self.owner.save(update_fields=['is_staff', 'is_superuser'])
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(f'{BASE}{self.foreign.pk}/').status_code, 404)
            self.assertEqual(self.client.delete(f'{BASE}{self.foreign.pk}/').status_code, 404)
            response = self.client.post(f'{BASE}mark_as_read/', {
                'notification_ids': [self.foreign.pk],
            }, format='json')
            self.assertEqual(response.data['marked_read'], 0)
        self.foreign.refresh_from_db()
        self.assertFalse(self.foreign.is_read)

    def test_anonymous_users_cannot_read_or_mutate_notifications(self):
        self.client.logout()
        self.client.credentials()
        for method, suffix in (
            ('get', ''), ('post', 'mark_as_read/'),
            ('post', 'mark_all_as_read/'), ('delete', f'{self.mine.pk}/'),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(self.client, method)(BASE + suffix)
                self.assertIn(response.status_code, (401, 403))
        self.mine.refresh_from_db()
        self.assertFalse(self.mine.is_read)

    def test_rbac_account_status_checks_are_preserved_for_inbox_actions(self):
        UserProfile.objects.filter(user=self.owner).update(status='suspended')
        for method, suffix in (
            ('get', ''), ('post', 'mark_as_read/'),
            ('post', 'mark_all_as_read/'), ('delete', f'{self.mine.pk}/'),
        ):
            with self.subTest(method=method, suffix=suffix):
                self.assertEqual(getattr(self.client, method)(BASE + suffix).status_code, 403)
        self.mine.refresh_from_db()
        self.assertFalse(self.mine.is_read)

    def test_inbox_access_does_not_grant_notification_broadcast_permission(self):
        response = self.client.post(f'{BASE}bulk_create/', {
            'recipient_ids': [self.other.pk], 'title': 'Broadcast', 'message': 'Unapproved broadcast',
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Notification.objects.filter(title='Broadcast').exists())

    def test_cache_is_invalidated_again_after_enclosing_transaction_commits(self):
        cache_key = f'notification_unread_count_{self.owner.pk}'
        foreign_key = f'notification_unread_count_{self.other.pk}'
        for action in ('read', 'delete'):
            notice = self.notice()
            cache.set(foreign_key, {'unread_count': 1})
            with self.subTest(action=action), self.captureOnCommitCallbacks(execute=True) as callbacks:
                if action == 'read':
                    response = self.client.post(f'{BASE}mark_as_read/', {
                        'notification_ids': [notice.pk],
                    }, format='json')
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertEqual(self.client.delete(f'{BASE}{notice.pk}/').status_code, 204)
                self.assertIsNone(cache.get(cache_key))
                # Simulate a concurrent poll caching the pre-commit count.
                cache.set(cache_key, {'unread_count': 99})
            self.assertTrue(callbacks)
            self.assertIsNone(cache.get(cache_key))
            self.assertEqual(cache.get(foreign_key), {'unread_count': 1})

    def test_audit_failure_rolls_back_read_state_so_retry_can_succeed(self):
        self.assertEqual(self.unread_count(), 2)
        with patch('apps.notifications.views.NotificationLog.objects.bulk_create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Audit unavailable'):
                self.client.post(f'{BASE}mark_as_read/', {
                    'notification_ids': [self.mine.pk],
                }, format='json')
        self.mine.refresh_from_db()
        self.assertFalse(self.mine.is_read)
        self.assertIsNone(self.mine.read_at)
        self.assertEqual(self.mine.status, 'SENT')
        self.assertFalse(self.mine.logs.filter(action='READ').exists())
        self.assertEqual(self.unread_count(), 2)
        response = self.client.post(f'{BASE}mark_as_read/', {
            'notification_ids': [self.mine.pk],
        }, format='json')
        self.assertEqual(response.data['marked_read'], 1)

    def test_detail_auto_read_and_opt_out_remain_consistent_with_mutation_api(self):
        self.assertEqual(self.unread_count(), 2)
        response = self.client.get(f'{BASE}{self.mine.pk}/?auto_read=false')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_read'])
        response = self.client.get(f'{BASE}{self.mine.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_read'])
        self.assertEqual(response.data['status'], 'READ')
        self.assertEqual(self.unread_count(), 1)
        self.assertTrue(self.mine.logs.get(action='READ').details['auto_read'])

    def test_archive_refreshes_the_unread_count(self):
        self.assertEqual(self.unread_count(), 2)
        response = self.client.post(f'{BASE}{self.mine.pk}/archive/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.unread_count(), 1)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, 'ARCHIVED')
