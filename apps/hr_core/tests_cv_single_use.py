"""Single-use CV links close at the parent as well as the private draft."""
from concurrent.futures import ThreadPoolExecutor
import io
import zipfile
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from . import tests_candidate_forms as forms
from . import tests_careers_sync_public_links as public_links
from .models import RecruitmentCandidate, RecruitmentFormInvitation, RecruitmentFormSubmission


@override_settings(ROOT_URLCONF='apps.hr_core.tests_candidate_forms')
class CVSingleUseTests(TestCase):
    setUp = forms.CandidateFormsTests.setUp
    cv_data = forms.CandidateFormsTests.cv_data
    upload_photo = forms.CandidateFormsTests.upload_photo
    create_link = public_links.CareersSyncPublicLinksTests.create_link
    register = public_links.CareersSyncPublicLinksTests.register
    invite = forms.CandidateFormsTests.invite
    session = forms.CandidateFormsTests.session

    def test_parent_link_and_existing_other_draft_close_after_submission(self):
        link, token = self.create_link()
        first, second = self.register(token), self.register(token)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {first['session']}")
        self.upload_photo()
        data = self.cv_data()
        data['candidate'].update(position_applied='Lead Process Engineer', nationality='Emirati')
        response = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        invitation = RecruitmentFormInvitation.objects.get(application_link_id=link['id'], candidate__isnull=False)
        self.assertEqual(invitation.candidate.position, 'Lead Process Engineer')
        self.assertEqual(invitation.candidate.nationality, 'Emirati')
        exported = self.hr_client.get(f'/api/v1/hr/recruitment-forms/{invitation.pk}/export/1/')
        self.assertEqual(exported.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            document = archive.read('word/document.xml').decode()
        self.assertIn('Lead Process Engineer', document)
        self.assertIn('Emirati', document)
        receipt = self.public.get(self.session_url)
        self.assertEqual(receipt.data, {'closed': True, 'kind': 'cv', 'state': 'submitted'})
        outsider = APIClient()
        reopened = outsider.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
        self.assertEqual(reopened.status_code, 409)
        self.assertEqual(str(reopened.data['code']), 'cv_link_closed')
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {second['session']}")
        for action in ('save', 'submit'):
            blocked = self.public.post(self.session_url, {'action': action, 'revision': 0, 'data': data}, format='json')
            self.assertEqual(blocked.status_code, 409)
        self.assertEqual(RecruitmentFormSubmission.objects.filter(invitation__application_link_id=link['id']).count(), 1)

    def test_private_invitation_reopens_as_receipt_without_new_session_or_data(self):
        invitation = self.invite()
        self.session(invitation)
        data = self.cv_data()
        data['candidate'].update(position_applied='Lead Engineer', nationality='Indian')
        response = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        reopened = APIClient().post(self.access, {'token': invitation['token']}, format='json')
        self.assertEqual(reopened.data, {'closed': True, 'kind': 'cv', 'state': 'submitted'})
        candidate = RecruitmentCandidate.objects.get(pk=RecruitmentFormInvitation.objects.get(pk=invitation['id']).candidate_id)
        self.assertEqual(candidate.position, 'Lead Engineer')
        self.assertEqual(candidate.nationality, 'Indian')

    def test_oversized_candidate_fields_cannot_be_saved(self):
        invitation = self.invite()
        self.session(invitation)
        data = self.cv_data()
        data['candidate'].update(position_applied='x' * 201, nationality='x' * 101)
        response = self.public.post(self.session_url, {'action': 'save', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('candidate.position_applied', response.data['fields'])
        self.assertIn('candidate.nationality', response.data['fields'])


@override_settings(ROOT_URLCONF='apps.hr_core.tests_candidate_forms')
class CVSingleUseConcurrencyTests(TransactionTestCase):
    setUp = forms.CandidateFormsTests.setUp
    cv_data = forms.CandidateFormsTests.cv_data
    upload_photo = forms.CandidateFormsTests.upload_photo
    create_link = public_links.CareersSyncPublicLinksTests.create_link
    register = public_links.CareersSyncPublicLinksTests.register

    @skipUnless(connection.vendor == 'postgresql', 'Real row-lock verification requires PostgreSQL')
    def test_two_open_drafts_racing_to_submit_create_only_one_candidate(self):
        link, token = self.create_link()
        drafts = [self.register(token), self.register(token)]
        for draft in drafts:
            self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {draft['session']}")
            self.upload_photo()
        barrier = Barrier(2, timeout=15)
        data = self.cv_data()

        def submit(draft):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {draft['session']}")
                barrier.wait()
                return client.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json').status_code
            finally:
                connections.close_all()

        with patch('apps.notifications.services.NotificationService.create_notification'), ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, drafts))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(RecruitmentFormSubmission.objects.filter(invitation__application_link_id=link['id']).count(), 1)
        self.assertEqual(RecruitmentFormInvitation.objects.filter(application_link_id=link['id'], candidate__isnull=False).count(), 1)
