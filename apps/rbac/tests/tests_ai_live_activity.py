from datetime import timedelta
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.ai_live_activity import live_activity
from apps.rbac.ai_champion_models import ActivityEvent, AIUsageLog
from apps.rbac.ai_measurement_models import AIWorkflowRun
from apps.rbac.ai_champion_views import AIChampionViewSet
from apps.rbac.models import Organization, Role, UserProfile


class LiveActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Live A', code='LIVE_A')
        cls.other_org = Organization.objects.create(name='Live B', code='LIVE_B')
        cls.admin = get_user_model().objects.create_user(username='live-admin', email='live-admin@example.test')
        cls.member = get_user_model().objects.create_user(username='live-member', email='live-member@example.test')
        cls.outsider = get_user_model().objects.create_user(username='live-other', email='live-other@example.test')
        for user, org in [(cls.admin, cls.org), (cls.member, cls.org), (cls.outsider, cls.other_org)]:
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.organization = org
            profile.save()
        role, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Administrator'})
        cls.admin.rbac_profile.roles.add(role)

    def setUp(self):
        self.now = timezone.now()

    def run_for(self, user=None, age=1, status='running', org=None):
        return AIWorkflowRun.objects.create(user=user or self.member, organization=org or self.org,
            module='planning_package', operation='generate', deduplication_key=str(uuid.uuid4()),
            started_at=self.now - timedelta(minutes=age), status=status)

    def request(self, actor=None, **params):
        request = APIRequestFactory().get('/rbac/ai-champion/live-activity/', params)
        force_authenticate(request, actor or self.admin)
        return AIChampionViewSet.as_view({'get': 'live_activity'})(request)

    def test_directory_includes_quiet_users_without_inventing_presence(self):
        report = live_activity(self.org.pk, now=self.now)
        self.assertEqual(report['count'], 2)
        self.assertEqual({r['state'] for r in report['results']}, {'quiet'})
        self.assertTrue(all(r['last_signal_at'] is None for r in report['results']))

    def test_module_filter_and_catalogue_remain_in_organization_scope(self):
        ActivityEvent.objects.create(user=self.member, application='documents', action_type='view', timestamp=self.now - timedelta(minutes=1))
        ActivityEvent.objects.create(user=self.outsider, application='private_module', action_type='view', timestamp=self.now - timedelta(minutes=1))
        report = live_activity(self.org.pk, module='documents', now=self.now)
        self.assertEqual(report['count'], 1)
        self.assertEqual(report['results'][0]['id'], str(self.member.pk))
        self.assertEqual(report['modules'], ['documents'])
        self.assertEqual(live_activity(self.org.pk, module='private_module', now=self.now)['count'], 0)

    def test_sorting_applies_before_pagination(self):
        self.admin.first_name, self.member.first_name = 'Zed', 'Amy'
        self.admin.save(); self.member.save()
        first = live_activity(self.org.pk, ordering='user', page_size=1, now=self.now)
        last = live_activity(self.org.pk, ordering='-user', page_size=1, now=self.now)
        self.assertEqual(first['results'][0]['id'], str(self.member.pk))
        self.assertEqual(last['results'][0]['id'], str(self.admin.pk))

    def test_detail_counts_profile_link_and_separate_request_history(self):
        run = self.run_for(status='completed')
        AIUsageLog.objects.create(user=self.member, provider='openai', model_name='test', application='planning_package', provenance='server', workflow=run, timestamp=self.now - timedelta(minutes=2))
        for n in range(25):
            ActivityEvent.objects.create(user=self.member, application='documents', action_type='view', timestamp=self.now - timedelta(seconds=n+1))
        detail = live_activity(self.org.pk, selected_user=self.member.pk, now=self.now)['selected']
        self.assertEqual(detail['profile_id'], str(self.member.rbac_profile.pk))
        self.assertEqual(detail['events_24h'], 25)
        self.assertEqual(detail['ai_calls_24h'], 1)
        self.assertEqual(len(detail['ai_requests']), 1)  # not hidden behind newer events
        self.assertEqual(len(detail['activity_log']), 20)
        expanded = live_activity(self.org.pk, selected_user=self.member.pk, timeline_limit=100, now=self.now)['selected']
        self.assertEqual(len(expanded['activity_log']), 25)
        self.assertEqual(expanded['workflow_checks'], 0)

    def test_invalid_sort_and_history_limit_are_rejected(self):
        self.assertEqual(self.request(ordering='email;drop').status_code, 400)
        self.assertEqual(self.request(timeline_limit=10000).status_code, 400)

    def test_submitted_activity_does_not_become_ai_usage(self):
        ActivityEvent.objects.create(user=self.member, application='documents', action_type='view',
                                     timestamp=self.now - timedelta(minutes=1), metadata={'secret': 'must-not-appear'})
        AIUsageLog.objects.create(user=self.member, provider='openai', model_name='test', application='fake', provenance='client')
        report = live_activity(self.org.pk, selected_user=self.member.pk, now=self.now)
        row = next(r for r in report['results'] if r['id'] == str(self.member.pk))
        self.assertEqual((row['state'], row['events_24h'], row['ai_calls_24h']), ('recent', 1, 0))
        self.assertNotIn('must-not-appear', str(report))
        self.assertEqual(report['selected']['timeline'][0]['source'], 'Submitted activity')

    def test_processing_stale_and_future_workflows(self):
        self.run_for(age=2)
        self.run_for(user=self.admin, age=361)
        self.run_for(user=self.admin, age=-5)
        report = live_activity(self.org.pk, now=self.now)
        self.assertEqual(report['summary']['processing'], 1)
        self.assertEqual(report['summary']['attention'], 1)
        self.assertEqual(report['results'][0]['id'], str(self.member.pk))

    def test_recent_boundary_and_old_activity(self):
        ActivityEvent.objects.create(user=self.member, application='documents', timestamp=self.now - timedelta(minutes=6))
        ActivityEvent.objects.create(user=self.admin, application='old', timestamp=self.now - timedelta(hours=25))
        report = live_activity(self.org.pk, state='quiet', now=self.now)
        self.assertEqual(report['count'], 2)
        self.assertEqual(sum(r['events_24h'] for r in report['results']), 1)
        self.assertEqual(report['results'][0]['id'], str(self.member.pk))

    def test_timeline_returns_twenty_latest_signals_from_one_source(self):
        for n in range(25):
            ActivityEvent.objects.create(user=self.member, application='documents', feature=f'action-{n}',
                                         timestamp=self.now - timedelta(minutes=n + 1))
        report = live_activity(self.org.pk, selected_user=self.member.pk, now=self.now)
        timeline = report['selected']['timeline']
        self.assertEqual(len(timeline), 20)
        self.assertTrue(timeline[0]['action'].startswith('action-0:'))
        self.assertTrue(timeline[-1]['action'].startswith('action-19:'))

    def test_server_counts_and_organization_isolation(self):
        good = self.run_for(status='completed')
        wrong_org = self.run_for(org=self.other_org, status='completed')
        for workflow in [good, wrong_org]:
            AIUsageLog.objects.create(user=self.member, workflow=workflow, provider='openai', model_name='test',
                                     application='planning_package', provenance='server', success=False,
                                     timestamp=self.now - timedelta(seconds=5))
        report = live_activity(self.org.pk, selected_user=self.member.pk, now=self.now)
        row = next(r for r in report['results'] if r['id'] == str(self.member.pk))
        self.assertEqual((row['ai_calls_24h'], row['failed_ai_calls_24h']), (1, 1))
        self.assertNotIn(str(wrong_org.pk), str(report))

    def test_search_pagination_and_selected_scope(self):
        report = live_activity(self.org.pk, search='member', page=900, now=self.now)
        self.assertEqual((report['count'], report['page']), (1, 1))
        response = self.request(user=str(self.outsider.pk))
        self.assertEqual(response.status_code, 404, response.data)

    def test_permissions_and_invalid_filters(self):
        self.assertEqual(self.request(self.member).status_code, 403)
        self.assertEqual(self.request(state='online').status_code, 400)
        self.assertEqual(self.request(page_size=1000).status_code, 400)
        self.assertEqual(self.request(page=0).status_code, 400)
        self.assertEqual(self.request(user='invalid-id').status_code, 400)
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scope'], 'Your organization')
