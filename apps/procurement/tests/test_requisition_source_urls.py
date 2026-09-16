"""Imported requisition originals get fresh URLs within existing read access."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from botocore.exceptions import ClientError, NoCredentialsError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from django.utils.text import get_valid_filename
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.requisition_source_documents import (
    refreshed_requisition_attachments,
)
from apps.rbac.models import (
    Module, Organization, Permission, UserPermissionOverride, UserProfile,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)

MEDIA_BASE = 'https://source-bucket.s3.eu-west-1.amazonaws.com/media/'
PR_NUMBER = 'RAD-PRJ-PR-0042_2026'
PR_ID = UUID('53cae61a-7eb7-4d02-811f-0a03901b42f1')
FILENAME = f'{PR_NUMBER}_Purchase_Requisition_2026-09-16.pdf'
LEGACY_KEY = f'procurement/signed_requisitions/2026/{FILENAME}'
EXPIRED_URL = f'{MEDIA_BASE}{LEGACY_KEY}?X-Amz-Date=20200101T000000Z&X-Amz-Expires=3600'
FRESH_URL = f'{MEDIA_BASE}{LEGACY_KEY}?X-Amz-Signature=fresh'
STORAGE_URL = 'django.core.files.storage.default_storage.url'
LEGACY_DIGEST = 'a1b2c3d4e5f6' + 'ab' * 26


@override_settings(MEDIA_URL=MEDIA_BASE)
class RequisitionSourceURLTests(SimpleTestCase):
    def requisition(self, attachments):
        return SimpleNamespace(pk=PR_ID, id=PR_ID, pr_number=PR_NUMBER, attachments=attachments)

    def attachment(self, **values):
        attachment = {
            'type': 'signed_purchase_requisition_pdf',
            'filename': FILENAME,
            'url': EXPIRED_URL,
            's3_url': EXPIRED_URL,
            'uploaded_at': '2026-09-16T09:00:00Z',
            'extraction': {'supplier': 'Original supplier'},
        }
        attachment.update(values)
        return attachment

    def test_expired_legacy_s3_urls_refresh_without_mutating_stored_metadata(self):
        original = [self.attachment()]
        before = deepcopy(original)
        requisition = self.requisition(original)

        with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(requisition)

        storage_url.assert_called_once_with(LEGACY_KEY)
        self.assertEqual(refreshed[0], {**before[0], 'url': FRESH_URL, 's3_url': FRESH_URL})
        self.assertEqual(requisition.attachments, before)

    def test_legacy_attachment_with_only_s3_url_is_supported(self):
        attachment = self.attachment()
        del attachment['url']
        with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition([attachment]))
        storage_url.assert_called_once_with(LEGACY_KEY)
        self.assertEqual(refreshed[0]['s3_url'], FRESH_URL)

    def test_uuid_bound_storage_key_refreshes_without_needing_old_url(self):
        key = f'procurement/signed_requisitions/{PR_ID}/2026/{FILENAME}'
        attachment = self.attachment(storage_key=key, url='', s3_url='')
        with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition([attachment]))
        storage_url.assert_called_once_with(key)
        self.assertEqual(refreshed[0]['url'], FRESH_URL)
        self.assertEqual(refreshed[0]['s3_url'], FRESH_URL)
        self.assertEqual(refreshed[0]['storage_key'], key)

    def test_uuid_bound_original_survives_pr_number_rename(self):
        key = f'procurement/signed_requisitions/{PR_ID}/2026/{FILENAME}'
        for fields in (
            {'storage_key': key, 'url': '', 's3_url': ''},
            {'url': MEDIA_BASE + key, 's3_url': MEDIA_BASE + key},
        ):
            original = self.attachment(**fields)
            requisition = self.requisition([original])
            requisition.pr_number = 'RENAMED-PR-0042_2026'
            with self.subTest(fields=fields), patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
                refreshed = refreshed_requisition_attachments(requisition)
            storage_url.assert_called_once_with(key)
            self.assertEqual(refreshed[0]['url'], FRESH_URL)
            self.assertEqual(refreshed[0]['filename'], FILENAME)
            self.assertEqual(requisition.attachments, [original])

    def test_legacy_filename_still_requires_the_current_pr_number(self):
        requisition = self.requisition([self.attachment()])
        requisition.pr_number = 'RENAMED-PR-0042_2026'
        with patch(STORAGE_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(requisition)
        storage_url.assert_not_called()
        self.assertEqual(refreshed[0]['url'], '')

    def test_s3_key_alias_is_validated_and_refreshed(self):
        key = f'procurement/signed_requisitions/{PR_ID}/2026/{FILENAME}'
        with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition([
                self.attachment(s3_key=key),
            ]))
        storage_url.assert_called_once_with(key)
        self.assertEqual(refreshed[0]['url'], FRESH_URL)

    def test_storage_collision_suffix_is_supported(self):
        key = LEGACY_KEY.replace('.pdf', '_aB12cD3.pdf')
        attachment = self.attachment(url=MEDIA_BASE + key, s3_url=MEDIA_BASE + key)
        with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
            refreshed_requisition_attachments(self.requisition([attachment]))
        storage_url.assert_called_once_with(key)

    def test_old_digest_named_urls_refresh_for_year_and_unknown_folders(self):
        filename = get_valid_filename('Approved supplier PR (signed).pdf')
        for folder in ('2026', 'unknown'):
            key = f'procurement/signed_requisitions/{folder}/{LEGACY_DIGEST[:12]}_{filename}'
            old_url = f'{MEDIA_BASE}{key}?X-Amz-Signature=expired'
            fresh_url = f'{MEDIA_BASE}{key}?X-Amz-Signature=fresh'
            original = [self.attachment(
                filename=filename, sha256=LEGACY_DIGEST, url=old_url, s3_url=old_url,
            )]
            before = deepcopy(original)
            with self.subTest(folder=folder), patch(STORAGE_URL, return_value=fresh_url) as storage_url:
                refreshed = refreshed_requisition_attachments(self.requisition(original))
                storage_url.assert_called_once_with(key)
                self.assertEqual(refreshed, [{**before[0], 'url': fresh_url, 's3_url': fresh_url}])
                self.assertEqual(original, before)

    def test_old_digest_named_explicit_key_uses_the_same_attachment_digest(self):
        key = f'procurement/signed_requisitions/2026/{LEGACY_DIGEST[:12]}_signed-original.pdf'
        for field in ('storage_key', 's3_key'):
            with self.subTest(field=field), patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
                refreshed = refreshed_requisition_attachments(self.requisition([
                    self.attachment(sha256=LEGACY_DIGEST, **{field: key}),
                ]))
                storage_url.assert_called_once_with(key)
                self.assertEqual(refreshed[0]['url'], FRESH_URL)

    def test_old_digest_named_source_requires_complete_matching_digest(self):
        key = f'procurement/signed_requisitions/2026/{LEGACY_DIGEST[:12]}_signed-original.pdf'
        old_url = f'{MEDIA_BASE}{key}?X-Amz-Signature=expired'
        for digest in (None, '', LEGACY_DIGEST[:12], 'b' * 64, 'x' * 64):
            for explicit_key in (False, True):
                attachment = self.attachment(url=old_url, s3_url=old_url)
                if digest is not None:
                    attachment['sha256'] = digest
                if explicit_key:
                    attachment['storage_key'] = key
                with self.subTest(digest=digest, explicit_key=explicit_key), patch(STORAGE_URL) as storage_url:
                    refreshed = refreshed_requisition_attachments(self.requisition([attachment]))
                    storage_url.assert_not_called()
                    self.assertEqual(refreshed[0]['url'], '')
                    self.assertEqual(refreshed[0]['s3_url'], '')

    def test_old_digest_named_url_does_not_borrow_another_attachments_digest(self):
        key = f'procurement/signed_requisitions/unknown/{LEGACY_DIGEST[:12]}_signed-original.pdf'
        with patch(STORAGE_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition([
                self.attachment(url=MEDIA_BASE + key, s3_url=MEDIA_BASE + key),
                {'type': 'quotation', 'sha256': LEGACY_DIGEST},
            ]))
        storage_url.assert_not_called()
        self.assertEqual(refreshed[0]['url'], '')

    def test_matching_digest_does_not_authorize_a_foreign_storage_host(self):
        key = f'procurement/signed_requisitions/2026/{LEGACY_DIGEST[:12]}_signed-original.pdf'
        foreign_url = f'https://foreign.example.test/media/{key}'
        with patch(STORAGE_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition([
                self.attachment(sha256=LEGACY_DIGEST, url=foreign_url, s3_url=foreign_url),
            ]))
        storage_url.assert_not_called()
        self.assertEqual(refreshed[0]['url'], '')
        self.assertEqual(refreshed[0]['s3_url'], '')

    @override_settings(MEDIA_URL='/media/')
    def test_relative_local_storage_url_is_refreshed(self):
        local_url = '/media/' + LEGACY_KEY
        attachment = self.attachment(url=local_url + '?stale=1', s3_url=local_url + '?stale=1')
        storage = FileSystemStorage(base_url='/media/')
        with patch('apps.procurement.services.requisition_source_documents.default_storage', storage):
            refreshed = refreshed_requisition_attachments(self.requisition([attachment]))
        self.assertEqual(refreshed[0]['url'], local_url)
        self.assertEqual(refreshed[0]['s3_url'], local_url)

    def test_foreign_host_scheme_and_media_prefix_never_sign_a_key(self):
        urls = [
            f'https://foreign.example.test/media/{LEGACY_KEY}',
            f'https://source-bucket.s3.eu-west-1.amazonaws.com.evil.test/media/{LEGACY_KEY}',
            f'http://source-bucket.s3.eu-west-1.amazonaws.com/media/{LEGACY_KEY}',
            f'https://source-bucket.s3.eu-west-1.amazonaws.com/media-other/{LEGACY_KEY}',
            f'https://source-bucket.s3.eu-west-1.amazonaws.com@foreign.example.test/media/{LEGACY_KEY}',
            f'//source-bucket.s3.eu-west-1.amazonaws.com/media/{LEGACY_KEY}',
        ]
        for url in urls:
            with self.subTest(url=url), patch(STORAGE_URL) as storage_url:
                attachment = self.attachment(url=url, s3_url=url)
                refreshed_requisition_attachments(self.requisition([attachment]))
                storage_url.assert_not_called()

    @override_settings(MEDIA_URL='/media/')
    def test_local_media_prefix_does_not_authorize_a_remote_host(self):
        url = f'https://foreign.example.test/media/{LEGACY_KEY}'
        with patch(STORAGE_URL) as storage_url:
            refreshed_requisition_attachments(self.requisition([
                self.attachment(url=url, s3_url=url),
            ]))
        storage_url.assert_not_called()

    def test_cross_record_and_traversal_storage_keys_are_never_signed(self):
        invalid_keys = [
            f'procurement/signed_requisitions/{UUID(int=1)}/2026/{FILENAME}',
            LEGACY_KEY.replace(PR_NUMBER, 'RAD-PRJ-PR-0099_2026'),
            f'procurement/signed_requisitions/2026/../2026/{FILENAME}',
            f'procurement/signed_requisitions/2026/%2e%2e/2026/{FILENAME}',
            LEGACY_KEY.replace('/', '\\'),
            '/' + LEGACY_KEY,
            LEGACY_KEY.replace('/2026/', '/2025/'),
            'private/secret.pdf',
            LEGACY_KEY + '/secret.pdf',
        ]
        for key in invalid_keys:
            with self.subTest(key=key), patch(STORAGE_URL) as storage_url:
                refreshed_requisition_attachments(self.requisition([
                    self.attachment(storage_key=key),
                ]))
                storage_url.assert_not_called()

    def test_invalid_explicit_key_clears_response_urls_without_falling_back_or_mutating_record(self):
        for field in ('storage_key', 's3_key'):
            original = [self.attachment(**{field: 'private/secret.pdf'})]
            before = deepcopy(original)
            with self.subTest(field=field), patch(STORAGE_URL) as storage_url:
                refreshed = refreshed_requisition_attachments(self.requisition(original))
                storage_url.assert_not_called()
                self.assertEqual(refreshed, [{**before[0], 'url': '', 's3_url': ''}])
                self.assertEqual(original, before)

    def test_storage_failure_clears_expired_urls_without_losing_original_metadata(self):
        original = [self.attachment()]
        before = deepcopy(original)
        failures = (
            OSError('Private storage is unavailable'),
            NoCredentialsError(),
            ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private'}}, 'GetObject'),
        )
        for failure in failures:
            with self.subTest(error=type(failure).__name__), patch(STORAGE_URL, side_effect=failure):
                refreshed = refreshed_requisition_attachments(self.requisition(original))
                self.assertEqual(refreshed, [{**before[0], 'url': '', 's3_url': ''}])
                self.assertEqual(original, before)

    def test_legacy_url_with_encoded_traversal_or_other_pr_filename_is_not_signed(self):
        invalid_keys = [
            f'procurement/signed_requisitions/2026/%2e%2e/2026/{FILENAME}',
            f'procurement/signed_requisitions/2026/%252e%252e/2026/{FILENAME}',
            LEGACY_KEY.replace(PR_NUMBER, 'RAD-PRJ-PR-0099_2026'),
            'private/secret.pdf',
        ]
        for key in invalid_keys:
            with self.subTest(key=key), patch(STORAGE_URL) as storage_url:
                url = MEDIA_BASE + key
                refreshed_requisition_attachments(self.requisition([
                    self.attachment(url=url, s3_url=url),
                ]))
                storage_url.assert_not_called()

    def test_unrelated_attachments_are_preserved_without_signing(self):
        attachments = [
            self.attachment(type='quotation'),
            {'filename': 'specification.pdf', 'url': EXPIRED_URL},
            {'type': 'signed_purchase_order_pdf', 'url': EXPIRED_URL},
        ]
        with patch(STORAGE_URL) as storage_url:
            refreshed = refreshed_requisition_attachments(self.requisition(attachments))
        storage_url.assert_not_called()
        self.assertEqual(refreshed, attachments)

    def test_attachment_metadata_remains_read_only_in_serializer(self):
        self.assertTrue(PurchaseRequisitionSerializer().fields['attachments'].read_only)


@override_settings(ROOT_URLCONF=__name__, MEDIA_URL=MEDIA_BASE)
class RequisitionSourceURLAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.owner = users.objects.create_user('pr-source-owner', email='pr-owner@example.test')
        self.assigned = users.objects.create_user('pr-source-assigned', email='pr-assigned@example.test')
        self.unassigned = users.objects.create_user('pr-source-unassigned', email='pr-unassigned@example.test')
        org, _ = Organization.objects.get_or_create(
            code='pr-source-tests', defaults={'name': 'PR source tests'},
        )
        self.profiles = {}
        for user in (self.owner, self.assigned, self.unassigned):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.roles.clear()
            self.profiles[user.pk] = profile
        module, _ = Module.objects.get_or_create(
            code='procurement_requisitions', defaults={'name': 'Purchase requisitions'},
        )
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.original_attachments = [{
            'type': 'signed_purchase_requisition_pdf',
            'filename': FILENAME,
            'url': EXPIRED_URL,
            's3_url': EXPIRED_URL,
            'uploaded_at': '2026-09-16T09:00:00Z',
        }]
        self.requisition = PurchaseRequisition.objects.create(
            id=PR_ID,
            pr_number=PR_NUMBER,
            issued_by=self.owner,
            status='approved',
            approval_workflow_config=[{
                'user_id': str(self.assigned.pk),
                'user_email': self.assigned.email,
                'role': 'Project Manager',
                'status': 'approved',
            }],
            attachments=deepcopy(self.original_attachments),
        )
        self.url = f'/api/v1/procurement/requisitions/{self.requisition.pk}/'
        self.content_url = self.url + 'uploaded-documents/0/content/'
        self.client = APIClient()

    def test_owner_and_assigned_reader_receive_fresh_original_at_approved_and_converted_status(self):
        for status in ('approved', 'converted'):
            self.requisition.status = status
            self.requisition.save(update_fields=['status'])
            for user in (self.owner, self.assigned):
                with self.subTest(status=status, user=user.username):
                    self.client.force_authenticate(user)
                    with patch(STORAGE_URL, return_value=FRESH_URL) as storage_url:
                        response = self.client.get(self.url)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data['attachments'][0]['url'], FRESH_URL)
                    self.assertEqual(response.data['attachments'][0]['s3_url'], FRESH_URL)
                    storage_url.assert_called_once_with(LEGACY_KEY)
                    self.requisition.refresh_from_db()
                    self.assertEqual(self.requisition.attachments, self.original_attachments)

    def test_unassigned_reader_cannot_refresh_or_read_original(self):
        self.client.force_authenticate(self.unassigned)
        with patch(STORAGE_URL) as storage_url, patch('django.core.files.storage.default_storage.open') as storage_open:
            for url in (self.url, self.content_url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403, response.data)
            storage_url.assert_not_called()
            storage_open.assert_not_called()

    def test_anonymous_reader_cannot_refresh_or_read_original(self):
        with patch(STORAGE_URL) as storage_url, patch('django.core.files.storage.default_storage.open') as storage_open:
            for url in (self.url, self.content_url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (401, 403))
            storage_url.assert_not_called()
            storage_open.assert_not_called()

    def test_explicit_read_deny_still_blocks_owner_and_assigned_reader(self):
        permission = Permission.objects.filter(
            module__code='procurement_requisitions', action='read', is_active=True,
        ).first()
        self.assertIsNotNone(permission)
        for user in (self.owner, self.assigned):
            UserPermissionOverride.objects.create(
                user_profile=self.profiles[user.pk], permission=permission, allowed=False,
            )
            cache.clear()
            self.client.force_authenticate(user)
            with self.subTest(user=user.username), patch(STORAGE_URL) as storage_url, patch('django.core.files.storage.default_storage.open') as storage_open:
                for url in (self.url, self.content_url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 403, response.data)
                storage_url.assert_not_called()
                storage_open.assert_not_called()

    def test_serialized_content_links_use_saved_attachment_positions_without_mutation(self):
        original = [
            {'type': 'quotation', 'filename': 'quote.pdf', 'url': EXPIRED_URL},
            deepcopy(self.original_attachments[0]),
            {'type': 'signed_purchase_requisition_pdf', 'filename': 'missing.pdf', 'storage_key': 'private/secret.pdf'},
        ]
        self.requisition.attachments = original
        self.requisition.save(update_fields=['attachments'])
        self.client.force_authenticate(self.owner)
        with patch(STORAGE_URL, return_value=FRESH_URL):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        attachments = response.data['attachments']
        self.assertNotIn('content_url', attachments[0])
        self.assertEqual(attachments[1]['content_url'], self.url + 'uploaded-documents/1/content/')
        self.assertEqual(attachments[2]['content_url'], '')
        self.requisition.refresh_from_db()
        self.assertEqual(self.requisition.attachments, original)

    @override_settings(
        MIDDLEWARE=[*settings.MIDDLEWARE, 'django.middleware.clickjacking.XFrameOptionsMiddleware'],
        X_FRAME_OPTIONS='DENY',
    )
    def test_content_returns_exact_original_bytes_with_existing_read_access_and_frame_policy(self):
        original_pdf = b'%PDF-1.4\nOriginal signed source bytes\x00\xff\n%%EOF'
        for status in ('approved', 'converted'):
            self.requisition.status = status
            self.requisition.save(update_fields=['status'])
            for user in (self.owner, self.assigned):
                self.client.force_authenticate(user)
                with self.subTest(status=status, user=user.username), patch(
                    'django.core.files.storage.default_storage.open', return_value=BytesIO(original_pdf),
                ) as storage_open, patch(STORAGE_URL) as storage_url:
                    response = self.client.get(self.content_url)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(b''.join(response.streaming_content), original_pdf)
                    self.assertEqual(response['Content-Type'], 'application/pdf')
                    self.assertTrue(response['Content-Disposition'].startswith('inline;'))
                    self.assertIn(FILENAME, response['Content-Disposition'])
                    self.assertEqual(response['Cache-Control'], 'private, no-store')
                    self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                    self.assertEqual(response['X-Frame-Options'], 'DENY')
                    storage_open.assert_called_once_with(LEGACY_KEY, 'rb')
                    storage_url.assert_not_called()

    def test_content_rejects_unrelated_foreign_cross_record_and_traversal_paths(self):
        self.requisition.attachments = [
            {'type': 'quotation', 'storage_key': LEGACY_KEY},
            {'type': 'signed_purchase_requisition_pdf', 'storage_key': 'private/secret.pdf'},
            {'type': 'signed_purchase_requisition_pdf', 'storage_key': f'procurement/signed_requisitions/{UUID(int=1)}/2026/{FILENAME}'},
            {'type': 'signed_purchase_requisition_pdf', 'storage_key': f'procurement/signed_requisitions/{PR_ID}/2026/../2026/{FILENAME}'},
            {'type': 'signed_purchase_requisition_pdf', 'url': f'https://foreign.example.test/media/{LEGACY_KEY}'},
        ]
        self.requisition.save(update_fields=['attachments'])
        self.client.force_authenticate(self.owner)
        with patch('django.core.files.storage.default_storage.open') as storage_open:
            for index in (*range(5), 999):
                response = self.client.get(self.url + f'uploaded-documents/{index}/content/')
                self.assertEqual(response.status_code, 404, response.data)
            storage_open.assert_not_called()

    def test_content_reports_missing_files_and_storage_errors_without_private_details(self):
        self.client.force_authenticate(self.owner)
        failures = [
            (FileNotFoundError('private filesystem path'), 404),
            (ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'private key'}}, 'GetObject'), 404),
            (NoCredentialsError(), 503),
            (ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private bucket'}}, 'GetObject'), 503),
        ]
        for failure, expected_status in failures:
            with self.subTest(failure=type(failure).__name__), patch(
                'django.core.files.storage.default_storage.open', side_effect=failure,
            ):
                response = self.client.get(self.content_url)
            self.assertEqual(response.status_code, expected_status, response.data)
            self.assertNotIn('private', str(response.data))
