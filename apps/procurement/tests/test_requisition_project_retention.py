"""Ordinary PR edits retain confirmed projects until references really change."""

import json
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project as EnterpriseProject
from apps.procurement.models import Project, ProjectRelationshipResolution, PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.project_relationships import resolve_project_relationship
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='project-retention-pr')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/requisitions/'


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='')
class RequisitionProjectRetentionTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user('retention-owner', email='retention-owner@example.test')
        self.editor = User.objects.create_user('retention-editor', email='retention-editor@example.test')
        self.reader = User.objects.create_user('retention-reader', email='retention-reader@example.test')
        org, _ = Organization.objects.get_or_create(code='retention', defaults={'name': 'Retention'})
        module, _ = Module.objects.get_or_create(code='procurement_requisitions', defaults={'name': 'Purchase Requisitions'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = Role.objects.create(code='retention-reviewers', name='Retention reviewers', level=3)
        RoleModule.objects.create(role=role, module=module)
        for user in (self.editor, self.reader):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.roles.clear()
            profile.status, profile.is_deleted = 'active', False
            profile.save()
            UserRole.objects.create(user_profile=profile, role=role)
            for permission in module.permissions.filter(action__in=('read', 'create', 'update'), is_active=True):
                UserPermissionOverride.objects.create(user_profile=profile, permission=permission,
                    allowed=permission.action == 'read' or user == self.editor)
        self.alpha = EnterpriseProject.objects.create(code='RET-100', name='Confirmed Alpha')
        self.beta = EnterpriseProject.objects.create(code='RET-200', name='Confirmed Beta')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RETENTION-PR', issued_by=self.owner, status='approved',
            project='Legacy free text', project_details=[{'project_number': 'LEGACY-42', 'project_name': 'Old label'}],
            price_remarks_data={'source_approval_reviews': [{'recorded': 'preserve'}]},
        )
        self.url = f'{BASE}{self.pr.pk}/'
        self.client = APIClient()
        self.client.force_authenticate(self.editor)

    def confirm_project(self, project=None):
        resolve_project_relationship(record_type='purchase_requisition', record_id=self.pr.pk,
            enterprise_project_id=(project or self.alpha).pk, expected_project_id=None,
            user=self.editor, reason='Verified against source')
        self.pr.refresh_from_db()

    def test_guarded_full_form_multipart_edit_keeps_reconciled_project_and_audit(self):
        self.confirm_project()
        audit = list(ProjectRelationshipResolution.objects.values())
        source_metadata = self.pr.price_remarks_data
        response = self.client.patch(self.url, {
            'pr_number': self.pr.pr_number, 'project': self.pr.project,
            'project_details': json.dumps(self.pr.project_details),
            'description_reason': 'Updated procurement explanation',
        }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.enterprise_project_id, self.alpha.pk)
        self.assertEqual(self.pr.description_reason, 'Updated procurement explanation')
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.price_remarks_data, source_metadata)
        self.assertEqual(list(ProjectRelationshipResolution.objects.values()), audit)

    def test_equivalent_codes_rows_and_uuid_representations_keep_manual_choice(self):
        master = Project.objects.create(project_number='RET-MASTER', project_name='Legacy master', enterprise_project=self.beta)
        self.pr.project_details = [
            {'project_number': self.beta.code, 'project_id': str(master.pk), 'project_name': 'Old label'},
            {'project_code': 420123, 'project_name': 'Numeric source'},
        ]
        self.pr.save(update_fields=['project_details'])
        self.confirm_project()
        response = self.client.patch(self.url, {
            'project': ' LEGACY  free TEXT ',
            'project_details': [
                {'code': '420123', 'project_name': 'Edited presentation'},
                {'project_number': ' ret-200 ', 'project_id': master.pk.hex.upper()},
                {'code': 'RET-200'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.enterprise_project_id, self.alpha.pk)

    def test_changed_reference_recomputes_exact_project(self):
        self.confirm_project()
        response = self.client.patch(self.url, {'project': self.beta.code}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.enterprise_project_id, self.beta.pk)

    def test_changed_master_identity_recomputes_project(self):
        master = Project.objects.create(project_number='RET-MASTER', project_name='Beta master', enterprise_project=self.beta)
        self.confirm_project()
        response = self.client.patch(self.url, {'project_details': [{'project_id': str(master.pk)}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.enterprise_project_id, self.beta.pk)

    def test_removed_or_conflicting_references_clear_automatic_link(self):
        for references in ([], [{'code': self.alpha.code}, {'code': self.beta.code}]):
            with self.subTest(references=references):
                self.pr.project = self.alpha.code
                self.pr.project_details = []
                self.pr.enterprise_project = self.alpha
                self.pr.save(update_fields=['project', 'project_details', 'enterprise_project'])
                response = self.client.patch(self.url, {'project': '', 'project_details': references}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.pr.refresh_from_db()
                self.assertIsNone(self.pr.enterprise_project_id)

    def test_unchanged_unlinked_source_is_not_silently_linked_by_unrelated_edit(self):
        self.pr.project = self.alpha.code
        self.pr.save(update_fields=['project'])
        response = self.client.patch(self.url, {'project': self.alpha.code, 'description_reason': 'Unrelated'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertIsNone(self.pr.enterprise_project_id)

    def test_create_still_resolves_exact_references(self):
        response = self.client.post(BASE, {'pr_number': 'RETENTION-NEW', 'project': self.alpha.code}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(PurchaseRequisition.objects.get(pk=response.data['id']).enterprise_project_id, self.alpha.pk)

    def test_explicit_authorized_project_change_and_clear_remain_supported(self):
        self.confirm_project()
        for project_id in (self.beta.pk, None):
            with self.subTest(project_id=project_id):
                response = self.client.patch(self.url, {
                    'enterprise_project': project_id, 'project': self.pr.project,
                    'project_details': self.pr.project_details,
                }, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.pr.refresh_from_db()
                self.assertEqual(self.pr.enterprise_project_id, project_id)

    def test_reader_cannot_change_references_or_explicit_project(self):
        self.confirm_project()
        before = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        self.client.force_authenticate(self.reader)
        for payload in ({'project': self.beta.code}, {'enterprise_project': self.beta.pk}):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format='json')
                self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before)

    def test_reconciliation_after_validation_is_preserved_under_update_lock(self):
        serializer = PurchaseRequisitionSerializer(self.pr, data={
            'project': self.pr.project, 'project_details': self.pr.project_details,
            'description_reason': 'Concurrent ordinary edit',
        }, partial=True, context={'request': SimpleNamespace(user=self.editor)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.confirm_project()
        saved = serializer.save()
        self.assertEqual(saved.enterprise_project_id, self.alpha.pk)

    def test_automatic_candidate_is_rechecked_against_locked_current_references(self):
        serializer = PurchaseRequisitionSerializer(self.pr, data={'project': self.beta.code}, partial=True,
            context={'request': SimpleNamespace(user=self.editor)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['enterprise_project'], self.beta)
        # Another editor already saved these references and the operator then
        # confirmed a different canonical project before this stale save.
        PurchaseRequisition.objects.filter(pk=self.pr.pk).update(project=self.beta.code)
        self.confirm_project()
        saved = serializer.save()
        self.assertEqual(saved.enterprise_project_id, self.alpha.pk)
