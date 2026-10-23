import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import tests_candidate_forms as fixtures
from .models import RecruitmentFormAttachment, RecruitmentFormInvitation, RecruitmentFormSubmission
from .recruitment_cv import cv_source, extract_cv
from .recruitment_form_schema import display_sections


@override_settings(ROOT_URLCONF='apps.hr_core.tests_candidate_forms', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CVAssistedEntryTests(TestCase):
    setUp = fixtures.CandidateFormsTests.setUp
    invite = fixtures.CandidateFormsTests.invite
    session = fixtures.CandidateFormsTests.session
    cv_data = fixtures.CandidateFormsTests.cv_data
    upload_photo = fixtures.CandidateFormsTests.upload_photo

    def cv_file(self, text='Candidate Example\nEngineer\nEducation: BSc Engineering'):
        from docx import Document
        document = Document()
        document.add_paragraph(text)
        output = io.BytesIO(); document.save(output)
        return SimpleUploadedFile('candidate.docx', output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    def upload_cv(self):
        response = self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file()}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_photo_is_required_and_attachments_are_private_and_immutable_on_submit(self):
        invitation = self.invite()
        access = self.public.post(self.access, {'token': invitation['token']}, format='json').data
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {access['session']}")
        refused = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': self.cv_data()}, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertIn('photo', refused.data['detail'])
        photo = self.upload_photo()
        stored = RecruitmentFormAttachment.objects.get(pk=photo['id'])
        self.assertEqual(stored.content_type, 'image/jpeg')
        self.assertTrue(bytes(stored.content).startswith(b'\xff\xd8'))
        outsider = APIClient()
        self.assertEqual(outsider.get(f"/api/v1/candidate-forms/uploads/{photo['id']}/").status_code, 403)
        other = self.invite(); self.session(other)
        self.assertEqual(self.public.get(f"/api/v1/candidate-forms/uploads/{photo['id']}/").status_code, 404)
        # Replaced invitation is revoked; obtain an independent active draft.
        self.public.credentials(HTTP_AUTHORIZATION=f"Bearer {access['session']}")
        self.assertEqual(self.public.get(f"/api/v1/candidate-forms/uploads/{photo['id']}/").status_code, 403)

    def test_submission_snapshots_files_and_hr_export_contains_photo(self):
        invitation = self.invite(); self.session(invitation)
        cv = self.upload_cv()
        first_photo = RecruitmentFormAttachment.objects.get(invitation_id=invitation['id'], kind='photo')
        submitted = self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': self.cv_data()}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        first = RecruitmentFormSubmission.objects.get(invitation_id=invitation['id'])
        self.assertEqual(first.attachments.count(), 2)
        self.assertEqual(self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file()}, format='multipart').status_code, 409)
        download = self.hr_client.get(f"/api/v1/hr/recruitment-forms/{invitation['id']}/attachments/{cv['id']}/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        word = self.hr_client.get(f"/api/v1/hr/recruitment-forms/{invitation['id']}/export/1/")
        import zipfile
        with zipfile.ZipFile(io.BytesIO(word.content)) as archive:
            self.assertTrue(any(name.startswith('word/media/') for name in archive.namelist()))
        corrected = self.hr_client.post(f"/api/v1/hr/recruitment-forms/{invitation['id']}/review/", {'action': 'correction', 'version': 1, 'note': 'Update photo'}, format='json')
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file()}, format='multipart').status_code, 409)
        self.session(self.invite())
        next_photo = self.upload_photo()
        self.assertNotEqual(next_photo['id'], first_photo.pk)
        self.assertEqual(first.attachments.filter(kind='photo').get().pk, first_photo.pk)

    def test_expiry_blocks_upload_read_and_extraction(self):
        invitation = self.invite(); self.session(invitation)
        cv = self.upload_cv()
        RecruitmentFormInvitation.objects.filter(pk=invitation['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.public.get(f"/api/v1/candidate-forms/uploads/{cv['id']}/").status_code, 403)
        self.assertEqual(self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file()}, format='multipart').status_code, 403)
        with patch('apps.hr_core.recruitment_cv.extract_cv') as inference:
            self.assertEqual(self.public.post('/api/v1/candidate-forms/extract-cv/', {'attachment': cv['id']}, format='json').status_code, 403)
            inference.assert_not_called()

    def test_invalid_and_oversized_files_rejected_without_storing(self):
        self.session(self.invite())
        before = RecruitmentFormAttachment.objects.count()
        for kind, name, content in [('photo', 'photo.jpg', b'not an image'), ('cv', 'cv.pdf', b'not a PDF'), ('cv', 'cv.exe', b'MZ'), ('photo', 'huge.png', b'x' * (5 * 1024 * 1024 + 1))]:
            result = self.public.post('/api/v1/candidate-forms/uploads/', {'kind': kind, 'file': SimpleUploadedFile(name, content)}, format='multipart')
            self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(RecruitmentFormAttachment.objects.count(), before)

    def test_extraction_is_proposal_only_and_failure_preserves_draft(self):
        invitation = self.invite(); self.session(invitation)
        cv = self.upload_cv()
        before = RecruitmentFormInvitation.objects.get(pk=invitation['id']).draft
        with patch('apps.hr_core.recruitment_cv.extract_cv', return_value={'candidate': {'name': 'From CV'}}):
            result = self.public.post('/api/v1/candidate-forms/extract-cv/', {'attachment': cv['id']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['review_required'])
        self.assertEqual(RecruitmentFormInvitation.objects.get(pk=invitation['id']).draft, before)
        with patch('apps.hr_core.recruitment_cv.extract_cv', side_effect=RuntimeError('sensitive provider body')):
            failed = self.public.post('/api/v1/candidate-forms/extract-cv/', {'attachment': cv['id']}, format='json')
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn('sensitive', str(failed.data))
        self.assertEqual(RecruitmentFormInvitation.objects.get(pk=invitation['id']).draft, before)

    def test_local_extraction_needs_no_api_key_and_cannot_acknowledge(self):
        self.session(self.invite())
        upload = self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file(
            'Name: Candidate Example\nCurrent Position: Engineer\nYear of Birth: 1990\nEducation:\nBSc Engineering | Example University | 2010-2014\n'
            'Languages:\nEnglish: Fluent\nFields of Competence:\nTechnology: Python, AutoCAD\n'
            'Experience Summary:\nSix years in engineering.\nRelevant Project Experience:\n2019 - Present\n'
            'Title: Engineer\nCompany: Example Ltd\nKey Responsibilities: Design review\n'
            'Employment History:\n2014 - 2019\nTitle: Designer\nCompany: Previous Ltd\n'
            'Key Achievements:\nDelivered a major project\nCertifications:\nPMP')}, format='multipart')
        attachment = RecruitmentFormAttachment.objects.get(pk=upload.data['id'])
        with patch('apps.core.ai_consumer_clients.lazy_provider_client') as network:
            extracted = extract_cv(attachment)
            network.assert_not_called()
        self.assertEqual(extracted['candidate']['name'], 'Candidate Example')
        self.assertEqual(extracted['education'][0]['institution'], 'Example University')
        self.assertEqual(extracted['projects'][0]['company'], 'Example Ltd')
        self.assertEqual(extracted['employment'][0]['company'], 'Previous Ltd')
        self.assertEqual(extracted['employment'][0]['period'], '2014 - 2019')
        self.assertEqual(extracted['candidate']['birth_year'], '1990')
        self.assertEqual(extracted['achievements']['certifications'], 'PMP')
        self.assertFalse(extracted['acknowledgement']['accurate'])

    def test_scanned_pdf_runs_local_ocr(self):
        import fitz
        from PIL import Image
        image = Image.new('RGB', (600, 800), 'white')
        stream = io.BytesIO(); image.save(stream, format='PNG')
        document = fitz.open(); page = document.new_page()
        page.insert_image(page.rect, stream=stream.getvalue())
        attachment = SimpleNamespace(content=document.tobytes(), name='scan.pdf')
        document.close()
        with patch('pytesseract.image_to_string', return_value='Name: OCR Candidate\nEducation:\nBSc Civil Engineering') as ocr:
            extracted = extract_cv(attachment)
        self.assertEqual(extracted['candidate']['name'], 'OCR Candidate')
        self.assertEqual(ocr.call_args.kwargs['lang'], 'eng')
        self.assertLessEqual(ocr.call_args.kwargs['timeout'], 12)

    def test_hr_register_projects_saved_data_and_snapshot_files(self):
        invitation = self.invite(); self.session(invitation); cv = self.upload_cv()
        data = self.cv_data()
        data['competence'] = {'technology': 'Python, AutoCAD'}
        data['summary'] = {'text': 'Six years of engineering experience'}
        self.assertEqual(self.public.post(self.session_url, {'action': 'submit', 'revision': 0, 'data': data}, format='json').status_code, 200)
        record = RecruitmentFormInvitation.objects.get(pk=invitation['id']).candidate
        record.education = ''; record.experience = ''; record.skills = ''; record.save()
        response = self.hr_client.get(f'/api/v1/hr/recruitment-candidates/{record.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(data['education'][0]['qualification'], response.data['education'])
        self.assertEqual(response.data['experience'], data['summary']['text'])
        self.assertEqual(response.data['skills'], 'Python, AutoCAD')
        self.assertEqual(len(response.data['documents']), 2)
        self.assertEqual(response.data['submitted_forms'][0]['version'], 1)
        photo = self.hr_client.get(f'/api/v1/hr/recruitment-candidates/{record.pk}/photo/')
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(APIClient().get(f'/api/v1/hr/recruitment-candidates/{record.pk}/photo/').status_code, 401)
        record.education = 'HR confirmed qualification'; record.save()
        self.assertEqual(self.hr_client.get(f'/api/v1/hr/recruitment-candidates/{record.pk}/').data['education'], record.education)
        # Draft-only replacement files must not appear in the confirmed register.
        self.hr_client.post(f"/api/v1/hr/recruitment-forms/{invitation['id']}/review/", {'action': 'correction', 'version': 1, 'note': 'Update'}, format='json')
        self.assertEqual(self.public.post('/api/v1/candidate-forms/uploads/', {'kind': 'cv', 'file': self.cv_file()}, format='multipart').status_code, 409)
        self.session(self.invite())
        newer = self.upload_cv()
        current = self.hr_client.get(f'/api/v1/hr/recruitment-candidates/{record.pk}/').data
        self.assertIn(cv['id'], [item['id'] for item in current['documents']])
        self.assertNotIn(newer['id'], [item['id'] for item in current['documents']])

    def test_unfilled_rad_template_is_not_imported_as_candidate_data(self):
        from .recruitment_cv_local import parse_cv_text, CVTemplateError
        with self.assertRaises(CVTemplateError):
            parse_cv_text('Name: RASHID\nCurrent Position: SENIOR XYZ\nYear of Birth: 19XX\nEducation:\nBSc XXXX')

    def test_pdf_text_and_template_order(self):
        import fitz
        document = fitz.open(); page = document.new_page(); page.insert_text((40, 50), 'CV Candidate Example - Engineer')
        content = document.tobytes(); document.close()
        text, images, content_type = cv_source(content, 'example.pdf', True)
        self.assertIn('Engineer', text); self.assertEqual(images, []); self.assertEqual(content_type, 'application/pdf')
        titles = [section['title'].lower() for section in display_sections('cv')]
        self.assertEqual(titles[:7], ['candidate details', 'education', 'languages', 'fields of competence', 'experience summary', 'relevant project experience', 'employment history'])
        self.assertEqual(titles[7:9], ['key achievements', 'certifications (if any)'])
