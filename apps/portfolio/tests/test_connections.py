from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project
from apps.portfolio.connections import build_project_connections
from apps.rbac.models import (Module, Organization, Permission, Role, RoleModule,
                              RolePermission, UserPermissionOverride, UserProfile, UserRole)
from apps.rbac.module_actions import ensure_module_actions


class ProjectConnectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('connection-reader', email='connections@example.test')
        org, _ = Organization.objects.get_or_create(code='connections-test', defaults={'name': 'Connection tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='connection-reader-test', name='Connection reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.grant('project_control')

    def grant(self, *codes):
        for code in codes:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)

    @staticmethod
    def row(parent='P', sub='P-1', row_id=1, **values):
        return {'id': row_id, 'project_code': parent, 'subproject_code': sub, 'title': 'Workbook title', **values}

    @staticmethod
    def department(report, key, index=0):
        return next(item for item in report['rows'][index]['departments'] if item['key'] == key)

    def test_exact_child_precedes_parent_without_changing_codes_or_operational_data(self):
        parent = Project.objects.create(code='P', name='Recorded parent', progress=12)
        child = Project.objects.create(code='P-1', name='Recorded child', progress=34)
        data = build_project_connections(self.user, [self.row(sub='  p-1  ')])
        linked = data['rows'][0]
        self.assertEqual(linked['match_status'], 'matched')
        self.assertEqual(linked['match_level'], 'subproject')
        self.assertEqual(linked['project']['id'], str(child.pk))
        self.assertEqual(linked['project']['url'], f'/projects?project={child.pk}')
        self.assertNotIn('view=portfolio', linked['project']['url'])
        self.assertEqual(linked['subproject_code'], '  p-1  ')
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual((parent.name, parent.progress, child.name, child.progress),
                         ('Recorded parent', 12, 'Recorded child', 34))

    def test_parent_fallback_counts_unique_records_not_rows_and_totals_ignore_pagination(self):
        parent = Project.objects.create(code='P', name='Recorded parent')
        Project.objects.create(code='OUTSIDE', name='Not in workbook')
        rows = [self.row(sub='P-1'), self.row(sub='P-2', row_id=2), self.row('NEW', 'NEW-1', row_id=3)]
        data = build_project_connections(self.user, rows, limit=1, offset=1)
        self.assertEqual(data['totals'], {'registered_projects': 2, 'workbook_projects': 2,
                         'matched_projects': 1, 'matched_rows': 2, 'unmatched_rows': 1,
                         'ambiguous_rows': 0, 'restricted_rows': 0})
        self.assertEqual(data['rows'][0]['match_level'], 'parent')
        self.assertEqual(data['rows'][0]['project']['id'], str(parent.pk))
        self.assertEqual(data['total_rows'], 3)
        self.assertEqual(data['returned_rows'], 1)
        self.assertTrue(data['truncated'])
        self.assertEqual(Project.objects.count(), 2)
        last_page = build_project_connections(self.user, rows, limit=1, offset=2)
        self.assertFalse(last_page['truncated'])
        self.assertEqual((last_page['offset'], last_page['limit']), (2, 1))
        self.assertEqual(last_page['totals'], data['totals'])

    def test_deleted_hidden_and_ambiguous_child_block_parent_fallback(self):
        parent = Project.objects.create(code='P', name='Visible parent')
        hidden = Project.objects.create(code='P-HIDDEN', name='Do not expose hidden title')
        Project.objects.create(code='P-DELETED', name='Do not expose deleted title', is_deleted=True)
        Project.objects.create(code='P-DUP', name='First case variant')
        Project.objects.create(code='p-dup', name='Second case variant')
        rows = [self.row(sub=code, row_id=i) for i, code in enumerate(['p-hidden', 'p-deleted', 'P-DUP'])]
        with patch('apps.portfolio.connections.accessible_enterprise_projects',
                   return_value=Project.objects.filter(pk=parent.pk)):
            data = build_project_connections(self.user, rows)
        self.assertEqual([row['match_status'] for row in data['rows']], ['restricted', 'restricted', 'ambiguous'])
        self.assertEqual(data['totals']['registered_projects'], 1)
        self.assertEqual(data['totals']['restricted_rows'], 2)
        self.assertEqual(data['totals']['ambiguous_rows'], 1)
        self.assertTrue(all(row['project'] is None and not row['departments'] for row in data['rows']))
        self.assertNotIn(hidden.name, str(data))

    def test_normalization_does_not_strip_leading_zero_decimal_or_punctuation(self):
        Project.objects.create(code='00123', name='Different identifier')
        Project.objects.create(code='A-B', name='Different punctuation')
        data = build_project_connections(self.user, [self.row('123', '123.0'), self.row('AB', 'A B', row_id=2)])
        self.assertEqual(data['totals']['unmatched_rows'], 2)
        self.assertEqual(Project.objects.count(), 2)

    def test_reused_subproject_code_under_different_source_parents_requires_review(self):
        Project.objects.create(code='P', name='Accessible parent')
        Project.objects.create(code='OTHER', name='Other parent')
        Project.objects.create(code='SHARED', name='One canonical record')
        data = build_project_connections(self.user, [self.row('P', 'SHARED'), self.row('OTHER', 'shared', row_id=2)])
        self.assertEqual(data['totals']['ambiguous_rows'], 2)
        self.assertEqual(data['totals']['matched_rows'], 0)
        self.assertTrue(all(row['project'] is None for row in data['rows']))

    def test_source_collision_outside_filter_and_visibility_cannot_become_a_match(self):
        from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource

        source = PortfolioSource.objects.create()
        snapshot = PortfolioSnapshot.objects.create(source=source, sha256='a' * 64,
                    parser_version='test', file_name='synthetic.xlsx', reporting_date=timezone.localdate(), row_count=2)
        project = Project.objects.create(code='P-1', name='Accessible project')
        visible = PortfolioRow.objects.create(snapshot=snapshot, project_code='P', subproject_code='P-1',
                                               source_row=8, title='Visible source title', business_unit='Shown')
        PortfolioRow.objects.create(snapshot=snapshot, project_code='SECRET-PARENT', subproject_code='p-1',
                                     source_row=9, title='Private source title', business_unit='Hidden')
        selected_rows = list(snapshot.rows.filter(pk=visible.pk).values())
        with patch('apps.portfolio.connections.accessible_enterprise_projects',
                   return_value=Project.objects.filter(pk=project.pk)):
            data = build_project_connections(self.user, selected_rows)
        self.assertEqual(data['totals']['ambiguous_rows'], 1)
        self.assertEqual(data['totals']['workbook_projects'], 1)
        self.assertEqual(data['totals']['matched_rows'], 0)
        self.assertEqual(data['rows'][0]['match_status'], 'ambiguous')
        self.assertIsNone(data['rows'][0]['project'])
        self.assertEqual(data['rows'][0]['departments'], [])
        self.assertNotIn('SECRET-PARENT', str(data))
        self.assertNotIn('Private source title', str(data))
        self.assertNotIn('Hidden', str(data))

    def test_source_collision_does_not_include_other_snapshot_versions(self):
        from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource

        source = PortfolioSource.objects.create()
        current = PortfolioSnapshot.objects.create(source=source, sha256='a' * 64,
                    parser_version='test', file_name='current.xlsx', reporting_date=timezone.localdate(), row_count=1)
        older = PortfolioSnapshot.objects.create(source=source, sha256='b' * 64,
                    parser_version='test', file_name='older.xlsx', reporting_date=timezone.localdate(), row_count=1)
        PortfolioRow.objects.create(snapshot=current, project_code='P', subproject_code='P-1', source_row=8)
        PortfolioRow.objects.create(snapshot=older, project_code='OTHER', subproject_code='P-1', source_row=8)
        project = Project.objects.create(code='P-1', name='Accessible project')
        data = build_project_connections(self.user, list(current.rows.values()))
        self.assertEqual(data['rows'][0]['match_status'], 'matched')
        self.assertEqual(data['rows'][0]['project']['id'], str(project.pk))

    def test_denied_project_register_does_not_query_or_disclose_totals(self):
        permission = Permission.objects.get(module__code='project_control', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        with patch('apps.portfolio.connections.accessible_enterprise_projects') as read:
            data = build_project_connections(self.user, [self.row()])
        read.assert_not_called()
        self.assertEqual(data['status'], 'restricted')
        self.assertIsNone(data['totals']['registered_projects'])
        self.assertEqual(data['rows'], [])

    def test_each_department_requires_its_own_grant_even_for_staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        Project.objects.create(code='P-1', name='Accessible')
        data = build_project_connections(self.user, [self.row()])
        for key in ['procurement', 'sales', 'finance', 'qhse']:
            item = self.department(data, key)
            self.assertEqual(item['status'], 'restricted')
            self.assertIsNone(item['record_count'])
            self.assertIsNone(item['url'])
        self.assertEqual(self.department(data, 'project_control')['counts']['registered_projects'], 1)

    def test_procurement_uses_saved_fks_and_does_not_infer_from_matching_text(self):
        from apps.procurement.models import Project as ProcurementProject, PurchaseOrder, Vendor
        self.grant('procurement', 'procurement_orders')
        project = Project.objects.create(code='P-1', name='Accessible')
        master = ProcurementProject.objects.create(project_number='LEGACY', project_name='Legacy title',
                                                    enterprise_project=project)
        vendor = Vendor.objects.create(name='Synthetic vendor', vendor_code='CONNECTION-VENDOR')
        PurchaseOrder.objects.create(po_number='LINKED', vendor=vendor, total_amount=1, enterprise_project=project)
        PurchaseOrder.objects.create(po_number='TEXT-ONLY', vendor=vendor, total_amount=2, project_number='P-1')
        data = build_project_connections(self.user, [self.row()])
        item = self.department(data, 'procurement')
        self.assertEqual(item['record_count'], 2)
        self.assertEqual(item['counts'], {'project_registers': 1, 'purchase_orders': 1, 'purchase_requisitions': None})
        self.assertEqual(item['url'], f'/procurement/projects/{master.pk}')
        self.assertIsNone(PurchaseOrder.objects.get(po_number='TEXT-ONLY').enterprise_project_id)

    def test_project_control_counts_actual_live_objects_without_join_multiplication(self):
        from apps.project_control.models import Estimate, WBSNode
        project = Project.objects.create(code='P-1', name='Accessible')
        for index in range(2):
            WBSNode.objects.create(project=project, code=str(index), name='Recorded WBS')
        WBSNode.objects.create(project=project, code='deleted', name='Deleted WBS', is_deleted=True)
        for index in range(3):
            Estimate.objects.create(project=project, title='Recorded estimate', version=index + 1)
        item = self.department(build_project_connections(self.user, [self.row()]), 'project_control')
        self.assertEqual(item['counts']['wbs_nodes'], 2)
        self.assertEqual(item['counts']['estimates'], 3)
        self.assertEqual(item['record_count'], 6)

    def test_sales_only_saved_converted_project_and_existing_row_visibility(self):
        from apps.sales.models import Client, Deal
        self.grant('sales_opportunities')
        project = Project.objects.create(code='P-1', name='Accessible')
        client = Client.objects.create(client_code='CONNECTION', company_name='Synthetic client', account_manager=self.user)
        deal = Deal.objects.create(deal_code='CONVERTED', deal_name='Recorded opportunity', client=client,
                                   owner=self.user, estimated_value=100, expected_close_date=timezone.localdate(),
                                   converted_project=project)
        Deal.objects.create(deal_code='P-1', deal_name=project.name, client=client,
                            owner=self.user, estimated_value=100, expected_close_date=timezone.localdate())
        with patch('apps.portfolio.connections._visible', side_effect=lambda qs, *args: qs):
            item = self.department(build_project_connections(self.user, [self.row()]), 'sales')
        self.assertEqual(item['record_count'], 1)
        self.assertEqual(item['url'], f'/sales/opportunities?record={deal.pk}')
        with patch('apps.portfolio.connections._visible', side_effect=lambda qs, *args: qs.none()):
            item = self.department(build_project_connections(self.user, [self.row()]), 'sales')
        self.assertEqual(item['record_count'], 0)
        self.assertEqual(item['status'], 'unlinked')

    def test_qhse_separate_exact_code_and_ambiguous_case_variants_require_review(self):
        from apps.qhse.models import QHSERunningProject
        self.grant('qhse_detailed')
        Project.objects.create(code='P-1', name='Accessible')
        QHSERunningProject.objects.create(sr_no=1, project_no='p-1', project_title='Source title',
                                         client='Synthetic client', project_manager='Synthetic manager')
        item = self.department(build_project_connections(self.user, [self.row()]), 'qhse')
        self.assertEqual(item['record_count'], 1)
        self.assertEqual(item['match_basis'], 'exact_project_code')
        self.assertEqual(item['url'], '/qhse/general/detailed')
        QHSERunningProject.objects.create(sr_no=2, project_no='P-1', project_title='Other source title',
                                         client='Synthetic client', project_manager='Synthetic manager')
        item = self.department(build_project_connections(self.user, [self.row()]), 'qhse')
        self.assertEqual(item['status'], 'review_required')
        self.assertIsNone(item['record_count'])

    def test_source_failure_is_unknown_and_does_not_change_identity(self):
        Project.objects.create(code='P-1', name='Accessible')
        with patch('apps.portfolio.connections._procurement', side_effect=DatabaseError('private details')):
            data = build_project_connections(self.user, [self.row()])
        self.assertEqual(data['rows'][0]['match_status'], 'matched')
        self.assertEqual(self.department(data, 'procurement')['status'], 'unavailable')
        self.assertIsNone(self.department(data, 'procurement')['record_count'])
        self.assertNotIn('private details', str(data))

    def test_empty_workbook_still_reports_registered_count(self):
        Project.objects.create(code='REGISTERED', name='Accessible')
        data = build_project_connections(self.user, [])
        self.assertEqual(data['totals']['registered_projects'], 1)
        self.assertEqual(data['totals']['workbook_projects'], 0)
        self.assertEqual(data['rows'], [])

    def test_explicit_department_deny_masks_existing_records_and_keeps_other_departments(self):
        from apps.procurement.models import PurchaseOrder, Vendor
        self.grant('procurement_orders')
        project = Project.objects.create(code='P-1', name='Accessible')
        vendor = Vendor.objects.create(name='Synthetic vendor', vendor_code='DENIED-VENDOR')
        PurchaseOrder.objects.create(po_number='DENIED-PO', vendor=vendor, total_amount=1, enterprise_project=project)
        permission = Permission.objects.get(module__code='procurement_orders', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        data = build_project_connections(self.user, [self.row()])
        item = self.department(data, 'procurement')
        self.assertEqual(item['status'], 'restricted')
        self.assertIsNone(item['record_count'])
        self.assertNotIn('counts', item)
        self.assertIsNone(item['url'])
        self.assertEqual(self.department(data, 'project_control')['status'], 'linked')

    def test_invalid_optional_source_does_not_erase_other_departments(self):
        Project.objects.create(code='P-1', name='Accessible')
        with patch('apps.portfolio.connections._finance', side_effect=ValueError('source data invalid')):
            data = build_project_connections(self.user, [self.row()])
        self.assertEqual(self.department(data, 'finance')['status'], 'unavailable')
        self.assertEqual(self.department(data, 'project_control')['status'], 'linked')

    def test_finance_counts_recorded_invoices_by_verified_code_not_textual_project_id(self):
        from apps.invoice_tracker.models import CustomerInvoice
        self.grant('finance_outgoing')
        project = Project.objects.create(code='P-1', name='Accessible')
        CustomerInvoice.objects.create(invoice_number='CON-INV-1', rad_project_no=' p-1 ', invoice_amount=100)
        CustomerInvoice.objects.create(invoice_number='CON-INV-2', rad_project_no='OTHER',
                                       project_id=str(project.pk), project_name=project.name, invoice_amount=100)
        item = self.department(build_project_connections(self.user, [self.row()]), 'finance')
        self.assertEqual(item['status'], 'linked')
        self.assertEqual(item['record_count'], 1)
        self.assertEqual(item['url'], '/finance/outgoing-invoices?queue=all&project_exact=P-1')
        self.assertEqual(CustomerInvoice.objects.count(), 2)

    def test_finance_does_not_broaden_its_stricter_internal_space_rule(self):
        from apps.invoice_tracker.models import CustomerInvoice
        self.grant('finance_outgoing')
        Project.objects.create(code='P 1', name='Accessible')
        CustomerInvoice.objects.create(invoice_number='CON-SPACED', rad_project_no='P  1', invoice_amount=100)
        item = self.department(build_project_connections(self.user, [self.row(sub='P  1')]), 'finance')
        self.assertEqual(item['status'], 'unlinked')
        self.assertEqual(item['record_count'], 0)

    def test_handover_uses_existing_shared_register_grant_without_deal_access(self):
        from apps.sales.models import Client, Deal, ProjectHandover, Quote
        self.grant('sales_handovers')
        project = Project.objects.create(code='P-1', name='Accessible')
        client = Client.objects.create(client_code='HANDOVER', company_name='Synthetic client', account_manager=self.user)
        deal = Deal.objects.create(deal_code='HANDOVER-DEAL', deal_name='Recorded opportunity', client=client,
                                   owner=self.user, estimated_value=100, expected_close_date=timezone.localdate())
        quote = Quote.objects.create(quote_number='HANDOVER-QUOTE', deal=deal, client=client,
                                     subtotal=100, total_amount=100, valid_until=timezone.localdate())
        handover = ProjectHandover.objects.create(opportunity=deal, proposal=quote, owner=self.user,
                    project_manager=self.user, contract_value=100, currency='AED',
                    signed_contract_reference='Recorded contract', project=project)
        item = self.department(build_project_connections(self.user, [self.row()]), 'sales')
        self.assertEqual(item['record_count'], 1)
        self.assertEqual(item['counts']['converted_opportunities'], None)
        self.assertEqual(item['counts']['project_handovers'], 1)
        self.assertEqual(item['url'], f'/sales/project-handovers?record={handover.pk}')
