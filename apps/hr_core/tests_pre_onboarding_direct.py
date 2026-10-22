from datetime import timedelta
from unittest.mock import Mock, patch
import hashlib
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from . import tests_candidate_forms as fixtures
from .models import RecruitmentCandidate, RecruitmentFormInvitation, RecruitmentFormSubmission


@override_settings(ROOT_URLCONF='apps.hr_core.tests_candidate_forms', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DirectPreOnboardingTests(TestCase):
    setUp = fixtures.CandidateFormsTests.setUp

    def start(self):
        created = self.hr_client.post('/api/v1/hr/recruitment-application-links/', {'kind': 'pre_onboarding'}, format='json')
        self.assertEqual(created.status_code, 201)
        token = created.data['link'].split('#apply=')[1]
        first = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
        second = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertNotEqual(first.data['session'], second.data['session'])
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {first.data['session']}")
        return first.data, second.data

    def data(self):
        return {'identity': {'first_name': 'New', 'family_name': 'Applicant', 'birth_date': '1990-01-01', 'nationality': 'Indian'},
                'contact': {'email': 'new@example.test', 'phone': '+971 50 123 4567'},
                'emergency': {'name': 'Emergency', 'phone': '+971 50 123 4568', 'relationship': 'Sibling'},
                'beneficiary': {'candidate_name': 'New Applicant', 'beneficiary_name': 'Family Member', 'beneficiary_relationship': 'Sibling', 'typed_name': 'New Applicant', 'date': '2026-10-22', 'accepted': True}}

    def test_blank_direct_form_has_no_position_or_registration_and_drafts_are_isolated(self):
        before = RecruitmentCandidate.objects.count()
        first, second = self.start()
        self.assertNotIn('registration_required', first)
        form = self.public.get(self.session_url)
        self.assertEqual(form.status_code, 200)
        self.assertEqual(form.data['kind'], 'pre_onboarding')
        self.assertEqual(form.data['position'], '')
        self.assertEqual(form.data['data']['contact'], {})
        self.assertEqual(form.data['verified_email'], '')
        saved = self.public.post(self.session_url, {'action': 'save', 'revision': 0, 'data': self.data()}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {second['session']}")
        self.assertEqual(self.public.get(self.session_url).data['data']['contact'], {})
        self.assertEqual(RecruitmentCandidate.objects.count(), before)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_email_confirmed_inside_form_is_required_before_submission(self):
        first, _ = self.start()
        data = self.data()
        payload = {'action': 'submit', 'revision': 0, 'data': data}
        refused = self.public.post(self.session_url, payload, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertTrue(refused.data['verification_required'])
        self.assertFalse(RecruitmentFormSubmission.objects.exists())
        with patch('apps.hr_core.recruitment_forms.secrets.randbelow', return_value=123456):
            sent = self.public.post(self.session_url, {'action': 'send_code', 'email': data['contact']['email']}, format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(mail.outbox[0].to, ['new@example.test'])
        blocked = self.public.post(self.session_url, {'action': 'send_code', 'email': 'another@example.test'}, format='json')
        self.assertEqual(blocked.status_code, 429)
        wrong = self.public.post(self.session_url, {'action': 'verify_email', 'email': data['contact']['email'], 'code': '000000'}, format='json')
        self.assertEqual(wrong.status_code, 403)
        invitation = RecruitmentFormInvitation.objects.get(token_hash=hashlib.sha256(first['token'].encode()).hexdigest())
        invitation.refresh_from_db(); self.assertEqual(invitation.otp_attempts, 1)
        verified = self.public.post(self.session_url, {'action': 'verify_email', 'email': data['contact']['email'], 'code': '123456'}, format='json')
        self.assertEqual(verified.status_code, 200, verified.data)
        changed = self.data(); changed['contact']['email'] = 'another@example.test'
        self.assertEqual(self.public.post(self.session_url, {**payload, 'data': changed}, format='json').status_code, 400)
        submitted = self.public.post(self.session_url, payload, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        invitation.refresh_from_db()
        self.assertEqual(invitation.verified_email, 'new@example.test')
        self.assertIsNone(invitation.opening_id)
        self.assertEqual(invitation.candidate.name, 'New Applicant')

    def test_expired_form_cannot_send_verification_mail(self):
        self.start()
        RecruitmentFormInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        result = self.public.post(self.session_url, {'action': 'send_code', 'email': 'new@example.test'}, format='json')
        self.assertEqual(result.status_code, 403)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_migration_retains_legacy_proof_without_verifying_blank_new_drafts(self):
        from importlib import import_module
        from django.apps import apps
        self.start()
        legacy = RecruitmentFormInvitation.objects.create(kind='pre_onboarding', owner=self.hr,
            recipient='known@example.test', session_hash='legacy-otp-session', token_hash='legacy-proof',
            expires_at=timezone.now() + timedelta(days=1))
        unconfirmed = RecruitmentFormInvitation.objects.create(kind='pre_onboarding', owner=self.hr,
            recipient='unconfirmed@example.test', token_hash='not-yet-verified', expires_at=legacy.expires_at)
        migration = import_module('apps.hr_core.migrations.0022_pre_onboarding_verified_email')
        migration.retain_verified_sessions(apps, Mock(connection=Mock(alias='default')))
        legacy.refresh_from_db(); unconfirmed.refresh_from_db()
        self.assertEqual(legacy.verified_email, legacy.recipient)
        self.assertEqual(unconfirmed.verified_email, '')
        self.assertFalse(RecruitmentFormInvitation.objects.filter(recipient='').exclude(verified_email='').exists())
