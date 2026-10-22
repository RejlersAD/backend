from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient
from apps.rbac.models import Module, Permission, UserPermissionOverride
from apps.rbac.route_guard import secure_module_endpoints
from apps.procurement.tests.approval_fixtures import grant_approval
from apps.hr_core.models import EmployeeMaster, RecruitmentCandidate, RecruitmentOpening, RecruitmentFormInvitation, RecruitmentFormSubmission
from apps.hr_core.recruitment_form_schema import validate_form

urlpatterns = [path('api/v1/hr/', include('apps.hr_core.urls')), path('api/v1/candidate-forms/', include('apps.hr_core.candidate_form_urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='http://localhost:5173')
class CandidateFormsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.hr = get_user_model().objects.create_superuser(username='forms-hr', email='hr@example.test', password='test-only')
        self.other = get_user_model().objects.create_user(username='forms-other', email='other@example.test')
        grant_approval(self.hr, 'hr_management')
        module = Module.objects.get(code='hr_management')
        for action in ('read', 'create', 'update', 'export'):
            Permission.objects.get_or_create(code=f'hr_management.{action}', defaults={'module': module, 'name': action, 'action': action})
        self.candidate = RecruitmentCandidate.objects.create(name='Test Candidate', email='candidate@example.test', mobile='+91 1234567890', position='Engineer', department='Engineering')
        self.opening = RecruitmentOpening.objects.create(title='Engineer', department='Engineering')
        self.hr_client = APIClient(); self.hr_client.force_authenticate(self.hr)
        self.public = APIClient()
        self.url = '/api/v1/hr/recruitment-forms/'
        self.access = '/api/v1/candidate-forms/access/'
        self.session_url = '/api/v1/candidate-forms/session/'

    def invite(self, kind='cv'):
        response = self.hr_client.post(self.url, {'candidate': self.candidate.pk, 'opening': self.opening.pk, 'kind': kind}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_public_templates_contain_only_blank_schema_and_cannot_save(self):
        from apps.hr_core.recruitment_form_schema import schema_for
        before = RecruitmentFormInvitation.objects.count()
        for kind in ('cv', 'pre_onboarding'):
            url = f'/api/v1/candidate-forms/templates/{kind}/'
            response = self.public.get(url)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data, schema_for(kind))
            self.assertEqual(response['Cache-Control'], 'no-store, private')
            self.assertEqual(self.public.post(url, {'data': {}}, format='json').status_code, 405)
        self.assertEqual(self.public.get('/api/v1/candidate-forms/templates/unknown/').status_code, 404)
        self.assertEqual(self.public.post(self.session_url, {'action': 'save', 'data': {}}, format='json').status_code, 403)
        self.assertEqual(RecruitmentFormInvitation.objects.count(), before)

    def session(self, invitation):
        response = self.public.post(self.access, {'token': invitation['token']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['session']}")
        form = self.public.get(self.session_url).data
        if form['kind'] == 'cv' and form['state'] == 'draft':
            self.upload_photo()
        return form

    def upload_photo(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        output = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(output, 'PNG')
        response = self.public.post('/api/v1/candidate-forms/uploads/',
            {'kind': 'photo', 'file': SimpleUploadedFile('photo.png', output.getvalue(), content_type='image/png')}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def cv_data(self):
        return {'candidate': {'name': 'Test Candidate', 'email': 'candidate@example.test', 'phone': '+91 1234567890'},
                'education': [{'qualification': 'BSc', 'institution': 'Example University', 'period': '2015-2019'}],
                'projects': [{'period': '2020-present', 'title': 'Engineer', 'company': 'Example', 'projects': 'Project A', 'responsibilities': 'Design review'}],
                'acknowledgement': {'name': 'Test Candidate', 'accurate': True}}

    def submit_cv(self, invitation):
        current = self.session(invitation)
        return self.public.post(self.session_url, {'action': 'submit', 'revision': current['revision'], 'data': self.cv_data()}, format='json')

    def test_cv_draft_submit_receipt_and_no_employee_creation(self):
        before = EmployeeMaster.objects.count()
        invitation = self.invite()
        record = RecruitmentFormInvitation.objects.get(pk=invitation['id'])
        self.assertNotEqual(record.token_hash, invitation['token'])
        self.assertNotIn(invitation['token'], str(record.__dict__))
        form = self.session(invitation)
        self.assertEqual(form['position'], 'Engineer')
        self.assertNotIn('interview_notes', form)
        draft = self.public.post(self.session_url, {'action': 'save', 'revision': 0, 'data': {'candidate': {'name': 'Draft'}}}, format='json')
        self.assertEqual(draft.status_code, 200, draft.data)
        self.assertEqual(self.public.get(self.session_url).data['data']['candidate']['name'], 'Draft')
        with patch('apps.notifications.services.NotificationService.create_notification') as notify, self.captureOnCommitCallbacks(execute=True):
            submitted = self.public.post(self.session_url, {'action': 'submit', 'revision': 1, 'data': self.cv_data()}, format='json')
            self.assertEqual(submitted.status_code, 200, submitted.data)
        notify.assert_called_once()
        self.assertEqual(submitted.data['state'], 'submitted')
        self.assertEqual(RecruitmentFormSubmission.objects.count(), 1)
        self.assertEqual(EmployeeMaster.objects.count(), before)
        duplicate = self.public.post(self.session_url, {'action': 'submit', 'revision': 2, 'data': self.cv_data()}, format='json')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(RecruitmentFormSubmission.objects.count(), 1)

    def test_stale_draft_and_unknown_fields_rejected(self):
        self.session(self.invite())
        stale = self.public.post(self.session_url, {'action': 'save', 'revision': 9, 'data': {}}, format='json')
        self.assertEqual(stale.status_code, 409)
        bad = self.public.post(self.session_url, {'action': 'save', 'revision': 0, 'data': {'salary': 100}}, format='json')
        self.assertEqual(bad.status_code, 400)
        missing = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': {}}, format='json')
        self.assertEqual(missing.status_code, 400)

    def test_expired_revoked_closed_and_unknown_links_denied(self):
        invitation = self.invite()
        record = RecruitmentFormInvitation.objects.get(pk=invitation['id'])
        record.expires_at = timezone.now() - timedelta(seconds=1); record.save()
        self.assertEqual(self.public.post(self.access, {'token': invitation['token']}, format='json').status_code, 403)
        second = self.invite()
        self.session(second)
        self.hr_client.post(f"{self.url}{second['id']}/command/", {'action': 'revoke'}, format='json')
        self.assertEqual(self.public.get(self.session_url).status_code, 403)
        third = self.invite()
        self.opening.is_open = False; self.opening.save()
        self.assertEqual(self.public.post(self.access, {'token': third['token']}, format='json').status_code, 403)
        self.assertEqual(self.public.post(self.access, {'token': 'x' * 43}, format='json').status_code, 403)

    def test_pre_onboarding_requires_suitable_interview_and_email_code(self):
        denied = self.hr_client.post(self.url, {'candidate': self.candidate.pk, 'opening': self.opening.pk, 'kind': 'pre_onboarding'}, format='json')
        self.assertEqual(denied.status_code, 400)
        self.candidate.interview_outcome = 'suitable'; self.candidate.save()
        invitation = self.invite('pre_onboarding')
        result = self.public.post(self.access, {'token': invitation['token']}, format='json')
        self.assertTrue(result.data['verification_required'])
        self.assertNotIn('session', result.data)
        with patch('apps.hr_core.recruitment_forms.secrets.randbelow', return_value=123456):
            sent = self.public.post(self.access, {'token': invitation['token'], 'send_code': True}, format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertIn('123456', mail.outbox[0].body)
        bad = self.public.post(self.access, {'token': invitation['token'], 'code': '000000'}, format='json')
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(RecruitmentFormInvitation.objects.get(pk=invitation['id']).otp_attempts, 1)
        verified = self.public.post(self.access, {'token': invitation['token'], 'code': '123456'}, format='json')
        self.assertEqual(verified.status_code, 200, verified.data)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {verified.data['session']}")
        form = self.public.get(self.session_url)
        self.assertEqual(form.data['kind'], 'pre_onboarding')
        data = {'identity': {'first_name': 'Test', 'family_name': 'Candidate', 'birth_date': '1990-01-01', 'nationality': 'Indian'},
                'contact': {'phone': '+91 1234567890', 'email': 'candidate@example.test'},
                'emergency': {'name': 'Emergency', 'phone': '+91 2222222222', 'relationship': 'Sibling'},
                'dependants': [{'name': 'Child', 'relationship': 'Child', 'birthday': '2020-01-01'}],
                'beneficiary': {'candidate_name': 'Test Candidate', 'beneficiary_name': 'Family Member', 'beneficiary_relationship': 'Sibling', 'typed_name': 'Test Candidate', 'date': '2026-10-22', 'accepted': True}}
        saved = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        stored = RecruitmentFormSubmission.objects.get(invitation_id=invitation['id'])
        self.assertEqual(stored.data['dependants'][0]['name'], 'Child')
        self.assertIn('Family Member', stored.declaration_text)

    def test_hr_correction_preserves_versions_and_verification_is_explicit(self):
        invitation = self.invite()
        self.assertEqual(self.submit_cv(invitation).status_code, 200)
        review_url = f"{self.url}{invitation['id']}/review/"
        correction = self.hr_client.post(review_url, {'action': 'correction', 'version': 1, 'note': 'Clarify experience'}, format='json')
        self.assertEqual(correction.status_code, 200, correction.data)
        data = self.cv_data(); data['summary'] = {'text': 'Updated experience'}
        updated = self.public.post(self.session_url, {'action': 'submit', 'revision': 1, 'data': data}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(RecruitmentFormSubmission.objects.count(), 2)
        stale = self.hr_client.post(review_url, {'action': 'verify', 'version': 1}, format='json')
        self.assertEqual(stale.status_code, 409)
        checked = self.hr_client.post(review_url, {'action': 'verify', 'version': 2}, format='json')
        self.assertEqual(checked.status_code, 200, checked.data)
        self.assertEqual(checked.data['state'], 'verified')

    def test_export_and_private_routes_require_hr_and_export_grant(self):
        invitation = self.invite(); self.submit_cv(invitation)
        url = f"{self.url}{invitation['id']}/export/1/"
        response = self.hr_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'PK'))
        self.assertIn('no-store', response['Cache-Control'])
        for user in (None, self.other):
            self.public.force_authenticate(user)
            self.assertIn(self.public.get(url).status_code, (401, 403))
            self.assertIn(self.public.get(f"{self.url}{invitation['id']}/review/").status_code, (401, 403))

    def test_email_is_explicit_and_transport_failure_remains_retryable(self):
        invitation = self.invite()
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)
        command = f"{self.url}{invitation['id']}/command/"
        with patch('apps.hr_core.recruitment_forms.send_email', side_effect=RuntimeError('test')):
            failed = self.hr_client.post(command, {'action': 'send', 'token': invitation['token']}, format='json')
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(RecruitmentFormInvitation.objects.get(pk=invitation['id']).delivery_status, 'failed')
        sent = self.hr_client.post(command, {'action': 'send', 'token': invitation['token']}, format='json')
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertEqual(len(mail.outbox), 1)
        self.hr_client.post(command, {'action': 'send', 'token': invitation['token']}, format='json')
        self.assertEqual(len(mail.outbox), 1)

    def test_replacement_link_revokes_old_session_and_no_cross_candidate_access(self):
        first = self.invite(); self.session(first)
        self.invite()
        self.assertEqual(self.public.get(self.session_url).status_code, 403)
        self.assertEqual(self.public.post(self.access, {'token': first['token']}, format='json').status_code, 403)
        self.assertEqual(self.public.post(self.access, [], format='json').status_code, 400)

    def test_malformed_or_excessive_repeatable_fields_are_rejected(self):
        from rest_framework.exceptions import ValidationError
        for data in ({'education': [{}] * 41}, {'relatives_question': 'bad'}, {'identity': {'birth_date': '2099-01-01'}}):
            kind = 'cv' if 'education' in data else 'pre_onboarding'
            with self.assertRaises(ValidationError): validate_form(kind, data)

    def test_otp_attempt_limit_cooldown_and_expiry_are_enforced(self):
        self.candidate.interview_outcome = 'suitable'; self.candidate.save()
        invitation = self.invite('pre_onboarding')
        with patch('apps.hr_core.recruitment_forms.secrets.randbelow', return_value=123456):
            self.public.post(self.access, {'token': invitation['token'], 'send_code': True}, format='json')
        self.assertEqual(self.public.post(self.access, {'token': invitation['token'], 'send_code': True}, format='json').status_code, 429)
        for _ in range(5):
            self.assertEqual(self.public.post(self.access, {'token': invitation['token'], 'code': '000000'}, format='json').status_code, 403)
        self.assertEqual(self.public.post(self.access, {'token': invitation['token'], 'code': '123456'}, format='json').status_code, 403)
        record = RecruitmentFormInvitation.objects.get(pk=invitation['id'])
        self.assertEqual(record.otp_attempts, 5)
        record.otp_attempts = 0; record.otp_expires_at = timezone.now() - timedelta(seconds=1); record.save()
        self.assertEqual(self.public.post(self.access, {'token': invitation['token'], 'code': '123456'}, format='json').status_code, 403)

    def test_explicit_export_and_approval_denials_are_preserved(self):
        invitation = self.invite(); self.submit_cv(invitation)
        export_permission = Permission.objects.get(code='hr_management.export')
        approve_permission = Permission.objects.get(code='hr_management.approve')
        UserPermissionOverride.objects.create(user_profile=self.hr.rbac_profile, permission=export_permission, allowed=False)
        UserPermissionOverride.objects.create(user_profile=self.hr.rbac_profile, permission=approve_permission, allowed=False)
        self.assertEqual(self.hr_client.get(f"{self.url}{invitation['id']}/export/1/").status_code, 403)
        response = self.hr_client.post(f"{self.url}{invitation['id']}/review/", {'action': 'verify', 'version': 1}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.hr_client.get(f"{self.url}{invitation['id']}/review/").status_code, 200)

    def test_form_schema_preview_contains_template_sections_without_candidate_data(self):
        response = self.hr_client.get(f'{self.url}schema/pre_onboarding/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('beneficiary', [section['key'] for section in response.data['sections']])
        self.assertNotIn(self.candidate.email, str(response.data))
