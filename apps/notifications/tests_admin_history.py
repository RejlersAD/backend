"""System Health history uses real guards and projects only safe event metadata."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import (Module, Organization, Permission, Role, RoleModule,
                              RolePermission, UserPermissionOverride, UserProfile, UserRole)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints
from .models import Notification, NotificationCategory, NotificationLog


urlpatterns = [
    path('api/v1/rbac/', include('apps.rbac.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
]
secure_module_endpoints(urlpatterns)
URL = '/api/v1/rbac/analytics/notification-history/'


@override_settings(ROOT_URLCONF=__name__, WEB_PUSH_VAPID_PRIVATE_KEY='', TIME_ZONE='Asia/Dubai')
class NotificationHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.block_http = patch('requests.sessions.Session.request', side_effect=AssertionError('No external delivery'))
        self.http = self.block_http.start()
        self.addCleanup(self.block_http.stop)
        self.addCleanup(self.http.assert_not_called)
        self.org = Organization.objects.create(code='history-synthetic', name='History synthetic')
        self.admin = self.actor('history-admin', superuser=True)
        self.recipient = self.actor('history-recipient', first_name='Elina', last_name='Example')
        self.module, _ = Module.objects.get_or_create(code='admin_dashboard', defaults={'name': 'Admin dashboard'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.category, _ = NotificationCategory.objects.get_or_create(name='APPROVAL')
        Notification.objects.all().delete()
        self.notice = Notification.objects.create(
            recipient=self.recipient, category=self.category, title='SENSITIVE TITLE',
            message='SENSITIVE BODY', action_url='https://secret.test/?token=secret',
            metadata={'token': 'SENSITIVE METADATA'}, email_error='SENSITIVE SMTP ERROR', status='READ',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def actor(self, name, superuser=False, **kwargs):
        user = get_user_model().objects.create_user(name, email=name + '@example.test', **kwargs)
        user.is_superuser = superuser
        user.is_staff = superuser
        user.save(update_fields=['is_superuser', 'is_staff'])
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.org})
        profile.roles.clear()
        profile.status = 'active'
        profile.save(update_fields=['status'])
        return user

    def event(self, action, details=None, hours=0, notification=None):
        row = NotificationLog.objects.create(notification=notification or self.notice,
                                             action=action, details={} if details is None else details)
        if hours:
            NotificationLog.objects.filter(pk=row.pk).update(timestamp=timezone.now() - timedelta(hours=hours))
        return row

    def test_route_uses_existing_health_authority_and_does_not_mutate_read_state(self):
        self.assertTrue(issubclass(resolve(URL).func.cls, ModuleActionGuardMixin))
        self.event('created')
        before_notice = list(Notification.objects.values())
        before_logs = list(NotificationLog.objects.values())
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['timezone'], 'Asia/Dubai')
        self.assertEqual(response.data['results'][0]['recipient']['name'], 'Elina Example')
        self.assertEqual(response.data['results'][0]['category'], 'APPROVAL')
        self.assertEqual(before_notice, list(Notification.objects.values()))
        self.assertEqual(before_logs, list(NotificationLog.objects.values()))

    def test_unprivileged_staff_and_read_grant_alone_cannot_view_history(self):
        role = Role.objects.create(code='history-reader', name='History reader', level=3)
        UserRole.objects.create(user_profile=self.recipient.rbac_profile, role=role)
        RoleModule.objects.create(role=role, module=self.module)
        for permission in self.module.permissions.filter(action='read'):
            RolePermission.objects.create(role=role, permission=permission)
        for staff in (False, True):
            self.recipient.is_staff = staff
            self.recipient.save(update_fields=['is_staff'])
            self.client.force_authenticate(self.recipient)
            self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))

    def test_active_super_admin_role_can_read_and_explicit_denial_wins(self):
        role, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': 'Synthetic super admin', 'level': 1})
        UserRole.objects.create(user_profile=self.recipient.rbac_profile, role=role)
        self.client.force_authenticate(self.recipient)
        self.assertEqual(self.client.get(URL).status_code, 200)
        for user in (self.recipient, self.admin):
            for permission in self.module.permissions.filter(action='read'):
                UserPermissionOverride.objects.create(user_profile=user.rbac_profile, permission=permission, allowed=False)
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(URL).status_code, 403)

    def test_inactive_profile_and_disabled_module_are_denied(self):
        self.admin.rbac_profile.status = 'inactive'
        self.admin.rbac_profile.save(update_fields=['status'])
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.admin.rbac_profile.status = 'active'
        self.admin.rbac_profile.save(update_fields=['status'])
        self.module.is_active = False
        self.module.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_events_keep_their_own_meaning_and_attempts_despite_parent_read_status(self):
        expected = {
            'CrEaTeD': ('recorded', 'Recorded', 'not_applicable', 'Not applicable'),
            'READ': ('read', 'Marked read', 'not_applicable', 'Not applicable'),
            'ARCHIVED': ('archived', 'Archived', 'not_applicable', 'Not applicable'),
            'email_sent': ('sent', 'Email send recorded', 'sent', 'Email send recorded'),
            'email_skipped': ('skipped', 'Skipped', 'skipped', 'Skipped'),
            'teams_sent': ('sent', 'Teams request accepted', 'sent', 'Teams request accepted'),
            'teams_failed': ('failed', 'Failed', 'failed', 'Failed'),
            'teams_skipped': ('skipped', 'Skipped', 'skipped', 'Skipped'),
            'web_push_sent': ('sent', 'Push request accepted', 'sent', 'Push request accepted'),
            'web_push_failed': ('failed', 'Failed', 'failed', 'Failed'),
            'web_push_skipped': ('skipped', 'Skipped', 'skipped', 'Skipped'),
            'private_unknown_action': ('unknown', 'Unknown', 'unknown', 'Unknown'),
        }
        event_expectations = {}
        for action in expected:
            event_expectations[self.event(action).pk] = expected[action]
        result = self.client.get(URL).data
        self.assertEqual(result['count'], len(expected))
        for row in result['results']:
            self.assertEqual((row['outcome'], row['outcome_label'], row['delivery_status'],
                              row['delivery_status_label']), event_expectations[row['id']])
            self.assertEqual(row['notification_id'], self.notice.pk)

    def test_read_status_follows_current_inbox_flag_including_archived_and_marked_unread(self):
        # Archive preserves the flag; admin mark-unread can leave status READ.
        for status, is_read, read_at in (
            ('SENT', False, None), ('READ', True, timezone.now()),
            ('ARCHIVED', False, None), ('ARCHIVED', True, timezone.now()),
            ('READ', False, None), ('SENT', False, timezone.now()), ('SENT', True, None),
        ):
            with self.subTest(status=status, is_read=is_read, read_at=read_at):
                NotificationLog.objects.all().delete()
                Notification.objects.filter(pk=self.notice.pk).update(
                    status=status, is_read=is_read, read_at=read_at,
                )
                self.event('email_sent')
                row = self.client.get(URL).data['results'][0]
                self.assertEqual((row['read_status'], row['read_status_label']),
                                 ('read', 'Marked read') if is_read else ('unread', 'Unread'))
                self.assertEqual(row['delivery_status_label'], 'Email send recorded')

    def test_current_read_state_changes_without_rewriting_historical_delivery_events(self):
        Notification.objects.filter(pk=self.notice.pk).update(status='SENT', is_read=False)
        for action in ('created', 'web_push_failed', 'web_push_sent', 'READ'):
            self.event(action)
        first = self.client.get(URL).data['results']
        self.assertTrue(all(row['read_status'] == 'unread' for row in first))
        # This is a synthetic domain action, not a side effect of reading history.
        self.notice.refresh_from_db()
        self.notice.mark_as_read()
        before = list(Notification.objects.values())
        before_logs = list(NotificationLog.objects.values())
        second = self.client.get(URL).data['results']
        self.assertTrue(all(row['read_status'] == 'read' for row in second))
        for previous, current in zip(first, second):
            self.assertEqual({key: value for key, value in previous.items() if not key.startswith('read_')},
                             {key: value for key, value in current.items() if not key.startswith('read_')})
        self.assertEqual(before, list(Notification.objects.values()))
        self.assertEqual(before_logs, list(NotificationLog.objects.values()))

    def test_projection_redacts_sensitive_fields_and_unsafe_unknown_actions_and_reasons(self):
        self.event('https://secret.test/?token=RAW_ACTION', details={'error': 'WEBHOOK TOKEN'})
        for details in ({'reason': 'https://secret.test/?token=RAW_REASON', 'error': 'ERROR TOKEN'},
                        {'reason': {'secret': 'NESTED SECRET'}}, ['MALFORMED SECRET'], 'STRING SECRET'):
            self.event('teams_skipped', details=details)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results']
        expected_fields = {'id', 'timestamp', 'notification_id', 'recipient', 'category', 'action',
                           'event_label', 'channel', 'outcome', 'outcome_label', 'reason_label',
                           'delivery_status', 'delivery_status_label', 'read_status', 'read_status_label'}
        for row in rows:
            self.assertEqual(set(row), expected_fields)
            self.assertIsNone(row['reason_label'])
        unknown = next(row for row in rows if row['action'] == 'other')
        self.assertEqual((unknown['channel'], unknown['outcome']), ('other', 'unknown'))
        body = response.content.decode()
        for secret in ('SENSITIVE', 'RAW_ACTION', 'RAW_REASON', 'WEBHOOK TOKEN', 'ERROR TOKEN', 'secret.test',
                       'NESTED SECRET', 'MALFORMED SECRET', 'STRING SECRET'):
            self.assertNotIn(secret, body)

    def test_only_known_skip_reasons_have_safe_labels(self):
        self.event('web_push_skipped', {'reason': 'channel_disabled', 'endpoint': 'https://secret.test'})
        self.event('teams_failed', {'reason': 'channel_disabled'})
        rows = self.client.get(URL).data['results']
        self.assertIsNone(rows[0]['reason_label'])
        self.assertEqual(rows[1]['reason_label'], 'Channel is disabled')

    def test_skip_labels_do_not_invent_why_email_or_browser_push_was_ineligible(self):
        self.notice.email_sent = True
        self.notice.send_in_app = False
        self.notice.save(update_fields=['email_sent', 'send_in_app'])
        self.event('email_skipped', {'reason': 'email_not_required'})
        self.event('web_push_skipped', {'reason': 'notification_not_unread'})
        rows = self.client.get(URL).data['results']
        self.assertEqual(rows[0]['reason_label'], 'Notification is no longer eligible for browser push')
        self.assertEqual(rows[1]['reason_label'], 'Email is not required')

    def test_default_period_filters_and_recipient_or_notification_search(self):
        current = self.event('teams_failed')
        self.event('email_sent', hours=30)
        self.event('created', hours=200)
        self.event('created', hours=800)
        self.assertEqual(self.client.get(URL).data['count'], 1)
        self.assertEqual(self.client.get(URL, {'hours': 168}).data['count'], 2)
        self.assertEqual(self.client.get(URL, {'hours': 720}).data['count'], 3)
        for search in ('Elina Example', self.recipient.username, self.recipient.email, str(self.notice.pk)):
            response = self.client.get(URL, {'search': search})
            self.assertEqual([row['id'] for row in response.data['results']], [current.pk])
        self.assertEqual(self.client.get(URL, {'search': 'No matching recipient'}).data['count'], 0)
        self.assertEqual(self.client.get(URL, {'hours': 720, 'channel': 'email', 'outcome': 'sent'}).data['count'], 1)
        self.assertEqual(self.client.get(URL, {'channel': 'web_push'}).data['count'], 0)

    def test_unknown_filters_are_consistent_with_unknown_display(self):
        self.event('PRIVATE SECRET ACTION')
        self.event('READ')
        for filters in ({'channel': 'other'}, {'outcome': 'unknown'}):
            response = self.client.get(URL, filters)
            self.assertEqual(response.data['count'], 1)
            self.assertEqual(response.data['results'][0]['action'], 'other')

    def test_stable_bounded_pagination_uses_id_for_tied_timestamps(self):
        events = [self.event('created') for _ in range(28)]
        timestamp = timezone.now()
        NotificationLog.objects.filter(pk__in=[row.pk for row in events]).update(timestamp=timestamp)
        first = self.client.get(URL)
        self.assertEqual((first.data['count'], len(first.data['results'])), (28, 25))
        self.assertIsNotNone(first.data['next'])
        second = self.client.get(URL, {'page': 2})
        ids = [row['id'] for row in first.data['results'] + second.data['results']]
        self.assertEqual(ids, sorted([row.pk for row in events], reverse=True))
        self.assertEqual(self.client.get(URL, {'page_size': 100}).data['count'], 28)
        self.assertEqual(self.client.get(URL, {'page': 99}).status_code, 404)

    def test_invalid_queries_fail_with_validation_errors_and_large_search_is_safe(self):
        for name, values in {
            'hours': ('0', '25', '-1', 'Infinity', 'NaN', '9' * 200),
            'page': ('0', '-1', 'Infinity', '1.0', 'last', '9' * 200),
            'page_size': ('0', '101', '-1', 'Infinity', '25.0', '9' * 200),
            'channel': ('sms', 'SECRET'), 'outcome': ('delivered', 'SECRET'),
            'search': ('x' * 201,),
        }.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    response = self.client.get(URL, {name: value})
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIn(name, response.data)
        for value in ('9' * 200, '\u0669' * 200, '\u00b2', '\u0661\u0662\u0663'):
            self.assertEqual(self.client.get(URL, {'search': value}).status_code, 200)

    def test_reads_do_not_invent_missing_events_and_existing_deletion_still_cascades(self):
        self.assertEqual(self.client.get(URL).data['count'], 0)  # email_error is not a logged event
        self.event('created')
        self.notice.delete()
        self.assertEqual(self.client.get(URL).data['count'], 0)

    def test_admin_history_does_not_widen_personal_logs_or_expose_detail_mutations(self):
        foreign = self.event('created')
        own_notice = Notification.objects.create(recipient=self.admin, title='Own', message='Own')
        own = self.event('created', notification=own_notice)
        personal = self.client.get('/api/v1/notifications/logs/').data
        rows = personal['results'] if isinstance(personal, dict) else personal
        self.assertEqual([row['id'] for row in rows], [own.pk])
        self.assertEqual(self.client.get(f'/api/v1/notifications/logs/{foreign.pk}/').status_code, 404)
        self.assertEqual(self.client.get(URL + str(foreign.pk) + '/').status_code, 404)
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(URL, {}, format='json')
            self.assertEqual(response.status_code, 405)
        self.assertEqual(NotificationLog.objects.count(), 2)
