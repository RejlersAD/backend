from datetime import timedelta
from unittest.mock import Mock, patch
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.hr_core.models import RecruitmentApplicationLink, RecruitmentCandidate, RecruitmentFormInvitation, RecruitmentOpening, RecruitmentPositionSync
from apps.hr_core.recruitment_sync import fetch_positions, sync_positions
from apps.hr_core import tests_candidate_forms as fixtures


def source_row(pk=10, title='Lead I&amp;C Engineer', location='Abu Dhabi'):
    return {'id': pk, 'title': {'rendered': title}, 'link': f'https://www.rejlers.com/ae/careers/open-positions/job-{pk}/', 'knowit_custom': {'location': location}}


def response(rows, total=None, pages=1):
    return Mock(status_code=200, content=b'[]', headers={'X-WP-Total': str(len(rows) if total is None else total), 'X-WP-TotalPages': str(pages)}, json=Mock(return_value=rows))


@override_settings(ROOT_URLCONF='apps.hr_core.tests_candidate_forms', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CareersSyncPublicLinksTests(TestCase):
    setUp = fixtures.CandidateFormsTests.setUp
    cv_data = fixtures.CandidateFormsTests.cv_data
    upload_photo = fixtures.CandidateFormsTests.upload_photo

    def create_link(self, kind='cv', opening=None):
        payload = {'kind': kind, 'opening': opening}
        result = self.hr_client.post('/api/v1/hr/recruitment-application-links/', payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        token = result.data['link'].split('#apply=')[1]
        return result.data, token

    def register(self, token, name='New Applicant', email='new@example.test', opening=None):
        result = self.public.post('/api/v1/candidate-forms/apply/', {'token': token, 'register': True, 'name': name, 'email': email, 'opening': opening or self.opening.pk}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        return result.data

    def test_feed_parser_reads_all_pages_and_decodes_titles_and_locations(self):
        first = [source_row(pk=i + 1) for i in range(100)]
        with patch('apps.hr_core.recruitment_sync.requests.get', side_effect=[response(first, 101, 2), response([source_row(101)], 101, 2)]) as get:
            rows = fetch_positions()
        self.assertEqual(len(rows), 101)
        self.assertEqual(rows[0]['title'], 'Lead I&C Engineer')
        self.assertEqual(rows[0]['location'], 'Abu Dhabi')
        self.assertEqual(get.call_args_list[1].kwargs['params']['page'], 2)

    def test_sync_is_idempotent_preserves_manual_positions_and_closes_missing_source_jobs(self):
        old = RecruitmentOpening.objects.create(title='Old', department='', source='rejlers_careers', source_id=20)
        with patch('apps.hr_core.recruitment_sync.requests.get', return_value=response([source_row()])):
            first, second = sync_positions(self.hr), sync_positions(self.hr)
        self.assertEqual(first['created'], 1)
        self.assertEqual(second['created'], 0)
        self.assertEqual(RecruitmentOpening.objects.filter(source_id=10).count(), 1)
        old.refresh_from_db(); self.assertFalse(old.is_open)
        self.opening.refresh_from_db(); self.assertTrue(self.opening.is_open)
        self.assertIsNotNone(RecruitmentPositionSync.objects.get().last_success_at)
        with patch('apps.hr_core.recruitment_sync.requests.get', return_value=response([source_row(20, 'Reopened', 'Navi Mumbai')])):
            sync_positions()
        old.refresh_from_db(); self.assertTrue(old.is_open); self.assertEqual(old.location, 'Navi Mumbai')

    def test_failed_or_incomplete_sync_preserves_last_good_jobs_and_persists_failure(self):
        job = RecruitmentOpening.objects.create(title='Keep', department='', source='rejlers_careers', source_id=77)
        for remote in (response([source_row()], total=2), response([source_row(), source_row()], total=2), response([source_row(location='')])):
            with patch('apps.hr_core.recruitment_sync.requests.get', return_value=remote):
                result = self.hr_client.post('/api/v1/hr/recruitment-position-sync/', {}, format='json')
            self.assertEqual(result.status_code, 502, result.data)
            self.assertEqual(RecruitmentPositionSync.objects.get().status, 'failed')
            job.refresh_from_db(); self.assertTrue(job.is_open)
            self.assertFalse(RecruitmentOpening.objects.filter(source_id=10).exists())

    def test_active_lease_prevents_overlapping_fetch_and_manual_edits_do_not_override_source(self):
        RecruitmentPositionSync.objects.create(lease_expires_at=timezone.now() + timedelta(minutes=2), status='running')
        with patch('apps.hr_core.recruitment_sync.requests.get') as get:
            self.assertEqual(sync_positions()['status'], 'running')
            get.assert_not_called()
        job = RecruitmentOpening.objects.create(title='Source', department='', source='rejlers_careers', source_id=50)
        result = self.hr_client.patch(f'/api/v1/hr/recruitment-openings/{job.pk}/', {'is_open': False}, format='json')
        self.assertEqual(result.status_code, 400)

    def test_candidate_free_links_for_both_forms_and_optional_position_create_no_candidates(self):
        before = RecruitmentCandidate.objects.count()
        for kind in ('cv', 'pre_onboarding'):
            link, token = self.create_link(kind)
            record = RecruitmentApplicationLink.objects.get(pk=link['id'])
            self.assertIsNone(record.opening_id)
            self.assertNotIn(token, str(record.__dict__))
            entry = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
            self.assertEqual(entry.status_code, 201, entry.data)
            self.assertIn('session', entry.data)
        self.assertEqual(RecruitmentCandidate.objects.count(), before)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_public_cv_visitors_have_isolated_drafts_and_candidate_is_created_once_on_submit(self):
        link, token = self.create_link()
        before = RecruitmentCandidate.objects.count()
        first = self.register(token)
        second = self.register(token, name='Different Applicant', email='different@example.test')
        self.assertNotEqual(first['session'], second['session'])
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {first['session']}")
        self.upload_photo()
        form = self.public.get(self.session_url)
        self.assertTrue(form.data['public_application'])
        self.assertEqual(form.data['data']['candidate']['name'], 'New Applicant')
        self.assertEqual(RecruitmentCandidate.objects.count(), before)
        data = self.cv_data(); data['candidate']['name'] = 'New Applicant'; data['candidate']['email'] = 'new@example.test'
        submitted = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(RecruitmentCandidate.objects.count(), before + 1)
        self.assertEqual(self.public.post(self.session_url, {'action': 'submit', 'revision': 1, 'data': data}, format='json').status_code, 409)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {second['session']}")
        self.assertEqual(self.public.get(self.session_url).status_code, 409)
        details = self.hr_client.get('/api/v1/hr/recruitment-application-links/', {'link': link['id']})
        self.assertEqual(details.data['submitted_applications'], 1)
        self.assertEqual(details.data['applications'][0]['candidate_name'], 'New Applicant')

    def test_public_pre_onboarding_requires_email_verification_but_not_candidate_selection(self):
        link, token = self.create_link('pre_onboarding')
        registered = self.register(token)
        self.assertTrue(registered['verification_required'])
        self.assertNotIn('session', registered)
        with patch('apps.hr_core.recruitment_forms.secrets.randbelow', return_value=123456):
            self.assertEqual(self.public.post(self.access, {'token': registered['token'], 'send_code': True}, format='json').status_code, 200)
        unlocked = self.public.post(self.access, {'token': registered['token'], 'code': '123456'}, format='json')
        self.assertEqual(unlocked.status_code, 200, unlocked.data)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {unlocked.data['session']}")
        self.assertEqual(self.public.get(self.session_url).data['kind'], 'pre_onboarding')
        data = {'identity': {'first_name': 'New', 'family_name': 'Applicant', 'birth_date': '1990-01-01', 'nationality': 'Indian'},
                'contact': {'phone': '+91 1234567890', 'email': 'new@example.test'},
                'emergency': {'name': 'Emergency', 'phone': '+91 2222222222', 'relationship': 'Sibling'},
                'beneficiary': {'candidate_name': 'New Applicant', 'beneficiary_name': 'Family Member', 'beneficiary_relationship': 'Sibling', 'typed_name': 'New Applicant', 'date': '2026-10-22', 'accepted': True}}
        submitted = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        invitation = RecruitmentFormInvitation.objects.get(application_link_id=link['id'])
        self.assertEqual(invitation.candidate.name, 'New Applicant')
        self.assertEqual(invitation.candidate.interview_outcome, '')
        review = self.hr_client.post(f'/api/v1/hr/recruitment-forms/{invitation.pk}/review/', {'action': 'verify', 'version': 1}, format='json')
        self.assertEqual(review.status_code, 400)

    def test_revoking_public_parent_or_closing_position_blocks_existing_private_sessions(self):
        link, token = self.create_link(opening=self.opening.pk)
        registered = self.register(token)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {registered['session']}")
        revoked = self.hr_client.post(f"/api/v1/hr/recruitment-application-links/{link['id']}/revoke/", {}, format='json')
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.public.get(self.session_url).status_code, 403)
        self.assertEqual(self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json').status_code, 403)
        _, other_token = self.create_link(opening=self.opening.pk)
        self.opening.is_open = False; self.opening.save()
        self.assertEqual(self.public.post('/api/v1/candidate-forms/apply/', {'token': other_token}, format='json').status_code, 403)

    def test_public_registration_cannot_choose_another_position_from_a_specific_link(self):
        _, token = self.create_link(opening=self.opening.pk)
        other = RecruitmentOpening.objects.create(title='Other', department='Other')
        result = self.public.post('/api/v1/candidate-forms/apply/', {'token': token, 'register': True, 'name': 'Other', 'email': 'other@example.test', 'opening': other.pk}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(RecruitmentFormInvitation.objects.exists())

    def test_direct_cv_without_position_or_registration_submits_isolated_candidate(self):
        RecruitmentOpening.objects.all().delete()
        before = RecruitmentCandidate.objects.count()
        link, token = self.create_link()
        first = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
        second = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertNotEqual(first.data['session'], second.data['session'])
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {first.data['session']}")
        self.upload_photo()
        form = self.public.get(self.session_url)
        self.assertEqual(form.data['data'], {'candidate': {'position_applied': '', 'nationality': ''}})
        self.assertEqual(form.data['position'], '')
        self.assertIn('expires_at', form.data)
        submitted = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': self.cv_data()}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(RecruitmentCandidate.objects.count(), before + 1)
        invitation = RecruitmentFormInvitation.objects.get(application_link_id=link['id'], candidate__isnull=False)
        self.assertIsNone(invitation.opening_id)
        self.assertEqual(invitation.recipient, self.cv_data()['candidate']['email'])
        self.assertEqual(invitation.candidate.position, '')
        reviewed = self.hr_client.post(f'/api/v1/hr/recruitment-forms/{invitation.pk}/review/', {'action': 'verify', 'version': 1}, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {second.data['session']}")
        self.assertEqual(self.public.get(self.session_url).status_code, 409)

    def test_both_link_types_expire_in_three_days_and_block_live_sessions(self):
        for kind in ('cv', 'pre_onboarding'):
            link, token = self.create_link(kind)
            record = RecruitmentApplicationLink.objects.get(pk=link['id'])
            self.assertAlmostEqual((record.expires_at - record.created_at).total_seconds(), 3 * 86400, delta=2)
            record.expires_at = timezone.now() - timedelta(seconds=1)
            record.save()
            self.assertEqual(self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json').status_code, 403)
        _, token = self.create_link()
        opened = self.public.post('/api/v1/candidate-forms/apply/', {'token': token}, format='json').data
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {opened['session']}")
        RecruitmentApplicationLink.objects.filter(applications__token_hash__isnull=False).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.public.get(self.session_url).status_code, 403)

    def test_existing_link_expiry_migration_caps_but_does_not_extend_deadlines(self):
        from importlib import import_module
        from django.apps import apps
        migration = import_module('apps.hr_core.migrations.0020_cv_direct_access_three_day_expiry')
        link, _ = self.create_link()
        record = RecruitmentApplicationLink.objects.get(pk=link['id'])
        deadline = record.created_at + timedelta(days=3)
        RecruitmentApplicationLink.objects.filter(pk=record.pk).update(expires_at=deadline + timedelta(days=20))
        migration.cap_existing_expiry(apps, Mock(connection=Mock(alias='default')))
        record.refresh_from_db()
        self.assertEqual(record.expires_at, deadline)
        earlier = deadline - timedelta(days=2)
        RecruitmentApplicationLink.objects.filter(pk=record.pk).update(expires_at=earlier)
        migration.cap_existing_expiry(apps, Mock(connection=Mock(alias='default')))
        record.refresh_from_db()
        self.assertEqual(record.expires_at, earlier)
        invitation = RecruitmentFormInvitation.objects.create(
            application_link=record, owner=self.hr, kind='cv', recipient='', token_hash='migration-test-token',
            expires_at=deadline + timedelta(days=10))
        migration.cap_existing_expiry(apps, Mock(connection=Mock(alias='default')))
        invitation.refresh_from_db()
        self.assertEqual(invitation.expires_at, earlier)

    def test_public_cannot_manage_links_or_sync_and_scheduled_task_is_configured(self):
        from django.conf import settings
        for path in ('recruitment-application-links/', 'recruitment-position-sync/'):
            self.assertIn(self.public.post(f'/api/v1/hr/{path}', {}, format='json').status_code, (401, 403))
        schedule = settings.CELERY_BEAT_SCHEDULE['hr-careers-position-sync']
        self.assertEqual(schedule['task'], 'hr_core.sync_rejlers_positions')
        self.assertGreaterEqual(schedule['schedule'], 300)
