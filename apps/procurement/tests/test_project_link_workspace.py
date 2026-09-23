"""Project Links lists and guarded, audited PO connections."""

from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.file_replica.models import ReplicaScope, ReplicaSource
from apps.file_replica.paths import path_key
from apps.procurement.models import Project as ProcurementProject
from apps.procurement.models import ProjectRelationshipResolution, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.project_link_workspace import build_project_link_workspace
from apps.procurement.views import ProjectViewSet, PurchaseOrderViewSet
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('projects', ProjectViewSet, basename='link-workspace-project')
router.register('orders', PurchaseOrderViewSet, basename='link-workspace-order')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/projects/'


@override_settings(ROOT_URLCONF=__name__)
class ProjectLinkWorkspaceTests(TestCase):
    def setUp(self):
        cache.clear()
        organization = Organization.objects.create(code='link-workspace', name='Link workspace')
        self.reader = self.make_user('reader', organization, ['read'])
        self.editor = self.make_user('editor', organization, ['read', 'update'])
        self.root = self.make_user('root', organization, ['read', 'update'])
        self.root.is_superuser = True
        self.root.save(update_fields=['is_superuser'])
        self.alpha = Project.objects.create(code='5900985', name='PE4 and PE5 revamp', currency='AED')
        self.beta = Project.objects.create(code='5900986', name='Gas compression', currency='USD')
        self.deleted = Project.objects.create(code='DELETED', name='Removed project', is_deleted=True)
        self.master = ProcurementProject.objects.create(project_number='5900985', project_name=self.alpha.name, enterprise_project=self.alpha)
        self.source = ReplicaSource.objects.create(name='Office project names', root_path=r'\\test-server\Projects')
        self.alpha_scope = self.make_scope('5900985-PE4 and PE5 revamp', self.alpha)
        self.beta_scope = self.make_scope('5900986-Gas compression', self.beta)
        for project in [self.alpha, self.beta]:
            for user in [self.reader, self.editor]:
                ProjectMember.objects.create(project=project, user=user, role='viewer', is_active=True)
        self.vendor = Vendor.objects.create(vendor_code='WORKSPACE-V', name='Control equipment supplier')
        self.unlinked = self.make_order('PO-UNLINKED')
        self.linked = self.make_order('PO-LINKED', enterprise_project=self.alpha)
        self.cancelled = self.make_order('PO-CANCELLED', enterprise_project=self.beta, status='cancelled')
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    def make_user(self, name, organization, actions):
        user = get_user_model().objects.create_user(f'workspace-{name}', email=f'workspace-{name}@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
        UserRole.objects.filter(user_profile=profile).delete()
        role = Role.objects.create(code=f'workspace-{name}', name=f'Workspace {name}', level=3)
        UserRole.objects.create(user_profile=profile, role=role)
        module, _ = Module.objects.update_or_create(code='procurement', defaults={'name': 'Procurement', 'is_active': True})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action__in=actions, is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        control, _ = Module.objects.update_or_create(code='project_control', defaults={'name': 'Project Control', 'is_active': True})
        ensure_module_actions(Module, Permission, module_ids=[control.pk])
        RoleModule.objects.create(role=role, module=control)
        for permission in control.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        orders, _ = Module.objects.update_or_create(code='procurement_orders', defaults={'name': 'Purchase Orders', 'is_active': True})
        ensure_module_actions(Module, Permission, module_ids=[orders.pk])
        return user

    def make_scope(self, name, project=None):
        return ReplicaScope.objects.create(source=self.source, relative_path=name, path_key=path_key(name), project=project, access_enabled=project is not None)

    def make_order(self, number, **fields):
        return PurchaseOrder.objects.create(
            po_number=number, title=f'Equipment package {number}', category='other',
            vendor=self.vendor, total_amount=Decimal('12456.78'), currency='AED', **fields,
        )

    def test_returns_all_projects_and_pos_with_exact_current_links_and_money(self):
        response = self.client.get(BASE + 'link-workspace/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([project['id'] for project in response.data['projects']], [str(self.alpha_scope.pk), str(self.beta_scope.pk)])
        alpha = response.data['projects'][0]
        self.assertEqual(alpha['project_id'], str(self.alpha.pk))
        self.assertEqual(alpha['folder_name'], self.alpha_scope.relative_path)
        self.assertEqual(alpha['procurement_project_id'], str(self.master.pk))
        self.assertEqual(alpha['purchase_order_count'], 1)
        self.assertIsNone(response.data['projects'][1]['procurement_project_id'])
        orders = response.data['purchase_orders']
        self.assertEqual(orders['count'], 3)
        self.assertEqual(orders['page_size'], 25)
        by_id = {row['id']: row for row in orders['results']}
        self.assertIsNone(by_id[str(self.unlinked.pk)]['current_project'])
        linked = by_id[str(self.linked.pk)]
        self.assertEqual(linked['current_project'], {'id': str(self.alpha.pk), 'code': self.alpha.code, 'name': self.alpha.name})
        self.assertEqual(linked['total_amount'], '12456.78')
        self.assertEqual(linked['vendor_name'], self.vendor.name)
        self.assertEqual(by_id[str(self.cancelled.pk)]['status'], 'cancelled')

    def test_search_filters_and_pages_do_not_filter_the_left_project_list(self):
        searches = [self.linked.po_number, 'equipment package PO-LINKED', self.alpha.name, self.alpha.code]
        for search in searches:
            with self.subTest(search=search):
                data = self.client.get(BASE + 'link-workspace/', {'search': search}).data
                self.assertEqual(data['purchase_orders']['count'], 1)
                self.assertEqual(data['purchase_orders']['results'][0]['id'], str(self.linked.pk))
                self.assertEqual(len(data['projects']), 2)
        data = self.client.get(BASE + 'link-workspace/', {'search': self.vendor.name, 'page_size': 1, 'page': 2}).data
        self.assertEqual(data['purchase_orders']['count'], 3)
        self.assertEqual(data['purchase_orders']['total_pages'], 3)
        self.assertEqual(len(data['purchase_orders']['results']), 1)
        self.assertEqual(data['purchase_orders']['results'][0]['id'], str(self.linked.pk))
        self.assertEqual(self.client.get(BASE + 'link-workspace/', {'link_status': 'linked'}).data['purchase_orders']['count'], 2)
        self.assertEqual(self.client.get(BASE + 'link-workspace/', {'link_status': 'unlinked'}).data['purchase_orders']['count'], 1)
        data = self.client.get(BASE + 'link-workspace/', {'project_id': self.alpha.pk}).data
        self.assertEqual(data['purchase_orders']['results'][0]['id'], str(self.linked.pk))

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL legacy project schema regression')
    def test_legacy_unique_project_id_without_primary_key_can_list_and_select_orders(self):
        zero = Project.objects.create(code='5900999', name='Project without purchase orders')
        zero_scope = self.make_scope('5900999-Project without purchase orders', zero)
        self.client.force_authenticate(self.root)
        # Match the synchronized schema described by procurement migration 0035.
        # A temporary shadow preserves the real table and its FK dependencies.
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMPORARY TABLE core_project (LIKE public.core_project INCLUDING DEFAULTS) ON COMMIT DROP')
            cursor.execute('INSERT INTO pg_temp.core_project SELECT * FROM public.core_project')
            cursor.execute('CREATE UNIQUE INDEX legacy_project_id_unique ON pg_temp.core_project (id)')
            cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid = 'pg_temp.core_project'::regclass AND contype = 'p'")
            self.assertEqual(cursor.fetchone()[0], 0)
        try:
            with transaction.atomic():
                response = self.client.get(BASE + 'link-workspace/', {
                    'page': 1, 'page_size': 15, 'search': '', 'link_status': 'unlinked',
                })
                self.assertEqual(response.status_code, 200, response.data)
                counts = {row['id']: row['purchase_order_count'] for row in response.data['projects']}
                self.assertEqual(counts[str(self.alpha_scope.pk)], 1)
                self.assertEqual(counts[str(self.beta_scope.pk)], 1)  # Includes the cancelled order.
                self.assertEqual(counts[str(zero_scope.pk)], 0)
                self.assertEqual(response.data['purchase_orders']['count'], 1)
                self.assertEqual(response.data['purchase_orders']['results'][0]['id'], str(self.unlinked.pk))
                selected = self.client.get(BASE + 'link-workspace/', {
                    'scope_id': self.beta_scope.pk, 'link_status': 'linked',
                })
                self.assertEqual(selected.status_code, 200, selected.data)
                self.assertEqual(selected.data['purchase_orders']['count'], 1)
                self.assertEqual(selected.data['purchase_orders']['results'][0]['id'], str(self.cancelled.pk))
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TABLE pg_temp.core_project')

    def test_read_is_bounded_and_does_not_mutate_business_records(self):
        before = list(PurchaseOrder.objects.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            payload = build_project_link_workspace({}, self.reader)
        self.assertEqual(payload['purchase_orders']['count'], 3)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for query in queries))
        self.assertEqual(before, list(PurchaseOrder.objects.order_by('pk').values()))
        self.assertFalse(ProjectRelationshipResolution.objects.exists())
        for index in range(20):
            self.make_order(f'PO-QUERY-{index}', enterprise_project=self.alpha)
        with CaptureQueriesContext(connection) as larger:
            build_project_link_workspace({}, self.reader)
        self.assertLessEqual(len(larger), len(queries) + 1)

    def test_permissions_are_effective_and_anonymous_or_denied_cannot_read(self):
        self.assertEqual(self.client.get(BASE + 'link-workspace/').data['permissions'], {'can_connect': False, 'can_create_po': False})
        role = self.reader.rbac_profile.roles.get(code='workspace-reader')
        orders = Module.objects.get(code='procurement_orders')
        RoleModule.objects.create(role=role, module=orders)
        for permission in orders.permissions.filter(action='create', is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        self.assertFalse(self.client.get(BASE + 'link-workspace/').data['permissions']['can_create_po'])
        self.client.force_authenticate(self.editor)
        self.assertTrue(self.client.get(BASE + 'link-workspace/').data['permissions']['can_connect'])
        permission = Permission.objects.filter(module__code='procurement', action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.editor.rbac_profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(BASE + 'link-workspace/').status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(BASE + 'link-workspace/').status_code, [401, 403])

    def test_invalid_filters_and_out_of_range_pages_are_explicit(self):
        for params in [{'page': 0}, {'page_size': 101}, {'project_id': 'bad'}, {'project_id': self.deleted.pk}, {'link_status': 'guess'}]:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(BASE + 'link-workspace/', params).status_code, 400)
        self.assertEqual(self.client.get(BASE + 'link-workspace/', {'page': 2}).status_code, 404)
        empty = self.client.get(BASE + 'link-workspace/', {'search': 'nothing-matches'}).data['purchase_orders']
        self.assertEqual(empty['count'], 0)
        self.assertEqual(empty['results'], [])

    def test_connect_uses_existing_audit_and_rejects_stale_project_without_changing_money(self):
        before = PurchaseOrder.objects.values().get(pk=self.unlinked.pk)
        data = {'record_type': 'purchase_order', 'record_id': str(self.unlinked.pk),
                'enterprise_project_id': str(self.alpha.pk), 'expected_project_id': None,
                'reason': f'Linked from Project Links: {self.alpha.code} — {self.alpha.name}'}
        self.assertEqual(self.client.post(BASE + 'resolve-relationship/', data, format='json').status_code, 403)
        self.client.force_authenticate(self.editor)
        response = self.client.post(BASE + 'resolve-relationship/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        after = PurchaseOrder.objects.values().get(pk=self.unlinked.pk)
        for field, value in before.items():
            if field not in {'enterprise_project_id', 'updated_at'}:
                self.assertEqual(after[field], value, field)
        audit = ProjectRelationshipResolution.objects.get()
        self.assertEqual(audit.resolved_by, self.editor)
        self.assertEqual(audit.enterprise_project, self.alpha)
        self.assertEqual(audit.reason, data['reason'])
        data['enterprise_project_id'] = str(self.beta.pk)
        stale = self.client.post(BASE + 'resolve-relationship/', data, format='json')
        self.assertEqual(stale.status_code, 400, stale.data)
        self.assertIn('expected_project_id', stale.data)
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 1)

    def folder_payload(self, scope=None, order=None, **changes):
        scope = scope or self.alpha_scope
        order = order or self.unlinked
        data = {'scope_id': str(scope.pk), 'order_id': str(order.pk),
                'expected_project_id': order.enterprise_project_id,
                'expected_folder_project_id': scope.project_id}
        data.update(changes)
        return data

    def test_admin_sees_all_discovered_folder_names_while_source_paused_without_writes(self):
        unmapped = self.make_scope('5900990-New compressor project')
        invalid = self.make_scope('General documents')
        self.source.enabled = False
        self.source.save()
        self.client.force_authenticate(self.root)
        before = Project.objects.count()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(BASE + 'link-workspace/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['projects']), 4)
        rows = {row['id']: row for row in response.data['projects']}
        self.assertTrue(rows[str(unmapped.pk)]['can_prepare'])
        self.assertIsNone(rows[str(unmapped.pk)]['project_id'])
        self.assertFalse(rows[str(invalid.pk)]['can_prepare'])
        self.assertTrue(rows[str(invalid.pk)]['preparation_error'])
        self.assertEqual(Project.objects.count(), before)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for query in queries))
        unmapped.refresh_from_db()
        self.assertIsNone(unmapped.project_id)
        selected = self.client.get(BASE + 'link-workspace/', {'scope_id': unmapped.pk})
        self.assertEqual(selected.data['purchase_orders']['count'], 0)

    def test_unpublished_folder_names_are_not_exposed_to_ordinary_users(self):
        hidden = self.make_scope('5900990-Private project')
        self.assertNotIn(str(hidden.pk), [row['id'] for row in self.client.get(BASE + 'link-workspace/').data['projects']])
        self.client.force_authenticate(self.editor)
        response = self.client.post(BASE + 'connect-folder-order/', self.folder_payload(hidden), format='json')
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(self.client.get(BASE + 'link-workspace/', {'scope_id': hidden.pk}).status_code, 404)

    def test_explicit_connect_prepares_one_project_and_retains_folder_publication(self):
        scope = self.make_scope('5900990-New compressor project')
        self.client.force_authenticate(self.root)
        response = self.client.post(BASE + 'connect-folder-order/', self.folder_payload(scope), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        project = Project.objects.get(code='5900990')
        self.assertEqual(project.name, 'New compressor project')
        scope.refresh_from_db()
        self.assertEqual(scope.project_id, project.pk)
        self.assertFalse(scope.access_enabled)
        self.unlinked.refresh_from_db()
        self.assertEqual(self.unlinked.enterprise_project_id, project.pk)
        self.assertEqual(ProjectRelationshipResolution.objects.get().resolved_by, self.root)
        repeated = self.client.post(BASE + 'connect-folder-order/', self.folder_payload(scope), format='json')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertFalse(repeated.data['changed'])
        self.assertEqual(Project.objects.filter(code='5900990').count(), 1)
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 1)
        listing = self.client.get(BASE + 'link-workspace/', {'scope_id': scope.pk})
        self.assertEqual(listing.data['purchase_orders']['count'], 1)

    def test_stale_order_rolls_back_preparation_and_stale_folder_cannot_redirect_order(self):
        scope = self.make_scope('5900990-New compressor project')
        self.client.force_authenticate(self.root)
        stale = self.folder_payload(scope, self.linked, expected_project_id=None)
        response = self.client.post(BASE + 'connect-folder-order/', stale, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Project.objects.filter(code='5900990').exists())
        scope.refresh_from_db()
        self.assertIsNone(scope.project_id)
        self.assertFalse(ProjectRelationshipResolution.objects.exists())
        stale = self.folder_payload(self.alpha_scope)
        self.alpha_scope.project = self.beta
        self.alpha_scope.save()
        response = self.client.post(BASE + 'connect-folder-order/', stale, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('expected_folder_project_id', response.data)
        self.unlinked.refresh_from_db()
        self.assertIsNone(self.unlinked.enterprise_project_id)

    def test_prepare_requires_create_permissions_and_explicit_concurrency_fields(self):
        scope = self.make_scope('5900990-New compressor project')
        self.client.force_authenticate(self.editor)
        data = {'scope_id': str(self.alpha_scope.pk), 'expected_folder_project_id': self.alpha.pk}
        self.assertEqual(self.client.post(BASE + 'prepare-folder-project/', data, format='json').status_code, 403)
        self.client.force_authenticate(self.root)
        for missing in ['expected_project_id', 'expected_folder_project_id']:
            payload = self.folder_payload(scope)
            payload.pop(missing)
            response = self.client.post(BASE + 'connect-folder-order/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn(missing, response.data)
        permission = Permission.objects.filter(module__code='project_control', action='create', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.root.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.client.post(BASE + 'connect-folder-order/', self.folder_payload(scope), format='json').status_code, 403)
        self.assertFalse(Project.objects.filter(code='5900990').exists())

    def test_create_po_preparation_returns_canonical_identity_without_creating_an_order(self):
        self.client.force_authenticate(self.root)
        before = PurchaseOrder.objects.count()
        mapped = self.client.post(BASE + 'prepare-folder-project/', {
            'scope_id': str(self.alpha_scope.pk), 'expected_folder_project_id': self.alpha.pk,
        }, format='json')
        self.assertEqual(mapped.status_code, 200, mapped.data)
        self.assertEqual(mapped.data['id'], str(self.alpha.pk))
        self.assertEqual(mapped.data['procurement_project_id'], str(self.master.pk))
        scope = self.make_scope('5900990-New compressor project')
        prepared = self.client.post(BASE + 'prepare-folder-project/', {
            'scope_id': str(scope.pk), 'expected_folder_project_id': None,
        }, format='json')
        self.assertEqual(prepared.status_code, 200, prepared.data)
        self.assertEqual(prepared.data['code'], '5900990')
        self.assertIsNone(prepared.data['procurement_project_id'])
        self.assertEqual(PurchaseOrder.objects.count(), before)
        self.assertFalse(ProjectRelationshipResolution.objects.exists())
        scope.refresh_from_db()
        self.assertFalse(scope.access_enabled)

    def test_existing_exact_code_is_reused_and_ambiguous_or_invalid_new_codes_fail(self):
        self.client.force_authenticate(self.root)
        scope = self.make_scope('5900985-Original project directory')
        data = self.folder_payload(scope, expected_folder_project_id=self.alpha.pk)
        response = self.client.post(BASE + 'connect-folder-order/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Project.objects.filter(code='5900985').count(), 1)
        duplicate = self.make_scope('5900990-First directory')
        self.make_scope('5900990-Second directory')
        for rejected in [duplicate, self.make_scope('Shared tender documents')]:
            response = self.client.post(BASE + 'connect-folder-order/', self.folder_payload(rejected, self.cancelled), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Project.objects.filter(code='5900990').exists())

    def test_available_requisitions_include_canonical_project_without_per_row_queries(self):
        PurchaseRequisition.objects.create(pr_number='PR-PRESET-ONE', enterprise_project=self.alpha)
        self.client.force_authenticate(self.root)
        endpoint = '/api/v1/procurement/orders/available-requisitions/'
        with CaptureQueriesContext(connection) as first:
            response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]['enterprise_project'], self.alpha.pk)
        for number in range(10):
            PurchaseRequisition.objects.create(pr_number=f'PR-PRESET-{number}', enterprise_project=self.beta)
        with CaptureQueriesContext(connection) as larger:
            response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(len(larger), len(first) + 1)
