from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.ai_adoption_service import adoption_dashboard
from apps.rbac.ai_champion_models import ActivityEvent, AIUsageLog, AIPricingConfig, MonthlyChampion
from apps.rbac.ai_champion_service import compute_scores
from apps.rbac.ai_champion_views import AIChampionViewSet
from apps.rbac.models import Organization, Role, UserProfile


class AIAdoptionReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Adoption reporting A', code='ADOPT_A')
        cls.other_org = Organization.objects.create(name='Adoption reporting B', code='ADOPT_B')
        cls.admin = get_user_model().objects.create_user(username='adoption-admin', email='adoption-admin@example.test')
        cls.other = get_user_model().objects.create_user(username='adoption-other', email='adoption-other@example.test')
        cls.root = get_user_model().objects.create_user(username='adoption-root', email='adoption-root@example.test', is_superuser=True)
        for user, org in [(cls.admin, cls.org), (cls.other, cls.other_org)]:
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.organization = org
            profile.save()
        admin_role, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Administrator'})
        cls.admin.rbac_profile.roles.add(admin_role)

    def setUp(self):
        self.end = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        self.start = self.end - timedelta(days=7)

    def event(self, user=None, action='view', app='documents', **kwargs):
        return ActivityEvent.objects.create(user=user or self.admin, application=app,
                                            action_type=action, timestamp=kwargs.pop('timestamp', self.end - timedelta(days=1)), **kwargs)

    def usage(self, user=None, **kwargs):
        return AIUsageLog.objects.create(user=user or self.admin, provider='openai', model_name='test-model',
                                         application=kwargs.pop('application', 'documents'),
                                         timestamp=kwargs.pop('timestamp', self.end - timedelta(days=1)),
                                         tokens_input=100, tokens_output=50, **kwargs)

    def test_empty_window_reports_missing_cost_and_no_candidate(self):
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        self.assertEqual(report['totals']['requests'], 0)
        self.assertIsNone(report['totals']['recorded_cost_usd'])
        self.assertIsNone(report['totals']['success_rate'])
        self.assertIsNone(report['totals']['avg_latency_ms'])
        self.assertEqual(report['recognition']['candidates'], [])
        self.assertIn('no_ai_usage', [f['code'] for f in report['quality']['flags']])
        self.assertIsNone(report['quality']['pipeline_coverage_percent'])

    def test_counts_separate_views_work_events_and_ai_success(self):
        self.event()
        self.event(action='edit', success=False)
        self.usage(cost_usd=Decimal('0.25'), latency_ms=200)
        self.usage(success=False, cost_usd=Decimal('0.10'), latency_ms=0)
        self.usage(user=self.other, application='other-organization', cost_usd=Decimal('99'))
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        t = report['totals']
        self.assertEqual((t['tracked_users'], t['events'], t['page_views'], t['other_events']), (1, 2, 1, 1))
        self.assertEqual((t['requests'], t['successful_requests'], t['failed_requests']), (2, 1, 1))
        self.assertEqual(t['success_rate'], 50)
        self.assertEqual(t['recorded_cost_usd'], 0.35)
        self.assertEqual(t['tokens'], 300)
        self.assertEqual(t['avg_latency_ms'], 200)
        self.assertEqual([r['application'] for r in report['applications']], ['documents'])
        self.assertEqual(sum(r['requests'] for r in report['daily']), 2)
        self.assertEqual(report['quality']['requests_without_active_pricing'], 2)

    def test_page_views_do_not_create_ai_candidates(self):
        self.event()
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        self.assertEqual(report['leaderboard']['count'], 1)
        self.assertEqual(report['recognition']['candidates'], [])
        self.assertEqual(report['applications'][0]['telemetry_status'], 'activity_only')
        self.assertIsNone(report['applications'][0]['recorded_cost_usd'])
        self.assertEqual(report['contributions']['results'], [])

    def test_contribution_register_excludes_browsing_and_other_organizations(self):
        self.event(app='browsing-only')
        self.usage(request_id='visible-request', cost_usd=Decimal('0.25'))
        self.usage(user=self.other, request_id='private-request', application='private-ai')
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        rows = report['contributions']['results']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user']['email'], self.admin.email)
        self.assertEqual(rows[0]['requests'], 1)
        self.assertEqual(rows[0]['recorded_cost_usd'], .25)
        self.assertEqual(rows[0]['evidence'][0]['request_id'], 'visible-request')
        self.assertEqual(rows[0]['eligibility'], 'not_evaluated')
        self.assertIsNone(rows[0]['verified_outcomes'])
        self.assertNotIn('private-request', str(report))
        self.assertNotIn('browsing-only', str(rows))

    def test_request_samples_are_latest_three_in_window_per_module(self):
        for i in range(5):
            self.usage(request_id=f'request-{i}', timestamp=self.start + timedelta(hours=i), success=i != 4)
        self.usage(request_id='other-module', application='design')
        self.usage(request_id='outside-window', timestamp=self.end)
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        rows = {r['application']: r for r in report['contributions']['results']}
        self.assertEqual(rows['documents']['requests'], 5)
        self.assertEqual(rows['documents']['successful'], 4)
        self.assertEqual(rows['documents']['tokens'], 750)
        self.assertEqual({e['request_id'] for e in rows['documents']['evidence']}, {'request-2', 'request-3', 'request-4'})
        self.assertEqual(rows['design']['evidence'][0]['request_id'], 'other-module')
        self.assertNotIn('outside-window', str(rows))

    def test_calendar_candidates_are_separate_from_stored_awards(self):
        self.event()
        self.usage()
        saved = MonthlyChampion.objects.create(period_year=2026, period_month=7, rank=1, user=self.admin, champion_score=77)
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        self.assertEqual(report['recognition']['current_period'], {'year': 2026, 'month': 9})
        self.assertEqual(report['recognition']['latest_podium'][0]['month'], 7)
        self.assertEqual(len(report['recognition']['candidates']), 1)
        saved.refresh_from_db()
        self.assertEqual(saved.champion_score, 77)
        self.assertEqual(MonthlyChampion.objects.filter(user=self.admin).count(), 1)

    def test_window_excludes_end_and_includes_start(self):
        self.event(timestamp=self.start)
        self.event(timestamp=self.start - timedelta(seconds=1))
        self.event(timestamp=self.end)
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        self.assertEqual(report['totals']['events'], 1)
        self.assertEqual(sum(r['page_views'] for r in report['daily']), 1)

    def test_scoring_optional_scope_is_backwards_compatible(self):
        self.event()
        self.event(user=self.other)
        self.assertEqual(len(compute_scores(self.start, self.end)), 2)
        self.assertEqual([r['user_id'] for r in compute_scores(self.start, self.end, user_ids=[self.admin.pk])], [self.admin.pk])
        self.assertEqual(compute_scores(self.start, self.end, user_ids=[]), [])

    def test_pricing_flag_handles_unknown_and_configured_models(self):
        self.usage(cost_usd=0)
        AIPricingConfig.objects.create(provider='openai', model_name='test-model', effective_from=self.start,
                                      input_cost_per_1k=Decimal('0.001'), output_cost_per_1k=Decimal('0.002'))
        report = adoption_dashboard(self.start, self.end, user_ids=[self.admin.pk])
        self.assertEqual(report['quality']['requests_without_active_pricing'], 0)
        self.assertEqual(report['totals']['recorded_cost_usd'], 0)
        self.assertTrue(report['models'][0]['pricing_configured'])

    def request(self, user, params=None):
        request = APIRequestFactory().get('/api/v1/rbac/ai-champion/adoption-dashboard/', params or {})
        if user:
            force_authenticate(request, user=user)
        return AIChampionViewSet.as_view({'get': 'adoption_dashboard'})(request)

    def test_authentication_and_admin_permission(self):
        self.assertIn(self.request(None).status_code, [401, 403])
        self.assertEqual(self.request(self.other).status_code, 403)
        self.other.is_staff = True
        self.other.save(update_fields=['is_staff'])
        self.assertEqual(self.request(self.other).status_code, 403)

    def test_admin_cannot_see_other_organization(self):
        from django.utils import timezone as django_timezone
        self.event(timestamp=django_timezone.now() - timedelta(hours=1))
        self.event(user=self.other, app='private-application', timestamp=django_timezone.now() - timedelta(hours=1))
        response = self.request(self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scope'], 'Your organization')
        self.assertEqual(response.data['totals']['events'], 1)
        self.assertNotIn('private-application', str(response.data))
        self.assertEqual(self.request(self.root).data['totals']['events'], 2)

    def test_admin_without_organization_receives_no_global_data(self):
        self.admin.rbac_profile.organization = None
        from django.utils import timezone as django_timezone
        self.event(user=self.other, timestamp=django_timezone.now() - timedelta(hours=1))
        self.assertEqual(self.request(self.admin).data['totals']['events'], 0)

    def test_days_parameter_is_clamped(self):
        response = self.request(self.root, {'days': 9999})
        self.assertEqual((response.data['window']['end'] - response.data['window']['start']).days, 365)
        response = self.request(self.root, {'days': 'invalid'})
        self.assertEqual((response.data['window']['end'] - response.data['window']['start']).days, 30)

    def test_planning_pipeline_records_usage_without_prompt_or_key(self):
        from apps.planning_intelligence.services.claude_client import _log_usage
        AIPricingConfig.objects.create(provider='anthropic', model_name='test-claude', effective_from=self.start,
                                      input_cost_per_1k=Decimal('0.01'), output_cost_per_1k=Decimal('0.02'))
        arguments = dict(project=None, user=self.admin, model='test-claude', feature='analysis',
                         tokens_input=1000, tokens_output=500, latency_ms=120, success=True, error_code='')
        _log_usage(**arguments)
        _log_usage(**{**arguments, 'success': False, 'error_code': 'TimeoutError', 'tokens_input': 0, 'tokens_output': 0})
        logs = list(AIUsageLog.objects.filter(user=self.admin).order_by('timestamp'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].total_tokens, 1500)
        self.assertEqual(logs[0].cost_usd, Decimal('0.02'))
        self.assertEqual(logs[0].application, 'planning_intelligence')
        self.assertFalse(logs[1].success)
        self.assertNotEqual(logs[0].request_id, logs[1].request_id)

    def test_planning_telemetry_failure_does_not_break_pipeline(self):
        from unittest.mock import patch
        from apps.planning_intelligence.services.claude_client import _log_usage
        arguments = dict(project=None, user=self.admin, model='test-claude', feature='analysis',
                         tokens_input=0, tokens_output=0, latency_ms=120, success=False, error_code='TimeoutError')
        with patch.object(AIUsageLog.objects, 'get_or_create', side_effect=RuntimeError('Telemetry unavailable')):
            _log_usage(**arguments)
        _log_usage(**{**arguments, 'user': None})
        self.assertEqual(AIUsageLog.objects.filter(user=self.admin).count(), 0)
