from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Organization, Role, UserProfile
from apps.rbac.ai_champion_models import AIUsageLog, ActivityEvent
from apps.rbac.ai_champion_views import AIChampionViewSet
from apps.rbac.ai_workforce_service import workforce_adoption


@override_settings(AI_ADOPTION_MODULE_APPLICATIONS={'workforce_test_ai': ['test-ai', 'test-ai-other']})
class WorkforceAdoptionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(code='WA_A', name='Workforce A')
        self.other_org = Organization.objects.create(code='WA_B', name='Workforce B')
        self.module = Module.objects.create(code='workforce_test_ai', name='Workforce AI')
        self.role = Role.objects.create(code='workforce_test_role', name='Workforce user')
        self.role.modules.add(self.module)
        self.now = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        self.start = datetime(2026, 8, 31, tzinfo=timezone.utc)

    def employee(self, name, org=None, linked=True, access=True):
        user = get_user_model().objects.create_user(username=name, email=f'{name}@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org or self.org})
        profile.organization = org or self.org
        if linked:
            profile.canonical_employee = EmployeeMaster.objects.create(
                employee_number=name, employee_code=name, emp_code=name, first_name=name, last_name='Test',
                join_date='2025-01-01', employment_status='active')
        profile.department = 'Engineering'
        profile.save()
        if access:
            profile.roles.add(self.role)
        return profile

    def usage(self, profile, at=None, **kwargs):
        AIUsageLog.objects.create(user=profile.user, application=kwargs.pop('application', 'test-ai'),
                                  provider='test', model_name='test', timestamp=at or self.start + timedelta(days=1), **kwargs)

    def report(self, **kwargs):
        return workforce_adoption('2026-08-31', now=self.now, **kwargs)

    def test_denominator_and_zero_are_distinct_from_missing(self):
        empty = self.report()
        self.assertIsNone(empty['totals']['weekly_adoption_rate'])
        self.assertIsNone(empty['totals']['repeat_rate'])
        self.employee('eligible')
        report = self.report()
        self.assertEqual(report['totals']['eligible'], 1)
        self.assertEqual(report['totals']['weekly_adoption_rate'], 0)
        self.assertEqual(report['totals']['no_observed_use'], 1)
        self.assertIsNone(report['quality']['coverage_percent'])

    def test_employee_and_module_eligibility(self):
        self.employee('unlinked', linked=False)
        self.employee('noaccess', access=False)
        excluded = self.employee('excluded')
        excluded.metadata = {'ai_adoption_excluded': True}
        excluded.save()
        inactive = self.employee('inactive')
        inactive.user.is_active = False
        inactive.user.save()
        self.employee('included')
        result = self.report()
        self.assertEqual(result['totals']['eligible'], 1)
        self.assertEqual(result['quality']['unlinked_accounts'], 1)
        self.assertEqual(result['quality']['employees_without_module_access'], 1)
        self.assertEqual(result['quality']['excluded_accounts'], 1)

    def test_one_employee_counted_once_across_requests_and_modules(self):
        p = self.employee('multi')
        self.usage(p)
        self.usage(p)
        self.usage(p, application='test-ai-other')
        self.employee('unused')
        result = self.report()['totals']
        self.assertEqual((result['eligible'], result['wau'], result['mau']), (2, 1, 1))
        self.assertEqual(result['weekly_adoption_rate'], 50)
        self.assertEqual(result['average_active_days'], 1)

    def test_retention_uses_previous_users_not_all_eligible(self):
        a, b, c = [self.employee(n) for n in ['returning', 'previous', 'new']]
        self.usage(a, self.start - timedelta(days=1))
        self.usage(b, self.start - timedelta(days=2))
        self.usage(a)
        self.usage(c)
        t = self.report()['totals']
        self.assertEqual((t['previous_wau'], t['returning'], t['repeat_rate']), (2, 1, 50))

    def test_page_visits_count_and_end_boundary_is_excluded(self):
        p = self.employee('boundaries')
        ActivityEvent.objects.create(user=p.user, application='test-ai', timestamp=self.start)
        self.usage(p, success=False)
        self.usage(p, application='unconfigured')
        self.usage(p, self.start + timedelta(weeks=1))
        self.assertEqual(self.report()['totals']['wau'], 1)
        self.usage(p, self.start)
        self.assertEqual(self.report()['totals']['wau'], 1)

    def test_partial_week_compares_equal_elapsed_time(self):
        p = self.employee('partial')
        self.usage(p, datetime(2026, 9, 11, 10, tzinfo=timezone.utc))
        self.usage(p, datetime(2026, 9, 4, 13, tzinfo=timezone.utc))
        r = workforce_adoption('2026-09-07', now=self.now)
        self.assertTrue(r['window']['partial'])
        self.assertEqual((r['totals']['wau'], r['totals']['previous_wau']), (1, 0))

    def test_scope_and_manager_identity_do_not_leak(self):
        p = self.employee('visible')
        hidden = self.employee('private-manager', org=self.other_org)
        p.manager = hidden
        p.save()
        self.usage(hidden)
        r = self.report(user_ids=[p.user_id])
        self.assertEqual(r['totals']['eligible'], 1)
        self.assertEqual(r['teams'][0]['name'], 'Not assigned')
        self.assertNotIn('private-manager', str(r))
        self.assertNotIn('Workforce B', str(r))

    def test_disabled_module_excludes_even_super_admin(self):
        p = self.employee('super')
        p.user.is_superuser = True
        p.user.save()
        with patch('apps.rbac.ai_cohort.is_module_enabled', return_value=False):
            self.assertEqual(self.report()['totals']['eligible'], 0)

    def test_group_totals_reconcile_and_week_validation(self):
        self.employee('one')
        self.employee('two', org=self.other_org)
        r = self.report()
        self.assertEqual(len(r['departments']), 2)
        self.assertEqual(sum(g['eligible'] for g in r['teams']), r['totals']['eligible'])
        self.assertEqual(len(r['trend']), 8)
        for value in ['bad', '2026-09-08', '2030-01-07', '2020-01-06']:
            with self.assertRaises(ValidationError):
                workforce_adoption(value, now=self.now)

    def test_endpoint_requires_admin_and_scopes_organization(self):
        p = self.employee('admin')
        hidden = self.employee('hidden', org=self.other_org)
        admin, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Administrator'})
        p.roles.add(admin)
        def request(user):
            req = APIRequestFactory().get('/api/v1/rbac/ai-champion/workforce-adoption/')
            force_authenticate(req, user=user)
            return AIChampionViewSet.as_view({'get': 'workforce_adoption'})(req)
        self.assertEqual(request(hidden.user).status_code, 403)
        response = request(p.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['totals']['eligible'], 1)
        self.assertEqual(response.data['scope'], 'Your organization')

    @override_settings(AI_ADOPTION_MODULE_APPLICATIONS=None)
    def test_default_cohort_includes_all_active_radai_modules_and_visits(self):
        from apps.rbac.ai_cohort import current_cohort
        module = Module.objects.create(code='radai_sales_test', name='RADAI Sales Cohort Test')
        person = self.employee('sales-only')
        self.role.modules.set([module])
        ActivityEvent.objects.create(user=person.user, application=module.code, action_type='view', timestamp=self.start)
        cohort, _, modules, _ = current_cohort(organization_id=self.org.pk)
        self.assertIn(person.user_id, cohort)
        self.assertIn(module, modules)
        self.assertEqual(self.report(organization_id=self.org.pk)['totals']['wau'], 1)

    def test_old_eligibility_snapshots_are_not_reused_under_new_policy(self):
        from apps.rbac.ai_measurement_models import AIWorkforceSnapshot
        from apps.rbac.ai_snapshots import cohort_at
        person = self.employee('new-policy')
        AIWorkforceSnapshot.objects.create(organization=self.org, capture_date=self.start.date(),
            captured_at=self.start, policy_version='eligible-linked-active-v1', people={}, quality={})
        cohort, _, _, _, basis = cohort_at(self.start, organization_id=self.org.pk)
        self.assertIn(person.user_id, cohort)
        self.assertEqual(basis['basis'], 'current_fallback')
