from datetime import datetime, timedelta, timezone

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from apps.rbac.ai_champion_models import AIUsageLog, ActivityEvent, MonthlyChampionPublication
from apps.rbac.ai_champion_views import AIChampionViewSet
from apps.rbac.monthly_champion_service import candidates, snapshot, publish, monthly_report
from apps.rbac.models import AuditLog, Organization, Role, UserProfile


class MonthlyChampionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='monthly-user', email='monthly@example.test')
        self.other = get_user_model().objects.create_user(username='monthly-other', email='other@example.test')
        self.root = get_user_model().objects.create_user(username='monthly-root', email='reviewer@example.test', is_superuser=True)
        self.org = Organization.objects.create(name='Monthly test', code='MONTHLY_TEST')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': self.org})
        profile.organization = self.org
        profile.save()
        role, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Administrator'})
        profile.roles.add(role)
        self.when = datetime(2025, 7, 10, tzinfo=timezone.utc)

    def usage(self, user=None, success=True, when=None):
        return AIUsageLog.objects.create(user=user or self.user, application='documents', provider='test',
                                         model_name='test', timestamp=when or self.when, success=success)

    def api(self, user, method='get', data=None):
        factory = APIRequestFactory()
        request = getattr(factory, method)('/api/v1/rbac/ai-champion/monthly-award/', data or {'year': 2025, 'month': 7}, format='json')
        if user:
            force_authenticate(request, user=user)
        return AIChampionViewSet.as_view({method: 'monthly_award'})(request)

    def test_page_visits_qualify_without_provider_calls_and_inactive_users_are_excluded(self):
        ActivityEvent.objects.create(user=self.user, application='documents', action_type='view', timestamp=self.when)
        self.usage(success=False)
        self.other.is_active = False
        self.other.save()
        self.usage(user=self.other)
        rows = candidates(2025, 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user_id'], str(self.user.pk))
        self.assertEqual(rows[0]['activity_count'], 1)

    def test_score_counts_radai_activity_and_does_not_duplicate_provider_calls(self):
        self.usage()
        self.usage(when=self.when + timedelta(days=1))
        self.usage(user=self.other)
        row = candidates(2025, 7)[0]
        self.assertEqual(row['user_id'], str(self.user.pk))
        self.assertEqual(row['score'], 100)
        self.assertEqual(row['breakdown'], {'activity_volume': 50, 'recorded_success_rate': 30, 'active_days': 20})
        before = snapshot(2025, 7)['fingerprint']
        ActivityEvent.objects.create(user=self.other, application='documents', timestamp=self.when)
        self.assertEqual(snapshot(2025, 7)['fingerprint'], before)
        ActivityEvent.objects.create(user=self.other, application='documents', timestamp=self.when)
        self.assertNotEqual(snapshot(2025, 7)['fingerprint'], before)

    def test_calendar_window_and_ties_are_deterministic(self):
        self.usage()
        self.usage(user=self.other)
        self.usage(when=datetime(2025, 8, 1, tzinfo=timezone.utc))
        rows = candidates(2025, 7)
        self.assertEqual([r['user_id'] for r in rows], sorted([str(self.user.pk), str(self.other.pk)]))
        self.assertTrue(all(r['requests'] == 1 for r in rows))

    def test_only_super_admin_can_publish(self):
        self.assertEqual(self.api(self.user, 'post').status_code, 403)
        self.assertEqual(self.api(self.other).status_code, 403)
        self.assertIn(self.api(None).status_code, [401, 403])

    def test_invalid_period_returns_validation_error(self):
        for values in [{'year': 0, 'month': 7}, {'year': 2025, 'month': 13}, {'year': 9998, 'month': 1}]:
            self.assertEqual(self.api(self.root, data=values).status_code, 400)

    def test_current_month_cannot_be_published(self):
        from django.utils import timezone as tz
        now = tz.now()
        with self.assertRaises(ValidationError):
            publish(now.year, now.month, '', 'Reviewed evidence.', self.root)

    def test_empty_stale_and_short_reason_rejected(self):
        with self.assertRaises(ValidationError):
            publish(2025, 7, snapshot(2025, 7)['fingerprint'], 'Reviewed evidence.', self.root)
        self.usage()
        preview = snapshot(2025, 7)
        with self.assertRaises(ValidationError):
            publish(2025, 7, preview['fingerprint'], 'short', self.root)
        self.usage()
        with self.assertRaises(ValidationError):
            publish(2025, 7, preview['fingerprint'], 'Reviewed evidence.', self.root)
        self.assertEqual(MonthlyChampionPublication.objects.count(), 0)

    def test_publish_preserves_snapshot_and_reviewer_and_rejects_overwrite(self):
        self.usage()
        preview = snapshot(2025, 7)
        response = self.api(self.root, 'post', {'year': 2025, 'month': 7, 'fingerprint': preview['fingerprint'], 'reason': 'Reviewed monthly contribution.'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['reviewer'], self.root.email)
        self.assertEqual(response.data['podium'][0]['requests'], 1)
        audit = AuditLog.objects.get(resource_type='MonthlyChampionPublication')
        self.assertEqual(str(audit.resource_id), response.data['id'])
        self.assertEqual(audit.user_id, self.root.pk)
        self.usage()
        report = monthly_report(2025, 7)
        self.assertEqual(report['publication']['podium'][0]['requests'], 1)
        self.assertEqual(report['candidates'][0]['requests'], 2)
        with self.assertRaises(ValidationError):
            publish(2025, 7, snapshot(2025, 7)['fingerprint'], 'Another decision.', self.root)
        self.assertEqual(MonthlyChampionPublication.objects.count(), 1)

    def test_organization_scope_applies_to_candidates_and_saved_history(self):
        self.usage(user=self.other)
        publish(2025, 7, snapshot(2025, 7)['fingerprint'], 'Reviewed contribution.', self.root)
        response = self.api(self.user)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_publish'])
        self.assertEqual(response.data['candidates'], [])
        self.assertEqual(response.data['history'], [])
        self.assertIsNone(response.data['publication'])
        self.assertNotIn(self.other.email, str(response.data))

    def test_mixed_organization_award_hides_other_people_and_freeform_reason(self):
        self.usage()
        self.usage(user=self.other)
        publish(2025, 7, snapshot(2025, 7)['fingerprint'], f'Reviewed {self.other.email} and their project.', self.root)
        report = self.api(self.user).data
        self.assertEqual(len(report['publication']['podium']), 1)
        self.assertNotIn(self.other.email, str(report))

    def test_audit_failure_rolls_back_publication(self):
        from unittest.mock import patch
        self.usage()
        with patch.object(AuditLog.objects, 'create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                publish(2025, 7, snapshot(2025, 7)['fingerprint'], 'Reviewed contribution.', self.root)
        self.assertFalse(MonthlyChampionPublication.objects.exists())

    def test_visit_only_candidate_and_provider_fanout_do_not_inflate_activity(self):
        ActivityEvent.objects.create(user=self.user, application='planning-package', action_type='view', timestamp=self.when)
        ActivityEvent.objects.create(user=self.user, application='planning_package', action_type='view', timestamp=self.when)
        preview = snapshot(2025, 7)
        self.assertEqual(preview['candidates'][0]['activity_count'], 2)
        for _ in range(4):
            AIUsageLog.objects.create(user=self.user, application='planning_package', provider='internal-api',
                                      model_name='test', timestamp=self.when, success=True)
        self.assertEqual(snapshot(2025, 7)['fingerprint'], preview['fingerprint'])

    def test_client_provider_claim_alone_is_not_radai_activity(self):
        AIUsageLog.objects.create(user=self.user, application='documents', provider='external-tool',
                                  model_name='test', provenance='client', timestamp=self.when)
        self.assertEqual(candidates(2025, 7), [])

    def test_shortlist_is_capped_at_twenty_and_only_top_ten_are_recognized(self):
        people = [get_user_model().objects.create_user(username=f'shortlist-{i}', email=f'shortlist-{i}@example.test') for i in range(22)]
        for user in people:
            ActivityEvent.objects.create(user=user, application='documents', action_type='view', timestamp=self.when)
        preview = snapshot(2025, 7)
        self.assertEqual(len(preview['candidates']), 20)
        self.assertEqual([r['rank'] for r in preview['candidates']], list(range(1, 21)))
        award = publish(2025, 7, preview['fingerprint'], 'Reviewed the activity shortlist.', self.root, preview['selected_user_ids'])
        self.assertEqual(len(award['podium']), 10)
        self.assertEqual([r['rank'] for r in award['podium']], list(range(1, 11)))
        stored = MonthlyChampionPublication.objects.get().snapshot
        self.assertEqual(len(stored['selected_user_ids']), 20)
        self.assertEqual(len(stored['candidates']), 20)

    def test_shortlist_validation_and_selection_bound_fingerprint(self):
        self.usage()
        self.usage(user=self.other)
        selected = [str(self.other.pk)]
        preview = snapshot(2025, 7, selected_user_ids=selected)
        self.assertEqual(preview['candidates'][0]['user_id'], str(self.other.pk))
        self.assertEqual(preview['candidates'][0]['rank'], 1)
        for invalid in [[str(self.root.pk)], selected * 2, selected * 21]:
            with self.assertRaises(ValidationError):
                snapshot(2025, 7, selected_user_ids=invalid)
        with self.assertRaises(ValidationError):
            snapshot(2025, 7, user_ids=[self.user.pk], selected_user_ids=selected)
        with self.assertRaises(ValidationError):
            publish(2025, 7, preview['fingerprint'], 'Reviewed selected employees.', self.root, [str(self.user.pk)])
        report = self.api(self.root, data={'year': 2025, 'month': 7, 'selected_user_ids': str(self.other.pk)})
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data['selected_user_ids'], selected)
        self.assertEqual(len(report.data['candidates']), 2)
        self.assertEqual(len(report.data['shortlist']), 1)

    def test_empty_shortlist_cannot_be_published(self):
        self.usage()
        preview = snapshot(2025, 7, selected_user_ids=[])
        self.assertEqual(preview['candidates'], [])
        with self.assertRaises(ValidationError):
            publish(2025, 7, preview['fingerprint'], 'Reviewed empty shortlist.', self.root, [])

    def test_older_published_awards_keep_three_places(self):
        from apps.rbac.monthly_champion_service import serialize_publication
        from types import SimpleNamespace
        rows = [{'rank': i, 'user_id': str(i)} for i in range(1, 6)]
        legacy = SimpleNamespace(snapshot={'candidates': rows, 'methodology': {'version': 'old'}},
            pk='old', period_year=2025, period_month=7, published_at=self.when, reviewer_name='Reviewer', reason='Original review')
        self.assertEqual(len(serialize_publication(legacy)['podium']), 3)
