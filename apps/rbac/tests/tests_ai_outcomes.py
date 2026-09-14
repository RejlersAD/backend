from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.rbac.models import Organization, UserProfile, Module, AuditLog
from apps.rbac.ai_outcome_models import AIOutcomeEvidence
from apps.rbac.ai_outcome_service import submit, review
from apps.rbac.ai_champion_views import AIChampionViewSet


@override_settings(AI_ADOPTION_MODULE_APPLICATIONS={'outcome_test': ['outcome_test']})
class OutcomeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(code='OUTCOME', name='Outcome pilot')
        self.module = Module.objects.create(code='outcome_test', name='Test AI')
        self.owner = get_user_model().objects.create_user(username='outcome_owner', email='owner@example.test')
        self.reviewer = get_user_model().objects.create_user(username='outcome_reviewer', email='reviewer@example.test', is_superuser=True)
        for user in [self.owner, self.reviewer]:
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.org})
            profile.organization = self.org
            profile.save()
        self.payload = dict(module=self.module.code, title='Pilot task', task_reference='job-001', evidence_url='https://example.test/evidence',
                            comparison='Same task scope and independently accepted quality.', baseline_minutes=120, ai_minutes=75,
                            review_minutes=15, rework_minutes=5, measurement='measured')

    def decision(self, row, **kwargs):
        return dict(id=row['id'], decision='approved', reason='Evidence checked against baseline.', comparable_quality_confirmed=True, **kwargs)

    def test_submit_review_net_time_and_audit(self):
        row = submit(self.payload, self.owner)
        self.assertEqual(row['saved_minutes'], 25)
        result = review(self.decision(row), self.reviewer, AIOutcomeEvidence.objects.all())
        self.assertEqual(result['status'], 'approved')
        self.assertEqual(AuditLog.objects.filter(resource_id=row['id']).count(), 2)
        with self.assertRaises(ValidationError):
            review(self.decision(row), self.reviewer, AIOutcomeEvidence.objects.all())

    def test_self_review_and_cross_scope_denied(self):
        row = submit(self.payload, self.owner)
        with self.assertRaises(PermissionDenied):
            review(self.decision(row), self.owner, AIOutcomeEvidence.objects.all())
        with self.assertRaises(NotFound):
            review(self.decision(row), self.reviewer, AIOutcomeEvidence.objects.none())

    def test_duplicate_and_bad_evidence_rejected(self):
        submit(self.payload, self.owner)
        with self.assertRaises(ValidationError):
            submit(self.payload, self.owner)
        with self.assertRaises(ValidationError):
            submit({**self.payload, 'evidence_url': 'http://example.test', 'task_reference': 'different'}, self.owner)

    def test_negative_savings_preserved_and_quality_required(self):
        row = submit({**self.payload, 'ai_minutes': 150}, self.owner)
        self.assertEqual(row['saved_minutes'], -50)
        values = self.decision(row)
        values['comparable_quality_confirmed'] = False
        with self.assertRaises(ValidationError):
            review(values, self.reviewer, AIOutcomeEvidence.objects.all())

    def test_audit_failure_rolls_back(self):
        row = submit(self.payload, self.owner)
        with patch('apps.rbac.ai_outcome_service.audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                review(self.decision(row), self.reviewer, AIOutcomeEvidence.objects.all())
        self.assertEqual(AIOutcomeEvidence.objects.get(pk=row['id']).status, 'pending')

    def test_endpoint_requires_admin_and_separates_measurement(self):
        row = submit({**self.payload, 'measurement': 'self_reported'}, self.owner)
        review(self.decision(row), self.reviewer, AIOutcomeEvidence.objects.all())
        view = AIChampionViewSet.as_view({'get': 'outcomes'})
        request = APIRequestFactory().get('/outcomes/')
        force_authenticate(request, user=self.owner)
        self.assertEqual(view(request).status_code, 403)
        request = APIRequestFactory().get('/outcomes/')
        force_authenticate(request, user=self.reviewer)
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['totals']['measured_minutes_saved'])
        self.assertEqual(response.data['totals']['self_reported_minutes_saved'], 25)
